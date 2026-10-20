// MG996R のパルス幅の換算（純関数。Arduino.h を含まず、ホスト試験から縛る）。
// 出力は Timer1 の Fast PWM（mode 14・TOP＝ICR1・分周 8）。1 カウント＝0.5µs（16MHz÷8）。
#pragma once

#include <stdint.h>

#include "config.h"

// 周期 20 ms（50 Hz）＝ 40000 カウント。TOP＝ICR1＝40000−1
constexpr uint16_t SERVO_PWM_TOP = 39999;
// パルス幅 [µs] 1 つぶんのカウント数（分周 8 で 0.5µs/カウント）
constexpr int SERVO_PWM_TICKS_PER_US = 2;

// 0.1° → パルス幅 [µs]。SERVO_PULSE_MIN_US〜MAX_US の外へ出さない
inline int servo_pulse_us_from_ddeg(int pitch_ddeg) {
  int32_t pulse = static_cast<int32_t>(SERVO_PULSE_CENTER_US) +
                  (static_cast<int32_t>(pitch_ddeg) * static_cast<int32_t>(SERVO_US_PER_DEG)) / 10;
  if (pulse < SERVO_PULSE_MIN_US) {
    pulse = SERVO_PULSE_MIN_US;
  }
  if (pulse > SERVO_PULSE_MAX_US) {
    pulse = SERVO_PULSE_MAX_US;
  }
  return static_cast<int>(pulse);
}

// パルス幅 [µs] → OCR1A（TOP を超えない）
inline uint16_t servo_ocr1a_from_us(int pulse_us) {
  int32_t ticks = static_cast<int32_t>(pulse_us) * SERVO_PWM_TICKS_PER_US;
  if (ticks < 0) {
    ticks = 0;
  }
  if (ticks > SERVO_PWM_TOP) {
    ticks = SERVO_PWM_TOP;
  }
  return static_cast<uint16_t>(ticks);
}

// 0.1° → OCR1A
inline uint16_t servo_ocr1a_from_ddeg(int pitch_ddeg) {
  return servo_ocr1a_from_us(servo_pulse_us_from_ddeg(pitch_ddeg));
}
