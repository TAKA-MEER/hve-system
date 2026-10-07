// Unity の試験（env:native）。cmd_codec の hello / hold / release / state。
// 規則は docs/plan/detailed/DetailedDesign-protocol.md §2。
#include <unity.h>

#include <cstring>
#include <string>

#include "cmd_codec.h"
#include "lift_core_version.h"

namespace {

// 捨てた hold をそのまま判定へ通しても上昇しないことを確かめる
void assert_hold_never_moves_up(const char* text) {
  HoldMsg hold;
  const bool ok = decode_hold(text, &hold, 1000);
  TEST_ASSERT_FALSE(ok);
  // 捨てた hold は持ち主にならないので、判定以前に動かない。
  // 万が一中身が残っていても、ceiling 無し・STOP 側に倒れていること
  TEST_ASSERT_FALSE(hold.has_ceiling);
}

LiftState moving_state() {
  LiftState state;
  state.seq = 88;
  state.dir = LiftDir::UP;
  state.duty = 40;
  state.reason = StopReason::NONE;
  state.bottom = false;
  state.height_mm = 812;
  state.height_ok = true;
  state.top_detect = false;
  state.ceiling_used = true;
  state.ceiling_present = true;
  state.ceiling_status = CeilingStatus::MEASURED;
  state.ceiling_mm = 1450;
  state.ceiling_age_ms = 95;
  state.ceiling_ok = true;
  state.ceiling_reason = StopReason::NONE;
  state.has_owner = true;
  state.owner_kind = 1;
  state.ui_clients = 1;
  state.module_connected = true;
  state.module_has_sensor = true;
  std::strncpy(state.module_ip, "192.168.5.23", sizeof(state.module_ip) - 1);
  std::strncpy(state.module_name, "hve-cam", sizeof(state.module_name) - 1);
  state.cmd_age_ms = 35;
  return state;
}

}  // namespace

// --- hello ---

void test_decode_valid_hello() {
  HelloMsg hello;
  TEST_ASSERT_TRUE(
      decode_hello("{\"t\":\"hello\",\"ceiling_sensor\":true,\"name\":\"hve-cam\",\"fw\":\"0.2.0\"}",
                   &hello));
  TEST_ASSERT_TRUE(hello.has_sensor);
  TEST_ASSERT_EQUAL_STRING("hve-cam", hello.name);
  TEST_ASSERT_EQUAL_STRING("0.2.0", hello.fw);
}

void test_decode_hello_without_sensor() {
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"ceiling_sensor\":false}", &hello));
  TEST_ASSERT_FALSE(hello.has_sensor);
}

void test_decode_hello_missing_sensor_is_true() {
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\"}", &hello));
  TEST_ASSERT_TRUE(hello.has_sensor);
}

void test_decode_hello_wrong_typed_sensor_is_true() {
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"ceiling_sensor\":1}", &hello));
  TEST_ASSERT_TRUE(hello.has_sensor);
}

void test_decode_unreadable_hello_is_true_but_rejected() {
  HelloMsg hello;
  // 読めない hello は「ceiling_sensor: true」として扱う
  TEST_ASSERT_FALSE(decode_hello("not json", &hello));
  TEST_ASSERT_TRUE(hello.has_sensor);
  TEST_ASSERT_FALSE(decode_hello("{\"t\":\"hold\",\"press\":1}", &hello));
  TEST_ASSERT_TRUE(hello.has_sensor);
  TEST_ASSERT_FALSE(decode_hello(nullptr, &hello));
  TEST_ASSERT_TRUE(hello.has_sensor);
}

// --- hold ---

void test_decode_valid_hold() {
  HoldMsg hold;
  TEST_ASSERT_TRUE(decode_hold("{\"t\":\"hold\",\"press\":17,\"dir\":\"up\",\"duty\":40,"
                               "\"ceiling\":{\"status\":\"MEASURED\",\"mm\":1450,\"age_ms\":80}}",
                               &hold, 1000));
  TEST_ASSERT_EQUAL_INT(17, hold.press);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hold.dir));
  TEST_ASSERT_EQUAL_INT(40, hold.duty);
  TEST_ASSERT_TRUE(hold.has_ceiling);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MEASURED),
                        static_cast<int>(hold.ceiling.status));
  TEST_ASSERT_EQUAL_INT(1450, hold.ceiling.mm);
  TEST_ASSERT_EQUAL_INT(80, hold.ceiling.age_ms);
  TEST_ASSERT_EQUAL_INT(1000, hold.ceiling.received_at_ms);
}

void test_decode_broken_hold_is_dropped() {
  assert_hold_never_moves_up("{\"t\":\"hold\",\"dir\":\"up\",");
  assert_hold_never_moves_up("not json at all");
  assert_hold_never_moves_up("");
  assert_hold_never_moves_up("{\"t\":\"cmd\",\"press\":1,\"dir\":\"up\",\"duty\":40}");
  assert_hold_never_moves_up("{\"t\":\"hold\",\"dir\":\"up\",\"duty\":40}");  // press が無い
  assert_hold_never_moves_up("{\"t\":\"hold\",\"press\":1,\"duty\":40}");  // dir が無い
  assert_hold_never_moves_up(
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"sideways\",\"duty\":40}");  // 知らない方向
  assert_hold_never_moves_up(
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\"}");  // duty が無い
  assert_hold_never_moves_up(
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":\"40\"}");  // duty の型が違う
  assert_hold_never_moves_up(nullptr);
}

void test_decode_hold_without_ceiling_is_missing() {
  HoldMsg hold;
  TEST_ASSERT_TRUE(decode_hold("{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40}", &hold,
                               1000));
  TEST_ASSERT_FALSE(hold.has_ceiling);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MISSING),
                        static_cast<int>(hold.ceiling.status));
  // dir・duty は読めた値のまま（天井が無いので上昇は止まる）
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hold.dir));
  TEST_ASSERT_EQUAL_INT(40, hold.duty);
}

void test_decode_hold_with_broken_ceiling_is_missing() {
  // 知らない status・型の違う中身は hold を捨てず MISSING として受ける
  const char* cases[] = {
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40,\"ceiling\":{\"status\":\"NEAR\"}}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40,\"ceiling\":{\"status\":1}}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40,\"ceiling\":{\"status\":\"MEASURED\"}}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40,"
      "\"ceiling\":{\"status\":\"MEASURED\",\"mm\":\"1450\",\"age_ms\":80}}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40,\"ceiling\":\"MEASURED\"}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40,\"ceiling\":null}",
  };
  for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
    HoldMsg hold;
    TEST_ASSERT_TRUE(decode_hold(cases[i], &hold, 1000));
    TEST_ASSERT_FALSE(hold.has_ceiling);
    TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MISSING),
                          static_cast<int>(hold.ceiling.status));
  }
}

void test_decode_hold_ceiling_statuses() {
  const char* statuses[] = {"MEASURED", "TOO_NEAR", "NO_ECHO", "READ_ERROR"};
  for (size_t i = 0; i < sizeof(statuses) / sizeof(statuses[0]); ++i) {
    std::string text = std::string("{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40,"
                                   "\"ceiling\":{\"status\":\"") +
                       statuses[i] + "\",\"mm\":1450,\"age_ms\":80}}";
    HoldMsg hold;
    TEST_ASSERT_TRUE(decode_hold(text.c_str(), &hold, 1000));
    TEST_ASSERT_TRUE(hold.has_ceiling);
  }
}

// --- release ---

void test_decode_valid_release() {
  ReleaseMsg release;
  TEST_ASSERT_TRUE(decode_release("{\"t\":\"release\",\"press\":17}", &release));
  TEST_ASSERT_EQUAL_INT(17, release.press);
}

void test_decode_broken_release_is_dropped() {
  ReleaseMsg release;
  TEST_ASSERT_FALSE(decode_release("{\"t\":\"release\"}", &release));
  TEST_ASSERT_FALSE(decode_release("{\"t\":\"hold\",\"press\":1}", &release));
  TEST_ASSERT_FALSE(decode_release("garbage", &release));
  TEST_ASSERT_FALSE(decode_release(nullptr, &release));
}

// --- state ---

void test_state_encode_contains_v2_fields() {
  const LiftState state = moving_state();
  char json[1024];
  const size_t written = state_encode(state, json, sizeof(json));
  TEST_ASSERT_GREATER_THAN(0, written);

  // v2 の形（protocol §2.2 の例どおり）を持っていること
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"t\":\"state\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"dir\":\"up\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"top_detect\":false"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"ceiling\":{"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"status\":\"MEASURED\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner\":\"module\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"module\":{"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"provisional\":["));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "CEILING_MARGIN_MM"));
  const std::string expected_fw = std::string("\"fw\":\"") + lift_core_version() + "\"";
  TEST_ASSERT_NOT_NULL(std::strstr(json, expected_fw.c_str()));

  LiftState back;
  TEST_ASSERT_TRUE(state_decode(json, &back));
  TEST_ASSERT_EQUAL_INT(88, back.seq);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(back.dir));
  TEST_ASSERT_EQUAL_INT(40, back.duty);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::NONE), static_cast<int>(back.reason));
  TEST_ASSERT_EQUAL_INT(812, back.height_mm);
  TEST_ASSERT_TRUE(back.height_ok);
  TEST_ASSERT_FALSE(back.top_detect);
  TEST_ASSERT_TRUE(back.ceiling_present);
  TEST_ASSERT_TRUE(back.ceiling_used);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MEASURED),
                        static_cast<int>(back.ceiling_status));
  TEST_ASSERT_EQUAL_INT(1450, back.ceiling_mm);
  TEST_ASSERT_EQUAL_INT(95, back.ceiling_age_ms);
  TEST_ASSERT_TRUE(back.ceiling_ok);
  TEST_ASSERT_TRUE(back.has_owner);
  TEST_ASSERT_EQUAL_INT(1, back.owner_kind);
  TEST_ASSERT_EQUAL_INT(1, back.ui_clients);
  TEST_ASSERT_TRUE(back.module_connected);
  TEST_ASSERT_TRUE(back.module_has_sensor);
  TEST_ASSERT_EQUAL_STRING("192.168.5.23", back.module_ip);
  TEST_ASSERT_EQUAL_STRING("hve-cam", back.module_name);
  TEST_ASSERT_EQUAL_INT(35, back.cmd_age_ms);
}

void test_state_encode_writes_nulls_when_no_owner_or_module() {
  LiftState state;
  char json[1024];
  TEST_ASSERT_GREATER_THAN(0, state_encode(state, json, sizeof(json)));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"ceiling\":null"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner\":null"));

  LiftState back;
  TEST_ASSERT_TRUE(state_decode(json, &back));
  TEST_ASSERT_FALSE(back.ceiling_present);
  TEST_ASSERT_FALSE(back.has_owner);
  TEST_ASSERT_FALSE(back.module_connected);
}

void test_state_encode_reports_every_stop_reason() {
  const StopReason reasons[] = {
      StopReason::NONE,          StopReason::CMD_STOP,     StopReason::CMD_TIMEOUT,
      StopReason::OWNER_GONE,    StopReason::CEILING_NEAR, StopReason::CEILING_STALE,
      StopReason::HEIGHT_UNKNOWN, StopReason::TOP,         StopReason::BOTTOM,
      StopReason::MAX_RUN,       StopReason::OUT_OF_RANGE,
  };
  for (size_t i = 0; i < sizeof(reasons) / sizeof(reasons[0]); ++i) {
    LiftState state = moving_state();
    state.reason = reasons[i];
    state.ceiling_reason = reasons[i];
    char json[1024];
    TEST_ASSERT_GREATER_THAN(0, state_encode(state, json, sizeof(json)));
    LiftState back;
    TEST_ASSERT_TRUE(state_decode(json, &back));
    TEST_ASSERT_EQUAL_INT(static_cast<int>(reasons[i]), static_cast<int>(back.reason));
    TEST_ASSERT_EQUAL_INT(static_cast<int>(reasons[i]),
                          static_cast<int>(back.ceiling_reason));
  }
}

void test_state_encode_reports_zero_when_buffer_is_short() {
  const LiftState state = moving_state();
  char json[8];
  TEST_ASSERT_EQUAL_INT(0, state_encode(state, json, sizeof(json)));
}

void test_state_decode_rejects_broken_input() {
  LiftState state;
  TEST_ASSERT_FALSE(state_decode("{", &state));
  TEST_ASSERT_FALSE(state_decode("{\"t\":\"cmd\"}", &state));
  TEST_ASSERT_FALSE(state_decode("{\"t\":\"state\",\"dir\":\"up\"}", &state));
  TEST_ASSERT_FALSE(state_decode("{\"t\":\"state\",\"dir\":\"sideways\",\"duty\":0,\"reason\":\"NONE\","
                                 "\"bottom\":false,\"height_mm\":0,\"height_ok\":false,"
                                 "\"top_detect\":false,\"ceiling\":null,\"owner\":null,"
                                 "\"ui_clients\":0,"
                                 "\"module\":{\"connected\":false,\"ceiling_sensor\":null,"
                                 "\"ip\":null,\"name\":null}}",
                                 &state));
  TEST_ASSERT_FALSE(state_decode(nullptr, &state));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_decode_valid_hello);
  RUN_TEST(test_decode_hello_without_sensor);
  RUN_TEST(test_decode_hello_missing_sensor_is_true);
  RUN_TEST(test_decode_hello_wrong_typed_sensor_is_true);
  RUN_TEST(test_decode_unreadable_hello_is_true_but_rejected);

  RUN_TEST(test_decode_valid_hold);
  RUN_TEST(test_decode_broken_hold_is_dropped);
  RUN_TEST(test_decode_hold_without_ceiling_is_missing);
  RUN_TEST(test_decode_hold_with_broken_ceiling_is_missing);
  RUN_TEST(test_decode_hold_ceiling_statuses);

  RUN_TEST(test_decode_valid_release);
  RUN_TEST(test_decode_broken_release_is_dropped);

  RUN_TEST(test_state_encode_contains_v2_fields);
  RUN_TEST(test_state_encode_writes_nulls_when_no_owner_or_module);
  RUN_TEST(test_state_encode_reports_every_stop_reason);
  RUN_TEST(test_state_encode_reports_zero_when_buffer_is_short);
  RUN_TEST(test_state_decode_rejects_broken_input);

  return UNITY_END();
}
