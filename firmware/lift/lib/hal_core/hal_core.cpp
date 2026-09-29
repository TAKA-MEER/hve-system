#include "hal_core.h"

namespace {

// HC-SR04 は「往復」を測る。音速 約 343 m/s は 0.0343 mm/µs なので、
// 距離 = 0.0343 / 2 = 0.017 mm/µs。整数で割り切れるように 100 倍して扱う
// （NewPing の 0.017 cm/µs と同じ値。20 mm なら約 118 µs・4 m なら約 23.5 ms）。
constexpr int64_t kMmPerEchoUsNum = 17;
constexpr int64_t kMmPerEchoUsDen = 100;

int64_t echo_us_to_mm(uint32_t echo_us) {
  return static_cast<int64_t>(echo_us) * kMmPerEchoUsNum / kMmPerEchoUsDen;
}

}  // namespace

bool sonar_echo_to_mm(uint32_t echo_us, int* out_mm) {
  if (out_mm == nullptr) {
    return false;
  }
  // 32 bit の echo_us を 64 bit で掛けてから割るので、途中でオーバーフローしない
  const int64_t mm = echo_us_to_mm(echo_us);
  if (mm < SONAR_MIN_RANGE_MM || mm > SONAR_MAX_RANGE_MM) {
    return false;
  }
  *out_mm = static_cast<int>(mm);
  return true;
}

bool bottom_pressed_from_level(int level) {
  return level == BOTTOM_PRESSED_LEVEL;
}

uint32_t motor_duty_to_pwm(int duty_pct, int resolution) {
  // 停止が 0 でなければ安全を満たさないので、ここを必ず通す
  if (duty_pct <= 0 || resolution <= 0) {
    return 0;
  }
  if (duty_pct > LIFT_DUTY_ABS_MAX_PCT) {
    duty_pct = LIFT_DUTY_ABS_MAX_PCT;
  }
  const int64_t full_scale = (static_cast<int64_t>(1) << resolution) - 1;
  return static_cast<uint32_t>((static_cast<int64_t>(duty_pct) * full_scale) / LIFT_DUTY_ABS_MAX_PCT);
}
