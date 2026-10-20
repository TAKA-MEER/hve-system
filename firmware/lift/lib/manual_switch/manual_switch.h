// 組み立て用ファーム（env:esp32dev_manual。台帳 W-2）の判定。Arduino.h は include しない
// ので env:native で試験できる。ピンを読む・モータへ出す側は src/manual/main.cpp。
//
// <on>-off-<on> のモーメンタリスイッチを読み、モータの向きを決めるだけ。
// **安全の判定（下端・ウォッチドッグ・連続駆動の上限・上端）は持たない**（W-2）。
// 持つのは配線の誤りで両側が同時に入ったときの停止と、反転のときに一度止める間だけ
// （MD10C とギアに逆向きの突入をかけないため。安全装置ではない）。
#pragma once

#include <cstdint>

enum class ManualDir : uint8_t { STOP, UP, DOWN };

// 動き始めるまでに、同じ側が入り続けていなければならない時間 [ms]（チャタリング除け）
constexpr uint32_t MANUAL_DEBOUNCE_MS = 30;
// 止めてから次に動き出せるまでの間 [ms]。反転は必ずこの間の停止を挟む
constexpr uint32_t MANUAL_REVERSE_PAUSE_MS = 150;

// スイッチの 2 つの入力の電圧（INPUT_PULLUP・共通端子は GND。入っている側が LOW = 0）
// から、いま求められている向き。**両方 LOW（配線の誤り）は停止**。
ManualDir manual_dir_from_levels(int up_level, int down_level);

// 毎回の loop() で入力の電圧を渡し、モータへ出す向きを受け取る。
// - 戻すとき（スイッチを離す・両方 LOW）はすぐ停止
// - 動き出すのは、同じ側が MANUAL_DEBOUNCE_MS 入り続け、かつ最後の停止から
//   MANUAL_REVERSE_PAUSE_MS たってから
// - 動いている最中に反対側が求められたら、いったん停止にする
// 起動直後は停止から始まり、起動から MANUAL_REVERSE_PAUSE_MS は動かない。
class ManualSwitch {
 public:
  explicit ManualSwitch(uint32_t now_ms = 0) : stopped_at_ms_(now_ms) {}

  ManualDir update(int up_level, int down_level, uint32_t now_ms);
  ManualDir output() const { return output_; }

 private:
  ManualDir output_ = ManualDir::STOP;
  ManualDir candidate_ = ManualDir::STOP;
  uint32_t candidate_since_ms_ = 0;
  uint32_t stopped_at_ms_ = 0;
};
