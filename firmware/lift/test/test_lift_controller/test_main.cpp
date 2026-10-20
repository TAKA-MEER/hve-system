// Unity の試験（env:native）。LiftController に偽 HAL を差し、hello / hold /
// release / close → step のあと偽モータへ実際に出た値を確かめる
// （DetailedDesign.md DD-2）。純関数だけでは不可。
// 速さの制御は SpeedLoop の出力（デューティ）をモータで見る（§4.1.2）。
#include <unity.h>

#include <cstring>

#include "fake_hal.h"
#include "lift_controller.h"
#include "speed_loop.h"

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
HoldMsg up_hold(int press, int speed, uint32_t at_ms, int age_ms = 80) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = LiftDir::UP;
  hold.speed = speed;
  hold.has_ceiling = true;
  hold.ceiling.status = CeilingStatus::MEASURED;
  hold.ceiling.mm = 1450;
  hold.ceiling.age_ms = static_cast<uint32_t>(age_ms);
  hold.ceiling.received_at_ms = at_ms;
  return hold;
}

HoldMsg down_hold(int press, int speed = 40) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = LiftDir::DOWN;
  hold.speed = speed;
  hold.has_ceiling = false;
  return hold;
}

void assert_reason(StopReason expected, const LiftState& state) {
  TEST_ASSERT_EQUAL_INT(static_cast<int>(expected), static_cast<int>(state.reason));
}

// 持ち主の hold を出し直しながら回し続ける（天井は新しいまま）。
// エンコーダも 20 mm/s で進める（#8a で止まらない・出力が 0 に落ちない速さ）
void hold_from_to(uint32_t start_ms, uint32_t end_ms, LiftController* ctrl, FakeHal* hal,
                  const ConnId& conn, int press) {
  for (uint32_t t = start_ms; t <= end_ms; t += 100) {
    hal->set_height(500, true, t);
    hal->set_encoder_count(static_cast<int32_t>(t / 5));
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
  hold.speed = 40;
  hold.has_ceiling = false;
  ctrl.on_hold(ui(1), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
  TEST_ASSERT_TRUE(hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_INT(40, state.speed);
  TEST_ASSERT_TRUE(state.has_owner);
  TEST_ASSERT_EQUAL_INT(0, state.owner_kind);  // ui
  TEST_ASSERT_FALSE(state.ceiling_used);
}

void test_watchdog_stops_motor_at_600ms() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  TEST_ASSERT_TRUE(hal.motor_moving());

  // エンコーダも進める（進めないと #8a が先に止める）
  hal.set_encoder_count(120);
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
  hold.speed = 40;
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
  hold.speed = 40;
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
  hold.speed = 40;
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
  // 取って代わられた側の古い press では持ち主は替わらない（持ち主は module のまま）
  HoldMsg old = up_hold(5, 20, 1200);
  ctrl.on_hold(ui(1), old, 1200);
  const LiftState state = ctrl.step(1200);
  TEST_ASSERT_TRUE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(1, state.owner_kind);  // module
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_INT(60, state.speed);
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

// --- 速さのフィードバック（§4.1.2。モータに出した値で縛る） ---

// (a) 目標に届かないとモータへ出したデューティが上がっていく（上限を緩めた状態で見る）
void test_duty_rises_while_speed_stays_below_target() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  ctrl.set_motor_max_pct(LIFT_DUTY_ABS_MAX_PCT);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);  // 進まない（#8a の 500 ms より短く回す）
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  const int d0 = ctrl.step(0).duty;
  ctrl.on_hold(ui(1), up_hold(1, 40, 100), 100);
  const int d1 = ctrl.step(100).duty;
  ctrl.on_hold(ui(1), up_hold(1, 40, 200), 200);
  const int d2 = ctrl.step(200).duty;
  TEST_ASSERT_TRUE(hal.motor_moving());
  TEST_ASSERT_GREATER_THAN(d0, d1);
  TEST_ASSERT_GREATER_THAN(d1, d2);
}

// (a2) 既定の上限（50 %）では出力が 50 を超えない（Spec-ui §2.1）
void test_duty_never_exceeds_default_motor_cap() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);  // 既定 50 %
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);  // 進まない（測った速さ 0 のまま）
  for (uint32_t t = 0; t <= 400; t += 100) {
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    const LiftState state = ctrl.step(t);
    TEST_ASSERT_TRUE(hal.motor_moving());
    TEST_ASSERT_LESS_OR_EQUAL(50, hal.motor_duty());
    TEST_ASSERT_LESS_OR_EQUAL(50, state.duty);
  }
  // 上限が無ければ 50 を超える状況（t=200 で 58）。50 で頭打ちになる
  TEST_ASSERT_EQUAL_INT(50, hal.motor_duty());
}

// (a3) 動いている最中に上限を下げたら次の周期から効く
void test_lowering_cap_mid_motion_applies_on_next_cycle() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  for (uint32_t t = 0; t <= 200; t += 100) {
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    ctrl.step(t);
  }
  TEST_ASSERT_EQUAL_INT(50, hal.motor_duty());
  ctrl.set_motor_max_pct(30);
  ctrl.on_hold(ui(1), up_hold(1, 40, 300), 300);
  const LiftState state = ctrl.step(300);
  TEST_ASSERT_TRUE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(30, hal.motor_duty());
  TEST_ASSERT_EQUAL_INT(30, state.duty);
}

// (b) 数が進まなければ LIFT_STALL_MS 後に ENCODER_STALL でモータが 0
void test_stall_when_encoder_never_moves() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  for (uint32_t t = 0; t <= 500; t += 100) {
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    const LiftState state = ctrl.step(t);
    TEST_ASSERT_TRUE(hal.motor_moving());
    assert_reason(StopReason::NONE, state);
  }
  ctrl.on_hold(ui(1), up_hold(1, 40, 600), 600);
  const LiftState stalled = ctrl.step(600);
  TEST_ASSERT_FALSE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(0, hal.motor_duty());
  assert_reason(StopReason::ENCODER_STALL, stalled);
}

// (c) 逆向きに進んでも (b) と同じ
void test_stall_when_encoder_runs_backwards() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  for (uint32_t t = 0; t <= 500; t += 100) {
    // 上昇の命令なのに数は減っていく（逆向き）
    hal.set_encoder_count(-static_cast<int32_t>(t / 5));
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    TEST_ASSERT_TRUE(ctrl.step(t).dir == LiftDir::UP);
  }
  hal.set_encoder_count(-120);
  ctrl.on_hold(ui(1), up_hold(1, 40, 600), 600);
  const LiftState stalled = ctrl.step(600);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::ENCODER_STALL, stalled);
}

// (d) 止まって押し直すと積分が残っていない（最初の出力が前向きの値だけ）
void test_integral_is_reset_after_stop_and_repress() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  // 積分を育てる（出力が上がり、既定の上限 50 % で頭打ちになる）
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 100), 100);
  ctrl.step(100);
  ctrl.on_hold(ui(1), up_hold(1, 40, 200), 200);
  const int grown = ctrl.step(200).duty;
  TEST_ASSERT_EQUAL_INT(LIFT_MOTOR_MAX_PCT_DEFAULT, grown);
  // 離して押し直す（新しい press）
  ctrl.on_release(ui(1), 1);
  assert_reason(StopReason::CMD_STOP, ctrl.step(300));
  ctrl.on_hold(ui(1), up_hold(2, 40, 400), 400);
  const int fresh = ctrl.step(400).duty;
  // 測った速さ 0・経過 20 ms の前向き＋比例だけ。積分が残っていれば grown 並みに大きい
  SpeedLoop expect;
  TEST_ASSERT_EQUAL_INT(
      expect.update(40, 0.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS, LIFT_MOTOR_MAX_PCT_DEFAULT),
      fresh);
  TEST_ASSERT_LESS_THAN(grown, fresh);
}

// (e) stalled のあと反対方向へは動かせる
void test_opposite_direction_moves_after_stall() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  for (uint32_t t = 0; t <= 600; t += 100) {
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    ctrl.step(t);
  }
  assert_reason(StopReason::ENCODER_STALL, ctrl.state());
  // 反対方向（下降）へは動かせる
  ctrl.on_hold(ui(1), down_hold(2, 40), 700);
  const LiftState state = ctrl.step(700);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::NONE, state);
}

// 持ち主が替わっても積分は持ち越さない
void test_integral_is_reset_when_owner_changes() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  // 積分を育てる（100 ms ごとに 4 ずつ。既定の上限 50 % で頭打ちになる）
  for (uint32_t t = 0; t <= 300; t += 100) {
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    ctrl.step(t);
  }
  const int grown = hal.motor_duty();
  TEST_ASSERT_EQUAL_INT(LIFT_MOTOR_MAX_PCT_DEFAULT, grown);
  // 別の持ち主が同じ速さで引き継ぐ。最初の出力は積分 0（100 ms ぶんだけ進む）に戻る
  ctrl.on_hold(module(2), up_hold(9, 40, 400), 400);
  const LiftState state = ctrl.step(400);
  assert_reason(StopReason::NONE, state);
  SpeedLoop expect;
  TEST_ASSERT_EQUAL_INT(expect.update(40, 0.0f, LiftDir::UP, 100, LIFT_MOTOR_MAX_PCT_DEFAULT),
                        hal.motor_duty());
  TEST_ASSERT_LESS_THAN(grown, hal.motor_duty());
}

// --- release と下端・速さの丸め ---

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

void test_speed_is_clamped_before_speed_loop() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  // LIFT_SPEED_ABS_MAX_MM_S を超える速さは丸める。出力はモータへの出力の上限（既定 50 %）で頭打ち
  ctrl.on_hold(ui(1), up_hold(1, LIFT_SPEED_ABS_MAX_MM_S + 50, 0), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(LIFT_SPEED_ABS_MAX_MM_S, state.speed);
  TEST_ASSERT_EQUAL_INT(LIFT_MOTOR_MAX_PCT_DEFAULT, hal.motor_duty());
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
  TEST_ASSERT_TRUE(state.duty > 0);
  TEST_ASSERT_EQUAL_INT(40, state.speed);
  TEST_ASSERT_EQUAL_INT(0, state.speed_meas);
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
  hal.set_encoder_count(0x00000100);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0x00000100u), 0x00000100u);
  const LiftState state = ctrl.step(0x00000100u);  // 経過 512 ms
  TEST_ASSERT_TRUE(state.height_ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
}

void test_height_unknown_shows_in_state_while_waiver() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  // 原点を取る前（height_ok が偽）でも state.height_ok は偽（W-1 の間も表示用に縛る）
  hal.set_height(0, false, 0);
  ctrl.on_hold(ui(1), down_hold(1), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(state.height_ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(hal.motor_dir()));
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

  RUN_TEST(test_duty_rises_while_speed_stays_below_target);
  RUN_TEST(test_duty_never_exceeds_default_motor_cap);
  RUN_TEST(test_lowering_cap_mid_motion_applies_on_next_cycle);
  RUN_TEST(test_stall_when_encoder_never_moves);
  RUN_TEST(test_stall_when_encoder_runs_backwards);
  RUN_TEST(test_integral_is_reset_after_stop_and_repress);
  RUN_TEST(test_opposite_direction_moves_after_stall);
  RUN_TEST(test_integral_is_reset_when_owner_changes);

  RUN_TEST(test_release_from_owner_stops_with_cmd_stop);
  RUN_TEST(test_release_from_non_owner_is_ignored);
  RUN_TEST(test_bottom_pressed_stops_down_motor);
  RUN_TEST(test_speed_is_clamped_before_speed_loop);
  RUN_TEST(test_motor_is_written_on_every_step);

  RUN_TEST(test_state_reports_top_detect_false_and_module);
  RUN_TEST(test_state_keeps_last_module_ceiling_after_release);
  RUN_TEST(test_state_keeps_last_module_ceiling_after_hold_timeout);
  RUN_TEST(test_state_ceiling_null_when_no_module_hold_ever);
  RUN_TEST(test_state_before_step_is_stopped);

  RUN_TEST(test_cmd_timeout_survives_millis_wrap);
  RUN_TEST(test_height_ok_in_state_survives_millis_wrap_when_fresh);
  RUN_TEST(test_height_unknown_shows_in_state_while_waiver);

  return UNITY_END();
}
