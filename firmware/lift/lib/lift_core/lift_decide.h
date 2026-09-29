// 昇降部の判定（純関数）。Arduino.h は include しない（DetailedDesign-names.md §1）。
// 規則は DetailedDesign.md §4.1 の表の順。定数の値は DetailedDesign-names.md §5。
#pragma once

#include <cstdint>

// 停止理由（DetailedDesign-names.md §3 の名前そのもの）
enum class StopReason : uint8_t {
  NONE = 0,
  CMD_STOP,
  CMD_TIMEOUT,
  CEILING,
  HEIGHT_UNKNOWN,
  TOP,
  BOTTOM,
  MAX_RUN,
};

// 方向（DetailedDesign-protocol.md §2.2 の値 up / down / stop）
enum class LiftDir : uint8_t {
  STOP = 0,
  UP = 1,
  DOWN = 2,
};

// 判定に使う定数（DetailedDesign-names.md §5。config.h ではなく判定の中腿上にある）
constexpr uint32_t LIFT_CMD_TIMEOUT_MS = 600;   // 指令が途絶えたら止まるまでの時間
constexpr uint32_t HEIGHT_STALE_MS = 600;       // 高さを「読めない」とみなすまでの時間（仮値）
constexpr int LIFT_TOP_MM = -1;                 // 上端の閾値。-1 は未設定（判定しない）
constexpr uint32_t LIFT_MAX_RUN_MS = 10000;     // 同じ方向へ動き続けてよい時間の上限（仮値）
constexpr int LIFT_DUTY_ABS_MAX_PCT = 100;      // デューティの絶対上限 [%]

// カメラ部からの指令（DetailedDesign-protocol.md §2.2）
// seq は「判定には使わない」ので持たない。
struct LiftCmd {
  LiftDir dir = LiftDir::STOP;
  int duty = 0;
  bool ceil_ok = false;
};

// 昇降部からカメラ部へ返す状態（DetailedDesign-protocol.md §2.3）
struct LiftState {
  int seq = 0;                    // 送出ごとの連番（WP-LIFT-02 で増やす）
  LiftDir dir = LiftDir::STOP;    // 指令ではなく、実際にモータへ出している方向
  int duty = 0;                   // 実際にモータへ出しているデューティ [%]
  StopReason reason = StopReason::NONE;
  bool bottom = false;
  int height_mm = 0;
  bool height_ok = false;         // 読み値が有効で、HEIGHT_STALE_MS 以内か
  int top_mm = LIFT_TOP_MM;       // 未設定なら -1（JSON では null）
  uint32_t cmd_age_ms = 0;
};

// 判定の入力。全部「外界から受け取った値」だけで、状態を持たない。
struct LiftDecideInput {
  bool has_cmd = false;           // 指令を一度も受けていないときは false
  LiftCmd cmd;
  uint32_t cmd_received_at_ms = 0;  // 最後の指令を受けた時刻
  uint32_t now_ms = 0;
  bool bottom_pressed = false;
  int height_mm = 0;
  bool height_ok = false;         // センサの読み値そのものの有効性（鮮度ではない）
  uint32_t height_at_ms = 0;      // その読み値を得た時刻
  int top_mm = LIFT_TOP_MM;       // 上端の閾値。-1 は未設定
  uint32_t run_ms = 0;            // 現在の指令の方向について実際にモータを回した時間
};

struct LiftDecideResult {
  LiftDir dir = LiftDir::STOP;
  int duty = 0;
  StopReason reason = StopReason::NONE;
};

// 経過時間（ms）を数える**唯一の**関数（DetailedDesign-names.md §1）。
//
// millis() は約 49.7 日で一周して 0 に戻るので、int64 に広げてから引くと、
// 一周をまたいだ直後に経過時間が巨大な負の値になり、ウォッチドッグも高さの
// 鮮度も効かなくなる（指令が途絶えても止まらない）。
//
// そのため uint32 のまま（2^32 を法として）引き算してから int32 に直す。
// こうすると一周をまたいでも正しい経過時間が出る。割り込みや WS のタスクが
// now より少し新しい時刻を書いた場合は小さな負の値になるので、誤停止しない。
int32_t elapsed_ms(uint32_t now_ms, uint32_t then_ms);

// 高さの読み値が HEIGHT_STALE_MS 以内か（= 上昇してよい高さか）。
// lift_decide の表 4 と LiftController の state.height_ok がここだけを共有する。
bool height_is_fresh(uint32_t now_ms, uint32_t height_at_ms);

// DetailedDesign.md §4.1 の表の順に、最初に当たったもので止める。
LiftDecideResult lift_decide(const LiftDecideInput& in);
