// LiftHal のうちハードウェアに触らない計算だけ（DetailedDesign.md §4.1・DD-2）。
// Arduino.h は include しない（DetailedDesign-names.md §1）ので env:native で試験できる。
// Arduino を呼ぶ側（ピンの読み書き・LEDC・PCNT）は firmware/lift/src/hal_esp32。
// 定数の値と出所は DetailedDesign-names.md §5。
#pragma once

#include <cstdint>

#include "lift_decide.h"  // LIFT_DUTY_ABS_MAX_PCT（デューティの上限は判定と同じものを使う）

// --- 高さのエンコーダ E6A2-CW3C（DetailedDesign-hardware.md §1.1）---

// 上昇で数が増える向き。実機で確かめるまで仮（+1）。
constexpr int ENC_UP_SIGN = +1;  // 仮
// 1 カウントあたりの上昇 [µm]。設計値（エンコーダ 1 周 37.5 mm ÷ (4 × ENC_PPR 360)
// ＝ 26.04 µm。DetailedDesign-hardware.md §1.1。整数に丸めたずれ 0.16 %）。
// WP-MEAS-01 で全行程を測って直す。
constexpr int32_t LIFT_UM_PER_COUNT = 26;

// #8a（ENCODER_STALL）で止めずに済む進行方向への最小の進み [µm]。
// 0.8 mm。カウントは LIFT_UM_PER_COUNT で割って出す（26 µm なら 31）。
constexpr int32_t LIFT_STALL_MIN_UM = 800;
// 切り上げる: 800 µm に満たない進みで止める側に倒す。切り捨てると、たとえば
// 30 カウント（780 µm）でも「進んだ」と数えて下限を割る。
constexpr int32_t LIFT_STALL_MIN_COUNTS =
    (LIFT_STALL_MIN_UM + LIFT_UM_PER_COUNT - 1) / LIFT_UM_PER_COUNT;
// 下端スイッチが押されたまま上昇側へ進んでよい余裕 [mm]。仮（P-14）。
// 超えたらスイッチの故障とみなす（DetailedDesign.md §4.1.1）。
constexpr int HOME_SLACK_MM = 20;  // 仮（P-14）

// PCNT の A/B 4 逓倍の数え方（DetailedDesign-hardware.md §1.1）。
// 値は driver/pcnt.h と同じ（hal_esp32.cpp で static_assert で一致を確かめる）。
// hal_core は Arduino.h を include しないので設定値をここに持ち、実物側は
// ここから取る。符号の判断を native で縛るため（DD-2）。
constexpr int kPcntInc = 1;      // PCNT_COUNT_INC と同じ
constexpr int kPcntDec = 2;      // PCNT_COUNT_DEC と同じ
constexpr int kPcntKeep = 0;     // PCNT_MODE_KEEP と同じ
constexpr int kPcntReverse = 1;  // PCNT_MODE_REVERSE と同じ

// A/B の 4 逓倍のチャンネル設定。ch0: パルス=A・制御=B、
// ch1: パルス=B・制御=A（ch0 と逆の符号。ESP-IDF の rotary_encoder の例と同じ）。
// ch1 を ch0 と同じ符号にすると、どちらに回しても合計が 0 になる。
struct PcntQuadConfig {
  int ch0_pos;
  int ch0_neg;
  int ch0_lctrl;
  int ch0_hctrl;
  int ch1_pos;
  int ch1_neg;
  int ch1_lctrl;
  int ch1_hctrl;
};

constexpr PcntQuadConfig kPcntQuadEncoder = {
    kPcntInc, kPcntDec, kPcntReverse, kPcntKeep,
    kPcntDec, kPcntInc, kPcntReverse, kPcntKeep,
};

// (A, B) の 1 段の遷移（prev → now。各 0/1）で PCNT の合計がどう変わるかを返す。
// パルス側のエッジ（立ち上がり/立ち下がり）で、そのときの制御側のレベルに応じた
// lctrl/hctrl で pos/neg を反転させるか決め、ch0 と ch1 の両方を足す。
int pcnt_quad_step(const PcntQuadConfig& cfg, int prev_a, int prev_b, int now_a, int now_b);

// 溢れの累積と今のカウンタから合計の数を求める
//（hal_esp32 が上限・下限の割り込みで溢れの累積へ移す。カウンタはハードウェアが 0 に戻す）。
inline int32_t encoder_total(int32_t overflow, int16_t raw) { return overflow + raw; }

// 前回の合計から limit / 2 を超えて飛んだら、ハードウェアが 0 に戻した直後で
// まだ割り込みが溢れの累積に足していない途中とみなし、その回は前回の合計の
// ままにする（次の回に割り込みが足した値で読み直す）。poll の間に本物の動きで
// limit / 2 進むことは無い。
inline int32_t encoder_total_guarded(int32_t prev_total, int32_t overflow, int16_t raw,
                                     int32_t limit) {
  const int32_t total = encoder_total(overflow, raw);
  int32_t jump = total - prev_total;
  if (jump < 0) {
    jump = -jump;
  }
  if (jump > limit / 2) {
    return prev_total;
  }
  return total;
}

// エンコーダの数と下端スイッチから高さと「読めるか」を決める
// （DetailedDesign.md §4.1.1。判断はすべてここに置き、hal_esp32 は読んで渡すだけ）。
class EncoderHeight {
 public:
  // エンコーダの累積の数（原点に依らない）と下端スイッチの状態を渡す。
  // up_sign は上昇で数が増える向き（+1 / -1。実機では ENC_UP_SIGN）。
  void update(int32_t count, bool bottom_pressed, int up_sign = ENC_UP_SIGN);
  bool ok() const { return has_home_ && !fault_; }
  // 原点を取ったあとの高さ [mm]。原点より下（ずれ）は負のまま出す。丸めない。
  int height_mm() const;

 private:
  bool has_home_ = false;      // 下端に一度でも着いたか
  bool prev_pressed_ = false;  // 前回押されていたか（押された瞬間を見るため）
  bool fault_ = false;         // 押されたまま HOME_SLACK_MM を超えた（スイッチの故障）
  int32_t home_count_ = 0;     // 原点の数
  int32_t last_count_ = 0;     // 最後に渡された数
  int last_sign_ = ENC_UP_SIGN;
};

// --- 下端リミットスイッチ ---

// 押されているときのピンのレベル。**常時閉（NC）配線**なので押すと開いて HIGH になり、
// 線が切れたときも同じ HIGH になる（断線が「下降しない」側に倒れる。
// DetailedDesign-hardware.md §1）。0 にすると押されたら LOW の配線になる。
constexpr int BOTTOM_PRESSED_LEVEL = 1;  // HIGH

// ピンのレベル（Arduino の HIGH = 1 / LOW = 0）から下端スイッチが押されているかを返す。
// BOTTOM_PRESSED_LEVEL のときだけ true。
bool bottom_pressed_from_level(int level);

// デューティ [%] を LEDC に書く値（0〜(1 << resolution) - 1）にする。
// 先に 0〜LIFT_DUTY_ABS_MAX_PCT に丸める。**停止（0%）は必ず 0 になる**。
uint32_t motor_duty_to_pwm(int duty_pct, int resolution);
