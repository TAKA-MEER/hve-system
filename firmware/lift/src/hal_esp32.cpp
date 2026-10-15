#include "hal_esp32.h"

#include <driver/pcnt.h>

#include "config.h"

namespace {

// PCNT の 1 単位。A/B の 4 逓倍で数える定番の構成
//（ch0: パルス=A・制御=B、ch1: パルス=B・制御=A）。
constexpr pcnt_unit_t kPcntUnit = PCNT_UNIT_0;

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

void config_pcnt_channel(pcnt_channel_t channel, int pulse_pin, int ctrl_pin) {
  pcnt_config_t cfg = {};
  cfg.pulse_gpio_num = pulse_pin;
  cfg.ctrl_gpio_num = ctrl_pin;
  cfg.channel = channel;
  cfg.unit = kPcntUnit;
  cfg.pos_mode = PCNT_COUNT_INC;
  cfg.neg_mode = PCNT_COUNT_DEC;
  cfg.lctrl_mode = PCNT_MODE_REVERSE;
  cfg.hctrl_mode = PCNT_MODE_KEEP;
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
  config_pcnt_channel(PCNT_CHANNEL_0, ENC_A_PIN, ENC_B_PIN);
  config_pcnt_channel(PCNT_CHANNEL_1, ENC_B_PIN, ENC_A_PIN);
  pcnt_set_filter_value(kPcntUnit, glitch_filter_ticks());
  pcnt_filter_enable(kPcntUnit);
  pcnt_counter_pause(kPcntUnit);
  pcnt_counter_clear(kPcntUnit);
  pcnt_counter_resume(kPcntUnit);
  int16_t raw = 0;
  pcnt_get_counter_value(kPcntUnit, &raw);
  prev_raw_ = raw;
  pcnt_ready_ = true;
}

void LiftEsp32Hal::poll(uint32_t now_ms) {
  if (!pcnt_ready_) {
    return;
  }
  // PCNT の生の値を読み、差を足し込む
  int16_t raw = prev_raw_;
  pcnt_get_counter_value(kPcntUnit, &raw);
  total_count_ = accumulate_pcnt(total_count_, prev_raw_, raw);
  prev_raw_ = raw;
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
