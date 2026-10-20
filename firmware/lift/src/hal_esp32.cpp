#include "hal_esp32.h"

#include <driver/pcnt.h>

#include "config.h"

namespace {

// PCNT の 1 単位。A/B の 4 逓倍で数える定番の構成
//（ch0: パルス=A・制御=B、ch1: パルス=B・制御=A。
// ch1 は ch0 と逆の符号。ESP-IDF の rotary_encoder の例と同じ）。
constexpr pcnt_unit_t kPcntUnit = PCNT_UNIT_0;

// hal_core の設定値と driver/pcnt.h が食い違っていないことをビルドで確かめる。
static_assert(kPcntInc == PCNT_COUNT_INC, "kPcntInc differs from PCNT_COUNT_INC");
static_assert(kPcntDec == PCNT_COUNT_DEC, "kPcntDec differs from PCNT_COUNT_DEC");
static_assert(kPcntKeep == PCNT_MODE_KEEP, "kPcntKeep differs from PCNT_MODE_KEEP");
static_assert(kPcntReverse == PCNT_MODE_REVERSE, "kPcntReverse differs from PCNT_MODE_REVERSE");

// 上限・下限に達するたびに割り込みが足す溢れの累積。loop() が読むときは
// クリティカル区間でカウンタと一緒に読む（合計 = 溢れの累積 + 今のカウンタ）。
volatile int32_t g_overflow = 0;
portMUX_TYPE g_pcnt_mux = portMUX_INITIALIZER_UNLOCKED;

// ENC_PCNT_LIMIT に達した溢れを累積する。カウンタはハードウェアが 0 に戻す
//（TRM）ので clear しない（割り込みに入るまでに数えた分を捨ててしまう）。
// IRAM に置く。呼ぶのはレジスタ読み書きだけで、Serial も delay も使わない。
// 32 bit の読み書きは途切れないのでクリティカル区間は要らない。
void IRAM_ATTR pcnt_limit_isr(void* /*arg*/) {
  uint32_t status = 0;
  pcnt_get_event_status(kPcntUnit, &status);
  if ((status & PCNT_EVT_H_LIM) != 0) {
    g_overflow += ENC_PCNT_LIMIT;
  }
  if ((status & PCNT_EVT_L_LIM) != 0) {
    g_overflow -= ENC_PCNT_LIMIT;
  }
}

// ENC_GLITCH_FILTER_NS [ns] を APB 80 MHz のクロック数に直す。上限 1023 に丸める。
uint16_t glitch_filter_ticks() {
  const int64_t ticks = (static_cast<int64_t>(ENC_GLITCH_FILTER_NS) * 80) / 1000;
  if (ticks < 0) {
    return 0;
  }
  if (ticks > 1023) {
    return 1023;
  }
  return static_cast<uint16_t>(ticks);
}

// 数え方は hal_core の kPcntQuadEncoder から取る（符号の判断を native で縛るため）。
// ここで別の値を書かないこと。
void config_pcnt_channel(pcnt_channel_t channel, int pulse_pin, int ctrl_pin, int pos_mode,
                         int neg_mode, int lctrl_mode, int hctrl_mode) {
  pcnt_config_t cfg = {};
  cfg.pulse_gpio_num = pulse_pin;
  cfg.ctrl_gpio_num = ctrl_pin;
  cfg.channel = channel;
  cfg.unit = kPcntUnit;
  cfg.pos_mode = static_cast<pcnt_count_mode_t>(pos_mode);
  cfg.neg_mode = static_cast<pcnt_count_mode_t>(neg_mode);
  cfg.lctrl_mode = static_cast<pcnt_ctrl_mode_t>(lctrl_mode);
  cfg.hctrl_mode = static_cast<pcnt_ctrl_mode_t>(hctrl_mode);
  cfg.counter_h_lim = static_cast<int16_t>(ENC_PCNT_LIMIT);
  cfg.counter_l_lim = static_cast<int16_t>(-ENC_PCNT_LIMIT);
  pcnt_unit_config(&cfg);
}

}  // namespace

void LiftEsp32Hal::begin() {
  // --- モータ（MD10C）---
  pinMode(MOTOR_DIR_PIN, OUTPUT);
  digitalWrite(MOTOR_DIR_PIN, LOW);
  if (!ledcSetup(PWM_CH, PWM_FREQ_HZ, PWM_RESOLUTION)) {
    Serial.printf("[hal] LEDC ch%u を %u Hz / %u bit で用意できません\n", PWM_CH, PWM_FREQ_HZ,
                  PWM_RESOLUTION);
  }
  ledcAttachPin(MOTOR_PWM_PIN, PWM_CH);
  // 起動直後は必ず停止。電源が落ちたあとに再起動しても、この 1 行で止まる
  ledcWrite(PWM_CH, 0);

  // --- 下端リミットスイッチ（NC 配線。押すと開いて HIGH。断線も HIGH）---
  pinMode(BOTTOM_PIN, INPUT_PULLUP);

  // --- 高さのエンコーダ（オープンコレクタ。外付けで 3.3 V へプルアップが前提）---
  pinMode(ENC_A_PIN, INPUT_PULLUP);
  pinMode(ENC_B_PIN, INPUT_PULLUP);
  config_pcnt_channel(PCNT_CHANNEL_0, ENC_A_PIN, ENC_B_PIN, kPcntQuadEncoder.ch0_pos,
                      kPcntQuadEncoder.ch0_neg, kPcntQuadEncoder.ch0_lctrl,
                      kPcntQuadEncoder.ch0_hctrl);
  config_pcnt_channel(PCNT_CHANNEL_1, ENC_B_PIN, ENC_A_PIN, kPcntQuadEncoder.ch1_pos,
                      kPcntQuadEncoder.ch1_neg, kPcntQuadEncoder.ch1_lctrl,
                      kPcntQuadEncoder.ch1_hctrl);
  pcnt_set_filter_value(kPcntUnit, glitch_filter_ticks());
  pcnt_filter_enable(kPcntUnit);
  // 上限・下限の割り込みで溢れの累積へ移す（unit_config が切るのでここで有効にする）
  pcnt_event_enable(kPcntUnit, PCNT_EVT_H_LIM);
  pcnt_event_enable(kPcntUnit, PCNT_EVT_L_LIM);
  pcnt_isr_service_install(0);
  pcnt_isr_handler_add(kPcntUnit, &pcnt_limit_isr, nullptr);
  pcnt_counter_pause(kPcntUnit);
  g_overflow = 0;
  pcnt_counter_clear(kPcntUnit);
  pcnt_counter_resume(kPcntUnit);
  pcnt_ready_ = true;
}

void LiftEsp32Hal::poll(uint32_t now_ms) {
  if (!pcnt_ready_) {
    return;
  }
  // 溢れの累積と今のカウンタを一緒に読み、合計にする
  int32_t overflow = 0;
  int16_t raw = 0;
  portENTER_CRITICAL(&g_pcnt_mux);
  overflow = g_overflow;
  pcnt_get_counter_value(kPcntUnit, &raw);
  portEXIT_CRITICAL(&g_pcnt_mux);
  total_count_ = encoder_total_guarded(total_count_, overflow, raw, ENC_PCNT_LIMIT);
  // 下端スイッチの状態と一緒に渡す。判断は EncoderHeight 側
  encoder_.update(total_count_, bottom_pressed(), ENC_UP_SIGN);
  height_mm_ = encoder_.height_mm();
  height_ok_ = encoder_.ok();
  height_at_ms_ = now_ms;
}

void LiftEsp32Hal::motor_set(LiftDir dir, int duty_pct) {
  if (dir == LiftDir::STOP) {
    // 停止は PWM 0。DIR は動かさない（不回っているのに向きだけ変えても意味がない）
    ledcWrite(PWM_CH, 0);
    return;
  }
  const bool up = (dir == LiftDir::UP);
  digitalWrite(MOTOR_DIR_PIN, (up == MOTOR_DIR_UP_LEVEL) ? HIGH : LOW);
  // 0% なら必ず 0 になることは motor_duty_to_pwm で保証されている
  ledcWrite(PWM_CH, static_cast<uint8_t>(motor_duty_to_pwm(duty_pct, PWM_RESOLUTION)));
}

bool LiftEsp32Hal::bottom_pressed() {
  return bottom_pressed_from_level(digitalRead(BOTTOM_PIN));
}

int32_t LiftEsp32Hal::encoder_count() const { return total_count_; }

int LiftEsp32Hal::height_mm() const { return height_mm_; }

bool LiftEsp32Hal::height_ok() const { return height_ok_; }

uint32_t LiftEsp32Hal::height_at_ms() const { return height_at_ms_; }
