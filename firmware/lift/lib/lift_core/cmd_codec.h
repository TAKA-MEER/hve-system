// JSON ⇔ 指令・状態（DetailedDesign-protocol.md §2.2・§2.3）。
// ArduinoJson は cpp 側だけで使う。壊れた入力をどう扱うかの詳細は cmd_decode。
#pragma once

#include <cstddef>

#include "lift_decide.h"

// カメラ部からの cmd を読む。読めない JSON・知らない t・型の違うフィールド・
// ceil_ok の欠落は、すべて「上昇させない」側（dir = STOP・ceil_ok = false）で埋めて
// false を返す。壊れた入力を 0 に落とすのと同じ扱い。
bool cmd_decode(const char* text, LiftCmd* out);

// state を JSON にする。書き込んだバイト数を返す（バッファが足りなければ 0）。
// fw は lift_core_version() から入れる。
size_t state_encode(const LiftState& state, char* out, size_t out_size);

// state の JSON を読む（往復を確かめるため。top_mm の null は LIFT_TOP_MM に戻す）。
bool state_decode(const char* text, LiftState* out);
