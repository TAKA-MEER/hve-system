// 速さのフィードバック（PI＋前向き）。Arduino.h は include しない
// （docs/plan/detailed/DetailedDesign-names.md §1）。
// 規則は docs/plan/detailed/DetailedDesign.md §4.1.2。定数の値は DetailedDesign-names.md §5.1。
#pragma once

#include <cstdint>

#include "lift_decide.h"  // LiftDir・LIFT_DUTY_ABS_MAX_PCT

// 速さの制御の定数（DetailedDesign-names.md §5.1。すべて仮）
constexpr uint32_t LIFT_SPEED_CTRL_MS = 20;    // 制御の周期
constexpr uint32_t LIFT_SPEED_WINDOW_MS = 100;  // 速さを測る窓
constexpr float LIFT_SPEED_KP = 0.5f;           // [%/(mm/s)]。仮（WP-MEAS-01 で合わせる）
constexpr float LIFT_SPEED_KI = 2.0f;           // [%/mm]。仮（同上）
constexpr float LIFT_FF_UP_PCT_PER_MM_S = 0.5f;    // 上昇の前向き。仮（同上）
constexpr float LIFT_FF_DOWN_PCT_PER_MM_S = 0.3f;  // 下降の前向き。仮（同上）

// エンコーダの数の差と経過時間から、上向きが正の速さ [mm/s] を求める純関数。
// um_per_count は 1 カウントあたりの進み [µm]、up_sign は上昇で数が増える向き。
// 経過時間が 0 以下なら 0 を返す。原点の有無に依らない（§4.1.2）。
float speed_from_counts(int32_t prev_count, int32_t now_count, uint32_t prev_ms,
                        uint32_t now_ms, int32_t um_per_count, int up_sign);

// 目標の速さ [mm/s] と測った速さ（進行方向が正）からデューティ [%] を出す。
// 出力 = 前向き（目標 × 上昇・下降の係数）＋ Kp × 偏差 ＋ Ki × 偏差の積分。
// 0〜max_pct（モータへの出力の上限。LiftController が設定から渡す）に丸める。
// 丸めに当たっている間は積分を進めない。
// 止まった・向きが替わった・持ち主が替わったら reset() で 0 に戻す（§4.1.2）。
class SpeedLoop {
 public:
  // 目標・測った値・向き・前回からの経過時間・出力の上限 → デューティ [%]。
  // 測った値が逆向きなら負で渡す（偏差が大きくなり、出力を上げる側に効く）。
  // max_pct の外（LIFT_MOTOR_MAX_PCT_MIN〜LIFT_DUTY_ABS_MAX_PCT の外）は
  // LIFT_DUTY_ABS_MAX_PCT に丸めて使う
  int update(int target_mm_s, float measured_mm_s, LiftDir dir, uint32_t dt_ms, int max_pct);
  void reset();

 private:
  float integral_ = 0.0f;  // 偏差の積分 [mm]
};
