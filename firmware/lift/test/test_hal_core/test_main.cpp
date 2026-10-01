// Unity の試験（env:native）。WP-LIFT-02 で足した hal_core の純関数の試験。
// ここで縛るのは「換算の式・測定範囲の判定・停止が 0 になる」こと。
// hal_esp32 がこれらを実際に使っているかはこの試験では見られないので、
// 変異チェックでは hal_esp32 側の配線も手で確かめること。
#include <unity.h>

#include "hal_core.h"

namespace {

// Arduino の HIGH = 1 / LOW = 0（Arduino.h は include しないので手で書く）
constexpr int kHigh = 1;
constexpr int kLow = 0;

// PWM の分解能。names.md §5 の PWM_RESOLUTION（昇降部 config.h / 8 bit）と同じ値。
// ここでは引数として渡すだけなので config.h ではなく試験側で持つ。
constexpr int kResolution = 8;
constexpr uint32_t kFullScale = (1u << kResolution) - 1;

// 距離 mm に対応する ECHO のパルス幅（µs）。hal_core.cpp の 0.017 mm/µs（100 で割る）と
// 対応する。切り捨てで境界をまたがないよう、必ず 1 µs 切り上げて作る。
uint32_t echo_us_for_mm(int mm) { return static_cast<uint32_t>((mm * 100 + 16) / 17); }

}  // namespace

void setUp() {}

void tearDown() {}

// --- 超音波: パルス幅 -> mm ---

void test_echo_width_becomes_mm_at_200mm() {
  int mm = -1;
  TEST_ASSERT_TRUE(sonar_echo_to_mm(echo_us_for_mm(200), &mm));
  TEST_ASSERT_EQUAL_INT(200, mm);
}

void test_echo_width_becomes_mm_at_800mm() {
  int mm = -1;
  TEST_ASSERT_TRUE(sonar_echo_to_mm(echo_us_for_mm(800), &mm));
  TEST_ASSERT_EQUAL_INT(800, mm);
}

void test_echo_width_is_linear_across_the_range() {
  // 換算の割合が途中で変わっていないことを、2 点ではなく連続で見る
  int mm = -1;
  for (int want = 100; want <= 2000; want += 137) {
    TEST_ASSERT_TRUE(sonar_echo_to_mm(echo_us_for_mm(want), &mm));
    TEST_ASSERT_EQUAL_INT(want, mm);
  }
}

void test_echo_width_counts_the_round_trip() {
  // 118 µs の ECHO は約 2 cm。片道の距離で割ると 2 倍の 40 mm になってしまうので、
  // 40 mm も範囲内なので assert が効く。
  int mm = -1;
  TEST_ASSERT_TRUE(sonar_echo_to_mm(echo_us_for_mm(SONAR_MIN_RANGE_MM), &mm));
  TEST_ASSERT_EQUAL_INT(SONAR_MIN_RANGE_MM, mm);
  TEST_ASSERT_NOT_EQUAL(2 * SONAR_MIN_RANGE_MM, mm);
}

void test_zero_echo_is_out_of_range() {
  int mm = -1;
  TEST_ASSERT_FALSE(sonar_echo_to_mm(0, &mm));
}

// --- 超音波: 測定範囲の外は無効 ---

void test_below_min_range_is_rejected() {
  int mm = -1;
  TEST_ASSERT_FALSE(sonar_echo_to_mm(echo_us_for_mm(SONAR_MIN_RANGE_MM) - 1, &mm));
}

void test_exactly_min_range_is_accepted() {
  int mm = -1;
  TEST_ASSERT_TRUE(sonar_echo_to_mm(echo_us_for_mm(SONAR_MIN_RANGE_MM), &mm));
  TEST_ASSERT_EQUAL_INT(SONAR_MIN_RANGE_MM, mm);
}

void test_over_max_range_is_rejected() {
  // 1 µs で 0.017 mm しか変わらないので、確実に範囲外になる幅（+100 µs ≒ +1.7 m）で見る
  int mm = -1;
  TEST_ASSERT_FALSE(sonar_echo_to_mm(echo_us_for_mm(SONAR_MAX_RANGE_MM) + 100, &mm));
}

void test_exactly_max_range_is_accepted() {
  int mm = -1;
  TEST_ASSERT_TRUE(sonar_echo_to_mm(echo_us_for_mm(SONAR_MAX_RANGE_MM), &mm));
  TEST_ASSERT_EQUAL_INT(SONAR_MAX_RANGE_MM, mm);
}

void test_out_of_range_does_not_write_the_out_value() {
  // 無効なときは out_mm を書き換えない（呼び出し側が前の有効値を保持できる）
  int mm = 12345;
  TEST_ASSERT_FALSE(sonar_echo_to_mm(echo_us_for_mm(SONAR_MAX_RANGE_MM) + 1000, &mm));
  TEST_ASSERT_EQUAL_INT(12345, mm);
}

void test_garbage_echo_is_rejected() {
  int mm = -1;
  TEST_ASSERT_FALSE(sonar_echo_to_mm(0xFFFFFFFFu, &mm));
}

void test_echo_timeout_window_covers_the_max_range() {
  // 割り込みの時間切れは 4 m の往復より長くなければならない（真的会 4 m まで
  // 測れなくなる）。かつ HC-SR04 の 1 回の測定周期 60 ms より短いこと。
  const uint32_t max_range_echo_us = echo_us_for_mm(SONAR_MAX_RANGE_MM);
  TEST_ASSERT_TRUE(SONAR_ECHO_TIMEOUT_US > max_range_echo_us);
  TEST_ASSERT_TRUE(SONAR_ECHO_TIMEOUT_US < 60000);
}

void test_no_echo_reports_zero_width() {
  // 割り込みが立ち上がりを得られなかったとき hal_esp32 は幅 0 µs として扱う。
  // 0 は測定範囲の外なので「高さが読めない」になる（ height_ok = false）。
  int mm = -1;
  TEST_ASSERT_FALSE(sonar_echo_to_mm(0, &mm));
}

void test_null_out_is_rejected() {
  TEST_ASSERT_FALSE(sonar_echo_to_mm(echo_us_for_mm(500), nullptr));
}

// --- 下端スイッチ ---

void test_bottom_high_means_pressed() {
  // NC 配線（hardware §1）。HIGH = 開いている = 押されている / 断線
  TEST_ASSERT_TRUE(bottom_pressed_from_level(kHigh));
}

void test_bottom_low_means_not_pressed() {
  TEST_ASSERT_FALSE(bottom_pressed_from_level(kLow));
}

void test_bottom_pressed_level_is_high() {
  // 配線を LOW に変えたときは定数も変えるので、ここで表に固定する
  TEST_ASSERT_EQUAL_INT(kHigh, BOTTOM_PRESSED_LEVEL);
}

void test_bottom_accepts_only_the_pressed_level() {
  TEST_ASSERT_EQUAL_INT(1, bottom_pressed_from_level(kHigh));
  TEST_ASSERT_EQUAL_INT(0, bottom_pressed_from_level(kLow));
}

// --- モータのデューティ -> PWM ---

void test_duty_zero_gives_zero_pwm() {
  // 停止が 0 でなければ「止まれ」が全開になる
  TEST_ASSERT_EQUAL_UINT32(0, motor_duty_to_pwm(0, kResolution));
}

void test_duty_negative_gives_zero_pwm() {
  TEST_ASSERT_EQUAL_UINT32(0, motor_duty_to_pwm(-10, kResolution));
}

void test_duty_full_gives_full_scale() {
  TEST_ASSERT_EQUAL_UINT32(kFullScale, motor_duty_to_pwm(100, kResolution));
}

void test_duty_over_full_is_clamped() {
  TEST_ASSERT_EQUAL_UINT32(kFullScale, motor_duty_to_pwm(250, kResolution));
}

void test_duty_is_linear_in_pwm() {
  // 8 bit なら 40% で 102。比例の関係が崩れていないことを見る
  TEST_ASSERT_EQUAL_UINT32(kFullScale * 40 / 100, motor_duty_to_pwm(40, kResolution));
  TEST_ASSERT_EQUAL_UINT32(kFullScale / 4, motor_duty_to_pwm(25, kResolution));
}

void test_pwm_stays_inside_the_channel() {
  for (int duty = -20; duty <= 120; duty += 7) {
    TEST_ASSERT_TRUE(motor_duty_to_pwm(duty, kResolution) <= kFullScale);
  }
}

void test_pwm_works_at_12_bit_too() {
  const uint32_t full = (1u << 12) - 1;
  TEST_ASSERT_EQUAL_UINT32(0, motor_duty_to_pwm(0, 12));
  TEST_ASSERT_EQUAL_UINT32(full, motor_duty_to_pwm(100, 12));
}

// --- 高さの中央値フィルタ（直近 5 回）---

namespace {
// 5 回分を入れる。最後の戻り値と中央値を返す
bool feed(HeightFilter& f, const int* mms, int n, int* out) {
  bool ok = false;
  for (int i = 0; i < n; ++i) {
    ok = f.on_echo(echo_us_for_mm(mms[i]), out);
  }
  return ok;
}
}  // namespace

void test_filter_is_not_ready_until_five_samples() {
  HeightFilter f;
  int mm = -1;
  for (int i = 0; i < 4; ++i) {
    TEST_ASSERT_FALSE(f.on_echo(echo_us_for_mm(2200), &mm));
  }
  TEST_ASSERT_TRUE(f.on_echo(echo_us_for_mm(2200), &mm));
  TEST_ASSERT_EQUAL_INT(2200, mm);
}

void test_filter_rejects_one_or_two_low_outliers() {
  const int one[] = {2200, 350, 2203, 2201, 2204};
  HeightFilter f1;
  int mm = -1;
  TEST_ASSERT_TRUE(feed(f1, one, 5, &mm));
  TEST_ASSERT_INT_WITHIN(3, 2201, mm);

  const int two[] = {350, 2203, 340, 2201, 2204};
  HeightFilter f2;
  TEST_ASSERT_TRUE(feed(f2, two, 5, &mm));
  TEST_ASSERT_INT_WITHIN(3, 2203, mm);
}

void test_filter_cannot_reject_three_in_a_row() {
  // 仕様の受け入れリスク（Spec-safety.md §2）。除けないことを明示しておく
  const int three[] = {2200, 350, 340, 360, 2204};
  HeightFilter f;
  int mm = -1;
  TEST_ASSERT_TRUE(feed(f, three, 5, &mm));
  TEST_ASSERT_INT_WITHIN(3, 360, mm);
}

void test_filter_window_slides() {
  HeightFilter f;
  int mm = -1;
  const int first[] = {1000, 1000, 1000, 1000, 1000};
  TEST_ASSERT_TRUE(feed(f, first, 5, &mm));
  TEST_ASSERT_INT_WITHIN(2, 1000, mm);
  // 3 個入れ替わると中央値が新しい値になる（古い値が落ちている）
  const int next[] = {2000, 2000, 2000};
  TEST_ASSERT_TRUE(feed(f, next, 3, &mm));
  TEST_ASSERT_INT_WITHIN(3, 2000, mm);
  // 4 個目で 1000 が 1 個だけ残る → まだ 2000 が多数派
  TEST_ASSERT_TRUE(f.on_echo(echo_us_for_mm(2000), &mm));
  TEST_ASSERT_INT_WITHIN(3, 2000, mm);
}

void test_filter_restarts_after_out_of_range() {
  HeightFilter f;
  int mm = -1;
  const int full[] = {2200, 2200, 2200, 2200, 2200};
  TEST_ASSERT_TRUE(feed(f, full, 5, &mm));
  // 範囲外（時間切れの幅 0 を含む）が 1 回来たら窓を空にする
  TEST_ASSERT_FALSE(f.on_echo(0, &mm));
  for (int i = 0; i < 4; ++i) {
    TEST_ASSERT_FALSE(f.on_echo(echo_us_for_mm(2200), &mm));
  }
  TEST_ASSERT_TRUE(f.on_echo(echo_us_for_mm(2200), &mm));
}

void test_filter_restarts_after_timeout() {
  HeightFilter f;
  int mm = -1;
  const int full[] = {2200, 2200, 2200, 2200, 2200};
  TEST_ASSERT_TRUE(feed(f, full, 5, &mm));
  f.on_invalid();
  for (int i = 0; i < 4; ++i) {
    TEST_ASSERT_FALSE(f.on_echo(echo_us_for_mm(2200), &mm));
  }
  TEST_ASSERT_TRUE(f.on_echo(echo_us_for_mm(2200), &mm));
}

void test_filter_null_out_is_false() {
  HeightFilter f;
  TEST_ASSERT_FALSE(f.on_echo(echo_us_for_mm(2200), nullptr));
}


int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();
  RUN_TEST(test_echo_width_becomes_mm_at_200mm);
  RUN_TEST(test_echo_width_becomes_mm_at_800mm);
  RUN_TEST(test_echo_width_is_linear_across_the_range);
  RUN_TEST(test_echo_width_counts_the_round_trip);
  RUN_TEST(test_zero_echo_is_out_of_range);
  RUN_TEST(test_below_min_range_is_rejected);
  RUN_TEST(test_exactly_min_range_is_accepted);
  RUN_TEST(test_over_max_range_is_rejected);
  RUN_TEST(test_exactly_max_range_is_accepted);
  RUN_TEST(test_out_of_range_does_not_write_the_out_value);
  RUN_TEST(test_garbage_echo_is_rejected);
  RUN_TEST(test_echo_timeout_window_covers_the_max_range);
  RUN_TEST(test_no_echo_reports_zero_width);
  RUN_TEST(test_null_out_is_rejected);
  RUN_TEST(test_bottom_high_means_pressed);
  RUN_TEST(test_bottom_low_means_not_pressed);
  RUN_TEST(test_bottom_pressed_level_is_high);
  RUN_TEST(test_bottom_accepts_only_the_pressed_level);
  RUN_TEST(test_duty_zero_gives_zero_pwm);
  RUN_TEST(test_duty_negative_gives_zero_pwm);
  RUN_TEST(test_duty_full_gives_full_scale);
  RUN_TEST(test_duty_over_full_is_clamped);
  RUN_TEST(test_duty_is_linear_in_pwm);
  RUN_TEST(test_pwm_stays_inside_the_channel);
  RUN_TEST(test_pwm_works_at_12_bit_too);
  RUN_TEST(test_filter_is_not_ready_until_five_samples);
  RUN_TEST(test_filter_rejects_one_or_two_low_outliers);
  RUN_TEST(test_filter_cannot_reject_three_in_a_row);
  RUN_TEST(test_filter_window_slides);
  RUN_TEST(test_filter_restarts_after_out_of_range);
  RUN_TEST(test_filter_restarts_after_timeout);
  RUN_TEST(test_filter_null_out_is_false);
  return UNITY_END();
}
