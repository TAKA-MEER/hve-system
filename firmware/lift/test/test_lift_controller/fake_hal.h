// 試験用の偽ハードウェア。hal.h の抽象をそのまま実装し、モータへ出た値を覚えておく。
// 純関数の試験だけでは呼び出し側の配線を縛れないので、必ずこれを通す（DetailedDesign.md DD-2）。
#pragma once

#include "hal.h"
#include "hal_core.h"

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
  int32_t encoder_count() const override { return encoder_count_; }

  // 試験が使う設定
  void set_bottom(bool pressed) { bottom_ = pressed; }
  void set_encoder_count(int32_t count) { encoder_count_ = count; }
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
  int32_t encoder_count_ = 0;
  int height_mm_ = 0;
  bool height_ok_ = false;
  uint32_t height_at_ms_ = 0;
};

// 実物（LiftEsp32Hal）と同じ作りの偽物。高さと「読めるか」は EncoderHeight が決め、
// 下降の BOTTOM に使う bottom_pressed() は読んだそのまま（時間の続きを掛けない）。
// poll() は LiftEsp32Hal::poll と同じく、数・読んだスイッチ・時刻を EncoderHeight へ渡す。
class EncoderFakeHal : public LiftHal {
 public:
  void motor_set(LiftDir dir, int duty_pct) override {
    motor_dir_ = dir;
    motor_duty_ = duty_pct;
  }

  bool bottom_pressed() override { return raw_pressed_; }
  int height_mm() const override { return height_mm_; }
  bool height_ok() const override { return height_ok_; }
  uint32_t height_at_ms() const override { return height_at_ms_; }
  int32_t encoder_count() const override { return count_; }

  // 試験が使う設定
  void set_raw_pressed(bool pressed) { raw_pressed_ = pressed; }
  void set_count(int32_t count) { count_ = count; }
  void poll(uint32_t now_ms) {
    encoder_.update(count_, raw_pressed_, now_ms, ENC_UP_SIGN);
    height_mm_ = encoder_.height_mm();
    height_ok_ = encoder_.ok();
    height_at_ms_ = now_ms;
  }

  LiftDir motor_dir() const { return motor_dir_; }
  bool motor_moving() const { return motor_dir_ != LiftDir::STOP && motor_duty_ > 0; }

 private:
  EncoderHeight encoder_;
  LiftDir motor_dir_ = LiftDir::STOP;
  int motor_duty_ = 0;
  bool raw_pressed_ = false;
  int32_t count_ = 0;
  int height_mm_ = 0;
  bool height_ok_ = false;
  uint32_t height_at_ms_ = 0;
};
