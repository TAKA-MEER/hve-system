// Unity の試験（env:native）。IoController に偽 HAL を差し、on_command → tick の
// あと偽ハードウェアへ実際に出た値を確かめる（DetailedDesign.md DD-2）。
// ウォッチドッグ・コイル・サーボ・丸めはここを通して縛る。
#include <unity.h>

#include "fake_io_hal.h"
#include "io_controller.h"

namespace {

IoCmd cmd(int pitch_ddeg, int32_t yaw_hsps) {
  IoCmd c;
  c.seq = 7;
  c.pitch_ddeg = pitch_ddeg;
  c.yaw_hsps = yaw_hsps;
  return c;
}

}  // namespace

// --- 起動直後とウォッチドッグ ---

void test_outputs_are_safe_before_any_command() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.tick(0);
  TEST_ASSERT_FALSE(hal.coils_on());  // コイルの電流は切る
  TEST_ASSERT_EQUAL_INT32(0, hal.yaw_hsps());
  TEST_ASSERT_EQUAL_INT(PITCH_INITIAL_DEG * 10, hal.pitch_ddeg());  // パルスは出し続ける
  TEST_ASSERT_EQUAL_INT(1, hal.servo_calls());
}

void test_command_reaches_hardware() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(200, 100), 0);
  ctrl.tick(0);
  TEST_ASSERT_EQUAL_INT32(100, hal.yaw_hsps());
  TEST_ASSERT_TRUE(hal.coils_on());
  TEST_ASSERT_EQUAL_INT(200, hal.pitch_ddeg());
}

void test_zero_yaw_cuts_coil_but_keeps_servo_pulse() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(200, 100), 0);
  ctrl.tick(0);
  TEST_ASSERT_TRUE(hal.coils_on());
  ctrl.on_command(cmd(200, 0), 100);
  ctrl.tick(100);
  TEST_ASSERT_FALSE(hal.coils_on());  // 止めている間はコイルの電流を切る
  TEST_ASSERT_EQUAL_INT(200, hal.pitch_ddeg());
}

void test_watchdog_stops_yaw_at_300ms() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(200, 100), 0);
  ctrl.tick(0);
  TEST_ASSERT_TRUE(hal.yaw_moving());

  ctrl.tick(IO_CMD_TIMEOUT_MS);
  TEST_ASSERT_TRUE(hal.yaw_moving());  // ちょうど 300 ms までは動かしてよい

  ctrl.tick(IO_CMD_TIMEOUT_MS + 1);
  TEST_ASSERT_FALSE(hal.yaw_moving());
  TEST_ASSERT_FALSE(hal.coils_on());  // 途絶でもコイルの電流を切る
  TEST_ASSERT_EQUAL_INT32(0, hal.yaw_hsps());
}

void test_watchdog_keeps_servo_pulse_at_last_angle() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(200, 100), 0);
  ctrl.tick(0);
  const int calls_before = hal.servo_calls();
  ctrl.tick(IO_CMD_TIMEOUT_MS + 1);
  ctrl.tick(IO_CMD_TIMEOUT_MS + 2);
  // パルスを止めない（止めると脱力してカメラが倒れる）。角度はそのまま保つ
  TEST_ASSERT_EQUAL_INT(200, hal.pitch_ddeg());
  TEST_ASSERT_EQUAL_INT(calls_before + 2, hal.servo_calls());
}

void test_fresh_command_after_watchdog_moves_again() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(200, 100), 0);
  ctrl.tick(0);
  ctrl.tick(IO_CMD_TIMEOUT_MS + 1);
  TEST_ASSERT_FALSE(hal.yaw_moving());

  ctrl.on_command(cmd(-100, -50), IO_CMD_TIMEOUT_MS + 1);
  ctrl.tick(IO_CMD_TIMEOUT_MS + 1);
  TEST_ASSERT_EQUAL_INT32(-50, hal.yaw_hsps());
  TEST_ASSERT_TRUE(hal.coils_on());
  TEST_ASSERT_EQUAL_INT(-100, hal.pitch_ddeg());
}

// --- 丸め（2 つ目の守り） ---

void test_pitch_is_clamped() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(1800, 0), 0);  // +180° → +60°
  ctrl.tick(0);
  TEST_ASSERT_EQUAL_INT(PITCH_MAX_DEG * 10, hal.pitch_ddeg());

  ctrl.on_command(cmd(-1800, 0), 10);  // -180° → -60°
  ctrl.tick(10);
  TEST_ASSERT_EQUAL_INT(PITCH_MIN_DEG * 10, hal.pitch_ddeg());
}

void test_pitch_beyond_16bit_int_is_clamped_on_correct_side() {
  // 丸める前に 16 bit の int に切り詰めると -100000 が正に反転して +60° になる
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(100000, 0), 0);
  ctrl.tick(0);
  TEST_ASSERT_EQUAL_INT(PITCH_MAX_DEG * 10, hal.pitch_ddeg());

  ctrl.on_command(cmd(-100000, 0), 10);
  ctrl.tick(10);
  TEST_ASSERT_EQUAL_INT(PITCH_MIN_DEG * 10, hal.pitch_ddeg());
}

void test_pitch_inside_range_passes_through() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(PITCH_MAX_DEG * 10, 0), 0);
  ctrl.tick(0);
  TEST_ASSERT_EQUAL_INT(PITCH_MAX_DEG * 10, hal.pitch_ddeg());
  ctrl.on_command(cmd(PITCH_MIN_DEG * 10, 0), 10);
  ctrl.tick(10);
  TEST_ASSERT_EQUAL_INT(PITCH_MIN_DEG * 10, hal.pitch_ddeg());
}

void test_yaw_is_clamped() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(0, 9999), 0);
  ctrl.tick(0);
  TEST_ASSERT_EQUAL_INT32(YAW_HSPS_ABS_MAX, hal.yaw_hsps());
  TEST_ASSERT_TRUE(hal.coils_on());

  ctrl.on_command(cmd(0, -9999), 10);
  ctrl.tick(10);
  TEST_ASSERT_EQUAL_INT32(-YAW_HSPS_ABS_MAX, hal.yaw_hsps());
  TEST_ASSERT_TRUE(hal.coils_on());
}

void test_yaw_inside_range_passes_through() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(0, YAW_HSPS_ABS_MAX), 0);
  ctrl.tick(0);
  TEST_ASSERT_EQUAL_INT32(YAW_HSPS_ABS_MAX, hal.yaw_hsps());
}

// --- millis() が一周したあと ---

// 一周をまたいでも、新しい指令（経過 200 ms）の間は動き続ける
void test_fresh_command_survives_millis_wrap() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(0, 100), 0xFFFFFF00u);
  ctrl.tick(0xFFFFFFC8u);  // 経過 200 ms
  TEST_ASSERT_TRUE(hal.yaw_moving());
}

// 一周をまたいでも、古い指令（経過 1000 ms）は止まり、コイルも切れる
void test_stale_command_stops_across_millis_wrap() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(0, 100), 0xFFFFFF00u);
  ctrl.tick(0x00000300u);  // 経過 1280 ms
  TEST_ASSERT_FALSE(hal.yaw_moving());
  TEST_ASSERT_FALSE(hal.coils_on());
  TEST_ASSERT_EQUAL_INT32(0, hal.yaw_hsps());
}

// 指令の時刻が now より少し新しいときは止めない
void test_newer_cmd_timestamp_does_not_stop() {
  FakeIoHal hal;
  IoController ctrl(&hal);
  ctrl.on_command(cmd(0, 100), 1005);  // now より 5 ms 新しい
  ctrl.tick(1000);
  TEST_ASSERT_TRUE(hal.yaw_moving());
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_outputs_are_safe_before_any_command);
  RUN_TEST(test_command_reaches_hardware);
  RUN_TEST(test_zero_yaw_cuts_coil_but_keeps_servo_pulse);
  RUN_TEST(test_watchdog_stops_yaw_at_300ms);
  RUN_TEST(test_watchdog_keeps_servo_pulse_at_last_angle);
  RUN_TEST(test_fresh_command_after_watchdog_moves_again);

  RUN_TEST(test_pitch_is_clamped);
  RUN_TEST(test_pitch_beyond_16bit_int_is_clamped_on_correct_side);
  RUN_TEST(test_pitch_inside_range_passes_through);
  RUN_TEST(test_yaw_is_clamped);
  RUN_TEST(test_yaw_inside_range_passes_through);

  RUN_TEST(test_fresh_command_survives_millis_wrap);
  RUN_TEST(test_stale_command_stops_across_millis_wrap);
  RUN_TEST(test_newer_cmd_timestamp_does_not_stop);

  return UNITY_END();
}
