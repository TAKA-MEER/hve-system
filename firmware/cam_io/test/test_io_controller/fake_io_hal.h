// 試験用の偽ハードウェア。io_hal.h の抽象をそのまま実装し、ハードウェアへ出た値を覚えておく。
// 純関数の試験だけでは呼び出し側の配線を縛れないので、必ずこれを通す（DetailedDesign.md DD-2）。
#pragma once

#include "io_controller.h"
#include "io_hal.h"

class FakeIoHal : public IoHal {
 public:
  void yaw_set_hsps(int32_t yaw_hsps) override {
    yaw_hsps_ = yaw_hsps;
    coils_on_ = true;
  }

  void yaw_coils_off() override {
    yaw_hsps_ = 0;
    coils_on_ = false;
  }

  void servo_set_ddeg(int pitch_ddeg) override {
    pitch_ddeg_ = pitch_ddeg;
    servo_calls_++;
  }

  int32_t yaw_hsps() const { return yaw_hsps_; }
  bool coils_on() const { return coils_on_; }
  bool yaw_moving() const { return coils_on_ && yaw_hsps_ != 0; }
  int pitch_ddeg() const { return pitch_ddeg_; }
  int servo_calls() const { return servo_calls_; }

 private:
  int32_t yaw_hsps_ = 0;
  bool coils_on_ = false;
  int pitch_ddeg_ = PITCH_INITIAL_DEG * 10;
  int servo_calls_ = 0;
};
