// Unity の試験（env:native）。UART の行の読み取り・組み立て。
// 読めない行は捨てる（false・0。捨てないと生存確認が延びて止まらなくなる）。
// DetailedDesign-protocol.md §5。
#include <unity.h>

#include <cstring>

#include "io_codec.h"

namespace {

// 読めない行は 0 にして false（＝ M 0 0 0 として受け付けない）
void assert_dropped(const char* line) {
  IoCmd cmd;
  cmd.seq = 999;
  cmd.pitch_ddeg = 999;
  cmd.yaw_hsps = 999;
  TEST_ASSERT_FALSE(parse_m_line(line, &cmd));
  TEST_ASSERT_EQUAL_UINT(0, cmd.seq);
  TEST_ASSERT_EQUAL_INT(0, cmd.pitch_ddeg);
  TEST_ASSERT_EQUAL_INT32(0, cmd.yaw_hsps);
}

}  // namespace

// --- M 行の正常系 ---

void test_parse_valid_zero_command() {
  IoCmd cmd;
  TEST_ASSERT_TRUE(parse_m_line("M 0 0 0", &cmd));
  TEST_ASSERT_EQUAL_UINT(0, cmd.seq);
  TEST_ASSERT_EQUAL_INT(0, cmd.pitch_ddeg);
  TEST_ASSERT_EQUAL_INT32(0, cmd.yaw_hsps);
}

void test_parse_valid_command() {
  IoCmd cmd;
  TEST_ASSERT_TRUE(parse_m_line("M 12 200 100", &cmd));
  TEST_ASSERT_EQUAL_UINT(12, cmd.seq);
  TEST_ASSERT_EQUAL_INT(200, cmd.pitch_ddeg);
  TEST_ASSERT_EQUAL_INT32(100, cmd.yaw_hsps);
}

void test_parse_accepts_out_of_range_values_for_controller_to_clamp() {
  // 範囲外の値はここでは受け付け、丸めは IoController の役（M の例の値）
  IoCmd cmd;
  TEST_ASSERT_TRUE(parse_m_line("M 65535 -1800 -680", &cmd));
  TEST_ASSERT_EQUAL_UINT(65535, cmd.seq);
  TEST_ASSERT_EQUAL_INT(-1800, cmd.pitch_ddeg);
  TEST_ASSERT_EQUAL_INT32(-680, cmd.yaw_hsps);
}

// --- 読めない行は捨てる ---

void test_drops_wrong_head_or_field_count() {
  assert_dropped("");
  assert_dropped("X 1 2 3");
  assert_dropped("m 1 2 3");
  assert_dropped("M 1 2");
  assert_dropped("M 1 2 3 4");
  assert_dropped("M 1");
  assert_dropped("M");
}

void test_drops_non_integer_fields() {
  assert_dropped("M a b c");
  assert_dropped("M 1.5 2 3");
  assert_dropped("M 1 2.0 3");
  assert_dropped("M 1 2 x");
  assert_dropped("M 1  2 3");  // 空白 2 つ
  assert_dropped("M 1 2 3 ");  // 末尾の空白
  assert_dropped(" M 1 2 3");  // 先頭の空白
  assert_dropped("M 1 2 3\n");
}

void test_drops_out_of_range_seq() {
  assert_dropped("M -1 0 0");
  assert_dropped("M 65536 0 0");
  assert_dropped("M 9999999999 0 0");
}

void test_drops_null_and_too_long_line() {
  assert_dropped(nullptr);
  // 33 文字（IO_LINE_MAX を超える）
  assert_dropped("M 65535 -1800 -680 extra padding!!");
  char long_line[64];
  std::memset(long_line, 'M', sizeof(long_line) - 1);
  long_line[sizeof(long_line) - 1] = '\0';
  assert_dropped(long_line);
}

// --- C 行の組み立て ---

void test_format_c_line() {
  char buf[IO_LINE_MAX + 1];
  TEST_ASSERT_EQUAL_UINT(7, format_c_line(0, 0, 0, buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_STRING("C 0 0 0", buf);

  TEST_ASSERT_EQUAL_UINT(16, format_c_line(4294967295u, 1, 0, buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_STRING("C 4294967295 1 0", buf);

  TEST_ASSERT_EQUAL_UINT(11, format_c_line(123, 0, 150, buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_STRING("C 123 0 150", buf);
}

void test_format_c_line_reports_zero_when_buffer_is_short() {
  char buf[8];
  buf[0] = 'x';
  TEST_ASSERT_EQUAL_UINT(0, format_c_line(4294967295u, 1, 0, buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_STRING("", buf);
  TEST_ASSERT_EQUAL_UINT(0, format_c_line(0, 0, 0, nullptr, 0));
}

// --- B 行の組み立て ---

void test_format_b_line() {
  char buf[IO_LINE_MAX + 1];
  TEST_ASSERT_EQUAL_UINT(9, format_b_line(0, "0.1.0", buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_STRING("B 0 0.1.0", buf);
}

void test_format_b_line_rejects_unreadable_fw() {
  char buf[IO_LINE_MAX + 1];
  TEST_ASSERT_EQUAL_UINT(0, format_b_line(0, nullptr, buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_UINT(0, format_b_line(0, "", buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_UINT(0, format_b_line(0, "has space", buf, sizeof(buf)));
  // IO_LINE_MAX を超える（B 0 ＋ 29 文字で 33 文字）
  TEST_ASSERT_EQUAL_UINT(0, format_b_line(0, "12345678901234567890123456789", buf, sizeof(buf)));
  char small[8];
  TEST_ASSERT_EQUAL_UINT(0, format_b_line(0, "0.1.0", small, sizeof(small)));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_parse_valid_zero_command);
  RUN_TEST(test_parse_valid_command);
  RUN_TEST(test_parse_accepts_out_of_range_values_for_controller_to_clamp);

  RUN_TEST(test_drops_wrong_head_or_field_count);
  RUN_TEST(test_drops_non_integer_fields);
  RUN_TEST(test_drops_out_of_range_seq);
  RUN_TEST(test_drops_null_and_too_long_line);

  RUN_TEST(test_format_c_line);
  RUN_TEST(test_format_c_line_reports_zero_when_buffer_is_short);
  RUN_TEST(test_format_b_line);
  RUN_TEST(test_format_b_line_rejects_unreadable_fw);

  return UNITY_END();
}
