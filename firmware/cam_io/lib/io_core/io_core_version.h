// Arduino.h を include しない最小の関数。後のパケットでここに判定を書く（DetailedDesign.md §4.5）。
// 名前は DetailedDesign-names.md §1。
#pragma once

inline const char* io_core_version() {
  return "0.1.0";
}
