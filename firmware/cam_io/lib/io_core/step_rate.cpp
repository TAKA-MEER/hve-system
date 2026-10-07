// StepRate（DetailedDesign.md §4.5）。速さ × 周期を足し、1 半ステップぶん溜まるごとに刻む。
// 680 半ステップ毎秒・100 µs 周期なら約 1.47 ms ごとに刻む。
#include "step_rate.h"

namespace {

// 1 半ステップに当たる位相 [半ステップ・µs / 秒]
const int64_t kPhasePerHalfStep = 1000000;

}  // namespace

StepRate::StepRate(uint32_t tick_us) : tick_us_(tick_us), hsps_(0), phase_(0) {}

void StepRate::set_hsps(int32_t hsps) {
  hsps_ = hsps;
  phase_ = 0;
}

bool StepRate::tick(int* dir_step) {
  if (dir_step == nullptr) {
    return false;
  }
  phase_ += static_cast<int64_t>(hsps_) * static_cast<int64_t>(tick_us_);
  if (phase_ >= kPhasePerHalfStep) {
    phase_ -= kPhasePerHalfStep;
    *dir_step = 1;
    return true;
  }
  if (phase_ <= -kPhasePerHalfStep) {
    phase_ += kPhasePerHalfStep;
    *dir_step = -1;
    return true;
  }
  return false;
}
