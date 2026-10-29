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
  in.speed_mm_s = 40;
  in.stalled = false;
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
  in.run_travel_um = 0;
  return in;
}

void assert_decision(const LiftDecideInput& in, LiftDir dir, int speed_mm_s,
                     StopReason reason) {
  const LiftDecideResult r = lift_decide(in);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(dir), static_cast<int>(r.dir));
  TEST_ASSERT_EQUAL_INT(speed_mm_s, r.speed_mm_s);
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
  in.speed_mm_s = 60;
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

// --- 表 4 高さが読めない・古い ---

void test_up_with_invalid_height_stops_with_height_unknown() {
  LiftDecideInput in = movable();
  in.height_ok = false;
  assert_decision(in, LiftDir::STOP, 0, StopReason::HEIGHT_UNKNOWN);
}

void test_up_with_stale_height_stops_with_height_unknown() {
  LiftDecideInput in = movable();
  in.height_at_ms = 1000;
  in.now_ms = 1000 + HEIGHT_STALE_MS + 1;
  in.ceiling.received_at_ms = in.now_ms;  // 天井は新しいまま（表 3 で止めない）
  assert_decision(in, LiftDir::STOP, 0, StopReason::HEIGHT_UNKNOWN);
}

void test_up_with_height_exactly_at_stale_limit_moves() {
  LiftDecideInput in = movable();
  in.height_at_ms = 1000;
  in.now_ms = 1000 + HEIGHT_STALE_MS;
  in.ceiling.received_at_ms = in.now_ms;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_down_with_invalid_height_still_moves() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::DOWN;
  in.height_ok = false;
  assert_decision(in, LiftDir::DOWN, 40, StopReason::NONE);
}

// --- 表 6 上端 ---

void test_up_at_top_threshold_stops_with_top() {
  LiftDecideInput in = movable();
  in.top_mm = 1500;
  in.height_mm = 1500;  // ちょうど届いた
  assert_decision(in, LiftDir::STOP, 0, StopReason::TOP);
}

void test_up_just_below_top_threshold_moves() {
  LiftDecideInput in = movable();
  in.top_mm = 1500;
  in.height_mm = 1499;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_down_above_top_threshold_still_moves() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::DOWN;
  in.top_mm = 1500;
  in.height_mm = 1600;
  assert_decision(in, LiftDir::DOWN, 40, StopReason::NONE);
}

void test_up_with_top_unset_moves_at_any_height() {
  LiftDecideInput in = movable();
  in.top_mm = -1;  // 未設定（#2a）
  in.height_mm = 5000;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_default_top_threshold_is_1800() {
  TEST_ASSERT_EQUAL_INT(1800, LIFT_TOP_MM);
  TEST_ASSERT_TRUE(LIFT_TOP_DETECT_ENABLED);
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
  in.run_travel_um = lift_max_run_um(LIFT_TOP_MM) + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::BOTTOM);
}

// --- 表 8 連続駆動の上限 ---

// 全行程 1800 mm × 10 × 150 % = 2 700 000 µm（30 mm/s なら 90 秒）
void test_max_run_limit_is_stroke_times_pct() {
  TEST_ASSERT_EQUAL_UINT32(2700000u, lift_max_run_um(1800));
  TEST_ASSERT_EQUAL_UINT32(600000u, lift_max_run_um(400));
}

void test_max_run_boundary_is_allowed_at_exact_limit() {
  LiftDecideInput in = movable();
  in.run_travel_um = lift_max_run_um(LIFT_TOP_MM);
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

// top_mm が 0 以上ならそれを全行程にする（1800 を決め打ちにしない）
void test_max_run_uses_top_mm_as_stroke() {
  LiftDecideInput in = movable();
  in.top_mm = 400;
  in.dir = LiftDir::DOWN;
  in.run_travel_um = 600000;
  assert_decision(in, LiftDir::DOWN, 40, StopReason::NONE);
  in.run_travel_um = 600001;
  assert_decision(in, LiftDir::STOP, 0, StopReason::MAX_RUN);
}

// top_mm が -1（未設定）のときは LIFT_STROKE_FALLBACK_MM で判定する
void test_max_run_top_unset_uses_stroke_fallback() {
  LiftDecideInput in = movable();
  in.top_mm = -1;
  const uint32_t limit = static_cast<uint32_t>(LIFT_STROKE_FALLBACK_MM) * 10u *
                         static_cast<uint32_t>(LIFT_MAX_RUN_PCT);
  TEST_ASSERT_EQUAL_UINT32(limit, lift_max_run_um(-1));
  TEST_ASSERT_EQUAL_UINT32(2850000u, limit);
  in.run_travel_um = limit;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
  in.run_travel_um = limit + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::MAX_RUN);
}

void test_run_over_max_run_stops_with_max_run() {
  LiftDecideInput in = movable();
  in.run_travel_um = lift_max_run_um(LIFT_TOP_MM) + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::MAX_RUN);
}

void test_run_over_max_run_stops_down_too() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::DOWN;
  in.run_travel_um = lift_max_run_um(LIFT_TOP_MM) + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::MAX_RUN);
}

// --- 表 8a エンコーダの停滞（#8 の次。spec #4d） ---

void test_stalled_stops_with_encoder_stall() {
  LiftDecideInput in = movable();
  in.stalled = true;
  assert_decision(in, LiftDir::STOP, 0, StopReason::ENCODER_STALL);
}

void test_stalled_stops_down_too() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::DOWN;
  in.stalled = true;
  assert_decision(in, LiftDir::STOP, 0, StopReason::ENCODER_STALL);
}

void test_stall_wins_over_moving() {
  LiftDecideInput in = movable();
  in.stalled = true;
  in.run_travel_um = 0;
  assert_decision(in, LiftDir::STOP, 0, StopReason::ENCODER_STALL);
}

void test_max_run_wins_over_stall() {
  // 表の順（#8 が先）に当たることの確認
  LiftDecideInput in = movable();
  in.stalled = true;
  in.run_travel_um = lift_max_run_um(LIFT_TOP_MM) + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::MAX_RUN);
}

void test_stop_command_wins_over_stall() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::STOP;
  in.stalled = true;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_STOP);
}

// --- 表 9 それ以外（速さの丸め。デューティは決めない） ---

void test_speed_is_clamped_into_0_to_max() {
  LiftDecideInput in = movable();
  in.speed_mm_s = -5;
  assert_decision(in, LiftDir::UP, 0, StopReason::NONE);

  in.speed_mm_s = 0;
  assert_decision(in, LiftDir::UP, 0, StopReason::NONE);

  in.speed_mm_s = LIFT_SPEED_ABS_MAX_MM_S;
  assert_decision(in, LiftDir::UP, LIFT_SPEED_ABS_MAX_MM_S, StopReason::NONE);

  in.speed_mm_s = LIFT_SPEED_ABS_MAX_MM_S + 50;
  assert_decision(in, LiftDir::UP, LIFT_SPEED_ABS_MAX_MM_S, StopReason::NONE);
}

void test_stop_command_never_keeps_a_speed() {
  LiftDecideInput in = movable();
  in.dir = LiftDir::STOP;
  in.speed_mm_s = 100;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_STOP);
}

// --- millis() が一周したあと（安全面の穴）---

void test_cmd_timestamp_newer_than_now_does_not_stop() {
  LiftDecideInput in = movable();
  in.now_ms = 0x00001000u;
  in.ceiling.received_at_ms = in.now_ms;  // 天井は新しいまま
  in.height_at_ms = in.now_ms;            // 高さも新しいまま
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
  RUN_TEST(test_stop_command_never_keeps_a_speed);

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

  RUN_TEST(test_up_with_invalid_height_stops_with_height_unknown);
  RUN_TEST(test_up_with_stale_height_stops_with_height_unknown);
  RUN_TEST(test_up_with_height_exactly_at_stale_limit_moves);
  RUN_TEST(test_down_with_invalid_height_still_moves);

  RUN_TEST(test_up_at_top_threshold_stops_with_top);
  RUN_TEST(test_up_just_below_top_threshold_moves);
  RUN_TEST(test_down_above_top_threshold_still_moves);
  RUN_TEST(test_up_with_top_unset_moves_at_any_height);
  RUN_TEST(test_default_top_threshold_is_1800);

  RUN_TEST(test_down_with_bottom_pressed_stops_with_bottom);
  RUN_TEST(test_up_with_bottom_pressed_still_moves);
  RUN_TEST(test_bottom_wins_over_max_run);

  RUN_TEST(test_max_run_limit_is_stroke_times_pct);
  RUN_TEST(test_max_run_boundary_is_allowed_at_exact_limit);
  RUN_TEST(test_max_run_uses_top_mm_as_stroke);
  RUN_TEST(test_max_run_top_unset_uses_stroke_fallback);
  RUN_TEST(test_run_over_max_run_stops_with_max_run);
  RUN_TEST(test_run_over_max_run_stops_down_too);

  RUN_TEST(test_stalled_stops_with_encoder_stall);
  RUN_TEST(test_stalled_stops_down_too);
  RUN_TEST(test_stall_wins_over_moving);
  RUN_TEST(test_max_run_wins_over_stall);
  RUN_TEST(test_stop_command_wins_over_stall);

  RUN_TEST(test_speed_is_clamped_into_0_to_max);

  RUN_TEST(test_cmd_timestamp_newer_than_now_does_not_stop);
  RUN_TEST(test_ceiling_age_across_millis_wrap_is_stale_at_1280ms);
  RUN_TEST(test_height_freshness_across_millis_wrap);

  return UNITY_END();
}
