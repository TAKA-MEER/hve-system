// Unity の試験（env:native）。lift_decide() の表の各行・境界・組み合わせ。
// 規則は docs/plan/detailed/DetailedDesign.md §4.1。
#include <unity.h>

#include "lift_decide.h"

namespace {

// すべて「動いてよい」条件から始めて、1 つだけ変えた試験を書く
LiftDecideInput movable() {
  LiftDecideInput in;
  in.has_cmd = true;
  in.owner_empty_reason = StopReason::CMD_TIMEOUT;
  in.dir = LiftDir::UP;
  in.duty = 40;
  in.ceiling.status = CeilingStatus::MEASURED;
  in.ceiling.mm = 1450;
  in.ceiling.age_ms = 80;
  in.ceiling.received_at_ms = 1000;
  in.connection_has_sensor = true;
  in.now_ms = 1000;
  in.bottom_pressed = false;
  in.height_mm = 500;
  in.height_ok = true;
  in.height_at_ms = 1000;
  in.top_mm = LIFT_TOP_MM;
  in.run_ms = 0;
  return in;
}

void assert_decision(const LiftDecideInput& in, LiftDir dir, int duty, StopReason reason) {
  const LiftDecideResult r = lift_decide(in);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(dir), static_cast<int>(r.dir));
  TEST_ASSERT_EQUAL_INT(duty, r.duty);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(reason), static_cast<int>(r.reason));
}

}  // namespace

// --- 表 1 持ち主がいない ---

void test_no_owner_stops_with_empty_reason() {
  LiftDecideInput in = movable();
  in.has_cmd = false;
  in.owner_empty_reason = StopReason::CMD_TIMEOUT;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_TIMEOUT);
}

void test_no_owner_after_close_stops_with_owner_gone() {
  LiftDecideInput in = movable();
  in.has_cmd = false;
  in.owner_empty_reason = StopReason::OWNER_GONE;
  assert_decision(in, LiftDir::STOP, 0, StopReason::OWNER_GONE);
}

void test_no_owner_after_release_stops_with_cmd_stop() {
  LiftDecideInput in = movable();
  in.has_cmd = false;
  in.owner_empty_reason = StopReason::CMD_STOP;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_STOP);
}

// --- 表 2 停止指令 ---

void test_cmd_stop_stops_with_cmd_stop() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::STOP;
  in.duty = 60;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_STOP);
}

// --- 表 3 天井 ---

void test_up_with_far_ceiling_moves() {
  LiftDecideInput in = movable();
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_up_with_near_ceiling_stops_with_ceiling_near() {
  LiftDecideInput in = movable();
  in.ceiling.mm = CEILING_MARGIN_MM;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING_NEAR);
}

void test_up_with_too_near_stops_with_ceiling_near() {
  LiftDecideInput in = movable();
  in.ceiling.status = CeilingStatus::TOO_NEAR;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING_NEAR);
}

void test_up_with_no_echo_moves() {
  LiftDecideInput in = movable();
  in.ceiling.status = CeilingStatus::NO_ECHO;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_up_with_read_error_stops_with_ceiling_stale() {
  LiftDecideInput in = movable();
  in.ceiling.status = CeilingStatus::READ_ERROR;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING_STALE);
}

void test_up_with_missing_ceiling_stops_with_ceiling_stale() {
  LiftDecideInput in = movable();
  in.ceiling.status = CeilingStatus::MISSING;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING_STALE);
}

void test_up_with_stale_ceiling_stops_with_ceiling_stale() {
  LiftDecideInput in = movable();
  in.ceiling.age_ms = 0;
  in.ceiling.received_at_ms = 1000;
  in.now_ms = 1000 + CEILING_STALE_MS + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING_STALE);
}

void test_down_without_ceiling_still_moves() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::DOWN;
  in.ceiling.status = CeilingStatus::MISSING;
  assert_decision(in, LiftDir::DOWN, 40, StopReason::NONE);
}

void test_up_without_sensor_ignores_ceiling() {
  LiftDecideInput in = movable();
  in.connection_has_sensor = false;
  in.ceiling.status = CeilingStatus::MISSING;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_ceiling_wins_over_height_unknown() {
  LiftDecideInput in = movable();
  in.ceiling.status = CeilingStatus::MISSING;
  in.height_ok = false;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING_STALE);
}

// --- 表 4 高さが読めない・古い（W-1 の間は判定しない） ---

void test_up_with_invalid_height_still_moves_while_waived() {
  TEST_ASSERT_FALSE(LIFT_TOP_DETECT_ENABLED);  // WAIVER(demo): W-1
  LiftDecideInput in = movable();
  in.height_ok = false;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_up_with_stale_height_still_moves_while_waived() {
  TEST_ASSERT_FALSE(LIFT_TOP_DETECT_ENABLED);  // WAIVER(demo): W-1
  LiftDecideInput in = movable();
  in.height_at_ms = 1000;
  in.now_ms = 1000 + HEIGHT_STALE_MS + 1;
  in.ceiling.received_at_ms = in.now_ms;  // 天井は新しいまま（表 3 で止めない）
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

// --- 表 6 上端（W-1 の間は判定しない） ---

void test_up_at_top_threshold_still_moves_while_waived() {
  TEST_ASSERT_FALSE(LIFT_TOP_DETECT_ENABLED);  // WAIVER(demo): W-1
  LiftDecideInput in = movable();
  in.top_mm = 1500;
  in.height_mm = 1600;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

// --- 表 7 下端 ---

void test_down_with_bottom_pressed_stops_with_bottom() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::DOWN;
  in.bottom_pressed = true;
  assert_decision(in, LiftDir::STOP, 0, StopReason::BOTTOM);
}

void test_up_with_bottom_pressed_still_moves() {
  LiftDecideInput in = movable();
  in.bottom_pressed = true;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_bottom_wins_over_max_run() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::DOWN;
  in.bottom_pressed = true;
  in.run_ms = LIFT_MAX_RUN_MS + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::BOTTOM);
}

// --- 表 8 連続駆動の上限 ---

void test_max_run_boundary_is_allowed_at_10000ms() {
  LiftDecideInput in = movable();
  in.run_ms = LIFT_MAX_RUN_MS;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_run_over_max_run_stops_with_max_run() {
  LiftDecideInput in = movable();
  in.run_ms = LIFT_MAX_RUN_MS + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::MAX_RUN);
}

void test_run_over_max_run_stops_down_too() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::DOWN;
  in.run_ms = LIFT_MAX_RUN_MS + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::MAX_RUN);
}

// --- 表 9 それ以外（デューティの丸め） ---

void test_duty_is_clamped_into_0_to_100() {
  LiftDecideInput in = movable();
  in.duty = -5;
  assert_decision(in, LiftDir::UP, 0, StopReason::NONE);

  in.duty = 0;
  assert_decision(in, LiftDir::UP, 0, StopReason::NONE);

  in.duty = 100;
  assert_decision(in, LiftDir::UP, 100, StopReason::NONE);

  in.duty = 150;
  assert_decision(in, LiftDir::UP, LIFT_DUTY_ABS_MAX_PCT, StopReason::NONE);
}

void test_stop_command_never_keeps_a_duty() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::STOP;
  in.duty = 100;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_STOP);
}

// --- millis() が一周したあと（安全面の穴）---

void test_cmd_timestamp_newer_than_now_does_not_stop() {
  LiftDecideInput in = movable();
  in.now_ms = 0x00001000u;
  in.ceiling.received_at_ms = in.now_ms;  // 天井は新しいまま
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_ceiling_age_across_millis_wrap_is_stale_at_1280ms() {
  LiftDecideInput in = movable();
  in.ceiling.age_ms = 0;
  in.ceiling.received_at_ms = 0xFFFFFF00u;
  in.now_ms = 0x00000400u;  // 経過 1280 ms
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING_STALE);
}

void test_height_freshness_across_millis_wrap() {
  TEST_ASSERT_TRUE(height_is_fresh(0x00000100u, 0xFFFFFF00u));  // 経過 512 ms は新鮮
  TEST_ASSERT_FALSE(height_is_fresh(0x00000400u, 0xFFFFFF00u));  // 経過 1280 ms は古い
  TEST_ASSERT_EQUAL_INT32(-5, elapsed_ms(0x00001000u, 0x00001005u));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_no_owner_stops_with_empty_reason);
  RUN_TEST(test_no_owner_after_close_stops_with_owner_gone);
  RUN_TEST(test_no_owner_after_release_stops_with_cmd_stop);

  RUN_TEST(test_cmd_stop_stops_with_cmd_stop);
  RUN_TEST(test_stop_command_never_keeps_a_duty);

  RUN_TEST(test_up_with_far_ceiling_moves);
  RUN_TEST(test_up_with_near_ceiling_stops_with_ceiling_near);
  RUN_TEST(test_up_with_too_near_stops_with_ceiling_near);
  RUN_TEST(test_up_with_no_echo_moves);
  RUN_TEST(test_up_with_read_error_stops_with_ceiling_stale);
  RUN_TEST(test_up_with_missing_ceiling_stops_with_ceiling_stale);
  RUN_TEST(test_up_with_stale_ceiling_stops_with_ceiling_stale);
  RUN_TEST(test_down_without_ceiling_still_moves);
  RUN_TEST(test_up_without_sensor_ignores_ceiling);
  RUN_TEST(test_ceiling_wins_over_height_unknown);

  RUN_TEST(test_up_with_invalid_height_still_moves_while_waived);
  RUN_TEST(test_up_with_stale_height_still_moves_while_waived);

  RUN_TEST(test_up_at_top_threshold_still_moves_while_waived);

  RUN_TEST(test_down_with_bottom_pressed_stops_with_bottom);
  RUN_TEST(test_up_with_bottom_pressed_still_moves);
  RUN_TEST(test_bottom_wins_over_max_run);

  RUN_TEST(test_max_run_boundary_is_allowed_at_10000ms);
  RUN_TEST(test_run_over_max_run_stops_with_max_run);
  RUN_TEST(test_run_over_max_run_stops_down_too);

  RUN_TEST(test_duty_is_clamped_into_0_to_100);

  RUN_TEST(test_cmd_timestamp_newer_than_now_does_not_stop);
  RUN_TEST(test_ceiling_age_across_millis_wrap_is_stale_at_1280ms);
  RUN_TEST(test_height_freshness_across_millis_wrap);

  return UNITY_END();
}
