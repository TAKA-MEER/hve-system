// Arduino.h を include しない最小の関数。後のパケットでここに判定を書く（DetailedDesign.md §4.1）。
// 名前は DetailedDesign-names.md §1。
#pragma once

inline const char* lift_core_version() {
  return "0.1.0";
}
