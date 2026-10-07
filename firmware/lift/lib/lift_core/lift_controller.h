// 受け付け・持ち主・天井・判定を偽 HAL のモータに効かせるところ。
// docs/plan/detailed/DetailedDesign.md §3・§4.1。Arduino.h は include しない。
#pragma once

#include <cstdint>

#include "cmd_codec.h"  // HelloMsg・HoldMsg
#include "hal.h"
#include "lift_arbiter.h"
#include "lift_decide.h"

class LiftController {
 public:
  // hal: 差し替えるハードウェア。top_mm: 上端の閾値（LIFT_TOP_MM と同じ値。-1 は未設定）
  LiftController(LiftHal* hal, int top_mm);

  // hello を受ける。/ws/ui で受けたら偽（呼び出し側はその接続を閉じること。
  // DetailedDesign.md §3.1）。2 度目以降の hello は無視する。
  // 真のときだけ接続として覚える
  bool on_hello(const ConnId& conn, const HelloMsg& hello);

  // hold を受ける。持ち主のものだけ覚え、それ以外は無視する
  void on_hold(const ConnId& conn, const HoldMsg& hold, uint32_t now_ms);

  // release を受ける。持ち主からのものだけ止める
  void on_release(const ConnId& conn, int press);

  // 接続が閉じた。持ち主ならその場で止める（OWNER_GONE）
  void on_close(int conn_id);

  // /ws/ui に繋いでいる画面の数（state に載せるだけ。LIFT_UI_CLIENTS_MAX 超は
  // 呼び出し側で閉じること）
  void set_ui_clients(int count);

  // 判定して、その結果をモータへ出して、状態を更新する
  LiftState step(uint32_t now_ms);

  // 直近の step() が出した状態（state メッセージの中身）
  const LiftState& state() const;

 private:
  // 持ち主の命令の中身（LiftArbiter が「誰のものか」だけを持ち、ここが中身を持つ）
  struct OwnerCmd {
    LiftDir dir = LiftDir::STOP;
    int duty = 0;
    bool has_sensor = true;
    CeilingReport ceiling;
  };

  // 接続ごとの hello の記録（ceiling_sensor は 1 度だけ受け付ける）
  struct ConnInfo {
    bool used = false;
    int id = 0;
    ConnKind kind = ConnKind::UI;
    bool hello_seen = false;
    bool has_sensor = true;
    char name[32] = {};
    char ip[64] = {};
  };
  static constexpr int kMaxConns = 8;

  ConnInfo* find_or_add(const ConnId& conn);
  ConnInfo* find(int conn_id);
  bool sensor_of(int conn_id, ConnKind kind) const;

  LiftHal* hal_;
  int top_mm_;
  LiftArbiter arbiter_;
  OwnerCmd owner_cmd_;
  bool has_owner_cmd_ = false;  // 持ち主の命令の中身を持っているか
  StopReason idle_reason_ = StopReason::CMD_TIMEOUT;  // 持ち主がいないときの停止理由
  bool had_owner_ = false;
  ConnInfo conns_[kMaxConns];
  int ui_clients_ = 0;
  uint32_t run_ms_;     // 現在の方向について実際にモータを回した時間
  LiftDir run_dir_;     // run_ms_ を数えている方向（変われば 0 に戻す）
  bool turning_;        // 直前の step でモータを回していたか
  uint32_t run_at_ms_;  // 積算の起点（直前の step の時刻）
  LiftState state_;
};
