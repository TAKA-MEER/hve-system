// 昇降の速度の設定の検証と JSON（docs/plan/detailed/DetailedDesign-protocol.md §3）。
// Arduino.h は include しない（docs/plan/detailed/DetailedDesign-names.md §1）。
#pragma once

#include <cstddef>

#include "lift_decide.h"  // LIFT_SPEED_ABS_MAX_MM_S（検証の上限は判定と同じものを使う）

// 単体の操作（昇降部の画面・物理ボタン）でも天井で止めるかの既定（spec Ui §2.2）
constexpr bool LIFT_CEILING_STANDALONE_DEFAULT = true;

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
  bool ceiling_standalone = LIFT_CEILING_STANDALONE_DEFAULT;  // 単体の操作でも天井で止めるか
};

// 検証（protocol §3）: 3 項目が揃うのは構造体が保証する。
// min ≦ init ≦ max・0〜LIFT_SPEED_ABS_MAX_MM_S の整数、motor_max_pct は
// LIFT_MOTOR_MAX_PCT_MIN〜LIFT_DUTY_ABS_MAX_PCT の整数なら true
bool validate_lift_settings(const LiftSettings& settings);

// JSON ⇔ 構造体。形は {"lift_up":{"min":..,"max":..,"init":..},"lift_down":{...},"motor_max_pct":50,
// "ceiling_standalone":true}。読めない JSON・欠けた項目（motor_max_pct を含む）・型の違う値・
// 検証に通らない値は false。
// ceiling_standalone だけは欠けていてもよく、そのときは ceiling_standalone_if_missing を使う
// （ほかの項目は捨てない）。NVS は既定（true）、PUT は「いま保存している値」を渡す。
// 真偽値でない ceiling_standalone は false（検証の誤り）
bool lift_settings_from_json(const char* text, LiftSettings* out,
                             bool ceiling_standalone_if_missing = LIFT_CEILING_STANDALONE_DEFAULT);

// PUT /api/settings 用の読み方。ceiling_standalone が本文に無ければ、いま保存している値
// （current.ceiling_standalone）を保つ（true に戻さない。古いカメラモジュールからの中継）
bool lift_settings_from_put(const char* text, const LiftSettings& current, LiftSettings* out);

// 起動時に NVS から読んだ文字列（無ければ nullptr か空）から設定を決める。
// 読めない・検証を通らないときは既定値にして *using_defaults を true にする
LiftSettings lift_settings_from_saved(const char* saved, bool* using_defaults);

// 書き込んだバイト数を返す（バッファが足りなければ 0）
size_t lift_settings_to_json(const LiftSettings& settings, char* out, size_t out_size);
