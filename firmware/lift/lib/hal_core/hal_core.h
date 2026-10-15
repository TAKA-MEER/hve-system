// LiftHal のうちハードウェアに触らない計算だけ（DetailedDesign.md §4.1・DD-2）。
// Arduino.h は include しない（DetailedDesign-names.md §1）ので env:native で試験できる。
// Arduino を呼ぶ側（ピンの読み書き・LEDC・PCNT）は firmware/lift/src/hal_esp32。
// 定数の値と出所は DetailedDesign-names.md §5。
#pragma once

#include <cstdint>

#include "lift_decide.h"  // LIFT_DUTY_ABS_MAX_PCT（デューティの上限は判定と同じものを使う）

// --- 高さのエンコーダ E6A2-CW3C（DetailedDesign-hardware.md §1.1）---

// 上昇で数が増える向き。実機で確かめるまで仮（+1）。
constexpr int ENC_UP_SIGN = +1;  // 仮
// 1 カウントあたりの巻き取りの進み [µm]。仮（H-V12。WP-MEAS-01 で巻き取りの周長 ÷
// (4 × ENC_PPR) に直す）。整数で持つ。
constexpr int32_t LIFT_UM_PER_COUNT = 100;  // 仮（H-V12。WP-MEAS-01 で巻き取りの周長 ÷ (4 × ENC_PPR) に直す）
// 下端スイッチが押されたまま上昇側へ進んでよい余裕 [mm]。仮（P-14）。
// 超えたらスイッチの故障とみなす（DetailedDesign.md §4.1.1）。
constexpr int HOME_SLACK_MM = 20;  // 仮（P-14）

// PCNT（16 bit 符号付き）の前回値から今回値への差を 2^16 を法として求め、
// 32 bit の累積に足し込む（DetailedDesign.md §4.1.1）。
// 32767 → -32768 をまたぐ +1、-32768 → 32767 をまたぐ -1 も正しく数える。
inline int32_t accumulate_pcnt(int32_t total, int16_t prev_raw, int16_t now_raw) {
  return total + static_cast<int16_t>(static_cast<int32_t>(now_raw) - prev_raw);
}

// エンコーダの数と下端スイッチから高さと「読めるか」を決める
// （DetailedDesign.md §4.1.1。判断はすべてここに置き、hal_esp32 は読んで渡すだけ）。
class EncoderHeight {
 public:
  // エンコーダの累積の数（原点に依らない）と下端スイッチの状態を渡す。
  // up_sign は上昇で数が増える向き（+1 / -1。実機では ENC_UP_SIGN）。
  void update(int32_t count, bool bottom_pressed, int up_sign = ENC_UP_SIGN);
  bool ok() const { return has_home_ && !fault_; }
  // 原点を取ったあとの高さ [mm]。原点より下（ずれ）は負のまま出す。丸めない。
  int height_mm() const;

 private:
  bool has_home_ = false;      // 下端に一度でも着いたか
  bool prev_pressed_ = false;  // 前回押されていたか（押された瞬間を見るため）
  bool fault_ = false;         // 押されたまま HOME_SLACK_MM を超えた（スイッチの故障）
  int32_t home_count_ = 0;     // 原点の数
  int32_t last_count_ = 0;     // 最後に渡された数
  int last_sign_ = ENC_UP_SIGN;
};

// --- 下端リミットスイッチ ---

// 押されているときのピンのレベル。**常時閉（NC）配線**なので押すと開いて HIGH になり、
// 線が切れたときも同じ HIGH になる（断線が「下降しない」側に倒れる。
// DetailedDesign-hardware.md §1）。0 にすると押されたら LOW の配線になる。
constexpr int BOTTOM_PRESSED_LEVEL = 1;  // HIGH

// ピンのレベル（Arduino の HIGH = 1 / LOW = 0）から下端スイッチが押されているかを返す。
// BOTTOM_PRESSED_LEVEL のときだけ true。
bool bottom_pressed_from_level(int level);

// デューティ [%] を LEDC に書く値（0〜(1 << resolution) - 1）にする。
// 先に 0〜LIFT_DUTY_ABS_MAX_PCT に丸める。**停止（0%）は必ず 0 になる**。
uint32_t motor_duty_to_pwm(int duty_pct, int resolution);
