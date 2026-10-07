// 一定周期の割り込みで、半ステップの速さ [1 秒あたり] を刻みに直す（位相の足し算）。
// DetailedDesign.md §4.5 の StepRate。Timer2 の割り込み（IO_STEP_TICK_US ごと）から呼ぶ
// （DetailedDesign-hardware.md §2.4）。Arduino.h は include しない
// （DetailedDesign-names.md §1）。
#pragma once

#include <cstdint>

class StepRate {
 public:
  // tick_us: 割り込みの周期 [µs]（IO_STEP_TICK_US）
  explicit StepRate(uint32_t tick_us);

  // 速さを替える（符号が向き。0 で止める）。替えたら位相は 0 に戻す
  void set_hsps(int32_t hsps);

  // 割り込み 1 回ごとに呼ぶ。刻むとき true を返し、dir_step に +1（正転）・-1（逆転）を入れる
  bool tick(int* dir_step);

 private:
  uint32_t tick_us_;
  int32_t hsps_;
  int64_t phase_;  // 溜まった分 [半ステップ・µs / 秒]。1000000 で 1 刻み
};
