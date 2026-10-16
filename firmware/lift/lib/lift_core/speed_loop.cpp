// docs/plan/detailed/DetailedDesign.md §4.1.2。
#include "speed_loop.h"

float speed_from_counts(int32_t prev_count, int32_t now_count, uint32_t prev_ms,
                        uint32_t now_ms, int32_t um_per_count, int up_sign) {
  const int32_t dt = elapsed_ms(now_ms, prev_ms);
  if (dt <= 0) {
    return 0.0f;
  }
  const int32_t delta = now_count - prev_count;
  // µm → mm（/1000）、ms → s（×1000）は相殺するので、delta × um/count × sign ÷ dt だけ残る
  return static_cast<float>(delta) * static_cast<float>(um_per_count) *
         static_cast<float>(up_sign) / static_cast<float>(dt);
}

int SpeedLoop::update(int target_mm_s, float measured_mm_s, LiftDir dir, uint32_t dt_ms,
                      int max_pct) {
  // 上限の外は絶対上限に丸める（呼び出し側が設定の値を渡す。ここでは守るだけ）
  if (max_pct < 0) {
    max_pct = 0;
  }
  if (max_pct > LIFT_DUTY_ABS_MAX_PCT) {
    max_pct = LIFT_DUTY_ABS_MAX_PCT;
  }
  const float cap = static_cast<float>(max_pct);
  const float ff_gain =
      (dir == LiftDir::DOWN) ? LIFT_FF_DOWN_PCT_PER_MM_S : LIFT_FF_UP_PCT_PER_MM_S;
  const float target = static_cast<float>(target_mm_s);
  const float error = target - measured_mm_s;
  const float ff = ff_gain * target;
  const float p = LIFT_SPEED_KP * error;

  // 積分を進めた場合の出力。丸めに当たらなければこれを出す
  float advanced = integral_;
  if (dt_ms > 0) {
    advanced += error * (static_cast<float>(dt_ms) / 1000.0f);
  }
  float out = ff + p + LIFT_SPEED_KI * advanced;
  if (out > cap) {
    // 上の丸めに当たったので積分は進めない。進めない値でも丸める
    out = ff + p + LIFT_SPEED_KI * integral_;
    if (out > cap) {
      out = cap;
    } else if (out < 0.0f) {
      out = 0.0f;
    }
    return static_cast<int>(out + 0.5f);
  }
  if (out < 0.0f) {
    out = ff + p + LIFT_SPEED_KI * integral_;
    if (out < 0.0f) {
      out = 0.0f;
    } else if (out > cap) {
      out = cap;
    }
    return static_cast<int>(out + 0.5f);
  }
  integral_ = advanced;
  return static_cast<int>(out + 0.5f);
}

void SpeedLoop::reset() { integral_ = 0.0f; }
