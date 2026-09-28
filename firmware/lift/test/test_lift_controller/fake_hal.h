// 試験用の偽ハードウェア。hal.h の抽象をそのまま実装し、モータへ出た値を覚えておく。
// 純関数の試験だけでは呼び出し側の配線を縛れないので、必ずこれを通す（DetailedDesign.md DD-2）。
#pragma once

#include "hal.h"

class FakeHal : public LiftHal {
 public:
  void motor_set(LiftDir dir, int duty_pct) override {
    motor_dir_ = dir;
    motor_duty_ = duty_pct;
    motor_calls_++;
  }

  bool bottom_pressed() override { return bottom_; }

  int height_mm() const override { return height_mm_; }
  bool height_ok() const override { return height_ok_; }
  uint32_t height_at_ms() const override { return height_at_ms_; }

  // 試験が使う設定
  void set_bottom(bool pressed) { bottom_ = pressed; }
  void set_height(int mm, bool ok, uint32_t at_ms) {
    height_mm_ = mm;
    height_ok_ = ok;
    height_at_ms_ = at_ms;
  }

  LiftDir motor_dir() const { return motor_dir_; }
  int motor_duty() const { return motor_duty_; }
  int motor_calls() const { return motor_calls_; }
  bool motor_moving() const { return motor_dir_ != LiftDir::STOP && motor_duty_ > 0; }

 private:
  LiftDir motor_dir_ = LiftDir::STOP;
  int motor_duty_ = 0;
  int motor_calls_ = 0;
  bool bottom_ = false;
  int height_mm_ = 0;
  bool height_ok_ = false;
  uint32_t height_at_ms_ = 0;
};
