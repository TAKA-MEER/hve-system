// LiftHal の実物（WP-LIFT-02）。MD10C・下端リミットスイッチ・HC-SR04 を
// Arduino 側で駆動する。DetailedDesign.md §4.1・DetailedDesign-hardware.md §1。
//
// ハードウェアに触らない計算（距離への換算・押されているか・デューティの PWM 化）は
// lib/hal_core にある。ここでは Arduino を呼ぶだけで、判定はしない。
#pragma once

#include <Arduino.h>

#include "hal.h"
#include "hal_core.h"

class LiftEsp32Hal : public LiftHal {
 public:
  // ピンを整えて起動する。**モータは必ず停止から始める**
  // （DetailedDesign-hardware.md §3: 電源が落ちるたびに再起動しても、そのたび止まる）。
  void begin();

  // 超音波の測定を出す。loop() から毎回呼ぶ（**loop を止めない**）。
  // 割り込みで測った ECHO の幅をここで読み取り、範囲外のときは無効にする。
  void poll(uint32_t now_ms);

  // --- LiftHal ---
  // 判定の結果（lift_decide を通したもの）をモータへ出す。指令をそのまま出さないこと
  void motor_set(LiftDir dir, int duty_pct) override;
  bool bottom_pressed() override;
  // 高さの読み値・その値の有効性・その値を得た時刻
  int height_mm() const override;
  bool height_ok() const override;
  uint32_t height_at_ms() const override;

 private:
  // TRIG を 10 us だけ叩いて測定を始める。1 回の測定のあいだは叩かない
  void trigger_sonar(uint32_t now_ms);
  // 割り込みが測った ECHO の幅から height_mm_ / height_ok_ / height_at_ms_ を更新する
  void apply_echo_width(uint32_t now_ms);
  // 反射が返らなかった（時間切れ・範囲外）ので「高さが読めない」にする
  void invalidate_height();

  // 直近 5 回の中央値（spec Spec-safety.md §2）。そろうまでは height_ok_ を立てない
  HeightFilter filter_;

  // --- loop() だけが触る値 ---
  int height_mm_ = 0;
  bool height_ok_ = false;
  uint32_t height_at_ms_ = 0;  // **有効な**値を得た時刻
  uint32_t next_sonar_ms_ = 0;
};
