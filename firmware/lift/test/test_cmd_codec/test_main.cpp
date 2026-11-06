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
  state.speed = 40;
  state.speed_meas = 39;
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
  std::strncpy(state.owner_name, "hve-cam", sizeof(state.owner_name) - 1);
  state.owner_operator = ModuleOperator::HUMAN;
  state.homed = true;
  std::strncpy(state.ceiling_from, "hve-cam", sizeof(state.ceiling_from) - 1);
  state.module_count = 1;
  std::strncpy(state.modules[0].name, "hve-cam", sizeof(state.modules[0].name) - 1);
  state.modules[0].role = ModuleRole::CONTROL;
  state.modules[0].op = ModuleOperator::HUMAN;
  state.modules[0].ceiling_sensor = true;
  std::strncpy(state.modules[0].ip, "192.168.5.23", sizeof(state.modules[0].ip) - 1);
  std::strncpy(state.modules[0].fw, "0.2.0", sizeof(state.modules[0].fw) - 1);
  state.cmd_age_ms = 35;
  return state;
}

// modules を上限まで並べ、busy_label・name・ip・fw を最大にした state（最悪の長さの見積り）
LiftState worst_case_state() {
  LiftState state = moving_state();
  state.owner_kind = 1;
  std::memset(state.owner_name, 'a', 16);
  std::memset(state.ceiling_from, 'b', 16);
  state.seq = 2147483647;
  state.height_mm = -2147483647;
  state.ceiling_age_ms = 2147483647u;  // 10 桁（uint32 の最大も 10 桁。state_decode は int で読む）
  state.cmd_age_ms = 2147483647u;  // 10 桁（uint32 の最大も 10 桁。state_decode は int で読む）
  state.module_count = LIFT_MODULE_LIST_MAX;
  for (int i = 0; i < LIFT_MODULE_LIST_MAX; ++i) {
    ModuleInfo& info = state.modules[i];
    std::memset(info.name, 'a' + i, 16);
    info.role = ModuleRole::CONTROL;
    info.op = ModuleOperator::AUTO;
    info.ceiling_sensor = false;
    // 日本語 16 文字（48 バイト）。UTF-8 は ArduinoJson がそのまま出す
    for (int k = 0; k < 16; ++k) {
      std::memcpy(info.busy_label + k * 3, "\xe6\xb8\xac", 3);
    }
    info.busy_label[48] = '\0';
    std::strncpy(info.ip, "255.255.255.255", sizeof(info.ip) - 1);
    std::memset(info.fw, '9', 15);
  }
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


// --- hello の新しい欄（protocol-modules §1.1） ---

void test_decode_hello_new_fields() {
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello(
      "{\"t\":\"hello\",\"name\":\"fk\",\"role\":\"control\",\"operator\":\"auto\","
      "\"ceiling_sensor\":false,\"busy_label\":\"\xe6\xb8\xac\xe5\xae\x9a\",\"fw\":\"0.1.0\"}",
      &hello));
  TEST_ASSERT_EQUAL_STRING("fk", hello.name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::CONTROL), static_cast<int>(hello.role));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO), static_cast<int>(hello.op));
  TEST_ASSERT_FALSE(hello.has_sensor);
  TEST_ASSERT_TRUE(hello.sensor_explicit);
  TEST_ASSERT_EQUAL_STRING("\xe6\xb8\xac\xe5\xae\x9a", hello.busy_label);
}

void test_decode_hello_role_defaults() {
  HelloMsg hello;
  // 欠けていれば control（今のカメラモジュールは送らない）
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\"}", &hello));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::CONTROL), static_cast<int>(hello.role));
  TEST_ASSERT_FALSE(hello.sensor_explicit);
  // 知らない値は observe（命じられない側に倒す）
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"role\":\"admin\"}", &hello));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::OBSERVE), static_cast<int>(hello.role));
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"role\":\"observe\"}", &hello));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::OBSERVE), static_cast<int>(hello.role));
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"role\":7}", &hello));  // 型違いも欠けと同じ
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::CONTROL), static_cast<int>(hello.role));
}

void test_decode_hello_operator_defaults_to_human() {
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\"}", &hello));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::HUMAN), static_cast<int>(hello.op));
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"operator\":\"robot\"}", &hello));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::HUMAN), static_cast<int>(hello.op));
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"operator\":\"human\"}", &hello));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::HUMAN), static_cast<int>(hello.op));
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"operator\":\"auto\"}", &hello));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO), static_cast<int>(hello.op));
}

void test_decode_hello_bad_name_is_nameless() {
  HelloMsg hello;
  const char* bad[] = {
      "{\"t\":\"hello\",\"name\":\"\"}",
      "{\"t\":\"hello\",\"name\":\"FK\"}",                // 大文字
      "{\"t\":\"hello\",\"name\":\"f k\"}",               // 空白
      "{\"t\":\"hello\",\"name\":\"f_k\"}",               // 下線
      "{\"t\":\"hello\",\"name\":\"abcdefghijklmnopq\"}",  // 17 文字
      "{\"t\":\"hello\",\"name\":5}",
  };
  for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i) {
    TEST_ASSERT_TRUE(decode_hello(bad[i], &hello));
    TEST_ASSERT_EQUAL_STRING("", hello.name);
  }
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"name\":\"abcdefghijklmnop\"}", &hello));  // 16 文字
  TEST_ASSERT_EQUAL_STRING("abcdefghijklmnop", hello.name);
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"name\":\"ss-2\"}", &hello));
  TEST_ASSERT_EQUAL_STRING("ss-2", hello.name);
}

void test_decode_hello_busy_label_limits() {
  HelloMsg hello;
  // a + 日本語 16 文字 = 49 バイト。切ると文字の途中で切れるので、切り詰めずに無しとして扱う
  std::string text = "{\"t\":\"hello\",\"busy_label\":\"a";
  for (int i = 0; i < 16; ++i) {
    text += "\xe6\xb8\xac";
  }
  text += "\"}";
  TEST_ASSERT_TRUE(decode_hello(text.c_str(), &hello));
  TEST_ASSERT_EQUAL_STRING("", hello.busy_label);
  // ちょうど 48 バイトは通る（日本語 16 文字）
  text = "{\"t\":\"hello\",\"busy_label\":\"";
  for (int i = 0; i < 16; ++i) {
    text += "\xe6\xb8\xac";
  }
  text += "\"}";
  TEST_ASSERT_TRUE(decode_hello(text.c_str(), &hello));
  TEST_ASSERT_EQUAL_INT(48, static_cast<int>(std::strlen(hello.busy_label)));
  // 空・文字列でないものは無し
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"busy_label\":\"\"}", &hello));
  TEST_ASSERT_EQUAL_STRING("", hello.busy_label);
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"busy_label\":12}", &hello));
  TEST_ASSERT_EQUAL_STRING("", hello.busy_label);
}

// --- ceiling ---

void test_decode_ceiling_message() {
  CeilingReport report;
  TEST_ASSERT_TRUE(decode_ceiling("{\"t\":\"ceiling\",\"status\":\"MEASURED\",\"mm\":1450,\"age_ms\":80}",
                                  &report, 1234));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MEASURED), static_cast<int>(report.status));
  TEST_ASSERT_EQUAL_INT(1450, report.mm);
  TEST_ASSERT_EQUAL_INT(80, report.age_ms);
  TEST_ASSERT_EQUAL_INT(1234, report.received_at_ms);
  TEST_ASSERT_TRUE(decode_ceiling("{\"t\":\"ceiling\",\"status\":\"NO_ECHO\",\"age_ms\":10}", &report, 5));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::NO_ECHO), static_cast<int>(report.status));
}

void test_decode_ceiling_rejects_broken() {
  CeilingReport report;
  TEST_ASSERT_FALSE(decode_ceiling("not json", &report, 0));
  TEST_ASSERT_FALSE(decode_ceiling("{\"t\":\"hold\",\"status\":\"MEASURED\",\"mm\":1,\"age_ms\":1}", &report, 0));
  TEST_ASSERT_FALSE(decode_ceiling("{\"t\":\"ceiling\",\"status\":\"MEASURED\",\"age_ms\":1}", &report, 0));
  TEST_ASSERT_FALSE(decode_ceiling("{\"t\":\"ceiling\",\"status\":\"WHAT\",\"age_ms\":1}", &report, 0));
  TEST_ASSERT_FALSE(decode_ceiling("{\"t\":\"ceiling\",\"status\":\"MEASURED\",\"mm\":1}", &report, 0));
  TEST_ASSERT_FALSE(decode_ceiling("{\"t\":\"ceiling\",\"status\":\"MEASURED\",\"mm\":-1,\"age_ms\":1}", &report, 0));
  TEST_ASSERT_FALSE(decode_ceiling("{\"t\":\"ceiling\",\"status\":\"MEASURED\",\"mm\":1,\"age_ms\":-1}", &report, 0));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MISSING), static_cast<int>(report.status));
}

// --- hold ---

void test_decode_valid_hold() {
  HoldMsg hold;
  TEST_ASSERT_TRUE(decode_hold("{\"t\":\"hold\",\"press\":17,\"dir\":\"up\",\"speed\":40,"
                               "\"ceiling\":{\"status\":\"MEASURED\",\"mm\":1450,\"age_ms\":80}}",
                               &hold, 1000));
  TEST_ASSERT_EQUAL_INT(17, hold.press);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hold.dir));
  TEST_ASSERT_EQUAL_INT(40, hold.speed);
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
  assert_hold_never_moves_up("{\"t\":\"cmd\",\"press\":1,\"dir\":\"up\",\"speed\":40}");
  assert_hold_never_moves_up("{\"t\":\"hold\",\"dir\":\"up\",\"speed\":40}");  // press が無い
  assert_hold_never_moves_up("{\"t\":\"hold\",\"press\":1,\"speed\":40}");  // dir が無い
  assert_hold_never_moves_up(
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"sideways\",\"speed\":40}");  // 知らない方向
  assert_hold_never_moves_up(
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\"}");  // speed が無い
  assert_hold_never_moves_up(
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":\"40\"}");  // speed の型が違う
  // duty だけの hold は読めない hold として捨てる（speed が無い）
  assert_hold_never_moves_up("{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40}");
  assert_hold_never_moves_up(
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"duty\":40,"
      "\"ceiling\":{\"status\":\"MEASURED\",\"mm\":1450,\"age_ms\":80}}");
  assert_hold_never_moves_up(nullptr);
}

void test_decode_hold_with_negative_speed_is_dropped() {
  assert_hold_never_moves_up("{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":-1}");
}

void test_decode_hold_without_ceiling_is_missing() {
  HoldMsg hold;
  TEST_ASSERT_TRUE(decode_hold("{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":40}", &hold,
                               1000));
  TEST_ASSERT_FALSE(hold.has_ceiling);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MISSING),
                        static_cast<int>(hold.ceiling.status));
  // dir・speed は読めた値のまま（天井が無いので上昇は止まる）
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hold.dir));
  TEST_ASSERT_EQUAL_INT(40, hold.speed);
}

void test_decode_hold_with_broken_ceiling_is_missing() {
  // 知らない status・型の違う中身は hold を捨てず MISSING として受ける
  const char* cases[] = {
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":40,\"ceiling\":{\"status\":\"NEAR\"}}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":40,\"ceiling\":{\"status\":1}}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":40,\"ceiling\":{\"status\":\"MEASURED\"}}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":40,"
      "\"ceiling\":{\"status\":\"MEASURED\",\"mm\":\"1450\",\"age_ms\":80}}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":40,\"ceiling\":\"MEASURED\"}",
      "{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":40,\"ceiling\":null}",
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
    std::string text = std::string("{\"t\":\"hold\",\"press\":1,\"dir\":\"up\",\"speed\":40,"
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
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"speed\":40"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"speed_meas\":39"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"top_detect\":false"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"ceiling\":{"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"status\":\"MEASURED\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner\":\"module\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"modules\":[{"));
  TEST_ASSERT_NULL(std::strstr(json, "\"module\":"));  // 今の module は消えた
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner_name\":\"hve-cam\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner_operator\":\"human\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"homed\":true"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"from\":\"hve-cam\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"provisional\":["));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "CEILING_MARGIN_MM"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "LIFT_MAX_RUN_PCT"));
  const std::string expected_fw = std::string("\"fw\":\"") + lift_core_version() + "\"";
  TEST_ASSERT_NOT_NULL(std::strstr(json, expected_fw.c_str()));

  LiftState back;
  TEST_ASSERT_TRUE(state_decode(json, &back));
  TEST_ASSERT_EQUAL_INT(88, back.seq);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(back.dir));
  TEST_ASSERT_EQUAL_INT(40, back.duty);
  TEST_ASSERT_EQUAL_INT(40, back.speed);
  TEST_ASSERT_EQUAL_INT(39, back.speed_meas);
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
  TEST_ASSERT_EQUAL_STRING("hve-cam", back.owner_name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::HUMAN),
                        static_cast<int>(back.owner_operator));
  TEST_ASSERT_TRUE(back.homed);
  TEST_ASSERT_EQUAL_STRING("hve-cam", back.ceiling_from);
  TEST_ASSERT_EQUAL_INT(1, back.module_count);
  TEST_ASSERT_EQUAL_STRING("hve-cam", back.modules[0].name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::CONTROL), static_cast<int>(back.modules[0].role));
  TEST_ASSERT_TRUE(back.modules[0].ceiling_sensor);
  TEST_ASSERT_EQUAL_STRING("", back.modules[0].busy_label);
  TEST_ASSERT_EQUAL_STRING("192.168.5.23", back.modules[0].ip);
  TEST_ASSERT_EQUAL_STRING("0.2.0", back.modules[0].fw);
  TEST_ASSERT_EQUAL_INT(35, back.cmd_age_ms);
}

void test_state_owner_button_round_trips() {
  LiftState state;
  state.has_owner = true;
  state.owner_kind = 2;
  char json[1024];
  TEST_ASSERT_GREATER_THAN(0, state_encode(state, json, sizeof(json)));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner\":\"button\""));

  LiftState back;
  TEST_ASSERT_TRUE(state_decode(json, &back));
  TEST_ASSERT_TRUE(back.has_owner);
  TEST_ASSERT_EQUAL_INT(2, back.owner_kind);
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
  TEST_ASSERT_EQUAL_INT(0, back.module_count);
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"modules\":[]"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner_name\":null"));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner_operator\":null"));
}

void test_state_arm_is_all_null() {
  // 測定機構の中身は決まるまで null（false を出すと fk が「付いていない」と読んで動かす）
  const LiftState state = moving_state();
  char json[2048];
  TEST_ASSERT_GREATER_THAN(0, state_encode(state, json, sizeof(json)));
  TEST_ASSERT_NOT_NULL(std::strstr(
      json, "\"arm\":{\"attached\":null,\"link\":null,\"confirm\":null,\"out\":null,\"drum\":null}"));
  TEST_ASSERT_NULL(std::strstr(json, "\"attached\":false"));
}

void test_state_owner_auto_and_modules_round_trip() {
  LiftState state = moving_state();
  std::strncpy(state.owner_name, "fk", sizeof(state.owner_name));
  state.owner_operator = ModuleOperator::AUTO;
  state.module_count = 2;
  ModuleInfo& fk = state.modules[1];
  std::strncpy(fk.name, "fk", sizeof(fk.name));
  fk.op = ModuleOperator::AUTO;
  fk.ceiling_sensor = false;
  std::strncpy(fk.busy_label, "\xe6\xb8\xac\xe5\xae\x9a", sizeof(fk.busy_label));
  std::strncpy(fk.fw, "0.1.0", sizeof(fk.fw));
  state.modules[1].role = ModuleRole::OBSERVE;
  char json[2048];
  TEST_ASSERT_GREATER_THAN(0, state_encode(state, json, sizeof(json)));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner_name\":\"fk\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner_operator\":\"auto\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"role\":\"observe\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"busy_label\":null"));  // hve-cam は無し
  LiftState back;
  TEST_ASSERT_TRUE(state_decode(json, &back));
  TEST_ASSERT_EQUAL_STRING("fk", back.owner_name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO), static_cast<int>(back.owner_operator));
  TEST_ASSERT_EQUAL_INT(2, back.module_count);
  TEST_ASSERT_EQUAL_STRING("fk", back.modules[1].name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::OBSERVE), static_cast<int>(back.modules[1].role));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO), static_cast<int>(back.modules[1].op));
  TEST_ASSERT_FALSE(back.modules[1].ceiling_sensor);
  TEST_ASSERT_EQUAL_STRING("\xe6\xb8\xac\xe5\xae\x9a", back.modules[1].busy_label);
}

void test_state_worst_case_fits_text_max() {
  // modules を上限まで並べ busy_label を最大にした state を実際に作って測る。
  // LIFT_STATE_TEXT_MAX は、それに余裕を持たせた値（測った長さ 1770 バイトの約 1.3 倍）
  const LiftState state = worst_case_state();
  static char json[8192];
  const size_t written = state_encode(state, json, sizeof(json));
  TEST_ASSERT_GREATER_THAN(0, written);
  TEST_ASSERT_TRUE(written < LIFT_STATE_TEXT_MAX);
  TEST_ASSERT_TRUE(written * 3 >= LIFT_STATE_TEXT_MAX * 2);  // 大きすぎる値のまま放置しない（1.5 倍まで）
  // 実際のバッファの大きさでも書ける
  static char exact[LIFT_STATE_TEXT_MAX];
  TEST_ASSERT_EQUAL_INT(static_cast<int>(written),
                        static_cast<int>(state_encode(state, exact, sizeof(exact))));
  // 戻して読める（途中で切れていない）
  LiftState back;
  TEST_ASSERT_TRUE(state_decode(exact, &back));
  TEST_ASSERT_EQUAL_INT(LIFT_MODULE_LIST_MAX, back.module_count);
}

void test_state_encode_reports_every_stop_reason() {
  const StopReason reasons[] = {
      StopReason::NONE,          StopReason::CMD_STOP,     StopReason::CMD_TIMEOUT,
      StopReason::OWNER_GONE,    StopReason::CEILING_NEAR, StopReason::CEILING_STALE,
      StopReason::HEIGHT_UNKNOWN, StopReason::TOP,         StopReason::BOTTOM,
      StopReason::MAX_RUN,       StopReason::ENCODER_STALL, StopReason::OUT_OF_RANGE,
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
                                 "\"modules\":[],\"homed\":false}",
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
  RUN_TEST(test_decode_hello_new_fields);
  RUN_TEST(test_decode_hello_role_defaults);
  RUN_TEST(test_decode_hello_operator_defaults_to_human);
  RUN_TEST(test_decode_hello_bad_name_is_nameless);
  RUN_TEST(test_decode_hello_busy_label_limits);

  RUN_TEST(test_decode_ceiling_message);
  RUN_TEST(test_decode_ceiling_rejects_broken);

  RUN_TEST(test_decode_valid_hold);
  RUN_TEST(test_decode_broken_hold_is_dropped);
  RUN_TEST(test_decode_hold_with_negative_speed_is_dropped);
  RUN_TEST(test_decode_hold_without_ceiling_is_missing);
  RUN_TEST(test_decode_hold_with_broken_ceiling_is_missing);
  RUN_TEST(test_decode_hold_ceiling_statuses);

  RUN_TEST(test_decode_valid_release);
  RUN_TEST(test_decode_broken_release_is_dropped);

  RUN_TEST(test_state_encode_contains_v2_fields);
  RUN_TEST(test_state_owner_button_round_trips);
  RUN_TEST(test_state_encode_writes_nulls_when_no_owner_or_module);
  RUN_TEST(test_state_arm_is_all_null);
  RUN_TEST(test_state_owner_auto_and_modules_round_trip);
  RUN_TEST(test_state_worst_case_fits_text_max);
  RUN_TEST(test_state_encode_reports_every_stop_reason);
  RUN_TEST(test_state_encode_reports_zero_when_buffer_is_short);
  RUN_TEST(test_state_decode_rejects_broken_input);

  return UNITY_END();
}
