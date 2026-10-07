// JSON ⇔ hello・hold・release・state（docs/plan/detailed/DetailedDesign-protocol.md §2）。
// ArduinoJson は cpp 側だけで使う。
#pragma once

#include <cstddef>
#include <cstdint>

#include "lift_decide.h"  // LiftDir・LiftState・StopReason・Ceiling*

// hello（/ws/module だけ。繋いだら 1 度）。
// 読めない hello は「ceiling_sensor: true」として扱う（protocol §2.1）。
struct HelloMsg {
  bool has_sensor = true;
  char name[32] = {};
  char fw[16] = {};
};

// t == "hello" のメッセージなら中身を読んで true。
// 読めないものは false を返すが、out は has_sensor = true のままにする
bool decode_hello(const char* text, HelloMsg* out);

// hold。ceiling が欠けている・読めない・知らない status・型の違う中身のときは
// hold を捨てず、has_ceiling を偽（status = MISSING）にして受け取る（protocol §2.1）。
// それ以外（読めない JSON・知らない t・press / dir / duty が読めない）は捨てて false。
struct HoldMsg {
  int press = 0;
  LiftDir dir = LiftDir::STOP;
  int duty = 0;
  bool has_ceiling = false;
  CeilingReport ceiling;  // 受け取った時刻（received_at_ms）を stamped して返す
};

bool decode_hold(const char* text, HoldMsg* out, uint32_t received_at_ms);

// release。t == "release" で press が読めれば true
struct ReleaseMsg {
  int press = 0;
};

bool decode_release(const char* text, ReleaseMsg* out);

// state を JSON にする。書き込んだバイト数を返す（バッファが足りなければ 0）。
// fw は lift_core_version() から入れる
size_t state_encode(const LiftState& state, char* out, size_t out_size);

// state の JSON を読む（往復を確かめるため）
bool state_decode(const char* text, LiftState* out);
