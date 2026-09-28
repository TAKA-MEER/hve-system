// Unity の試験（env:native）。cmd_codec の正常系と、壊れた入力が上昇にならないこと。
// DetailedDesign-protocol.md §2.2・§2.3。
#include <unity.h>

#include <cstring>
#include <string>

#include "cmd_codec.h"
#include "lift_core_version.h"

namespace {

// 指令全体が stop に落ちる場合（読めない JSON・知らない t・dir が読めない・duty の型が違う）
void assert_stopped(const char* text) {
  LiftCmd cmd;
  const bool ok = cmd_decode(text, &cmd);
  TEST_ASSERT_FALSE(ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(cmd.dir));
  TEST_ASSERT_EQUAL_INT(0, cmd.duty);
  TEST_ASSERT_FALSE(cmd.ceil_ok);
}

// 壊れた指令をそのまま判定へ通しても上昇しないことを確かめる
void assert_never_moves_up(const char* text, StopReason expected_reason) {
  LiftCmd cmd;
  cmd_decode(text, &cmd);

  LiftDecideInput in;
  in.has_cmd = true;
  in.cmd = cmd;
  in.height_mm = 500;
  in.height_ok = true;
  in.height_at_ms = 0;
  in.top_mm = LIFT_TOP_MM;
  const LiftDecideResult decided = lift_decide(in);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(decided.dir));
  TEST_ASSERT_EQUAL_INT(0, decided.duty);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(expected_reason), static_cast<int>(decided.reason));
}

}  // namespace

// --- cmd の正常系 ---

void test_decode_valid_up_command() {
  LiftCmd cmd;
  const bool ok = cmd_decode("{\"t\":\"cmd\",\"seq\":1234,\"dir\":\"up\",\"duty\":40,\"ceil_ok\":true}", &cmd);
  TEST_ASSERT_TRUE(ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(cmd.dir));
  TEST_ASSERT_EQUAL_INT(40, cmd.duty);
  TEST_ASSERT_TRUE(cmd.ceil_ok);
}

void test_decode_valid_down_command() {
  LiftCmd cmd;
  const bool ok = cmd_decode("{\"t\":\"cmd\",\"dir\":\"down\",\"duty\":100,\"ceil_ok\":false}", &cmd);
  TEST_ASSERT_TRUE(ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(cmd.dir));
  TEST_ASSERT_EQUAL_INT(100, cmd.duty);
  TEST_ASSERT_FALSE(cmd.ceil_ok);
}

void test_decode_valid_stop_command() {
  LiftCmd cmd;
  const bool ok = cmd_decode("{\"t\":\"cmd\",\"dir\":\"stop\",\"duty\":0,\"ceil_ok\":true}", &cmd);
  TEST_ASSERT_TRUE(ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(cmd.dir));
}

// --- 壊れた入力は上昇させない ---

void test_broken_json_stops() {
  assert_stopped("{\"t\":\"cmd\",\"dir\":\"up\",");
  assert_stopped("not json at all");
  assert_stopped("");
  assert_stopped("[1,2,3]");
  assert_stopped("5");
}

void test_unknown_t_stops() {
  assert_stopped("{\"t\":\"hold\",\"dir\":\"up\",\"duty\":40,\"ceil_ok\":true}");
  assert_stopped("{\"t\":\"state\",\"dir\":\"up\",\"duty\":40,\"ceil_ok\":true}");
  assert_stopped("{\"dir\":\"up\",\"duty\":40,\"ceil_ok\":true}");  // t 自体が無い
  assert_stopped("{\"t\":7,\"dir\":\"up\",\"duty\":40,\"ceil_ok\":true}");  // t の型が違う
}

void test_unreadable_dir_stops() {
  assert_stopped("{\"t\":\"cmd\",\"duty\":40,\"ceil_ok\":true}");                 // dir が無い
  assert_stopped("{\"t\":\"cmd\",\"dir\":1,\"duty\":40,\"ceil_ok\":true}");        // dir の型が違う
  assert_stopped("{\"t\":\"cmd\",\"dir\":\"sideways\",\"duty\":40,\"ceil_ok\":true}");  // 知らない値
  assert_stopped("{\"t\":\"cmd\",\"dir\":null,\"duty\":40,\"ceil_ok\":true}");
}

void test_wrong_typed_fields_stop() {
  assert_stopped("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":\"40\",\"ceil_ok\":true}");
  assert_stopped("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":40.5,\"ceil_ok\":true}");
  assert_stopped("{\"t\":\"cmd\",\"dir\":\"up\",\"ceil_ok\":true}");  // duty が無い
}

void test_missing_ceil_ok_is_false() {
  LiftCmd cmd;
  const bool ok = cmd_decode("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":40}", &cmd);
  TEST_ASSERT_FALSE(ok);
  TEST_ASSERT_FALSE(cmd.ceil_ok);
  // dir は読めた値のまま。天井の許可が無いので上昇はしない
  assert_never_moves_up("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":40}", StopReason::CEILING);
}

void test_wrong_typed_ceil_ok_is_false() {
  assert_never_moves_up("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":40,\"ceil_ok\":1}", StopReason::CEILING);
  assert_never_moves_up("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":40,\"ceil_ok\":\"true\"}", StopReason::CEILING);
  assert_never_moves_up("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":40,\"ceil_ok\":null}", StopReason::CEILING);
}

void test_null_text_stops() {
  assert_stopped(nullptr);
}

void test_broken_command_never_moves_up_through_decide() {
  assert_never_moves_up("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":40,\"ceil_ok\":false}", StopReason::CEILING);
  assert_never_moves_up("{\"t\":\"hold\",\"dir\":\"up\",\"duty\":40,\"ceil_ok\":true}", StopReason::CMD_STOP);
  assert_never_moves_up("garbage", StopReason::CMD_STOP);
  assert_never_moves_up("{\"t\":\"cmd\",\"dir\":\"sideways\",\"duty\":40,\"ceil_ok\":true}", StopReason::CMD_STOP);
  assert_never_moves_up("{\"t\":\"cmd\",\"dir\":\"up\",\"duty\":\"40\",\"ceil_ok\":true}", StopReason::CMD_STOP);
}

// --- state ---

void test_state_encode_contains_all_fields() {
  LiftState state;
  state.seq = 88;
  state.dir = LiftDir::UP;
  state.duty = 40;
  state.reason = StopReason::NONE;
  state.bottom = false;
  state.height_mm = 812;
  state.height_ok = true;
  state.top_mm = 1500;
  state.cmd_age_ms = 35;

  char json[256];
  const size_t written = state_encode(state, json, sizeof(json));
  TEST_ASSERT_GREATER_THAN(0, written);

  LiftState back;
  TEST_ASSERT_TRUE(state_decode(json, &back));
  TEST_ASSERT_EQUAL_INT(88, back.seq);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(back.dir));
  TEST_ASSERT_EQUAL_INT(40, back.duty);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::NONE), static_cast<int>(back.reason));
  TEST_ASSERT_EQUAL_INT(812, back.height_mm);
  TEST_ASSERT_TRUE(back.height_ok);
  TEST_ASSERT_EQUAL_INT(1500, back.top_mm);
  TEST_ASSERT_EQUAL_INT(35, back.cmd_age_ms);
  const std::string expected_fw = std::string("\"fw\":\"") + lift_core_version() + "\"";
  TEST_ASSERT_NOT_NULL(std::strstr(json, expected_fw.c_str()));
}

void test_state_encode_writes_null_for_unset_top() {
  LiftState state;
  state.top_mm = LIFT_TOP_MM;
  state.reason = StopReason::CMD_STOP;

  char json[256];
  TEST_ASSERT_GREATER_THAN(0, state_encode(state, json, sizeof(json)));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"top_mm\":null"));

  LiftState back;
  TEST_ASSERT_TRUE(state_decode(json, &back));
  TEST_ASSERT_EQUAL_INT(LIFT_TOP_MM, back.top_mm);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::CMD_STOP), static_cast<int>(back.reason));
}

void test_state_encode_reports_every_stop_reason() {
  const StopReason reasons[] = {
      StopReason::NONE,     StopReason::CMD_STOP, StopReason::CMD_TIMEOUT,
      StopReason::CEILING,  StopReason::TOP,     StopReason::BOTTOM,
      StopReason::MAX_RUN,  StopReason::HEIGHT_UNKNOWN,
  };
  for (size_t i = 0; i < sizeof(reasons) / sizeof(reasons[0]); ++i) {
    LiftState state;
    state.reason = reasons[i];
    char json[256];
    TEST_ASSERT_GREATER_THAN(0, state_encode(state, json, sizeof(json)));
    LiftState back;
    TEST_ASSERT_TRUE(state_decode(json, &back));
    TEST_ASSERT_EQUAL_INT(static_cast<int>(reasons[i]), static_cast<int>(back.reason));
  }
}

void test_state_encode_reports_zero_when_buffer_is_short() {
  LiftState state;
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
                                 "\"top_mm\":null,\"cmd_age_ms\":0}",
                                 &state));
  TEST_ASSERT_FALSE(state_decode(nullptr, &state));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_decode_valid_up_command);
  RUN_TEST(test_decode_valid_down_command);
  RUN_TEST(test_decode_valid_stop_command);

  RUN_TEST(test_broken_json_stops);
  RUN_TEST(test_unknown_t_stops);
  RUN_TEST(test_unreadable_dir_stops);
  RUN_TEST(test_wrong_typed_fields_stop);
  RUN_TEST(test_missing_ceil_ok_is_false);
  RUN_TEST(test_wrong_typed_ceil_ok_is_false);
  RUN_TEST(test_null_text_stops);
  RUN_TEST(test_broken_command_never_moves_up_through_decide);

  RUN_TEST(test_state_encode_contains_all_fields);
  RUN_TEST(test_state_encode_writes_null_for_unset_top);
  RUN_TEST(test_state_encode_reports_every_stop_reason);
  RUN_TEST(test_state_encode_reports_zero_when_buffer_is_short);
  RUN_TEST(test_state_decode_rejects_broken_input);

  return UNITY_END();
}
