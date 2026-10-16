// 昇降の速度の設定の検証と JSON（docs/plan/detailed/DetailedDesign-protocol.md §3）。
// Arduino.h は include しない（docs/plan/detailed/DetailedDesign-names.md §1）。
#pragma once

#include <cstddef>

#include "lift_decide.h"  // LIFT_SPEED_ABS_MAX_MM_S（検証の上限は判定と同じものを使う）

// 1 軸ぶんの下限・上限・初期値 [mm/s]
struct LiftAxisSettings {
  int min_mm_s = 10;
  int max_mm_s = 60;
  int init_mm_s = 30;
};

// 昇降の設定（DetailedDesign-names.md §1・既定値は §5.1）
struct LiftSettings {
  LiftAxisSettings up;
  LiftAxisSettings down;
  int motor_max_pct = LIFT_MOTOR_MAX_PCT_DEFAULT;  // モータへの出力の上限 [%]（Spec-ui §2.1）
};

// 検証（protocol §3）: 3 項目が揃うのは構造体が保証する。
// min ≦ init ≦ max・0〜LIFT_SPEED_ABS_MAX_MM_S の整数、motor_max_pct は
// LIFT_MOTOR_MAX_PCT_MIN〜LIFT_DUTY_ABS_MAX_PCT の整数なら true
bool validate_lift_settings(const LiftSettings& settings);

// JSON ⇔ 構造体。形は {"lift_up":{"min":..,"max":..,"init":..},"lift_down":{...},"motor_max_pct":50}。
// 読めない JSON・欠けた項目（motor_max_pct を含む）・型の違う値・検証に通らない値は false
bool lift_settings_from_json(const char* text, LiftSettings* out);

// 書き込んだバイト数を返す（バッファが足りなければ 0）
size_t lift_settings_to_json(const LiftSettings& settings, char* out, size_t out_size);
