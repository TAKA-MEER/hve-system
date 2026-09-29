#include "hal_esp32.h"

#include "config.h"

namespace {

// HC-SR04 の TRIG を HIGH にする時間。データシートどおり 10 us
constexpr uint32_t kTrigHighUs = 10;

// --- ECHO の割り込みが書き、loop() が読む値 ---
// ESP32 では 32 bit 整数の書き込みは途切れないので、volatile を付けるだけで足りる。
// 割り込みから this を通せないので、ハードウェアごとにファイル直下の変数に置く
// （HAL は 1 個しか作らない前提。main.cpp もその 1 個だけを作る）。
volatile uint32_t g_echo_rise_us = 0;   // 立ち上がりを捉えた micros()
volatile bool g_echo_rise_seen = false;  // 立ち上がりの後から、まだ落ちていない
volatile uint32_t g_echo_width_us = 0;   // 落ちた micros() - 立ち上がり。0 は「未測定」
volatile bool g_echo_done = false;       // 1 回測り終えた（loop() 側で読む）

// ECHO の両エッジ（CHANGE）で、割り込みの横幅だけ測る。loop() を止めないため
// （ECHO は 5 V なので分圧して 3.3 V にしてある。分圧なしだと壊す）。
// IRAM に置く。呼ぶのは micros() と digitalRead() だけで、両方とも
// ARDUINO_ISR_ATTR（IRAM から呼べる）。Serial も delay も使わない。
void IRAM_ATTR echo_edge_isr() {
  if (digitalRead(SONAR_ECHO_PIN) == HIGH) {
    g_echo_rise_us = micros();
    g_echo_rise_seen = true;
    return;
  }
  // 立ち上がりを見ないで落ちた（ノイズ）。何もしない。g_echo_rise_seen を
  // 落とさないので、次は「まだ立ち上がっていない」状態として扱われる
  if (!g_echo_rise_seen) {
    return;
  }
  g_echo_width_us = micros() - g_echo_rise_us;
  g_echo_rise_seen = false;
  g_echo_done = true;
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

  // --- HC-SR04 ---
  pinMode(SONAR_TRIG_PIN, OUTPUT);
  digitalWrite(SONAR_TRIG_PIN, LOW);
  // ECHO は分圧済み 3.3 V。内部プルアップは不要
  pinMode(SONAR_ECHO_PIN, INPUT);
  attachInterrupt(SONAR_ECHO_PIN, echo_edge_isr, CHANGE);

  // 起動後すぐ 1 回目の測定を出す
  next_sonar_ms_ = millis();
}

void LiftEsp32Hal::poll(uint32_t now_ms) {
  // 1 回測り終えたものを拾う
  if (g_echo_done) {
    g_echo_done = false;
    apply_echo_width(now_ms);
  }
  // 立ち上がりを捉えたまま落ちない = 反射が返らない。時間切れとして「読めない」にする
  if (g_echo_rise_seen && (micros() - g_echo_rise_us) > SONAR_ECHO_TIMEOUT_US) {
    g_echo_rise_seen = false;
    g_echo_width_us = 0;
    invalidate_height();
  }
  // 次の測定を出す。1 回 60 ms 以上空ける（DetailedDesign-hardware.md §0）
  if (now_ms >= next_sonar_ms_) {
    trigger_sonar(now_ms);
  }
}

void LiftEsp32Hal::trigger_sonar(uint32_t now_ms) {
  // 前回の幅を消してから叩く。鳴っているあいだの立ち上がりで上書きされる
  g_echo_width_us = 0;
  g_echo_rise_seen = false;
  digitalWrite(SONAR_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(SONAR_TRIG_PIN, HIGH);
  delayMicroseconds(kTrigHighUs);
  digitalWrite(SONAR_TRIG_PIN, LOW);
  next_sonar_ms_ = now_ms + SONAR_PERIOD_MS;
}

void LiftEsp32Hal::apply_echo_width(uint32_t now_ms) {
  int mm = 0;
  if (sonar_echo_to_mm(g_echo_width_us, &mm)) {
    // **有効な値を得た時刻だけを進める**。鮮度は「有効な値がいつ取れたか」で測る
    height_mm_ = mm;
    height_ok_ = true;
    height_at_ms_ = now_ms;
    return;
  }
  // 範囲外（2 cm 未満・4 m 超）なら「高さが読めない」。height_mm_ は直前の有効値を
  // 残し、height_at_ms_ も進めないので、判定の HEIGHT_STALE_MS にもひっかかる
  invalidate_height();
}

void LiftEsp32Hal::invalidate_height() {
  height_ok_ = false;
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

int LiftEsp32Hal::height_mm() const { return height_mm_; }

bool LiftEsp32Hal::height_ok() const { return height_ok_; }

uint32_t LiftEsp32Hal::height_at_ms() const { return height_at_ms_; }
