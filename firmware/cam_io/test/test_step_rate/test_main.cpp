// Unity の試験（env:native）。StepRate の刻みの数と向き。
// 速さ [半ステップ毎秒] × 周期 [µs] を足し、1000000 で 1 刻み。
#include <unity.h>

#include "step_rate.h"

namespace {

// ticks 回まわして刻んだ回数を数える。向きが違えば落とす
int count_steps(StepRate* rate, int ticks, int expected_dir) {
  int steps = 0;
  for (int i = 0; i < ticks; ++i) {
    int dir = 0;
    if (rate->tick(&dir)) {
      TEST_ASSERT_EQUAL_INT(expected_dir, dir);
      ++steps;
    }
  }
  return steps;
}

}  // namespace

void test_zero_rate_never_steps() {
  StepRate rate(100);
  rate.set_hsps(0);
  TEST_ASSERT_EQUAL_INT(0, count_steps(&rate, 1000, 1));
}

void test_step_count_matches_rate_over_one_second() {
  // 100 半ステップ毎秒・100 µs 周期 → 10000 回でちょうど 100 刻み
  StepRate rate(100);
  rate.set_hsps(100);
  TEST_ASSERT_EQUAL_INT(100, count_steps(&rate, 10000, 1));
}

void test_max_rate_steps_680_times_per_second() {
  // 上限 680 でも 1 秒でちょうど 680 刻み（約 1.47 ms ごと）
  StepRate rate(100);
  rate.set_hsps(680);
  TEST_ASSERT_EQUAL_INT(680, count_steps(&rate, 10000, 1));
}

void test_negative_rate_steps_backwards() {
  StepRate rate(100);
  rate.set_hsps(-100);
  TEST_ASSERT_EQUAL_INT(100, count_steps(&rate, 10000, -1));
}

void test_slow_rate_steps_once_per_10000_ticks() {
  // 1 半ステップ毎秒なら 10000 回で 1 刻み
  StepRate rate(100);
  rate.set_hsps(1);
  TEST_ASSERT_EQUAL_INT(2, count_steps(&rate, 20000, 1));
}

void test_changing_rate_resets_phase() {
  StepRate rate(100);
  rate.set_hsps(680);
  TEST_ASSERT_EQUAL_INT(0, count_steps(&rate, 7, 1));  // 位相 476000（まだ刻まない）
  rate.set_hsps(680);  // 溜まった位相を捨てる
  // 捨てなければ 8 回目（952000＋68000）で刻む。捨てたので 10 回でも刻まない
  TEST_ASSERT_EQUAL_INT(0, count_steps(&rate, 10, 1));
}

void test_null_dir_is_rejected() {
  StepRate rate(100);
  rate.set_hsps(680);
  TEST_ASSERT_FALSE(rate.tick(nullptr));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_zero_rate_never_steps);
  RUN_TEST(test_step_count_matches_rate_over_one_second);
  RUN_TEST(test_max_rate_steps_680_times_per_second);
  RUN_TEST(test_negative_rate_steps_backwards);
  RUN_TEST(test_slow_rate_steps_once_per_10000_ticks);
  RUN_TEST(test_changing_rate_resets_phase);
  RUN_TEST(test_null_dir_is_rejected);

  return UNITY_END();
}
