// 昇降部の判定（純関数）。Arduino.h は include しない（docs/plan/detailed/DetailedDesign-names.md §1）。
// 規則は docs/plan/detailed/DetailedDesign.md §4.1 の表の順。定数の値は DetailedDesign-names.md §5.1。
#pragma once

#include <cstdint>

#include "ceiling_check.h"  // StopReason・CeilingReport・CEILING_*

// 方向（DetailedDesign-protocol.md §2.1 の値 up / down / stop）
enum class LiftDir : uint8_t {
  STOP = 0,
  UP = 1,
  DOWN = 2,
};

// 判定に使う定数（DetailedDesign-names.md §5.1）
constexpr uint32_t LIFT_CMD_TIMEOUT_MS = 600;  // 持ち主の hold が途絶えたら止まるまでの時間
constexpr uint32_t HEIGHT_STALE_MS = 600;      // 高さを「読めない」とみなすまでの時間（仮値）
constexpr int LIFT_TOP_MM = -1;                // 上端の閾値。-1 は未設定（判定しない）
// WAIVER(demo): W-1 上端の検知を一時無効（false の間は判定 #4・#6 を行わない）
constexpr bool LIFT_TOP_DETECT_ENABLED = false;
constexpr uint32_t LIFT_MAX_RUN_MS = 10000;     // 同じ方向へ動き続けてよい時間の上限（仮値）
constexpr int LIFT_DUTY_ABS_MAX_PCT = 100;      // デューティの絶対上限 [%]

// 昇降部からカメラ部へ返す状態（DetailedDesign-protocol.md §2.2）
struct LiftState {
  int seq = 0;                  // 送出ごとの連番
  LiftDir dir = LiftDir::STOP;  // 指令ではなく、実際にモータへ出している方向
  int duty = 0;                 // 実際にモータへ出しているデューティ [%]
  StopReason reason = StopReason::NONE;
  bool bottom = false;
  int height_mm = 0;
  bool height_ok = false;  // 読み値が有効で、HEIGHT_STALE_MS 以内か（表示用）
  bool top_detect = false;  // 上端の検知が有効か（W-1 の間は常に false）
  // 持ち主の最後の hold に載っていた天井の値（古さは受け取ってからの経過を足したもの）
  bool ceiling_used = false;  // 持ち主の命令に天井の値を使っているか
  bool ceiling_present = false;  // 天井の値があるか（無いとき state の ceiling は null）
  CeilingStatus ceiling_status = CeilingStatus::MISSING;
  int ceiling_mm = 0;
  uint32_t ceiling_age_ms = 0;
  bool ceiling_ok = false;  // ceiling_check() の結果
  StopReason ceiling_reason = StopReason::CEILING_STALE;
  // 持ち主（いなければ has_owner が偽。種類は lift_arbiter.h の ConnKind）
  bool has_owner = false;
  uint8_t owner_kind = 0;  // 0 = UI（画面）・1 = MODULE（上部モジュール）
  int ui_clients = 0;      // /ws/ui に繋いでいる画面の数
  // 上部モジュールの接続
  bool module_connected = false;
  bool module_has_sensor = true;
  char module_ip[64] = {};
  char module_name[32] = {};
  uint32_t cmd_age_ms = 0;
};

// 判定の入力。全部「外界から受け取った値」だけで、状態を持たない。
struct LiftDecideInput {
  bool has_cmd = false;  // 持ち主がいるか（いないときは owner_empty_reason で止める）
  StopReason owner_empty_reason = StopReason::CMD_TIMEOUT;  // 持ち主がいないときの停止理由
  LiftDir dir = LiftDir::STOP;  // 持ち主の命令の方向
  int duty = 0;                 // 持ち主の命令のデューティ
  CeilingReport ceiling;        // 持ち主の最後の hold に載っていた天井の値
  bool connection_has_sensor = true;  // 持ち主の接続が距離計を持つか
  uint32_t now_ms = 0;
  bool bottom_pressed = false;
  int height_mm = 0;
  bool height_ok = false;         // センサの読み値そのものの有効性（鮮度ではない）
  uint32_t height_at_ms = 0;      // その読み値を得た時刻
  int top_mm = LIFT_TOP_MM;       // 上端の閾値。-1 は未設定
  uint32_t run_ms = 0;            // 現在の方向について実際にモータを回した時間
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
