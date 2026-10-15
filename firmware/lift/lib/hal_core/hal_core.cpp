#include "hal_core.h"

namespace {

int apply_ctrl(int base, int ctrl_mode) {
  if (ctrl_mode == kPcntReverse) {
    if (base == kPcntInc) {
      return kPcntDec;
    }
    if (base == kPcntDec) {
      return kPcntInc;
    }
    return base;
  }
  if (ctrl_mode == kPcntKeep) {
    return base;
  }
  return 0;  // DISABLE などは数えない
}

int to_delta(int mode) {
  if (mode == kPcntInc) {
    return +1;
  }
  if (mode == kPcntDec) {
    return -1;
  }
  return 0;
}

}  // namespace

int pcnt_quad_step(const PcntQuadConfig& cfg, int prev_a, int prev_b, int now_a, int now_b) {
  int total = 0;
  // ch0: パルス=A・制御=B
  if (now_a != prev_a) {
    const int base = (now_a != 0) ? cfg.ch0_pos : cfg.ch0_neg;
    total += to_delta(apply_ctrl(base, (now_b != 0) ? cfg.ch0_hctrl : cfg.ch0_lctrl));
  }
  // ch1: パルス=B・制御=A
  if (now_b != prev_b) {
    const int base = (now_b != 0) ? cfg.ch1_pos : cfg.ch1_neg;
    total += to_delta(apply_ctrl(base, (now_a != 0) ? cfg.ch1_hctrl : cfg.ch1_lctrl));
  }
  return total;
}

void EncoderHeight::update(int32_t count, bool bottom_pressed, int up_sign) {
  last_count_ = count;
  last_sign_ = (up_sign >= 0) ? +1 : -1;
  const int32_t signed_count = count * last_sign_;
  const int32_t signed_home = home_count_ * last_sign_;

  if (bottom_pressed && !prev_pressed_) {
    // 押された瞬間（起動時に既に押されていれば起動時）。そのときの数を原点にする。
    home_count_ = count;
    has_home_ = true;
    fault_ = false;
  } else if (bottom_pressed && has_home_ && !fault_) {
    if (signed_count < signed_home) {
      // 押されている間の下降側への進みは原点を下げる（高さは 0 のまま）。
      home_count_ = count;
    } else {
      // 上昇側へは原点を動かさない。HOME_SLACK_MM を超えたらスイッチの故障。
      const int64_t up_um =
          (static_cast<int64_t>(signed_count) - signed_home) * LIFT_UM_PER_COUNT;
      if (up_um > static_cast<int64_t>(HOME_SLACK_MM) * 1000) {
        fault_ = true;
      }
    }
  }
  // 離れている間は何もしない。一度故障したら、離れて押し直されるまで戻らない。
  prev_pressed_ = bottom_pressed;
}

int EncoderHeight::height_mm() const {
  const int64_t diff = static_cast<int64_t>(last_count_) - home_count_;
  return static_cast<int>(diff * last_sign_ * LIFT_UM_PER_COUNT / 1000);
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
