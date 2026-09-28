// Unity の試験（env:native）。LiftController に偽 HAL を差し、on_command → step の
// あと偽モータへ実際に出た値を確かめる（DetailedDesign.md DD-2）。純関数だけでは不可。
#include <unity.h>

#include "fake_hal.h"
#include "lift_controller.h"

namespace {

LiftCmd up_cmd(int duty = 40, bool ceil_ok = true) {
  LiftCmd cmd;
  cmd.dir = LiftDir::UP;
  cmd.duty = duty;
  cmd.ceil_ok = ceil_ok;
  return cmd;
}

LiftCmd down_cmd(int duty = 40) {
  LiftCmd cmd;
  cmd.dir = LiftDir::DOWN;
  cmd.duty = duty;
  cmd.ceil_ok = true;
  return cmd;
}

LiftCmd stop_cmd() {
  LiftCmd cmd;
  cmd.dir = LiftDir::STOP;
  cmd.duty = 0;
  cmd.ceil_ok = true;
  return cmd;
}

void assert_reason(StopReason expected, const LiftState& state) {
  TEST_ASSERT_EQUAL_INT(static_cast<int>(expected), static_cast<int>(state.reason));
}

// 途中の状態を更新せずに回し続ける
void run_until(uint32_t end_ms, LiftController* ctrl, FakeHal* hal) {
  for (uint32_t t = 0; t <= end_ms; t += 100) {
    hal->set_height(500, true, t);
    ctrl->on_command(up_cmd(), t);
    ctrl->step(t);
  }
}

}  // namespace

// --- 起動直後とウォッチドッグ ---

void test_motor_stays_stopped_before_any_command() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(state.dir));
  assert_reason(StopReason::CMD_TIMEOUT, state);
  TEST_ASSERT_FALSE(hal.motor_moving());
}

void test_up_command_reaches_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(40), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
  TEST_ASSERT_EQUAL_INT(40, hal.motor_duty());
  TEST_ASSERT_TRUE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(state.dir));
  TEST_ASSERT_EQUAL_INT(40, state.duty);
  assert_reason(StopReason::NONE, state);
}

void test_motor_is_written_on_every_step() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(), 0);
  ctrl.step(0);
  ctrl.step(100);
  ctrl.step(200);
  TEST_ASSERT_EQUAL_INT(3, hal.motor_calls());
}

void test_watchdog_stops_motor_at_600ms() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(), 0);
  ctrl.step(0);
  TEST_ASSERT_TRUE(hal.motor_moving());

  hal.set_height(500, true, 600);
  ctrl.step(600);
  TEST_ASSERT_TRUE(hal.motor_moving());  // ちょうど 600 ms までは動かしてよい

  hal.set_height(500, true, 601);
  const LiftState state = ctrl.step(601);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(hal.motor_dir()));
  TEST_ASSERT_EQUAL_INT(0, hal.motor_duty());
  assert_reason(StopReason::CMD_TIMEOUT, state);
  TEST_ASSERT_FALSE(hal.motor_moving());
}

void test_fresh_command_after_watchdog_moves_again() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(), 0);
  ctrl.step(0);
  hal.set_height(500, true, 1000);
  ctrl.step(1000);
  TEST_ASSERT_FALSE(hal.motor_moving());

  hal.set_height(500, true, 1000);
  ctrl.on_command(up_cmd(), 1000);
  TEST_ASSERT_TRUE(ctrl.step(1000).dir == LiftDir::UP);
}

void test_stop_command_stops_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(), 0);
  ctrl.step(0);
  ctrl.on_command(stop_cmd(), 100);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::CMD_STOP, state);
  TEST_ASSERT_FALSE(hal.motor_moving());
}

// --- 判定の結果をモータへ出す（指令をそのまま流さない） ---

void test_ceil_ok_false_stops_motor_even_though_command_says_up() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(40, false), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(hal.motor_dir()));
  TEST_ASSERT_EQUAL_INT(0, hal.motor_duty());
  assert_reason(StopReason::CEILING, state);
  TEST_ASSERT_FALSE(hal.motor_moving());
}

void test_ceil_ok_true_moves_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(40, true), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::NONE, state);
}

void test_duty_is_clamped_before_reaching_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(150), 0);
  ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(LIFT_DUTY_ABS_MAX_PCT, hal.motor_duty());
}

// --- 高さ ---

void test_invalid_height_stops_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, false, 0);
  ctrl.on_command(up_cmd(), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::HEIGHT_UNKNOWN, state);
}

void test_stale_height_stops_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(), 601);
  const LiftState state = ctrl.step(601);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::HEIGHT_UNKNOWN, state);
  TEST_ASSERT_FALSE(state.height_ok);
}

void test_height_ok_in_state_tracks_staleness() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(up_cmd(), 0);
  TEST_ASSERT_TRUE(ctrl.step(0).height_ok);
  TEST_ASSERT_TRUE(ctrl.step(HEIGHT_STALE_MS).height_ok);
  TEST_ASSERT_FALSE(ctrl.step(HEIGHT_STALE_MS + 1).height_ok);
}

void test_down_moves_with_stale_height() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_command(down_cmd(), 1000);
  const LiftState state = ctrl.step(1000);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::NONE, state);
}

// --- 下端 ---

void test_bottom_pressed_stops_down_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_bottom(true);
  ctrl.on_command(down_cmd(), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::BOTTOM, state);
  TEST_ASSERT_TRUE(state.bottom);
}

void test_bottom_pressed_still_allows_up_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_bottom(true);
  ctrl.on_command(up_cmd(), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::NONE, state);
}

// --- 上端 ---

void test_top_threshold_stops_up_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, 1500);
  hal.set_height(1500, true, 0);
  ctrl.on_command(up_cmd(), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::TOP, state);
  TEST_ASSERT_EQUAL_INT(1500, state.top_mm);
}

void test_below_top_threshold_moves_up_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, 1500);
  hal.set_height(1499, true, 0);
  ctrl.on_command(up_cmd(), 0);
  TEST_ASSERT_TRUE(ctrl.step(0).dir == LiftDir::UP);
}

void test_top_unset_does_not_stop_up_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(9999, true, 0);
  ctrl.on_command(up_cmd(), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_INT(LIFT_TOP_MM, state.top_mm);
}

// --- 連続駆動の上限 ---

void test_max_run_stops_motor_and_stays_stopped() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  run_until(LIFT_MAX_RUN_MS, &ctrl, &hal);
  TEST_ASSERT_TRUE(hal.motor_moving());  // ちょうど 10000 ms までは回ってよい

  hal.set_height(500, true, LIFT_MAX_RUN_MS + 100);
  ctrl.on_command(up_cmd(), LIFT_MAX_RUN_MS + 100);
  LiftState state = ctrl.step(LIFT_MAX_RUN_MS + 100);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);

  // 同じ方向の指令が来続けても止めたまま
  for (uint32_t t = LIFT_MAX_RUN_MS + 200; t <= LIFT_MAX_RUN_MS + 5000; t += 100) {
    hal.set_height(500, true, t);
    ctrl.on_command(up_cmd(), t);
    state = ctrl.step(t);
    TEST_ASSERT_FALSE(hal.motor_moving());
    assert_reason(StopReason::MAX_RUN, state);
  }
}

void test_stop_command_releases_max_run() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  run_until(LIFT_MAX_RUN_MS + 100, &ctrl, &hal);
  TEST_ASSERT_FALSE(hal.motor_moving());

  const uint32_t t = LIFT_MAX_RUN_MS + 200;
  hal.set_height(500, true, t);
  ctrl.on_command(stop_cmd(), t);
  ctrl.step(t);
  ctrl.on_command(up_cmd(), t + 100);
  const LiftState state = ctrl.step(t + 100);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::NONE, state);
}

void test_reverse_direction_after_max_run_moves() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  run_until(LIFT_MAX_RUN_MS + 100, &ctrl, &hal);
  TEST_ASSERT_FALSE(hal.motor_moving());

  const uint32_t t = LIFT_MAX_RUN_MS + 200;
  hal.set_height(500, true, t);
  ctrl.on_command(down_cmd(), t);
  const LiftState state = ctrl.step(t);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::NONE, state);
}

void test_run_timer_counts_only_while_turning() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_bottom(true);
  for (uint32_t t = 0; t <= LIFT_MAX_RUN_MS + 1000; t += 100) {
    hal.set_height(500, true, t);
    ctrl.on_command(down_cmd(), t);
    TEST_ASSERT_FALSE(ctrl.step(t).dir == LiftDir::DOWN);  // 下端で止まっている
  }

  hal.set_bottom(false);
  const uint32_t t = LIFT_MAX_RUN_MS + 1100;
  hal.set_height(500, true, t);
  ctrl.on_command(down_cmd(), t);
  TEST_ASSERT_TRUE(ctrl.step(t).dir == LiftDir::DOWN);  // 止まっていた時間は数えていない
}

// --- state メッセージ ---

void test_state_reports_actual_motor_output() {
  FakeHal hal;
  LiftController ctrl(&hal, 1500);
  hal.set_height(1600, true, 900);
  hal.set_bottom(true);
  ctrl.on_command(up_cmd(70, true), 1000);
  const LiftState state = ctrl.step(1035);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(state.dir));
  TEST_ASSERT_EQUAL_INT(0, state.duty);
  assert_reason(StopReason::TOP, state);
  TEST_ASSERT_TRUE(state.bottom);
  TEST_ASSERT_EQUAL_INT(1600, state.height_mm);
  TEST_ASSERT_TRUE(state.height_ok);
  TEST_ASSERT_EQUAL_INT(1500, state.top_mm);
  TEST_ASSERT_EQUAL_INT(35, state.cmd_age_ms);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(ctrl.state().dir));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(state.reason), static_cast<int>(ctrl.state().reason));
}

void test_state_before_step_is_stopped() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  const LiftState& state = ctrl.state();
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(state.dir));
  TEST_ASSERT_EQUAL_INT(0, state.duty);
  TEST_ASSERT_EQUAL_INT(LIFT_TOP_MM, state.top_mm);
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_motor_stays_stopped_before_any_command);
  RUN_TEST(test_up_command_reaches_motor);
  RUN_TEST(test_motor_is_written_on_every_step);
  RUN_TEST(test_watchdog_stops_motor_at_600ms);
  RUN_TEST(test_fresh_command_after_watchdog_moves_again);
  RUN_TEST(test_stop_command_stops_motor);

  RUN_TEST(test_ceil_ok_false_stops_motor_even_though_command_says_up);
  RUN_TEST(test_ceil_ok_true_moves_motor);
  RUN_TEST(test_duty_is_clamped_before_reaching_motor);

  RUN_TEST(test_invalid_height_stops_motor);
  RUN_TEST(test_stale_height_stops_motor);
  RUN_TEST(test_height_ok_in_state_tracks_staleness);
  RUN_TEST(test_down_moves_with_stale_height);

  RUN_TEST(test_bottom_pressed_stops_down_motor);
  RUN_TEST(test_bottom_pressed_still_allows_up_motor);

  RUN_TEST(test_top_threshold_stops_up_motor);
  RUN_TEST(test_below_top_threshold_moves_up_motor);
  RUN_TEST(test_top_unset_does_not_stop_up_motor);

  RUN_TEST(test_max_run_stops_motor_and_stays_stopped);
  RUN_TEST(test_stop_command_releases_max_run);
  RUN_TEST(test_reverse_direction_after_max_run_moves);
  RUN_TEST(test_run_timer_counts_only_while_turning);

  RUN_TEST(test_state_reports_actual_motor_output);
  RUN_TEST(test_state_before_step_is_stopped);

  return UNITY_END();
}
