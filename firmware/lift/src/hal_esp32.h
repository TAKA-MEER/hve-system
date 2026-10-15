// LiftHal の実物（WP-LIFT-05）。MD10C・下端リミットスイッチ・高さのエンコーダを
// Arduino 側で駆動する。DetailedDesign.md §4.1・DetailedDesign-hardware.md §1・§1.1。
//
// ハードウェアに触らない計算（原点・高さへの換算・押されているか・デューティの PWM 化）は
// lib/hal_core にある。ここでは読んで渡すだけで、判定はしない。
#pragma once

#include <Arduino.h>

#include "hal.h"
#include "hal_core.h"

class LiftEsp32Hal : public LiftHal {
 public:
  // ピンを整えて起動する。**モータは必ず停止から始める**
  // （DetailedDesign-hardware.md §3: 電源が落ちるたびに再起動しても、そのたび止まる）。
  void begin();

  // エンコーダの数を読み、高さを更新する。loop() から毎回呼ぶ（**loop を止めない**）。
  // PCNT の生の値を読んで差を足し込み、下端スイッチの状態と一緒に EncoderHeight へ渡す。
  void poll(uint32_t now_ms);

  // --- LiftHal ---
  // 判定の結果（lift_decide を通したもの）をモータへ出す。指令をそのまま出さないこと
  void motor_set(LiftDir dir, int duty_pct) override;
  bool bottom_pressed() override;
  // エンコーダの生の数（原点に依らない。WP-LIFT-06 の速さの制御が使う）
  int32_t encoder_count() const override;
  // 高さの読み値・その値の有効性・その値を得た時刻
  int height_mm() const override;
  bool height_ok() const override;
  uint32_t height_at_ms() const override;

 private:
  EncoderHeight encoder_;
  // 溢れの累積 + 今のカウンタの合計（原点に依らない）
  int32_t total_count_ = 0;
  bool pcnt_ready_ = false;

  // --- loop() だけが触る値 ---
  int height_mm_ = 0;
  bool height_ok_ = false;
  uint32_t height_at_ms_ = 0;  // **渡した時刻（毎回進む）**
};
