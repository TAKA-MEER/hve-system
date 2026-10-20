// Unity の試験（env:native）。組み立て用ファーム（W-2）のスイッチの判定。
// ここで縛るのは「どちらの側で動くか・両方 LOW で止まる・離したらすぐ止まる・
// チャタリング除け・反転のときに一度止める」こと。
// src/manual/main.cpp がこの結果をそのままモータへ出しているかはこの試験では見られない。
#include <unity.h>

#include "manual_switch.h"

namespace {

constexpr int kHigh = 1;
constexpr int kLow = 0;

// 起動から十分たった時刻（起動直後の停止の間を越えている）
constexpr uint32_t kT0 = 1000;

// up 側を入れ続け、動き出したところまで進める。戻り値は動き出した時刻
uint32_t start_up(ManualSwitch& sw, uint32_t t) {
  sw.update(kLow, kHigh, t);
  sw.update(kLow, kHigh, t + MANUAL_DEBOUNCE_MS);
  return t + MANUAL_DEBOUNCE_MS;
}

}  // namespace

void setUp() {}
void tearDown() {}

void test_levels_up_only_is_up() {
  TEST_ASSERT_EQUAL(ManualDir::UP, manual_dir_from_levels(kLow, kHigh));
}

void test_levels_down_only_is_down() {
  TEST_ASSERT_EQUAL(ManualDir::DOWN, manual_dir_from_levels(kHigh, kLow));
}

void test_levels_neither_is_stop() {
  TEST_ASSERT_EQUAL(ManualDir::STOP, manual_dir_from_levels(kHigh, kHigh));
}

void test_levels_both_low_is_stop() {
  TEST_ASSERT_EQUAL(ManualDir::STOP, manual_dir_from_levels(kLow, kLow));
}

void test_starts_stopped() {
  ManualSwitch sw;
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.output());
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kHigh, kHigh, kT0));
}

void test_does_not_move_right_after_boot() {
  ManualSwitch sw(0);
  // スイッチを入れたまま電源を入れても、停止の間が過ぎるまでは動かない
  sw.update(kLow, kHigh, 0);
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kLow, kHigh, MANUAL_REVERSE_PAUSE_MS - 1));
  TEST_ASSERT_EQUAL(ManualDir::UP, sw.update(kLow, kHigh, MANUAL_REVERSE_PAUSE_MS));
}

void test_up_after_debounce() {
  ManualSwitch sw;
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kLow, kHigh, kT0));
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kLow, kHigh, kT0 + MANUAL_DEBOUNCE_MS - 1));
  TEST_ASSERT_EQUAL(ManualDir::UP, sw.update(kLow, kHigh, kT0 + MANUAL_DEBOUNCE_MS));
}

void test_down_after_debounce() {
  ManualSwitch sw;
  sw.update(kHigh, kLow, kT0);
  TEST_ASSERT_EQUAL(ManualDir::DOWN, sw.update(kHigh, kLow, kT0 + MANUAL_DEBOUNCE_MS));
}

void test_chatter_restarts_debounce() {
  ManualSwitch sw;
  sw.update(kLow, kHigh, kT0);
  sw.update(kHigh, kHigh, kT0 + 10);  // 跳ねて一瞬離れた
  sw.update(kLow, kHigh, kT0 + 20);
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kLow, kHigh, kT0 + 20 + MANUAL_DEBOUNCE_MS - 1));
  TEST_ASSERT_EQUAL(ManualDir::UP, sw.update(kLow, kHigh, kT0 + 20 + MANUAL_DEBOUNCE_MS));
}

void test_release_stops_immediately() {
  ManualSwitch sw;
  const uint32_t t = start_up(sw, kT0);
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kHigh, kHigh, t + 1));
}

void test_both_low_while_moving_stops() {
  ManualSwitch sw;
  const uint32_t t = start_up(sw, kT0);
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kLow, kLow, t + 1));
  // 両方 LOW のままでは、いくら待っても動かない
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kLow, kLow, t + 10000));
}

void test_reverse_pauses_before_moving() {
  ManualSwitch sw;
  const uint32_t t = start_up(sw, kT0);
  // 上から下へ一気に倒した
  const uint32_t flip = t + 500;
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kHigh, kLow, flip));
  TEST_ASSERT_EQUAL(ManualDir::STOP, sw.update(kHigh, kLow, flip + MANUAL_REVERSE_PAUSE_MS - 1));
  TEST_ASSERT_EQUAL(ManualDir::DOWN, sw.update(kHigh, kLow, flip + MANUAL_REVERSE_PAUSE_MS));
}

void test_keeps_running_while_held() {
  ManualSwitch sw;
  const uint32_t t = start_up(sw, kT0);
  TEST_ASSERT_EQUAL(ManualDir::UP, sw.update(kLow, kHigh, t + 60000));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();
  RUN_TEST(test_levels_up_only_is_up);
  RUN_TEST(test_levels_down_only_is_down);
  RUN_TEST(test_levels_neither_is_stop);
  RUN_TEST(test_levels_both_low_is_stop);
  RUN_TEST(test_starts_stopped);
  RUN_TEST(test_does_not_move_right_after_boot);
  RUN_TEST(test_up_after_debounce);
  RUN_TEST(test_down_after_debounce);
  RUN_TEST(test_chatter_restarts_debounce);
  RUN_TEST(test_release_stops_immediately);
  RUN_TEST(test_both_low_while_moving_stops);
  RUN_TEST(test_reverse_pauses_before_moving);
  RUN_TEST(test_keeps_running_while_held);
  return UNITY_END();
}
