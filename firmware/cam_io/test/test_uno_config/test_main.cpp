// src/config.h の UNO の設定の縛り（DetailedDesign-names.md §5.4・旧版の実機確認）。
// 値そのものではなく性質を縛る（隣り合うコイルを順に・中性点なし・1 本ずつ切り替え）。
// 旧版は隣り合わないコイルを混ぜた並びで唸るだけで回らなかった（2026-09-29 実機）。
// ホストで回すため Arduino.h を含まない config.h を相対で読む。
#include <unity.h>

#include "../../src/config.h"
#include "io_controller.h"

void test_half_step_sequence_has_eight_phases() {
  TEST_ASSERT_EQUAL_UINT(8, sizeof(YAW_HALF_STEP_SEQUENCE) / sizeof(YAW_HALF_STEP_SEQUENCE[0]));
}

void test_half_step_sequence_never_goes_through_neutral() {
  for (unsigned int i = 0; i < 8; ++i) {
    TEST_ASSERT_NOT_EQUAL_MESSAGE(0, YAW_HALF_STEP_SEQUENCE[i], "中性点は使わない");
  }
}

void test_half_step_sequence_energises_adjacent_coils_in_order() {
  // 偶数番目は 1 本、奇数番目は前後の隣り合う 2 本（IN1 → IN1+IN2 → IN2 → …）
  for (unsigned int pos = 0; pos < 8; ++pos) {
    const uint8_t pattern = YAW_HALF_STEP_SEQUENCE[pos];
    if (pos % 2 == 0) {
      TEST_ASSERT_EQUAL_UINT8_MESSAGE(static_cast<uint8_t>(1u << (pos / 2)), pattern,
                                      "偶数番目は 1 本のコイル");
    } else {
      const unsigned int first = (pos - 1) / 2;
      const unsigned int second = (first + 1) % 4;
      TEST_ASSERT_EQUAL_UINT8_MESSAGE(
          static_cast<uint8_t>((1u << first) | (1u << second)), pattern,
          "奇数番目は隣り合う 2 本のコイル");
    }
  }
}

void test_half_step_sequence_changes_one_coil_at_a_time() {
  // 隣の相へ進むたびに変わるコイルは 1 本だけ（一周して先頭へ戻るところも同じ）
  for (unsigned int pos = 0; pos < 8; ++pos) {
    const uint8_t now = YAW_HALF_STEP_SEQUENCE[pos];
    const uint8_t next = YAW_HALF_STEP_SEQUENCE[(pos + 1) % 8];
    unsigned int changed = 0;
    for (unsigned int bit = 0; bit < 4; ++bit) {
      if (((now ^ next) >> bit) & 1u) {
        ++changed;
      }
    }
    TEST_ASSERT_EQUAL_UINT_MESSAGE(1, changed, "切り替わるコイルは 1 本だけ");
  }
}

void test_yaw_pins_match_portd_upper_bits() {
  // hal_uno は D4〜D7 を PORTD の上位 4bit として直接叩く。下位ビットと順番がずれると誤配線
  TEST_ASSERT_EQUAL_UINT8(4, YAW_PINS[0]);
  TEST_ASSERT_EQUAL_UINT8(5, YAW_PINS[1]);
  TEST_ASSERT_EQUAL_UINT8(6, YAW_PINS[2]);
  TEST_ASSERT_EQUAL_UINT8(7, YAW_PINS[3]);
}

void test_servo_pulse_at_both_pitch_limits_stays_inside() {
  // ピッチの両端（PITCH_MIN_DEG〜MAX_DEG＝±60°）のパルスが MIN〜MAX の中に入る関係。
  // 仮値のどれかだけを変えて食い違うと、サーボに範囲外のパルスが出る
  const int lo = SERVO_PULSE_CENTER_US + (PITCH_MIN_DEG * SERVO_US_PER_DEG) / 10;
  const int hi = SERVO_PULSE_CENTER_US + (PITCH_MAX_DEG * SERVO_US_PER_DEG) / 10;
  TEST_ASSERT_TRUE_MESSAGE(lo >= SERVO_PULSE_MIN_US, "下端のパルスが MIN を割らない");
  TEST_ASSERT_TRUE_MESSAGE(hi <= SERVO_PULSE_MAX_US, "上端のパルスが MAX を超えない");
  TEST_ASSERT_TRUE_MESSAGE(SERVO_PULSE_MIN_US < SERVO_PULSE_CENTER_US, "MIN < 中央");
  TEST_ASSERT_TRUE_MESSAGE(SERVO_PULSE_CENTER_US < SERVO_PULSE_MAX_US, "中央 < MAX");
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_half_step_sequence_has_eight_phases);
  RUN_TEST(test_half_step_sequence_never_goes_through_neutral);
  RUN_TEST(test_half_step_sequence_energises_adjacent_coils_in_order);
  RUN_TEST(test_half_step_sequence_changes_one_coil_at_a_time);
  RUN_TEST(test_yaw_pins_match_portd_upper_bits);
  RUN_TEST(test_servo_pulse_at_both_pitch_limits_stays_inside);
  return UNITY_END();
}
