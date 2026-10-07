// Unity の試験（env:native）。LiftController に偽 HAL を差し、hello / hold /
// release / close → step のあと偽モータへ実際に出た値を確かめる
// （DetailedDesign.md DD-2）。純関数だけでは不可。
#include <unity.h>

#include <cstring>

#include "fake_hal.h"
#include "lift_controller.h"

namespace {

ConnId ui(int id) {
  ConnId conn;
  conn.id = id;
  conn.kind = ConnKind::UI;
  return conn;
}

ConnId module(int id) {
  ConnId conn;
  conn.id = id;
  conn.kind = ConnKind::MODULE;
  return conn;
}

HelloMsg hello_with_sensor(bool has_sensor) {
  HelloMsg hello;
  hello.has_sensor = has_sensor;
  std::strncpy(hello.name, "hve-cam", sizeof(hello.name) - 1);
  return hello;
}

// 距離計を持つ接続からの上昇の hold（天井 MEASURED・十分遠い・送った瞬間は新しい）
HoldMsg up_hold(int press, int duty, uint32_t at_ms, int age_ms = 80) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = LiftDir::UP;
  hold.duty = duty;
  hold.has_ceiling = true;
  hold.ceiling.status = CeilingStatus::MEASURED;
  hold.ceiling.mm = 1450;
  hold.ceiling.age_ms = static_cast<uint32_t>(age_ms);
  hold.ceiling.received_at_ms = at_ms;
  return hold;
}

HoldMsg down_hold(int press, int duty = 40) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = LiftDir::DOWN;
  hold.duty = duty;
  hold.has_ceiling = false;
  return hold;
}

void assert_reason(StopReason expected, const LiftState& state) {
  TEST_ASSERT_EQUAL_INT(static_cast<int>(expected), static_cast<int>(state.reason));
}

// 持ち主の hold を出し直しながら回し続ける（天井は新しいまま）
void hold_from_to(uint32_t start_ms, uint32_t end_ms, LiftController* ctrl, FakeHal* hal,
                  const ConnId& conn, int press) {
  for (uint32_t t = start_ms; t <= end_ms; t += 100) {
    hal->set_height(500, true, t);
    ctrl->on_hold(conn, up_hold(press, 40, t), t);
    ctrl->step(t);
  }
}

void hold_until(uint32_t end_ms, LiftController* ctrl, FakeHal* hal, const ConnId& conn,
                int press) {
  hold_from_to(0, end_ms, ctrl, hal, conn, press);
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

void test_ui_up_without_ceiling_reaches_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  // /ws/ui の上昇では天井の値を求めない（spec #4c）。ceiling 無しで動く
  HoldMsg hold;
  hold.press = 1;
  hold.dir = LiftDir::UP;
  hold.duty = 40;
  hold.has_ceiling = false;
  ctrl.on_hold(ui(1), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
  TEST_ASSERT_EQUAL_INT(40, hal.motor_duty());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_TRUE(state.has_owner);
  TEST_ASSERT_EQUAL_INT(0, state.owner_kind);  // ui
  TEST_ASSERT_FALSE(state.ceiling_used);
}

void test_watchdog_stops_motor_at_600ms() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  TEST_ASSERT_TRUE(hal.motor_moving());

  ctrl.step(600);
  TEST_ASSERT_TRUE(hal.motor_moving());  // ちょうど 600 ms までは動かしてよい

  const LiftState state = ctrl.step(601);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::CMD_TIMEOUT, state);
  TEST_ASSERT_FALSE(state.has_owner);
}

// --- hello と距離計の有無 ---

void test_module_hold_without_hello_is_treated_as_having_sensor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  // hello を受けていない /ws/module の接続は「距離計を持つ」とみなす。
  // ceiling の無い hold では上昇させない
  HoldMsg hold;
  hold.press = 1;
  hold.dir = LiftDir::UP;
  hold.duty = 40;
  hold.has_ceiling = false;
  ctrl.on_hold(module(2), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_module_hold_without_ceiling_stops_with_stale() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  HoldMsg hold;
  hold.press = 1;
  hold.dir = LiftDir::UP;
  hold.duty = 40;
  hold.has_ceiling = false;  // ceiling を持つ接続の ceiling 無し hold
  ctrl.on_hold(module(2), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_FALSE(state.ceiling_ok);
}

void test_second_hello_with_no_sensor_is_ignored() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  // 2 度目の hello の ceiling_sensor: false は受け付けない
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(false)));
  HoldMsg hold;
  hold.press = 1;
  hold.dir = LiftDir::UP;
  hold.duty = 40;
  hold.has_ceiling = false;
  ctrl.on_hold(module(2), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_ui_hello_closes_connection() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  // /ws/ui で hello を受けたら、その接続を閉じる（偽を返す）
  TEST_ASSERT_FALSE(ctrl.on_hello(ui(1), hello_with_sensor(true)));
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
}

void test_read_error_stops_but_no_echo_moves() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));

  HoldMsg error = up_hold(1, 40, 0);
  error.ceiling.status = CeilingStatus::READ_ERROR;
  ctrl.on_hold(module(2), error, 0);
  assert_reason(StopReason::CEILING_STALE, ctrl.step(0));
  TEST_ASSERT_FALSE(hal.motor_moving());

  HoldMsg echo = up_hold(2, 40, 100);
  echo.ceiling.status = CeilingStatus::NO_ECHO;
  ctrl.on_hold(module(2), echo, 100);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_TRUE(hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::OUT_OF_RANGE),
                        static_cast<int>(state.ceiling_reason));
}

// --- 持ち主の切り替えと停止 ---

void test_old_press_from_non_owner_does_not_steal() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(5, 20, 1000), 1000);
  ctrl.on_hold(module(2), up_hold(3, 60, 1100), 1100);
  // 取って代わられた側の古い press では持ち主は替わらない（モータは module の 60 のまま）
  HoldMsg old = up_hold(5, 20, 1200);
  ctrl.on_hold(ui(1), old, 1200);
  const LiftState state = ctrl.step(1200);
  TEST_ASSERT_EQUAL_INT(60, hal.motor_duty());
  TEST_ASSERT_EQUAL_INT(1, state.owner_kind);  // module
  assert_reason(StopReason::NONE, state);
}

void test_owner_close_stops_immediately_with_owner_gone() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  TEST_ASSERT_TRUE(ctrl.step(0).dir == LiftDir::UP);
  ctrl.on_close(1);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::OWNER_GONE, state);
  TEST_ASSERT_FALSE(state.has_owner);
}

void test_owner_change_does_not_reset_max_run() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hold_until(9000, &ctrl, &hal, ui(1), 1);
  TEST_ASSERT_TRUE(hal.motor_moving());
  // 持ち主が替わっても同じ方向が続く限り数え続ける（上限を逃れられない）
  hold_from_to(9100, 11100, &ctrl, &hal, module(2), 9);
  const LiftState state = ctrl.step(11100);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

void test_same_press_after_release_does_not_restart() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  TEST_ASSERT_TRUE(ctrl.step(0).dir == LiftDir::UP);
  ctrl.on_release(ui(1), 1);
  assert_reason(StopReason::CMD_STOP, ctrl.step(100));
  // 同じ接続の同じ press では動き出さない
  ctrl.on_hold(ui(1), up_hold(1, 40, 200), 200);
  const LiftState state = ctrl.step(200);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CMD_STOP, state);
}

void test_same_press_after_timeout_does_not_restart() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  assert_reason(StopReason::CMD_TIMEOUT, ctrl.step(1000));
  ctrl.on_hold(ui(1), up_hold(1, 40, 1100), 1100);
  const LiftState state = ctrl.step(1100);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CMD_TIMEOUT, state);
}

void test_same_press_after_close_does_not_restart() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.on_close(1);
  assert_reason(StopReason::OWNER_GONE, ctrl.step(100));
  ctrl.on_hold(ui(1), up_hold(1, 40, 200), 200);
  const LiftState state = ctrl.step(200);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::OWNER_GONE, state);
}

void test_wrong_typed_ceiling_stops_at_once() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  ctrl.on_hold(module(2), up_hold(1, 40, 0), 0);
  TEST_ASSERT_TRUE(ctrl.step(0).dir == LiftDir::UP);
  // 型の違う ceiling の hold は捨てず MISSING として受ける。前の値で動き続けない
  HoldMsg broken = up_hold(1, 40, 100);
  broken.has_ceiling = false;
  ctrl.on_hold(module(2), broken, 100);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_ceiling_age_counts_transit_through_controller() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  // 送る瞬間に 590 ms 古い値。受け取ってから 20 ms 経てば CEILING_STALE_MS 超
  ctrl.on_hold(module(2), up_hold(1, 40, 1000, 590), 1000);
  TEST_ASSERT_TRUE(ctrl.step(1000).dir == LiftDir::UP);
  const LiftState state = ctrl.step(1020);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

// --- release と下端・デューティ ---

void test_release_from_owner_stops_with_cmd_stop() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.on_release(ui(1), 1);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CMD_STOP, state);
}

void test_release_from_non_owner_is_ignored() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.on_release(module(2), 1);
  TEST_ASSERT_TRUE(ctrl.step(100).dir == LiftDir::UP);
}

void test_bottom_pressed_stops_down_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_bottom(true);
  ctrl.on_hold(ui(1), down_hold(1), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::BOTTOM, state);
  TEST_ASSERT_TRUE(state.bottom);
}

void test_duty_is_clamped_before_reaching_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 150, 0), 0);
  ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(LIFT_DUTY_ABS_MAX_PCT, hal.motor_duty());
}

void test_motor_is_written_on_every_step() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.step(100);
  ctrl.step(200);
  TEST_ASSERT_EQUAL_INT(3, hal.motor_calls());
}

// --- state の中身 ---

void test_state_reports_top_detect_false_and_module() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(812, true, 900);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  ctrl.set_ui_clients(1);
  ctrl.on_hold(module(2), up_hold(17, 40, 1000), 1000);
  const LiftState state = ctrl.step(1035);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(state.dir));
  TEST_ASSERT_EQUAL_INT(40, state.duty);
  TEST_ASSERT_FALSE(state.top_detect);  // W-1 の間は常に false
  TEST_ASSERT_TRUE(state.ceiling_present);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MEASURED),
                        static_cast<int>(state.ceiling_status));
  TEST_ASSERT_EQUAL_INT(1450, state.ceiling_mm);
  TEST_ASSERT_EQUAL_INT(80 + 35, state.ceiling_age_ms);
  TEST_ASSERT_TRUE(state.ceiling_ok);
  TEST_ASSERT_TRUE(state.module_connected);
  TEST_ASSERT_TRUE(state.module_has_sensor);
  TEST_ASSERT_EQUAL_INT(1, state.ui_clients);
  TEST_ASSERT_EQUAL_INT(35, state.cmd_age_ms);
  TEST_ASSERT_EQUAL_INT(812, state.height_mm);
  TEST_ASSERT_TRUE(state.height_ok);
}

void test_state_keeps_last_module_ceiling_after_release() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(812, true, 900);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  ctrl.on_hold(module(2), up_hold(17, 40, 1000), 1000);
  ctrl.step(1035);
  ctrl.on_release(module(2), 17);
  const LiftState state = ctrl.step(1200);
  TEST_ASSERT_FALSE(state.has_owner);
  TEST_ASSERT_TRUE(state.ceiling_present);
  TEST_ASSERT_FALSE(state.ceiling_used);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MEASURED),
                        static_cast<int>(state.ceiling_status));
  TEST_ASSERT_EQUAL_INT(1450, state.ceiling_mm);
  TEST_ASSERT_EQUAL_INT(80 + 200, state.ceiling_age_ms);
  // 古さは増え続ける
  const LiftState later = ctrl.step(5000);
  TEST_ASSERT_TRUE(later.ceiling_present);
  TEST_ASSERT_EQUAL_INT(80 + 4000, later.ceiling_age_ms);
  // 止める判断は変えない
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(later.dir));
}

void test_state_keeps_last_module_ceiling_after_hold_timeout() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(812, true, 900);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  ctrl.on_hold(module(2), up_hold(17, 40, 1000), 1000);
  ctrl.step(1035);
  const LiftState state = ctrl.step(9000);
  TEST_ASSERT_FALSE(state.has_owner);
  TEST_ASSERT_TRUE(state.ceiling_present);
  TEST_ASSERT_EQUAL_INT(1450, state.ceiling_mm);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(state.dir));
}

void test_state_ceiling_null_when_no_module_hold_ever() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(812, true, 900);
  // 画面（UI）の hold と release だけ。上部モジュールの値は無い
  ctrl.on_hold(ui(1), down_hold(1), 1000);
  ctrl.step(1035);
  ctrl.on_release(ui(1), 1);
  const LiftState state = ctrl.step(1200);
  TEST_ASSERT_FALSE(state.ceiling_present);
}

void test_state_before_step_is_stopped() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  const LiftState& state = ctrl.state();
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(state.dir));
  TEST_ASSERT_EQUAL_INT(0, state.duty);
  TEST_ASSERT_FALSE(state.top_detect);
  TEST_ASSERT_FALSE(state.ceiling_present);
}

// --- millis() が一周したあと ---

void test_cmd_timeout_survives_millis_wrap() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0x00000400u);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0xFFFFFF00u), 0xFFFFFF00u);
  const LiftState state = ctrl.step(0x00000400u);  // 経過 1280 ms
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CMD_TIMEOUT, state);
}

void test_height_ok_in_state_survives_millis_wrap_when_fresh() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0xFFFFFF00u);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0x00000100u), 0x00000100u);
  const LiftState state = ctrl.step(0x00000100u);  // 経過 512 ms
  TEST_ASSERT_TRUE(state.height_ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_motor_stays_stopped_before_any_command);
  RUN_TEST(test_ui_up_without_ceiling_reaches_motor);
  RUN_TEST(test_watchdog_stops_motor_at_600ms);

  RUN_TEST(test_module_hold_without_hello_is_treated_as_having_sensor);
  RUN_TEST(test_module_hold_without_ceiling_stops_with_stale);
  RUN_TEST(test_second_hello_with_no_sensor_is_ignored);
  RUN_TEST(test_ui_hello_closes_connection);
  RUN_TEST(test_read_error_stops_but_no_echo_moves);

  RUN_TEST(test_old_press_from_non_owner_does_not_steal);
  RUN_TEST(test_owner_close_stops_immediately_with_owner_gone);
  RUN_TEST(test_owner_change_does_not_reset_max_run);
  RUN_TEST(test_same_press_after_release_does_not_restart);
  RUN_TEST(test_same_press_after_timeout_does_not_restart);
  RUN_TEST(test_same_press_after_close_does_not_restart);
  RUN_TEST(test_wrong_typed_ceiling_stops_at_once);
  RUN_TEST(test_ceiling_age_counts_transit_through_controller);

  RUN_TEST(test_release_from_owner_stops_with_cmd_stop);
  RUN_TEST(test_release_from_non_owner_is_ignored);
  RUN_TEST(test_bottom_pressed_stops_down_motor);
  RUN_TEST(test_duty_is_clamped_before_reaching_motor);
  RUN_TEST(test_motor_is_written_on_every_step);

  RUN_TEST(test_state_reports_top_detect_false_and_module);
  RUN_TEST(test_state_keeps_last_module_ceiling_after_release);
  RUN_TEST(test_state_keeps_last_module_ceiling_after_hold_timeout);
  RUN_TEST(test_state_ceiling_null_when_no_module_hold_ever);
  RUN_TEST(test_state_before_step_is_stopped);

  RUN_TEST(test_cmd_timeout_survives_millis_wrap);
  RUN_TEST(test_height_ok_in_state_survives_millis_wrap_when_fresh);

  return UNITY_END();
}
