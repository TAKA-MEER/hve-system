// 指令を受け、判定を 1 回やり、その結果をモータへ出して、状態を返すところ。
// DetailedDesign.md §4.1 の LiftController。
#pragma once

#include <cstdint>

#include "hal.h"
#include "lift_decide.h"

class LiftController {
 public:
  // hal: 差し替えるハードウェア。top_mm: 上端の閾値（LIFT_TOP_MM と同じ値。-1 は未設定）
  LiftController(LiftHal* hal, int top_mm);

  // カメラ部からの指令を受ける。壊れた指令も「停止」として受け取る（cmd_codec の責務）
  void on_command(const LiftCmd& cmd, uint32_t now_ms);

  // 判定して、その結果をモータへ出して、状態を更新する
  LiftState step(uint32_t now_ms);

  // 直近の step() が出した状態（state メッセージの中身）
  const LiftState& state() const;

 private:
  LiftHal* hal_;
  int top_mm_;
  bool has_cmd_;
  LiftCmd cmd_;
  uint32_t cmd_received_at_ms_;
  uint32_t run_ms_;     // 現在の指令方向について実際にモータを回した時間
  LiftDir run_dir_;     // run_ms_ を数えている方向（変われば 0 に戻す）
  bool turning_;        // 直前の step でモータを回していたか
  uint32_t run_at_ms_;  // 積算の起点（直前の step の時刻）
  LiftState state_;
};
