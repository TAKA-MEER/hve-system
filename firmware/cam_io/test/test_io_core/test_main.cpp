// Unity の試験（env:native）。判定の試験は WP-IO-01 から増える。
#include <unity.h>

#include <cstring>

#include "io_core_version.h"

void setUp() {}

void tearDown() {}

void test_io_core_version_returns_non_empty_string() {
  const char* version = io_core_version();
  TEST_ASSERT_NOT_NULL(version);
  TEST_ASSERT_TRUE(std::strlen(version) > 0);
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();
  RUN_TEST(test_io_core_version_returns_non_empty_string);
  return UNITY_END();
}
