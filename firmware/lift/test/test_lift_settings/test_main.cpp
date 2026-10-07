// Unity の試験（env:native）。lift_settings の検証と JSON。
// 規則は docs/plan/detailed/DetailedDesign-protocol.md §3。
#include <unity.h>

#include "lift_settings.h"

namespace {

LiftSettings defaults() { return LiftSettings(); }

}  // namespace

void test_defaults_are_valid() { TEST_ASSERT_TRUE(validate_lift_settings(defaults())); }

void test_min_above_init_is_rejected() {
  LiftSettings settings = defaults();
  settings.up.min_pct = 50;
  settings.up.init_pct = 30;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
}

void test_init_above_max_is_rejected() {
  LiftSettings settings = defaults();
  settings.down.init_pct = 61;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
}

void test_min_above_max_is_rejected() {
  LiftSettings settings = defaults();
  settings.up.min_pct = 61;
  settings.up.max_pct = 60;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
}

void test_out_of_range_is_rejected() {
  LiftSettings settings = defaults();
  settings.up.min_pct = -1;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
  settings = defaults();
  settings.down.max_pct = 101;
  TEST_ASSERT_FALSE(validate_lift_settings(settings));
}

void test_boundary_values_are_accepted() {
  LiftSettings settings;
  settings.up.min_pct = 0;
  settings.up.max_pct = 100;
  settings.up.init_pct = 0;
  settings.down.min_pct = 0;
  settings.down.max_pct = 100;
  settings.down.init_pct = 100;
  TEST_ASSERT_TRUE(validate_lift_settings(settings));
}

void test_from_json_round_trip() {
  const char* text = "{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
                     "\"lift_down\":{\"min\":5,\"max\":50,\"init\":20}}";
  LiftSettings settings;
  TEST_ASSERT_TRUE(lift_settings_from_json(text, &settings));
  TEST_ASSERT_EQUAL_INT(10, settings.up.min_pct);
  TEST_ASSERT_EQUAL_INT(60, settings.up.max_pct);
  TEST_ASSERT_EQUAL_INT(30, settings.up.init_pct);
  TEST_ASSERT_EQUAL_INT(5, settings.down.min_pct);
  TEST_ASSERT_EQUAL_INT(50, settings.down.max_pct);
  TEST_ASSERT_EQUAL_INT(20, settings.down.init_pct);

  char json[160];
  TEST_ASSERT_GREATER_THAN(0, lift_settings_to_json(settings, json, sizeof(json)));
  LiftSettings back;
  TEST_ASSERT_TRUE(lift_settings_from_json(json, &back));
  TEST_ASSERT_EQUAL_INT(10, back.up.min_pct);
  TEST_ASSERT_EQUAL_INT(20, back.down.init_pct);
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

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_defaults_are_valid);
  RUN_TEST(test_min_above_init_is_rejected);
  RUN_TEST(test_init_above_max_is_rejected);
  RUN_TEST(test_min_above_max_is_rejected);
  RUN_TEST(test_out_of_range_is_rejected);
  RUN_TEST(test_boundary_values_are_accepted);
  RUN_TEST(test_from_json_round_trip);
  RUN_TEST(test_from_json_rejects_broken_input);
  RUN_TEST(test_to_json_reports_zero_when_buffer_is_short);

  return UNITY_END();
}
