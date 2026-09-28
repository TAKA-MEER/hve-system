// 昇降部のハードウェアの抽象。Arduino.h は include しない（DetailedDesign-names.md §1）。
// 実物は firmware/lift/src/hal_esp32（WP-LIFT-02）。試験では偽物に差し替える。
#pragma once

#include <cstdint>

#include "lift_decide.h"

class LiftHal {
 public:
  virtual ~LiftHal() {}

  // モータへ方向とデューティを出す。停止は STOP と 0 で表す
  virtual void motor_set(LiftDir dir, int duty_pct) = 0;

  // 下端スイッチが押されているか
  virtual bool bottom_pressed() = 0;

  // 高さの超音波の最新の読み値・その値の有効性・その値を得た時刻
  virtual int height_mm() const = 0;
  virtual bool height_ok() const = 0;
  virtual uint32_t height_at_ms() const = 0;
};
