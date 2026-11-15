// 試験用の偽ハードウェア。io_hal.h の抽象をそのまま実装し、ハードウェアへ出た値を覚えておく。
// 純関数の試験だけでは呼び出し側の配線を縛れないので、必ずこれを通す（DetailedDesign.md DD-2）。
#pragma once

#include "io_controller.h"
#include "io_hal.h"
#include "step_rate.h"

class FakeIoHal : public IoHal {
 public:
  void yaw_set_hsps(int32_t yaw_hsps) override {
    if (yaw_hsps != yaw_hsps_) {
      rate_.set_hsps(yaw_hsps);  // 実物と同じ。同じ速さの再設定では位相を戻さない
    }
    yaw_hsps_ = yaw_hsps;
    coils_on_ = true;
  }

  void yaw_coils_off() override {
    rate_.set_hsps(0);
    yaw_hsps_ = 0;
    coils_on_ = false;
  }

  void servo_set_ddeg(int pitch_ddeg) override {
    pitch_ddeg_ = pitch_ddeg;
    servo_calls_++;
    servo_on_ = true;
  }

  void servo_off() override {
    servo_off_calls_++;
    servo_on_ = false;
  }

  int32_t yaw_hs_total() override { return rate_.total(); }

  // Timer2 の割り込みの代わり。ms の間、IO_STEP_TICK_US ごとに StepRate を刻む
  void run_ms(int ms) {
    for (int i = 0; i < ms * 10; ++i) {
      int dir = 0;
      rate_.tick(&dir);
    }
  }

  int32_t yaw_hsps() const { return yaw_hsps_; }
  bool coils_on() const { return coils_on_; }
  bool yaw_moving() const { return coils_on_ && yaw_hsps_ != 0; }
  int pitch_ddeg() const { return pitch_ddeg_; }
  int servo_calls() const { return servo_calls_; }
  int servo_off_calls() const { return servo_off_calls_; }
  bool servo_on() const { return servo_on_; }

 private:
  StepRate rate_{100};  // IO_STEP_TICK_US と同じ 100 µs
  int32_t yaw_hsps_ = 0;
  bool coils_on_ = false;
  int pitch_ddeg_ = PITCH_INITIAL_DEG * 10;
  int servo_calls_ = 0;
  int servo_off_calls_ = 0;
  bool servo_on_ = false;
};
