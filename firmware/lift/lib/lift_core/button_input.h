// 昇降部の点検用の物理ボタン（docs/plan/detailed/DetailedDesign.md §4.1.3）。
// Arduino.h は include しない（docs/plan/detailed/DetailedDesign-names.md §1）ので env:native で試験できる。
// ピンを読む側は src/main.cpp。ここは読んだ電圧から hold / release を LiftController へ渡すまで。
//
// 読みの部分は組み立て用の ManualSwitch をそのまま使う（入ってから動き出すまで 30 ms・
// 両方 LOW は止める・反転は 150 ms の停止を挟む・離したらすぐ止める）。
#pragma once

#include <cstdint>

#include "lift_controller.h"
#include "lift_settings.h"
#include "manual_switch.h"

// 物理ボタンの接続番号。WS の接続番号（0 以上）と重ならない
constexpr int LIFT_BUTTON_CONN_ID = -1;

// スイッチの 2 つの入力の電圧（INPUT_PULLUP。入っている側が LOW = 0）を毎回の loop() で受け、
// 求められている向きを返す。
// - 起動後、両方が HIGH（離れている）と一度読めるまでは、何を読んでも止まり
//   （起動したときに入っていた・固着したスイッチで動き出さない）
// - 止まりから動きへ替わるたびに press を 1 つ増やす（hold の press と同じ役割）
class ButtonInput {
 public:
  explicit ButtonInput(uint32_t now_ms = 0) : switch_(now_ms) {}

  ManualDir update(int up_level, int down_level, uint32_t now_ms);

  ManualDir dir() const { return dir_; }
  int press() const { return press_; }
  // 直前の update で、動きから止まりに替わったか
  bool just_stopped() const { return just_stopped_; }

 private:
  ManualSwitch switch_;
  bool armed_ = false;  // 両方 HIGH を一度読んだか
  ManualDir dir_ = ManualDir::STOP;
  int press_ = 0;
  bool just_stopped_ = false;
};

// 1 周期ぶん。動いている間は毎回 on_hold（速さは設定の上昇・下降の init_mm_s。
// 天井は持たない）、止まりに替わった回に on_release を呼ぶ。main.cpp の loop() は
// ピンを読んでこれを呼ぶだけ（判断を main.cpp に書かない）。
void button_step(ButtonInput& button, LiftController& controller, int up_level, int down_level,
                 const LiftSettings& settings, uint32_t now_ms);
