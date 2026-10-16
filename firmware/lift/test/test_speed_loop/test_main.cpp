// Unity の試験（env:native）。SpeedLoop の前向き・PI・丸め・積分の扱い。
// 規則は docs/plan/detailed/DetailedDesign.md §4.1.2。
#include <unity.h>

#include "speed_loop.h"

namespace {

// 前向きだけの出力（目標 × 係数を四捨五入）
int feedforward_up(int speed) {
  return static_cast<int>(LIFT_FF_UP_PCT_PER_MM_S * static_cast<float>(speed) + 0.5f);
}

int feedforward_down(int speed) {
  return static_cast<int>(LIFT_FF_DOWN_PCT_PER_MM_S * static_cast<float>(speed) + 0.5f);
}

}  // namespace

void test_slow_speed_raises_output() {
  SpeedLoop loop;
  // 測った値が 0（目標より遅い）と前向きより上がる
  const int out = loop.update(40, 0.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS, LIFT_DUTY_ABS_MAX_PCT);
  TEST_ASSERT_GREATER_THAN(feedforward_up(40), out);
}

void test_fast_speed_lowers_output() {
  SpeedLoop loop;
  // 測った値が目標より速いと前向きより下がる
  const int out = loop.update(40, 60.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS, LIFT_DUTY_ABS_MAX_PCT);
  TEST_ASSERT_LESS_THAN(feedforward_up(40), out);
}

void test_output_is_clamped_while_integral_holds() {
  SpeedLoop loop;
  // 目標の上限いっぱいで丸めに当てる。積分は進まない
  const int first =
      loop.update(LIFT_SPEED_ABS_MAX_MM_S, 0.0f, LiftDir::UP, 1000, LIFT_DUTY_ABS_MAX_PCT);
  TEST_ASSERT_EQUAL_INT(LIFT_DUTY_ABS_MAX_PCT, first);
  // 丸めの間に積分が進んでいないので、目標を下げると前向きの近くに戻る
  const int back = loop.update(10, 10.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS, LIFT_DUTY_ABS_MAX_PCT);
  TEST_ASSERT_INT_WITHIN(3, feedforward_up(10), back);
}

void test_output_is_clamped_at_motor_max_pct() {
  SpeedLoop loop;
  // 上限 50 % では 100 % まで出ない（Spec-ui §2.1。定格 12 V ÷ 電源 24 V）
  const int out = loop.update(LIFT_SPEED_ABS_MAX_MM_S, 0.0f, LiftDir::UP, 1000, 50);
  TEST_ASSERT_EQUAL_INT(50, out);
  // 丸めの間に積分が進んでいないので、目標を下げると前向きの近くに戻る
  const int back = loop.update(10, 10.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS, 50);
  TEST_ASSERT_INT_WITHIN(3, feedforward_up(10), back);
}

void test_reset_clears_integral() {
  SpeedLoop loop;
  // 遅い状態を積んで積分を育てる
  for (int i = 0; i < 10; ++i) {
    loop.update(40, 0.0f, LiftDir::UP, 100, LIFT_DUTY_ABS_MAX_PCT);
  }
  const int grown = loop.update(40, 0.0f, LiftDir::UP, 100, LIFT_DUTY_ABS_MAX_PCT);
  TEST_ASSERT_GREATER_THAN(feedforward_up(40), grown);
  // reset() で積分が 0 に戻り、最初の出力は前向き＋比例だけになる
  loop.reset();
  const int after = loop.update(40, 0.0f, LiftDir::UP, 0, LIFT_DUTY_ABS_MAX_PCT);
  const int expect =
      static_cast<int>(LIFT_FF_UP_PCT_PER_MM_S * 40.0f + LIFT_SPEED_KP * 40.0f + 0.5f);
  TEST_ASSERT_EQUAL_INT(expect, after);
}

void test_down_uses_down_feedforward() {
  SpeedLoop up;
  SpeedLoop down;
  // 同じ目標・同じ偏差（0）なら、上昇と下降で前向きの係数が違う
  TEST_ASSERT_EQUAL_INT(feedforward_up(40),
                        up.update(40, 40.0f, LiftDir::UP, 0, LIFT_DUTY_ABS_MAX_PCT));
  TEST_ASSERT_EQUAL_INT(feedforward_down(40),
                        down.update(40, 40.0f, LiftDir::DOWN, 0, LIFT_DUTY_ABS_MAX_PCT));
}

void test_reversed_speed_raises_output() {
  SpeedLoop loop;
  // 逆向きに回っている（負）と偏差が大きくなり、出力を上げる側に効く
  const int reversed = loop.update(40, -10.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS,
                                   LIFT_DUTY_ABS_MAX_PCT);
  SpeedLoop still;
  const int stopped =
      still.update(40, 0.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS, LIFT_DUTY_ABS_MAX_PCT);
  TEST_ASSERT_GREATER_THAN(stopped, reversed);
}

void test_speed_from_counts_converts_units() {
  // 100 counts × 100 µm × +1 ÷ 100 ms = 100 mm/s（上向きが正）
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 100.0f,
                           speed_from_counts(0, 100, 1000, 1100, 100, +1));
  // 下降の向き・符号の反転は呼び出し側。ここでは符号どおり
  TEST_ASSERT_FLOAT_WITHIN(0.001f, -100.0f,
                           speed_from_counts(0, 100, 1000, 1100, 100, -1));
  // 時刻が進まなければ 0
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, speed_from_counts(0, 100, 1000, 1000, 100, +1));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_slow_speed_raises_output);
  RUN_TEST(test_fast_speed_lowers_output);
  RUN_TEST(test_output_is_clamped_while_integral_holds);
  RUN_TEST(test_output_is_clamped_at_motor_max_pct);
  RUN_TEST(test_reset_clears_integral);
  RUN_TEST(test_down_uses_down_feedforward);
  RUN_TEST(test_reversed_speed_raises_output);
  RUN_TEST(test_speed_from_counts_converts_units);

  return UNITY_END();
}
