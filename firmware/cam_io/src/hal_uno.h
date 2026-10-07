// 28BYJ-48・MG996R・SRF02 の実物（DetailedDesign.md §4.5）。
// io_hal.h の実装に加え、Wire での SRF02 の読み取りを持つ。
// 決まりごとは DetailedDesign-hardware.md §2.4（止めている間はコイルの電流を切る・
// サーボのパルスは止めない・I2C に時間切れを付ける）。
// クラス名はファイル名（DetailedDesign-names.md §1）から機械的に対応付けたもの。
#pragma once

#include <Servo.h>
#include <stdint.h>

#include "io_hal.h"
#include "step_rate.h"

class HalUno : public IoHal {
 public:
  HalUno();

  // D4〜D7・D9・Wire・Timer2 を始める。Serial は main.cpp の役なので触らない
  void begin();

  // IoHal（IoController が丸め済みの値を出す。判定はしない）。
  // IoController は動いている間も毎回呼ぶので、同じ速さの再設定では
  // StepRate の位相を戻さない（戻すと刻みが進まなくなる）。
  void yaw_set_hsps(int32_t yaw_hsps) override;
  void yaw_coils_off() override;
  // 0.1° をパルス幅に直す。SERVO_PULSE_MIN_US〜MAX_US の外へ出さない
  void servo_set_ddeg(int pitch_ddeg) override;

  // SRF02 を SRF02_PERIOD_MS ごとに読む。新しい測定ができたら true を返し、
  // st（0＝読めた・1＝I2C で読めない）・cm（生の値。st が 1 なら 0）を入れる。
  // 測定の合間は false（st・cm を触らない）。I2C が固まっても戻る。
  bool srf02_poll(uint32_t now_ms, int* st, int* cm);

  // Timer2 の割り込み（TIMER2_COMPA_vect）から呼ぶ。割り込み内なので、
  // loop 側は触る値を割り込み禁止区間で守る（AVR は 32bit の読み書きが 1 命令でない）
  void isr_step();

 private:
  StepRate rate_;
  Servo servo_;
  int32_t last_hsps_;  // loop 側だけが触る（今出している速さ。替わったときだけ位相を戻す）
  uint8_t phase_;      // 割り込み側だけが触る（今の半ステップの相 0〜7）
  bool srf02_ranging_;
  bool srf02_cmd_ok_;
  uint32_t srf02_at_ms_;
};
