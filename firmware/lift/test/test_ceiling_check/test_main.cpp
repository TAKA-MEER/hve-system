// Unity の試験（env:native）。ceiling_check() の表の各行・境界。
// 規則は docs/plan/detailed/DetailedDesign.md §3.2。
#include <unity.h>

#include "ceiling_check.h"
#include "lift_decide.h"  // elapsed_ms・StopReason の完全な定義

namespace {

CeilingReport measured(int mm, uint32_t age_ms = 80, uint32_t received_at_ms = 1000) {
  CeilingReport report;
  report.status = CeilingStatus::MEASURED;
  report.mm = mm;
  report.age_ms = age_ms;
  report.received_at_ms = received_at_ms;
  return report;
}

void assert_verdict(const CeilingReport& report, uint32_t now_ms, bool has_sensor, bool ok,
                    StopReason reason) {
  const CeilingVerdict verdict = ceiling_check(report, now_ms, has_sensor);
  TEST_ASSERT_EQUAL_INT(ok, verdict.ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(reason), static_cast<int>(verdict.reason));
}

}  // namespace

// --- 距離計を持たない接続は常に ok ---

void test_no_sensor_is_always_ok() {
  assert_verdict(measured(0), 1000, false, true, StopReason::NONE);
  CeilingReport missing;
  assert_verdict(missing, 100000, false, true, StopReason::NONE);
}

// --- MEASURED ---

void test_measured_far_is_ok() {
  assert_verdict(measured(CEILING_MARGIN_MM + 1), 1000, true, true, StopReason::NONE);
}

void test_measured_at_margin_stops_with_near() {
  assert_verdict(measured(CEILING_MARGIN_MM), 1000, true, false, StopReason::CEILING_NEAR);
}

void test_measured_below_margin_stops_with_near() {
  assert_verdict(measured(0), 1000, true, false, StopReason::CEILING_NEAR);
}

// --- TOO_NEAR / NO_ECHO / READ_ERROR ---

void test_too_near_stops_with_near() {
  CeilingReport report;
  report.status = CeilingStatus::TOO_NEAR;
  report.received_at_ms = 1000;
  assert_verdict(report, 1000, true, false, StopReason::CEILING_NEAR);
}

void test_no_echo_is_ok_with_out_of_range() {
  CeilingReport report;
  report.status = CeilingStatus::NO_ECHO;
  report.received_at_ms = 1000;
  assert_verdict(report, 1000, true, true, StopReason::OUT_OF_RANGE);
}

void test_read_error_stops_with_stale() {
  CeilingReport report;
  report.status = CeilingStatus::READ_ERROR;
  report.received_at_ms = 1000;
  assert_verdict(report, 1000, true, false, StopReason::CEILING_STALE);
}

void test_missing_stops_with_stale() {
  CeilingReport report;
  report.status = CeilingStatus::MISSING;
  report.received_at_ms = 1000;
  assert_verdict(report, 1000, true, false, StopReason::CEILING_STALE);
}

// --- 古さは age_ms ＋受け取ってからの経過 ---

void test_stale_boundary_is_allowed_at_600ms() {
  // 送る瞬間に 590 ms 古く、受け取ってから 10 ms → 600 ms は許す
  assert_verdict(measured(1450, 590, 1000), 1010, true, true, StopReason::NONE);
}

void test_elapsed_after_receive_makes_it_stale() {
  // 送る瞬間は新しくても、受け取ってから 601 ms 経てば古い
  assert_verdict(measured(1450, 0, 1000), 1601, true, false, StopReason::CEILING_STALE);
}

void test_age_only_is_not_enough() {
  // age_ms だけ見れば新しい（590 ms）が、受け取ってからの 20 ms を足すと古い
  assert_verdict(measured(1450, 590, 1000), 1020, true, false, StopReason::CEILING_STALE);
}

void test_stale_wins_over_near() {
  // 近くても古ければ理由は STALE（値そのものを信じない）
  assert_verdict(measured(0, 0, 1000), 1601, true, false, StopReason::CEILING_STALE);
}

void test_stale_applies_to_no_echo_too() {
  CeilingReport report;
  report.status = CeilingStatus::NO_ECHO;
  report.age_ms = 0;
  report.received_at_ms = 1000;
  assert_verdict(report, 1601, true, false, StopReason::CEILING_STALE);
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_no_sensor_is_always_ok);

  RUN_TEST(test_measured_far_is_ok);
  RUN_TEST(test_measured_at_margin_stops_with_near);
  RUN_TEST(test_measured_below_margin_stops_with_near);

  RUN_TEST(test_too_near_stops_with_near);
  RUN_TEST(test_no_echo_is_ok_with_out_of_range);
  RUN_TEST(test_read_error_stops_with_stale);
  RUN_TEST(test_missing_stops_with_stale);

  RUN_TEST(test_stale_boundary_is_allowed_at_600ms);
  RUN_TEST(test_elapsed_after_receive_makes_it_stale);
  RUN_TEST(test_age_only_is_not_enough);
  RUN_TEST(test_stale_wins_over_near);
  RUN_TEST(test_stale_applies_to_no_echo_too);

  return UNITY_END();
}
