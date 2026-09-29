// Unity の試験（env:native）。lift_decide() の表の各行・境界・組み合わせ。
// 規則は DetailedDesign.md §4.1。
#include <unity.h>

#include "lift_decide.h"

namespace {

// すべて「動いてよい」条件から始めて、1 つだけ変えた試験を書く
LiftDecideInput movable() {
  LiftDecideInput in;
  in.has_cmd = true;
  in.cmd.dir = LiftDir::UP;
  in.cmd.duty = 40;
  in.cmd.ceil_ok = true;
  in.cmd_received_at_ms = 1000;
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

// --- 表 1 指令の途絶え ---

void test_no_command_yet_stops_with_cmd_timeout() {
  LiftDecideInput in = movable();
  in.has_cmd = false;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_TIMEOUT);
}

void test_cmd_timeout_boundary_is_allowed_at_600ms() {
  LiftDecideInput in = movable();
  in.cmd_received_at_ms = 1000;
  in.now_ms = 1000 + LIFT_CMD_TIMEOUT_MS;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_cmd_timeout_stops_over_600ms() {
  LiftDecideInput in = movable();
  in.cmd_received_at_ms = 1000;
  in.now_ms = 1000 + LIFT_CMD_TIMEOUT_MS + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_TIMEOUT);
}

void test_cmd_timeout_wins_over_cmd_stop() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::STOP;
  in.now_ms = 1000 + LIFT_CMD_TIMEOUT_MS + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_TIMEOUT);
}

// --- 表 2 停止指令 ---

void test_cmd_stop_stops_with_cmd_stop() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::STOP;
  in.cmd.duty = 60;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_STOP);
}

// --- 表 3 天井の許可 ---

void test_up_without_ceil_ok_stops_with_ceiling() {
  LiftDecideInput in = movable();
  in.cmd.ceil_ok = false;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING);
}

void test_up_with_ceil_ok_moves() {
  LiftDecideInput in = movable();
  in.cmd.ceil_ok = true;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_down_without_ceil_ok_still_moves() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::DOWN;
  in.cmd.ceil_ok = false;
  assert_decision(in, LiftDir::DOWN, 40, StopReason::NONE);
}

void test_ceiling_wins_over_height_unknown() {
  LiftDecideInput in = movable();
  in.cmd.ceil_ok = false;
  in.height_ok = false;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CEILING);
}

// --- 表 4 高さが読めない・古い ---

void test_up_with_invalid_height_stops_with_height_unknown() {
  LiftDecideInput in = movable();
  in.height_ok = false;
  assert_decision(in, LiftDir::STOP, 0, StopReason::HEIGHT_UNKNOWN);
}

void test_height_stale_boundary_is_allowed_at_600ms() {
  LiftDecideInput in = movable();
  in.height_at_ms = 1000;
  in.now_ms = 1000 + HEIGHT_STALE_MS;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_up_with_stale_height_stops_with_height_unknown() {
  LiftDecideInput in = movable();
  in.height_at_ms = 1000;
  in.now_ms = 1000 + HEIGHT_STALE_MS + 1;
  in.cmd_received_at_ms = in.now_ms;  // 指令は新しいまま（表 1 で止めない）
  assert_decision(in, LiftDir::STOP, 0, StopReason::HEIGHT_UNKNOWN);
}

void test_down_with_stale_height_still_moves() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::DOWN;
  in.height_ok = false;
  in.height_at_ms = 1000;
  in.now_ms = 1000 + HEIGHT_STALE_MS + 1;
  in.cmd_received_at_ms = in.now_ms;
  assert_decision(in, LiftDir::DOWN, 40, StopReason::NONE);
}

// --- 表 6 上端 ---

void test_up_at_top_threshold_stops_with_top() {
  LiftDecideInput in = movable();
  in.top_mm = 1500;
  in.height_mm = 1500;
  assert_decision(in, LiftDir::STOP, 0, StopReason::TOP);
}

void test_up_over_top_threshold_stops_with_top() {
  LiftDecideInput in = movable();
  in.top_mm = 1500;
  in.height_mm = 1501;
  assert_decision(in, LiftDir::STOP, 0, StopReason::TOP);
}

void test_up_below_top_threshold_moves() {
  LiftDecideInput in = movable();
  in.top_mm = 1500;
  in.height_mm = 1499;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_up_with_top_unset_does_not_stop_at_top() {
  LiftDecideInput in = movable();
  in.top_mm = LIFT_TOP_MM;  // -1（未設定）
  in.height_mm = 9999;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_top_mm_zero_counts_as_configured() {
  LiftDecideInput in = movable();
  in.top_mm = 0;
  in.height_mm = 0;
  assert_decision(in, LiftDir::STOP, 0, StopReason::TOP);
}

void test_down_at_top_still_moves() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::DOWN;
  in.top_mm = 1500;
  in.height_mm = 1500;
  assert_decision(in, LiftDir::DOWN, 40, StopReason::NONE);
}

void test_top_wins_over_bottom() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::UP;
  in.top_mm = 1500;
  in.height_mm = 1600;
  in.bottom_pressed = true;
  assert_decision(in, LiftDir::STOP, 0, StopReason::TOP);
}

// --- 表 7 下端 ---

void test_down_with_bottom_pressed_stops_with_bottom() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::DOWN;
  in.bottom_pressed = true;
  assert_decision(in, LiftDir::STOP, 0, StopReason::BOTTOM);
}

void test_up_with_bottom_pressed_still_moves() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::UP;
  in.bottom_pressed = true;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

void test_bottom_wins_over_max_run() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::DOWN;
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
  in.cmd.dir = LiftDir::DOWN;
  in.run_ms = LIFT_MAX_RUN_MS + 1;
  assert_decision(in, LiftDir::STOP, 0, StopReason::MAX_RUN);
}

// --- 表 9 それ以外（デューティの丸め） ---

void test_duty_is_clamped_into_0_to_100() {
  LiftDecideInput in = movable();
  in.cmd.duty = -5;
  assert_decision(in, LiftDir::UP, 0, StopReason::NONE);

  in.cmd.duty = 0;
  assert_decision(in, LiftDir::UP, 0, StopReason::NONE);

  in.cmd.duty = 100;
  assert_decision(in, LiftDir::UP, 100, StopReason::NONE);

  in.cmd.duty = 150;
  assert_decision(in, LiftDir::UP, LIFT_DUTY_ABS_MAX_PCT, StopReason::NONE);
}

void test_down_duty_is_clamped_too() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::DOWN;
  in.cmd.duty = 150;
  assert_decision(in, LiftDir::DOWN, 100, StopReason::NONE);
}

void test_stop_command_never_keeps_a_duty() {
  LiftDecideInput in = movable();
  in.cmd.dir = LiftDir::STOP;
  in.cmd.duty = 100;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_STOP);
}

// --- millis() が一周したあと（安全面の穴）---

// millis() は約 49.7 日で一周して 0 に戻る。一周をまたいだ直後に時計が
// 0xFFFFFF00 付近から 0x00000000 付近へ戻る状況を、int64 に広げて引くと
// 経過時間が巨大な負の値になり、ウォッチドッグも鮮度判定も効かなくなる
// （指令が途絶えても止まらない）。uint32 のまま引いてから int32 に直す
// ことで、どちらの判定も正しくなることを確かめる。

// 一周をまたいだ、まだ新しい指令（経過 512 ms < 600 ms）は止めない
void test_cmd_timeout_across_millis_wrap_still_moves_at_512ms() {
  LiftDecideInput in = movable();
  in.cmd_received_at_ms = 0xFFFFFF00u;
  in.now_ms = 0x00000100u;  // 経過 512 ms
  in.height_at_ms = in.now_ms;
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

// 一周をまたいだ古い指令（経過 1280 ms > 600 ms）は CMD_TIMEOUT で止める
void test_cmd_timeout_across_millis_wrap_stops_at_1280ms() {
  LiftDecideInput in = movable();
  in.cmd_received_at_ms = 0xFFFFFF00u;
  in.now_ms = 0x00000400u;  // 経過 1280 ms
  in.height_at_ms = in.now_ms;
  assert_decision(in, LiftDir::STOP, 0, StopReason::CMD_TIMEOUT);
}

// 高さの鮮度も一周をまたいでも判定できる（経過 512 ms は新鮮なので上昇できる）
void test_height_freshness_across_millis_wrap_is_fresh_at_512ms() {
  LiftDecideInput in = movable();
  in.height_at_ms = 0xFFFFFF00u;
  in.now_ms = 0x00000100u;  // 経過 512 ms
  in.cmd_received_at_ms = in.now_ms;  // 指令は新しく保つ（表 4 だけを試験する）
  TEST_ASSERT_TRUE(height_is_fresh(in.now_ms, in.height_at_ms));
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

// 高さの鮮度も一周をまたいで古くなったら「読めない」で止める
void test_height_freshness_across_millis_wrap_is_stale_at_1280ms() {
  LiftDecideInput in = movable();
  in.height_at_ms = 0xFFFFFF00u;
  in.now_ms = 0x00000400u;  // 経過 1280 ms
  in.cmd_received_at_ms = in.now_ms;  // 指令は新しく保つ（表 4 だけを試験する）
  TEST_ASSERT_FALSE(height_is_fresh(in.now_ms, in.height_at_ms));
  assert_decision(in, LiftDir::STOP, 0, StopReason::HEIGHT_UNKNOWN);
}

// 割り込みや WS のタスクが now より少し新しい時刻を書いていても、
// 経過は小さな負の値になるのでタイムアウトにしない（誤停止しない）
void test_cmd_timestamp_newer_than_now_does_not_time_out() {
  LiftDecideInput in = movable();
  in.now_ms = 0x00001000u;
  in.cmd_received_at_ms = 0x00001005u;  // now より 5 ms 新しい
  in.height_at_ms = in.now_ms;
  TEST_ASSERT_EQUAL_INT32(-5, elapsed_ms(in.now_ms, in.cmd_received_at_ms));
  assert_decision(in, LiftDir::UP, 40, StopReason::NONE);
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_no_command_yet_stops_with_cmd_timeout);
  RUN_TEST(test_cmd_timeout_boundary_is_allowed_at_600ms);
  RUN_TEST(test_cmd_timeout_stops_over_600ms);
  RUN_TEST(test_cmd_timeout_wins_over_cmd_stop);

  RUN_TEST(test_cmd_stop_stops_with_cmd_stop);
  RUN_TEST(test_stop_command_never_keeps_a_duty);

  RUN_TEST(test_up_without_ceil_ok_stops_with_ceiling);
  RUN_TEST(test_up_with_ceil_ok_moves);
  RUN_TEST(test_down_without_ceil_ok_still_moves);
  RUN_TEST(test_ceiling_wins_over_height_unknown);

  RUN_TEST(test_up_with_invalid_height_stops_with_height_unknown);
  RUN_TEST(test_height_stale_boundary_is_allowed_at_600ms);
  RUN_TEST(test_up_with_stale_height_stops_with_height_unknown);
  RUN_TEST(test_down_with_stale_height_still_moves);

  RUN_TEST(test_up_at_top_threshold_stops_with_top);
  RUN_TEST(test_up_over_top_threshold_stops_with_top);
  RUN_TEST(test_up_below_top_threshold_moves);
  RUN_TEST(test_up_with_top_unset_does_not_stop_at_top);
  RUN_TEST(test_top_mm_zero_counts_as_configured);
  RUN_TEST(test_down_at_top_still_moves);
  RUN_TEST(test_top_wins_over_bottom);

  RUN_TEST(test_down_with_bottom_pressed_stops_with_bottom);
  RUN_TEST(test_up_with_bottom_pressed_still_moves);
  RUN_TEST(test_bottom_wins_over_max_run);

  RUN_TEST(test_max_run_boundary_is_allowed_at_10000ms);
  RUN_TEST(test_run_over_max_run_stops_with_max_run);
  RUN_TEST(test_run_over_max_run_stops_down_too);

  RUN_TEST(test_duty_is_clamped_into_0_to_100);
  RUN_TEST(test_down_duty_is_clamped_too);

  RUN_TEST(test_cmd_timeout_across_millis_wrap_still_moves_at_512ms);
  RUN_TEST(test_cmd_timeout_across_millis_wrap_stops_at_1280ms);
  RUN_TEST(test_height_freshness_across_millis_wrap_is_fresh_at_512ms);
  RUN_TEST(test_height_freshness_across_millis_wrap_is_stale_at_1280ms);
  RUN_TEST(test_cmd_timestamp_newer_than_now_does_not_time_out);

  return UNITY_END();
}
