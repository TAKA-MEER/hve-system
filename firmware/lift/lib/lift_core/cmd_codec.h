// JSON ⇔ hello・hold・release・ceiling・state（docs/plan/detailed/DetailedDesign-protocol-modules.md §1〜§3）。
// ArduinoJson は cpp 側だけで使う。
#pragma once

#include <cstddef>
#include <cstdint>

#include "lift_decide.h"  // LiftDir・LiftState・StopReason・Ceiling*・ModuleRole

// state を入れるバッファの大きさ。WP-LIFT-13 で、modules を上限まで並べ busy_label を
// 最大にした state を実際に作って測った長さ（1770 バイト）に、余裕（約 1.3 倍）を持たせた値
// （test_cmd_codec の test_state_worst_case_fits_text_max。names.md §5.1）
constexpr size_t LIFT_STATE_TEXT_MAX = 2304;

// hello（/ws/module だけ。繋いだら 1 度）。
// 読めない hello は「ceiling_sensor: true」として扱う（protocol-modules §1.1）。
struct HelloMsg {
  bool has_sensor = true;
  bool sensor_explicit = false;  // ceiling_sensor を明示したか（天井の値の出どころにできるか。§2.3）
  ModuleRole role = ModuleRole::CONTROL;
  ModuleOperator op = ModuleOperator::HUMAN;
  char name[32] = {};
  char fw[16] = {};
  char busy_label[49] = {};  // 空なら無し（UTF-8 で 1〜48 バイト。超えたら切り詰めずに無し）
  char ip[64] = {};          // 接続元の IP。JSON には載らず、呼び出し側（main.cpp）が入れる
};

// t == "hello" のメッセージなら中身を読んで true。
// 読めないものは false を返すが、out は has_sensor = true のままにする
bool decode_hello(const char* text, HelloMsg* out);

// hold。ceiling が欠けている・読めない・知らない status・型の違う中身のときは
// hold を捨てず、has_ceiling を偽（status = MISSING）にして受け取る（protocol §2.1）。
// それ以外（読めない JSON・知らない t・press / dir / speed が読めない）は捨てて false。
// duty だけの hold は読めない hold として捨てる。
struct HoldMsg {
  int press = 0;
  LiftDir dir = LiftDir::STOP;
  int speed = 0;  // 目標の速さ [mm/s]（0 以上の整数）
  bool has_ceiling = false;
  CeilingReport ceiling;  // 受け取った時刻（received_at_ms）を stamped して返す
};

bool decode_hold(const char* text, HoldMsg* out, uint32_t received_at_ms);

// release。t == "release" で press が読めれば true
struct ReleaseMsg {
  int press = 0;
};

bool decode_release(const char* text, ReleaseMsg* out);

// ceiling（距離計を持つ上部モジュールが、止まっている間も送る。protocol-modules §2.3）。
// hold に載る ceiling と同じ中身。読めなければ false（前の値を使い続ける）
bool decode_ceiling(const char* text, CeilingReport* out, uint32_t received_at_ms);

// state を JSON にする。書き込んだバイト数を返す（バッファが足りなければ 0）。
// fw は lift_core_version() から入れる
size_t state_encode(const LiftState& state, char* out, size_t out_size);

// state の JSON を読む（往復を確かめるため）
bool state_decode(const char* text, LiftState* out);
