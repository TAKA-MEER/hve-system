// Arduino UNO の実物の設定（DetailedDesign-names.md §5.4）。
// io_core にある定数（IO_BAUD・IO_LINE_MAX・IO_CMD_TIMEOUT_MS・YAW_HSPS_ABS_MAX・
// PITCH_MIN_DEG・PITCH_MAX_DEG・PITCH_INITIAL_DEG）はそちらが正本。ここに重ねて書かない。
// Arduino.h を include しない（ホスト試験から YAW_HALF_STEP_SEQUENCE の並びを縛るため）。
#pragma once

#include <stdint.h>

// ステッピングの刻みの周期 [µs]（仮。Timer2 の割り込み。DetailedDesign-hardware.md §2.4）
constexpr uint32_t IO_STEP_TICK_US = 100;

// ULN2003 IN1〜IN4（DetailedDesign-hardware.md §2.2。D4〜D7＝PORTD の上位 4 bit）
// YAW_HALF_STEP_SEQUENCE の下位ビットと順番を揃える（bit0＝IN1＝D4）
constexpr uint8_t YAW_PINS[4] = {4, 5, 6, 7};

// 半ステップの相（旧版の実機確認どおり。隣り合うコイルを順に励磁する。
// IN1 → IN1+IN2 → IN2 → …。中性点（0x00）は使わない）
constexpr uint8_t YAW_HALF_STEP_SEQUENCE[8] = {
    0x01,  // IN1
    0x03,  // IN1, IN2
    0x02,  // IN2
    0x06,  // IN2, IN3
    0x04,  // IN3
    0x0c,  // IN3, IN4
    0x08,  // IN4
    0x09,  // IN4, IN1
};

// MG996R の信号（仮。DetailedDesign-hardware.md §2.2。Timer1 のハードウェア PWM＝OC1A で出す。servo_pwm.h）
constexpr uint8_t SERVO_PIN = 9;
// パルス幅と角度の対応（仮。MG996R のデータシートで確かめる）
constexpr int SERVO_PULSE_CENTER_US = 1500;
constexpr int SERVO_US_PER_DEG = 10;
// この外側へパルスを出さない（±60° に当たる範囲）
constexpr int SERVO_PULSE_MIN_US = 900;
constexpr int SERVO_PULSE_MAX_US = 2100;

// SRF02（この個体のアドレス＝0x72。工場出荷は 0x70。5V の I2C なので直結。DetailedDesign-hardware.md §2.2）
constexpr uint8_t SRF02_ADDR = 0x72;
// 測定の周期 [ms]（SRF02 は 1 回 約 66ms。65ms より早く始めない）
constexpr uint32_t SRF02_PERIOD_MS = 70;
// I2C の時間切れ [µs]（仮。Wire.setWireTimeout に渡す。時間切れは st=1）
constexpr uint32_t SRF02_WIRE_TIMEOUT_US = 25000;

#ifdef __AVR__
#include <avr/wdt.h>
// AVR のウォッチドッグの時間 [WDT の定数]（仮。loop が回るたびに叩く）
constexpr uint8_t IO_WDT_TIMEOUT = WDTO_500MS;
#endif
