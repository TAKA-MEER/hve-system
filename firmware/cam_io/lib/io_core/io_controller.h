// 指令を受け、ウォッチドッグと丸めをして、その結果をハードウェアへ出すところ。
// DetailedDesign.md §4.5 の IoController。ハードウェアは io_hal.h の抽象を通す。
// 途絶えたときはヨーを止めてコイルの電流を切り、ピッチはパルスを止めずその角度で保つ
// （DetailedDesign.md §3.4・DetailedDesign-hardware.md §2.4）。
#pragma once

#include <cstdint>

#include "io_codec.h"
#include "io_hal.h"

// 指令が途絶えたとみなすまでの時間 [ms]（仮。io_cmd_period_ms の 6 倍）
constexpr uint32_t IO_CMD_TIMEOUT_MS = 300;
// ヨーの速さの上限 [半ステップ毎秒]（axis_speed_abs_max_dps と揃える。60 deg/s 相当）
constexpr int32_t YAW_HSPS_ABS_MAX = 680;
// ピッチの可動範囲 [°]（仮。UnitV2 側の ±45 より広い 2 つ目の守り）
constexpr int PITCH_MIN_DEG = -60;
constexpr int PITCH_MAX_DEG = 60;
// 起動時・再起動時に向く角度 [°]（正面・水平。サーボの性質で一気に動く）
constexpr int PITCH_INITIAL_DEG = 0;

class IoController {
 public:
  explicit IoController(IoHal* hal);

  // UnitV2 からの指令を受ける。読めない行はここへ渡さない（捨てると途絶えて止まる）
  void on_command(const IoCmd& cmd, uint32_t now_ms);

  // 判定して、その結果をハードウェアへ出す。指令をそのまま流さないこと
  void tick(uint32_t now_ms);

 private:
  IoHal* hal_;
  bool has_cmd_;
  int pitch_ddeg_;      // 丸め済みの目標角 [0.1°]
  int32_t yaw_hsps_;    // 丸め済みの速さ [半ステップ毎秒]
  uint32_t cmd_at_ms_;  // 最後の指令を受けた時刻
};
