// LiftHal のうちハードウェアに触らない計算だけ（DetailedDesign.md §4.1・DD-2）。
// Arduino.h は include しない（DetailedDesign-names.md §1）ので env:native で試験できる。
// Arduino を呼ぶ側（ピンの読み書き・LEDC・割り込み）は firmware/lift/src/hal_esp32。
// 定数の値と出所は DetailedDesign-names.md §5。
#pragma once

#include <cstdint>

#include "lift_decide.h"  // LIFT_DUTY_ABS_MAX_PCT（デューティの上限は判定と同じものを使う）

// --- HC-SR04（DetailedDesign-hardware.md §0・§1）---

// 測定できる距離の範囲。外の値と時間切れは「高さが読めない」（判定 #4 HEIGHT_UNKNOWN）。
constexpr int SONAR_MIN_RANGE_MM = 20;     // 約 2 cm。これより近い値は読めない
constexpr int SONAR_MAX_RANGE_MM = 4000;   // 約 4 m。これより遠い値も読めない
// 割り込みで ECHO の時間切れを判定する時間（**仮**）。4 m 往復の約 23.5 ms より長く、
// HC-SR04 の 1 回の測定周期 60 ms より短い。
// **時間切れのときは幅 0 µs として扱う**（0 は測定範囲の外なので「読めない」になる）。
constexpr uint32_t SONAR_ECHO_TIMEOUT_US = 30000;

// --- 下端リミットスイッチ ---

// 押されているときのピンのレベル。**常時閉（NC）配線**なので押すと開いて HIGH になり、
// 線が切れたときも同じ HIGH になる（断線が「下降しない」側に倒れる。
// DetailedDesign-hardware.md §1）。0 にすると押されたら LOW の配線になる。
constexpr int BOTTOM_PRESSED_LEVEL = 1;  // HIGH

// ECHO のパルス幅（µs）を mm にして有効性を返す。測定範囲（SONAR_MIN_RANGE_MM〜
// SONAR_MAX_RANGE_MM）の外は false を返し、*out_mm には何も書かない
// （呼び出し側に無効な値を渡さない。読み値そのものは height_ok と組で判定に渡す）。
// out_mm が nullptr のときも false。
bool sonar_echo_to_mm(uint32_t echo_us, int* out_mm);

// 高さの読み値を「直近 SONAR_MEDIAN_WINDOW 回の有効な読み値の中央値」にする
// （spec Spec-safety.md §2「高さの読み値は、単発の外れ値で変わらない」）。
// WiFi 通信中に混ざる実際より低い偽値を弾くため。窓がそろうまでは「読めない」。
// 範囲外・時間切れが 1 回でも来たら窓を空にしてそろえ直す。
constexpr int SONAR_MEDIAN_WINDOW = 5;

struct HeightFilter {
  // ECHO の幅 [µs] を 1 回分入れる。窓がそろって中央値が出せるときだけ true を返し、
  // *out_mm に書く。範囲外（時間切れの幅 0 を含む）は窓を空にして false。
  // out_mm が nullptr のときも false（窓は変えない）。
  bool on_echo(uint32_t echo_us, int* out_mm);
  // 反射が返らなかった（時間切れ）。窓を空にする
  void on_invalid();

  int buf_[SONAR_MEDIAN_WINDOW] = {};
  int count_ = 0;  // 窓に入っている数（満杯でも SONAR_MEDIAN_WINDOW のまま）
  int next_ = 0;   // 次に書く位置
};

// ピンのレベル（Arduino の HIGH = 1 / LOW = 0）から下端スイッチが押されているかを返す。
// BOTTOM_PRESSED_LEVEL のときだけ true。
bool bottom_pressed_from_level(int level);

// デューティ [%] を LEDC に書く値（0〜(1 << resolution) - 1）にする。
// 先に 0〜LIFT_DUTY_ABS_MAX_PCT に丸める。**停止（0%）は必ず 0 になる**。
uint32_t motor_duty_to_pwm(int duty_pct, int resolution);
