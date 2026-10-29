// Unity の試験（env:native）。点検用の物理ボタン（DetailedDesign.md §4.1.3）。
// ButtonInput の読みと、button_step を通して LiftController・偽 HAL のモータに
// 実際に出た値を確かめる（DetailedDesign.md DD-2。main.cpp はピンを読んで button_step を呼ぶだけ）。
#include <unity.h>

#include "../test_lift_controller/fake_hal.h"
#include "button_input.h"

namespace {

constexpr int kHigh = 1;
constexpr int kLow = 0;
// 起動から十分たった時刻（ManualSwitch の起動直後の停止の間を越えている）
constexpr uint32_t kT0 = 1000;

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

HoldMsg remote_hold(int press, LiftDir dir, int speed = 40) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = dir;
  hold.speed = speed;
  hold.has_ceiling = false;  // MODULE の天井なし = MISSING
  return hold;
}

// 10 ms ごとに loop() を回す。高さは 500 mm・読めている・新しい（下端でも上端でもない）
struct Rig {
  FakeHal hal;
  LiftController ctrl;
  ButtonInput button;
  LiftSettings settings;
  uint32_t t = kT0;
  Rig() : ctrl(&hal, 1800) { hal.set_height(500, true, t); }

  // up / down はスイッチの電圧。ms ぶん 10 ms 刻みで進める
  void run(int up, int down, uint32_t ms) {
    const uint32_t end = t + ms;
    while (t < end) {
      one(up, down);
    }
  }
  void one(int up, int down) {
    t += 10;
    hal.set_height(500, true, t);
    button_step(button, ctrl, up, down, settings, t);
    ctrl.step(t);
  }
  // 離して、動き出せるまで待つ（反転の停止の間を越える）
  void release_and_settle() { run(kHigh, kHigh, MANUAL_REVERSE_PAUSE_MS + 50); }
};

}  // namespace

void setUp() {}
void tearDown() {}

// --- ButtonInput ---

void test_input_low_at_boot_stays_stopped() {
  ButtonInput input;
  for (uint32_t t = kT0; t < kT0 + 1000; t += 10) {
    TEST_ASSERT_EQUAL(ManualDir::STOP, input.update(kLow, kHigh, t));
  }
  TEST_ASSERT_EQUAL_INT(0, input.press());
}

void test_input_moves_after_debounce_once_released_was_seen() {
  ButtonInput input;
  TEST_ASSERT_EQUAL(ManualDir::STOP, input.update(kHigh, kHigh, kT0));
  const uint32_t t = kT0 + 10;
  TEST_ASSERT_EQUAL(ManualDir::STOP, input.update(kLow, kHigh, t));
  TEST_ASSERT_EQUAL(ManualDir::STOP, input.update(kLow, kHigh, t + MANUAL_DEBOUNCE_MS - 1));
  TEST_ASSERT_EQUAL(ManualDir::UP, input.update(kLow, kHigh, t + MANUAL_DEBOUNCE_MS));
  TEST_ASSERT_EQUAL_INT(1, input.press());
}

void test_input_release_stops_immediately() {
  ButtonInput input;
  input.update(kHigh, kHigh, kT0);
  input.update(kHigh, kLow, kT0 + 10);
  TEST_ASSERT_EQUAL(ManualDir::DOWN, input.update(kHigh, kLow, kT0 + 10 + MANUAL_DEBOUNCE_MS));
  const uint32_t t = kT0 + 100;
  TEST_ASSERT_EQUAL(ManualDir::STOP, input.update(kHigh, kHigh, t));
  TEST_ASSERT_TRUE(input.just_stopped());
  input.update(kHigh, kHigh, t + 10);
  TEST_ASSERT_FALSE(input.just_stopped());
}

void test_input_both_low_does_not_move() {
  ButtonInput input;
  input.update(kHigh, kHigh, kT0);
  for (uint32_t t = kT0 + 10; t < kT0 + 500; t += 10) {
    TEST_ASSERT_EQUAL(ManualDir::STOP, input.update(kLow, kLow, t));
  }
}

void test_input_press_increases_on_each_new_press() {
  ButtonInput input;
  uint32_t t = kT0;
  input.update(kHigh, kHigh, t);
  for (int i = 1; i <= 3; ++i) {
    t += MANUAL_REVERSE_PAUSE_MS + 50;
    input.update(kLow, kHigh, t);
    t += MANUAL_DEBOUNCE_MS;
    TEST_ASSERT_EQUAL(ManualDir::UP, input.update(kLow, kHigh, t));
    TEST_ASSERT_EQUAL_INT(i, input.press());
    t += 100;
    input.update(kLow, kHigh, t);  // 押し続けても増えない
    TEST_ASSERT_EQUAL_INT(i, input.press());
    input.update(kHigh, kHigh, t + 10);
    t += 10;
  }
}

// --- button_step を通した試験 ---

void test_step_held_at_boot_does_not_run_motor_until_released_and_pressed_again() {
  Rig rig;
  rig.run(kLow, kHigh, 1000);  // 倒したまま起動
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  rig.release_and_settle();
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  rig.run(kLow, kHigh, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL(LiftDir::UP, rig.hal.motor_dir());
}

void test_step_release_stops_motor_in_same_loop() {
  Rig rig;
  rig.release_and_settle();
  rig.run(kLow, kHigh, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  rig.one(kHigh, kHigh);  // 離した 1 回で止まる
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::CMD_STOP),
                        static_cast<int>(rig.ctrl.state().reason));
}

void test_step_speed_is_settings_init_value() {
  Rig rig;
  rig.settings.up.init_mm_s = 20;
  rig.settings.down.init_mm_s = 45;
  rig.release_and_settle();
  rig.run(kLow, kHigh, 200);
  TEST_ASSERT_EQUAL(LiftDir::UP, rig.hal.motor_dir());
  TEST_ASSERT_EQUAL_INT(20, rig.ctrl.state().speed);
  rig.release_and_settle();
  rig.run(kHigh, kLow, 200);
  TEST_ASSERT_EQUAL(LiftDir::DOWN, rig.hal.motor_dir());
  TEST_ASSERT_EQUAL_INT(45, rig.ctrl.state().speed);
}

void test_step_remote_new_press_does_not_take_over_while_button_runs() {
  Rig rig;
  rig.release_and_settle();
  rig.run(kHigh, kLow, 200);  // 物理ボタンで下降
  TEST_ASSERT_EQUAL(LiftDir::DOWN, rig.hal.motor_dir());
  for (int press = 1; press <= 3; ++press) {
    rig.ctrl.on_hold(ui(1), remote_hold(press, LiftDir::UP), rig.t);
    rig.ctrl.on_hold(module(2), remote_hold(press, LiftDir::UP), rig.t);
    rig.one(kHigh, kLow);
    TEST_ASSERT_EQUAL(LiftDir::DOWN, rig.hal.motor_dir());
    TEST_ASSERT_TRUE(rig.ctrl.state().has_owner);
    TEST_ASSERT_EQUAL_INT(2, rig.ctrl.state().owner_kind);
  }
}

void test_step_button_takes_over_and_remote_needs_new_press_after_release() {
  Rig rig;
  rig.release_and_settle();
  // UI が押し続けている（press 7 の hold を 10 ms ごとに送る）
  rig.ctrl.on_hold(ui(1), remote_hold(7, LiftDir::UP), rig.t);
  rig.one(kHigh, kHigh);
  TEST_ASSERT_EQUAL(LiftDir::UP, rig.hal.motor_dir());
  TEST_ASSERT_EQUAL_INT(0, rig.ctrl.state().owner_kind);
  // 物理ボタン（下降）を押す。UI の押し続けの hold は来続ける
  for (uint32_t i = 0; i < 20; ++i) {
    rig.ctrl.on_hold(ui(1), remote_hold(7, LiftDir::UP), rig.t);
    rig.one(kHigh, kLow);
  }
  TEST_ASSERT_EQUAL_INT(2, rig.ctrl.state().owner_kind);
  TEST_ASSERT_EQUAL(LiftDir::DOWN, rig.hal.motor_dir());
  // 物理ボタンを押している間に、UI が押し直した（新しい press 8）。無視されるが記録される
  for (uint32_t i = 0; i < 5; ++i) {
    rig.ctrl.on_hold(ui(1), remote_hold(8, LiftDir::UP), rig.t);
    rig.one(kHigh, kLow);
  }
  TEST_ASSERT_EQUAL(LiftDir::DOWN, rig.hal.motor_dir());
  // 物理ボタンを離す。UI の同じ press 8 の hold が来続けても動かない
  for (uint32_t i = 0; i < 20; ++i) {
    rig.ctrl.on_hold(ui(1), remote_hold(8, LiftDir::UP), rig.t);
    rig.one(kHigh, kHigh);
    TEST_ASSERT_FALSE(rig.hal.motor_moving());
  }
  TEST_ASSERT_FALSE(rig.ctrl.state().has_owner);
  // 新しい press なら動く
  rig.ctrl.on_hold(ui(1), remote_hold(9, LiftDir::UP), rig.t);
  rig.one(kHigh, kHigh);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL(LiftDir::UP, rig.hal.motor_dir());
}

void test_step_up_does_not_need_ceiling_value() {
  Rig rig;
  rig.release_and_settle();
  rig.run(kLow, kHigh, 300);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL(LiftDir::UP, rig.hal.motor_dir());
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::NONE),
                        static_cast<int>(rig.ctrl.state().reason));
  TEST_ASSERT_FALSE(rig.ctrl.state().ceiling_used);
}

void test_step_bottom_switch_blocks_down() {
  Rig rig;
  rig.hal.set_bottom(true);
  rig.release_and_settle();
  rig.run(kHigh, kLow, 300);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::BOTTOM),
                        static_cast<int>(rig.ctrl.state().reason));
}

void test_step_height_unknown_blocks_up() {
  Rig rig;
  rig.release_and_settle();
  // 原点の前（高さが読めない）
  for (int i = 0; i < 30; ++i) {
    rig.t += 10;
    rig.hal.set_height(0, false, rig.t);
    button_step(rig.button, rig.ctrl, kLow, kHigh, rig.settings, rig.t);
    rig.ctrl.step(rig.t);
  }
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::HEIGHT_UNKNOWN),
                        static_cast<int>(rig.ctrl.state().reason));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();
  RUN_TEST(test_input_low_at_boot_stays_stopped);
  RUN_TEST(test_input_moves_after_debounce_once_released_was_seen);
  RUN_TEST(test_input_release_stops_immediately);
  RUN_TEST(test_input_both_low_does_not_move);
  RUN_TEST(test_input_press_increases_on_each_new_press);
  RUN_TEST(test_step_held_at_boot_does_not_run_motor_until_released_and_pressed_again);
  RUN_TEST(test_step_release_stops_motor_in_same_loop);
  RUN_TEST(test_step_speed_is_settings_init_value);
  RUN_TEST(test_step_remote_new_press_does_not_take_over_while_button_runs);
  RUN_TEST(test_step_button_takes_over_and_remote_needs_new_press_after_release);
  RUN_TEST(test_step_up_does_not_need_ceiling_value);
  RUN_TEST(test_step_bottom_switch_blocks_down);
  RUN_TEST(test_step_height_unknown_blocks_up);
  return UNITY_END();
}
