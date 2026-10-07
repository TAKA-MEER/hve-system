// ヨー・ピッチのハードウェアの抽象。Arduino.h は include しない（DetailedDesign-names.md §1）。
// 実物は firmware/cam_io/src/hal_uno（WP-IO-02）。試験では偽物に差し替える。
// 使い分けは DetailedDesign.md §4.5・DetailedDesign-hardware.md §2.4:
// 止めている間・指令が途絶えたときはコイルの電流を切る。サーボのパルスは止めない。
#pragma once

#include <cstdint>

class IoHal {
 public:
  virtual ~IoHal() {}

  // ヨーの半ステップの速さ [1 秒あたり] を出す（符号が向き。IoController が丸め済み）。
  // 0 で止める指示には使わない（止めるときは yaw_coils_off）。
  virtual void yaw_set_hsps(int32_t yaw_hsps) = 0;

  // ヨーのコイルの電流を切る（止めている間・指令が途絶えたとき）
  virtual void yaw_coils_off() = 0;

  // ピッチの目標角 [0.1°] のパルスを出す。指令が途絶えても呼び続け、その角度で保つ
  virtual void servo_set_ddeg(int pitch_ddeg) = 0;
};
