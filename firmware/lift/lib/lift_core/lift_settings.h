// 昇降の速度の設定の検証と JSON（docs/plan/detailed/DetailedDesign-protocol.md §3）。
// Arduino.h は include しない（docs/plan/detailed/DetailedDesign-names.md §1）。
#pragma once

#include <cstddef>

// 1 軸ぶんの下限・上限・初期値 [%]
struct LiftAxisSettings {
  int min_pct = 10;
  int max_pct = 60;
  int init_pct = 30;
};

// 昇降の設定（DetailedDesign-names.md §1・既定値は §5.1）
struct LiftSettings {
  LiftAxisSettings up;
  LiftAxisSettings down;
};

// 検証（protocol §3）: 2 項目が揃うのは構造体が保証する。
// min ≦ init ≦ max・0〜100 の整数なら true
bool validate_lift_settings(const LiftSettings& settings);

// JSON ⇔ 構造体。形は {"lift_up":{"min":..,"max":..,"init":..},"lift_down":{...}}。
// 読めない JSON・欠けた軸・型の違う値・検証に通らない値は false
bool lift_settings_from_json(const char* text, LiftSettings* out);

// 書き込んだバイト数を返す（バッファが足りなければ 0）
size_t lift_settings_to_json(const LiftSettings& settings, char* out, size_t out_size);
