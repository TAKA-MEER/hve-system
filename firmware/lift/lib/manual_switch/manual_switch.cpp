#include "manual_switch.h"

namespace {
constexpr int kLow = 0;  // Arduino の LOW（Arduino.h は include しない）
}  // namespace

ManualDir manual_dir_from_levels(int up_level, int down_level) {
  const bool up = (up_level == kLow);
  const bool down = (down_level == kLow);
  if (up && down) {
    return ManualDir::STOP;
  }
  if (up) {
    return ManualDir::UP;
  }
  if (down) {
    return ManualDir::DOWN;
  }
  return ManualDir::STOP;
}

ManualDir ManualSwitch::update(int up_level, int down_level, uint32_t now_ms) {
  const ManualDir req = manual_dir_from_levels(up_level, down_level);
  if (req != candidate_) {
    candidate_ = req;
    candidate_since_ms_ = now_ms;
  }

  // 停止の要求と、動いている最中の向きの変更はすぐ止める（待たない）
  if (output_ != ManualDir::STOP && req != output_) {
    output_ = ManualDir::STOP;
    stopped_at_ms_ = now_ms;
    return output_;
  }

  if (output_ == ManualDir::STOP && req != ManualDir::STOP &&
      now_ms - candidate_since_ms_ >= MANUAL_DEBOUNCE_MS &&
      now_ms - stopped_at_ms_ >= MANUAL_REVERSE_PAUSE_MS) {
    output_ = req;
  }
  return output_;
}
