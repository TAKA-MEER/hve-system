// HalUno（DetailedDesign.md §4.5・DetailedDesign-hardware.md §2.2〜§2.4）。
// 刻みは Timer2 の割り込み（Servo が Timer1、millis が Timer0 を使うため）。
// UNO は 16MHz なので、100µs 周期はプリスケーラ 8・OCR2A=199（16M÷8÷200）。
#include "hal_uno.h"

#include <Arduino.h>
#include <Wire.h>
#include <avr/interrupt.h>

#include "config.h"

namespace {

// 割り込みから刻む相手。begin() で入れる（割り込みは全体で 1 つ）。
HalUno* g_hal = nullptr;

// SRF02 の測距命令（実測値を cm で返す。レジスタ 0 へ書く）
constexpr uint8_t kSrf02RangeRegister = 0x00;
constexpr uint8_t kSrf02RangeCm = 0x51;
// 読み取りの先頭（高位・下位の 2 バイト）
constexpr uint8_t kSrf02ReadRegister = 0x02;

void coils_write(uint8_t pattern) {
  // D4〜D7 は PORTD の上位 4bit。D0/D1（Serial）・D2/D3 は残す
  PORTD = static_cast<uint8_t>((PORTD & 0x0F) | (pattern << 4));
}

void coils_off() { PORTD = static_cast<uint8_t>(PORTD & 0x0F); }

}  // namespace

ISR(TIMER2_COMPA_vect) {
  if (g_hal != nullptr) {
    g_hal->isr_step();
  }
}

HalUno::HalUno()
    : rate_(IO_STEP_TICK_US),
      last_hsps_(0),
      phase_(0),
      srf02_ranging_(false),
      srf02_cmd_ok_(false),
      srf02_at_ms_(0) {}

void HalUno::begin() {
  static_assert(F_CPU == 16000000L, "UNO (16MHz) 以外では Timer2 の分周が合わない");
  static_assert(YAW_PINS[0] == 4 && YAW_PINS[1] == 5 && YAW_PINS[2] == 6 && YAW_PINS[3] == 7,
                "ULN2003 は D4〜D7（PORTD の上位 4bit）に付ける");
  for (uint8_t i = 0; i < 4; ++i) {
    pinMode(YAW_PINS[i], OUTPUT);
  }
  noInterrupts();
  coils_off();
  interrupts();
  servo_.attach(SERVO_PIN);
  Wire.begin();
  // 時間切れでもバスを戻す（true）。固まって loop が止まるのを防ぐ
  Wire.setWireTimeout(SRF02_WIRE_TIMEOUT_US, true);
  // Timer2 を CTC で IO_STEP_TICK_US ごとに刻む
  TCCR2A = (1 << WGM21);
  TCCR2B = 0;
  OCR2A = 199;
  TCNT2 = 0;
  TIMSK2 = (1 << OCIE2A);
  TCCR2B = (1 << CS21);  // プリスケーラ 8 で始める
  g_hal = this;
}

void HalUno::yaw_set_hsps(int32_t yaw_hsps) {
  noInterrupts();
  if (yaw_hsps != last_hsps_) {
    rate_.set_hsps(yaw_hsps);
    last_hsps_ = yaw_hsps;
  }
  if (yaw_hsps == 0) {
    coils_off();
  } else {
    coils_write(YAW_HALF_STEP_SEQUENCE[phase_]);
  }
  interrupts();
}

void HalUno::yaw_coils_off() {
  noInterrupts();
  rate_.set_hsps(0);
  last_hsps_ = 0;
  coils_off();
  interrupts();
}

void HalUno::servo_set_ddeg(int pitch_ddeg) {
  const int32_t pulse =
      static_cast<int32_t>(SERVO_PULSE_CENTER_US) +
      (static_cast<int32_t>(pitch_ddeg) * static_cast<int32_t>(SERVO_US_PER_DEG)) / 10;
  int32_t limited = pulse;
  if (limited < SERVO_PULSE_MIN_US) {
    limited = SERVO_PULSE_MIN_US;
  }
  if (limited > SERVO_PULSE_MAX_US) {
    limited = SERVO_PULSE_MAX_US;
  }
  servo_.writeMicroseconds(static_cast<int>(limited));
}

bool HalUno::srf02_poll(uint32_t now_ms, int* st, int* cm) {
  if (st == nullptr || cm == nullptr) {
    return false;
  }
  if (!srf02_ranging_) {
    Wire.beginTransmission(SRF02_ADDR);
    Wire.write(kSrf02RangeRegister);
    Wire.write(kSrf02RangeCm);
    srf02_cmd_ok_ = (Wire.endTransmission() == 0);
    srf02_ranging_ = true;
    srf02_at_ms_ = now_ms;
    return false;
  }
  if (static_cast<int32_t>(now_ms - srf02_at_ms_) < static_cast<int32_t>(SRF02_PERIOD_MS)) {
    return false;
  }
  srf02_ranging_ = false;
  if (!srf02_cmd_ok_) {
    *st = 1;
    *cm = 0;
    return true;
  }
  Wire.beginTransmission(SRF02_ADDR);
  Wire.write(kSrf02ReadRegister);
  if (Wire.endTransmission() != 0) {
    *st = 1;
    *cm = 0;
    return true;
  }
  if (Wire.requestFrom(SRF02_ADDR, static_cast<uint8_t>(2)) != 2) {
    *st = 1;
    *cm = 0;
    return true;
  }
  const int hi = Wire.read();
  const int lo = Wire.read();
  if (hi < 0 || lo < 0) {
    *st = 1;
    *cm = 0;
    return true;
  }
  *st = 0;
  *cm = (hi << 8) | lo;
  return true;
}

void HalUno::isr_step() {
  int dir_step = 0;
  if (!rate_.tick(&dir_step)) {
    return;
  }
  if (dir_step > 0) {
    phase_ = static_cast<uint8_t>((phase_ + 1) & 7);
  } else {
    phase_ = static_cast<uint8_t>((phase_ + 7) & 7);
  }
  coils_write(YAW_HALF_STEP_SEQUENCE[phase_]);
}
