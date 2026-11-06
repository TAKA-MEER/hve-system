// Unity の試験（env:native）。lift_settings の検証と JSON。
// 規則は docs/plan/detailed/DetailedDesign-protocol.md §3。
#include <unity.h>

#include <cstring>
#include <string>

#include "lift_settings.h"

namespace {

LiftSettings defaults() { return LiftSettings(); }

}  // namespace

void test_defaults_are_valid() {
  TEST_ASSERT_TRUE(validate_lift_settings(defaults()));
  TEST_ASSERT_EQUAL_INT(LIFT_MOTOR_MAX_PCT_DEFAULT, defaults().motor_max_pct);
}

void test_min_above_init_is_rejected() {
  LiftSettings settings = defaults();
  settings.up.min_mm_s = 50;
  settings.up.init_mm_s = 30;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
}

void test_init_above_max_is_rejected() {
  LiftSettings settings = defaults();
  settings.down.init_mm_s = 61;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
}

void test_min_above_max_is_rejected() {
  LiftSettings settings = defaults();
  settings.up.min_mm_s = 61;
  settings.up.max_mm_s = 60;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
}

void test_out_of_range_is_rejected() {
  LiftSettings settings = defaults();
  settings.up.min_mm_s = -1;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
  settings = defaults();
  settings.down.max_mm_s = LIFT_SPEED_ABS_MAX_MM_S + 1;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
}

void test_motor_max_pct_out_of_range_is_rejected() {
  LiftSettings settings = defaults();
  settings.motor_max_pct = LIFT_MOTOR_MAX_PCT_MIN - 1;  // 9
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
  settings = defaults();
  settings.motor_max_pct = LIFT_DUTY_ABS_MAX_PCT + 1;  // 101
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
  settings = defaults();
  settings.motor_max_pct = LIFT_MOTOR_MAX_PCT_MIN;
  TEST_ASSERT_TRUE(validate_lift_settings(settings));
  settings.motor_max_pct = LIFT_DUTY_ABS_MAX_PCT;
  TEST_ASSERT_TRUE(validate_lift_settings(settings));
}

void test_boundary_values_are_accepted() {
  LiftSettings settings;
  settings.up.min_mm_s = 0;
  settings.up.max_mm_s = LIFT_SPEED_ABS_MAX_MM_S;
  settings.up.init_mm_s = 0;
  settings.down.min_mm_s = 0;
  settings.down.max_mm_s = LIFT_SPEED_ABS_MAX_MM_S;
  settings.down.init_mm_s = 100;
  TEST_ASSERT_TRUE(validate_lift_settings(settings));
}

void test_from_json_round_trip() {
  const char* text = "{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
                     "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20},"
                     "\"motor_max_pct\":50}";
  LiftSettings settings;
  TEST_ASSERT_TRUE(lift_settings_from_json(text, &settings));
  TEST_ASSERT_EQUAL_INT(10, settings.up.min_mm_s);
  TEST_ASSERT_EQUAL_INT(60, settings.up.max_mm_s);
  TEST_ASSERT_EQUAL_INT(30, settings.up.init_mm_s);
  TEST_ASSERT_EQUAL_INT(5, settings.down.min_mm_s);
  TEST_ASSERT_EQUAL_INT(50, settings.down.max_mm_s);
  TEST_ASSERT_EQUAL_INT(20, settings.down.init_mm_s);
  TEST_ASSERT_EQUAL_INT(50, settings.motor_max_pct);

  char json[192];
  TEST_ASSERT_GREATER_THAN(0, lift_settings_to_json(settings, json, sizeof(json)));
  LiftSettings back;
  TEST_ASSERT_TRUE(lift_settings_from_json(json, &back));
  TEST_ASSERT_EQUAL_INT(10, back.up.min_mm_s);
  TEST_ASSERT_EQUAL_INT(20, back.down.init_mm_s);
  TEST_ASSERT_EQUAL_INT(50, back.motor_max_pct);
}

void test_from_json_rejects_missing_motor_max_pct() {
  LiftSettings settings;
  // motor_max_pct が無い古い形は通さない（全体が既定値になる。protocol §3）
  TEST_ASSERT_FALSE(
      lift_settings_from_json("{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
                              "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20}}",
                              &settings));
  // 9・101 は通さない
  TEST_ASSERT_FALSE(
      lift_settings_from_json("{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
                              "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20},"
                              "\"motor_max_pct\":9}",
                              &settings));
  TEST_ASSERT_FALSE(
      lift_settings_from_json("{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
                              "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20},"
                              "\"motor_max_pct\":101}",
                              &settings));
  // 整数でないものも通さない
  TEST_ASSERT_FALSE(
      lift_settings_from_json("{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
                              "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20},"
                              "\"motor_max_pct\":\"50\"}",
                              &settings));
  TEST_ASSERT_FALSE(
      lift_settings_from_json("{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
                              "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20},"
                              "\"motor_max_pct\":true}",
                              &settings));
}

void test_from_json_rejects_broken_input() {
  LiftSettings settings;
  TEST_ASSERT_FALSE(lift_settings_from_json("{", &settings));
  TEST_ASSERT_FALSE(lift_settings_from_json(nullptr, &settings));
  // 軸が欠けている
  TEST_ASSERT_FALSE(lift_settings_from_json("{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30}}",
                                            &settings));
  // 型が違う
  TEST_ASSERT_FALSE(
      lift_settings_from_json("{\"lift_up\":{\"min\":\"10\",\"max\":60,\"init\":30},"
                              "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20}}",
                              &settings));
  // 検証に通らない値は読めても受け付けない
  TEST_ASSERT_FALSE(
      lift_settings_from_json("{\"lift_up\":{\"min\":50,\"max\":60,\"init\":30},"
                              "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20}}",
                              &settings));
}

void test_to_json_reports_zero_when_buffer_is_short() {
  char json[8];
  TEST_ASSERT_EQUAL_INT(0, lift_settings_to_json(defaults(), json, sizeof(json)));
}

// --- ceiling_standalone（2026-11-06。protocol §3）---

namespace {
const char* kBodyWithoutStandalone =
    "{\"lift_up\":{\"min\":5,\"max\":50,\"init\":20},"
    "\"lift_down\":{\"min\":6,\"max\":55,\"init\":25},\"motor_max_pct\":40}";

std::string body_with_standalone(const char* value_json) {
  return std::string("{\"lift_up\":{\"min\":5,\"max\":50,\"init\":20},"
                     "\"lift_down\":{\"min\":6,\"max\":55,\"init\":25},"
                     "\"motor_max_pct\":40,\"ceiling_standalone\":") +
         value_json + "}";
}
}  // namespace

void test_ceiling_standalone_default_is_true() {
  TEST_ASSERT_TRUE(LIFT_CEILING_STANDALONE_DEFAULT);
  TEST_ASSERT_TRUE(LiftSettings().ceiling_standalone);
}

// 変異 8: 欠けた形（このファームより前に NVS に保存した形）で全体が既定値になる・false で読む
void test_saved_without_standalone_reads_true_and_keeps_other_items() {
  bool using_defaults = true;
  const LiftSettings s = lift_settings_from_saved(kBodyWithoutStandalone, &using_defaults);
  TEST_ASSERT_FALSE(using_defaults);
  TEST_ASSERT_TRUE(s.ceiling_standalone);
  TEST_ASSERT_EQUAL_INT(5, s.up.min_mm_s);
  TEST_ASSERT_EQUAL_INT(25, s.down.init_mm_s);
  TEST_ASSERT_EQUAL_INT(40, s.motor_max_pct);
}

void test_saved_false_is_kept_and_true_is_kept() {
  bool using_defaults = true;
  LiftSettings s = lift_settings_from_saved(body_with_standalone("false").c_str(), &using_defaults);
  TEST_ASSERT_FALSE(using_defaults);
  TEST_ASSERT_FALSE(s.ceiling_standalone);
  TEST_ASSERT_EQUAL_INT(40, s.motor_max_pct);
  s = lift_settings_from_saved(body_with_standalone("true").c_str(), &using_defaults);
  TEST_ASSERT_TRUE(s.ceiling_standalone);
}

void test_saved_missing_or_broken_uses_defaults() {
  bool using_defaults = false;
  LiftSettings s = lift_settings_from_saved(nullptr, &using_defaults);
  TEST_ASSERT_TRUE(using_defaults);
  TEST_ASSERT_TRUE(s.ceiling_standalone);
  using_defaults = false;
  lift_settings_from_saved("", &using_defaults);
  TEST_ASSERT_TRUE(using_defaults);
  using_defaults = false;
  s = lift_settings_from_saved("{broken", &using_defaults);
  TEST_ASSERT_TRUE(using_defaults);
  TEST_ASSERT_EQUAL_INT(LIFT_MOTOR_MAX_PCT_DEFAULT, s.motor_max_pct);
}

// 変異 9: ceiling_standalone の無い PUT で、保存してある false が true に戻る
void test_put_without_standalone_keeps_current_value() {
  LiftSettings current;
  current.ceiling_standalone = false;
  LiftSettings out;
  TEST_ASSERT_TRUE(lift_settings_from_put(kBodyWithoutStandalone, current, &out));
  TEST_ASSERT_FALSE(out.ceiling_standalone);
  TEST_ASSERT_EQUAL_INT(40, out.motor_max_pct);
  current.ceiling_standalone = true;
  TEST_ASSERT_TRUE(lift_settings_from_put(kBodyWithoutStandalone, current, &out));
  TEST_ASSERT_TRUE(out.ceiling_standalone);
}

void test_put_with_standalone_overrides_current_value() {
  LiftSettings current;
  current.ceiling_standalone = true;
  LiftSettings out;
  TEST_ASSERT_TRUE(lift_settings_from_put(body_with_standalone("false").c_str(), current, &out));
  TEST_ASSERT_FALSE(out.ceiling_standalone);
  current.ceiling_standalone = false;
  TEST_ASSERT_TRUE(lift_settings_from_put(body_with_standalone("true").c_str(), current, &out));
  TEST_ASSERT_TRUE(out.ceiling_standalone);
}

void test_standalone_that_is_not_boolean_is_rejected() {
  LiftSettings current;
  LiftSettings out;
  TEST_ASSERT_FALSE(lift_settings_from_put(body_with_standalone("\"yes\"").c_str(), current, &out));
  TEST_ASSERT_FALSE(lift_settings_from_put(body_with_standalone("1").c_str(), current, &out));
  TEST_ASSERT_FALSE(lift_settings_from_put(body_with_standalone("null").c_str(), current, &out));
  TEST_ASSERT_FALSE(lift_settings_from_put(body_with_standalone("{}").c_str(), current, &out));
  TEST_ASSERT_FALSE(lift_settings_from_json(body_with_standalone("\"false\"").c_str(), &out));
}

void test_to_json_writes_standalone_and_round_trips() {
  LiftSettings settings;
  settings.ceiling_standalone = false;
  char json[256];
  TEST_ASSERT_GREATER_THAN(0, lift_settings_to_json(settings, json, sizeof(json)));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"ceiling_standalone\":false"));
  LiftSettings back;
  TEST_ASSERT_TRUE(lift_settings_from_json(json, &back));
  TEST_ASSERT_FALSE(back.ceiling_standalone);
  settings.ceiling_standalone = true;
  lift_settings_to_json(settings, json, sizeof(json));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"ceiling_standalone\":true"));
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_defaults_are_valid);
  RUN_TEST(test_min_above_init_is_rejected);
  RUN_TEST(test_init_above_max_is_rejected);
  RUN_TEST(test_min_above_max_is_rejected);
  RUN_TEST(test_out_of_range_is_rejected);
  RUN_TEST(test_motor_max_pct_out_of_range_is_rejected);
  RUN_TEST(test_boundary_values_are_accepted);
  RUN_TEST(test_from_json_round_trip);
  RUN_TEST(test_from_json_rejects_missing_motor_max_pct);
  RUN_TEST(test_from_json_rejects_broken_input);
  RUN_TEST(test_to_json_reports_zero_when_buffer_is_short);
  RUN_TEST(test_ceiling_standalone_default_is_true);
  RUN_TEST(test_saved_without_standalone_reads_true_and_keeps_other_items);
  RUN_TEST(test_saved_false_is_kept_and_true_is_kept);
  RUN_TEST(test_saved_missing_or_broken_uses_defaults);
  RUN_TEST(test_put_without_standalone_keeps_current_value);
  RUN_TEST(test_put_with_standalone_overrides_current_value);
  RUN_TEST(test_standalone_that_is_not_boolean_is_rejected);
  RUN_TEST(test_to_json_writes_standalone_and_round_trips);

  return UNITY_END();
}
