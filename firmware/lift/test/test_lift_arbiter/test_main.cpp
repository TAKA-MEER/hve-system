// Unity の試験（env:native）。LiftArbiter の持ち主の規則。
// 規則は docs/plan/detailed/DetailedDesign.md §3.3。
#include <unity.h>

#include "lift_arbiter.h"

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

void assert_owner(LiftArbiter* arbiter, bool expected_has, int expected_id = 0,
                  int expected_press = 0) {
  ConnId conn;
  int press = -1;
  TEST_ASSERT_EQUAL_INT(expected_has, arbiter->owner(&conn, &press));
  if (expected_has) {
    TEST_ASSERT_EQUAL_INT(expected_id, conn.id);
    TEST_ASSERT_EQUAL_INT(expected_press, press);
  }
}

void assert_owner_kind(LiftArbiter* arbiter, ConnKind expected_kind) {
  ConnId conn;
  int press = 0;
  TEST_ASSERT_TRUE(arbiter->owner(&conn, &press));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(expected_kind), static_cast<int>(conn.kind));
}

}  // namespace

// --- 最初の hold が持ち主になる ---

void test_first_hold_becomes_owner() {
  LiftArbiter arbiter;
  assert_owner(&arbiter, false);
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  assert_owner(&arbiter, true, 1, 1);
}

void test_new_press_takes_over() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  TEST_ASSERT_TRUE(arbiter.on_hold(module(2), 7, 1100));
  assert_owner(&arbiter, true, 2, 7);
  assert_owner_kind(&arbiter, ConnKind::MODULE);
}

void test_same_press_continues_owner_and_refreshes_timeout() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  // 押し続け（同じ press）は受け付ける
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1500));
  // 1500 の hold のおかげで 1500 + 600 までは持ち主のまま
  arbiter.tick(1500 + LIFT_CMD_TIMEOUT_MS);
  assert_owner(&arbiter, true, 1, 1);
  arbiter.tick(1500 + LIFT_CMD_TIMEOUT_MS + 1);
  assert_owner(&arbiter, false);
}

// --- 古い press は無視する ---

void test_old_press_from_non_owner_is_ignored() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 5, 1000));
  TEST_ASSERT_TRUE(arbiter.on_hold(module(2), 3, 1100));
  // 取って代わられた側（ui 1）が古い press 5 で押し続けても持ち主は替わらない
  TEST_ASSERT_FALSE(arbiter.on_hold(ui(1), 5, 1200));
  assert_owner(&arbiter, true, 2, 3);
}

void test_same_press_after_stop_does_not_restart() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  TEST_ASSERT_TRUE(arbiter.on_release(ui(1), 1));
  assert_owner(&arbiter, false);
  // 同じ接続の同じ press では持ち主になれない
  TEST_ASSERT_FALSE(arbiter.on_hold(ui(1), 1, 2000));
  assert_owner(&arbiter, false);
  // より新しい press なら持ち主になれる
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 2, 2100));
  assert_owner(&arbiter, true, 1, 2);
}

void test_same_press_after_timeout_does_not_restart() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  arbiter.tick(1000 + LIFT_CMD_TIMEOUT_MS + 1);
  assert_owner(&arbiter, false);
  TEST_ASSERT_FALSE(arbiter.on_hold(ui(1), 1, 2000));
  assert_owner(&arbiter, false);
}

void test_same_press_after_close_does_not_restart() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  arbiter.on_close(1);
  assert_owner(&arbiter, false);
  TEST_ASSERT_FALSE(arbiter.on_hold(ui(1), 1, 2000));
  assert_owner(&arbiter, false);
}

// --- release ---

void test_release_from_owner_clears() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  TEST_ASSERT_TRUE(arbiter.on_release(ui(1), 1));
  assert_owner(&arbiter, false);
}

void test_release_from_non_owner_is_ignored() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  TEST_ASSERT_FALSE(arbiter.on_release(module(2), 1));
  assert_owner(&arbiter, true, 1, 1);
}

void test_release_with_old_press_is_ignored() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 2, 1100));
  // 遅れて届いた古い press の release では止めない
  TEST_ASSERT_FALSE(arbiter.on_release(ui(1), 1));
  assert_owner(&arbiter, true, 1, 2);
}

// --- close と timeout ---

void test_close_of_owner_clears_immediately() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  arbiter.on_close(1);
  assert_owner(&arbiter, false);
}

void test_close_of_non_owner_keeps_owner() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  arbiter.on_close(2);
  assert_owner(&arbiter, true, 1, 1);
}

void test_timeout_boundary_keeps_owner_at_600ms() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  arbiter.tick(1000 + LIFT_CMD_TIMEOUT_MS);
  assert_owner(&arbiter, true, 1, 1);
}

void test_timeout_clears_owner_over_600ms() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 1000));
  arbiter.tick(1000 + LIFT_CMD_TIMEOUT_MS + 1);
  assert_owner(&arbiter, false);
}

void test_timeout_across_millis_wrap() {
  LiftArbiter arbiter;
  TEST_ASSERT_TRUE(arbiter.on_hold(ui(1), 1, 0xFFFFFF00u));
  arbiter.tick(0x00000100u);  // 経過 512 ms
  assert_owner(&arbiter, true, 1, 1);
  arbiter.tick(0x00000400u);  // 経過 1280 ms
  assert_owner(&arbiter, false);
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_first_hold_becomes_owner);
  RUN_TEST(test_new_press_takes_over);
  RUN_TEST(test_same_press_continues_owner_and_refreshes_timeout);

  RUN_TEST(test_old_press_from_non_owner_is_ignored);
  RUN_TEST(test_same_press_after_stop_does_not_restart);
  RUN_TEST(test_same_press_after_timeout_does_not_restart);
  RUN_TEST(test_same_press_after_close_does_not_restart);

  RUN_TEST(test_release_from_owner_clears);
  RUN_TEST(test_release_from_non_owner_is_ignored);
  RUN_TEST(test_release_with_old_press_is_ignored);

  RUN_TEST(test_close_of_owner_clears_immediately);
  RUN_TEST(test_close_of_non_owner_keeps_owner);
  RUN_TEST(test_timeout_boundary_keeps_owner_at_600ms);
  RUN_TEST(test_timeout_clears_owner_over_600ms);
  RUN_TEST(test_timeout_across_millis_wrap);

  return UNITY_END();
}
