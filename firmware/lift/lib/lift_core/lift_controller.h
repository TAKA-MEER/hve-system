// 受け付け・持ち主・天井・判定を偽 HAL のモータに効かせるところ。
// docs/plan/detailed/DetailedDesign.md §3・§4.1・§4.1.2。Arduino.h は include しない。
#pragma once

#include <cstdint>

#include "cmd_codec.h"  // HelloMsg・HoldMsg
#include "hal.h"
#include "lift_arbiter.h"
#include "lift_decide.h"
#include "lift_settings.h"
#include "speed_loop.h"

class LiftController {
 public:
  // hal: 差し替えるハードウェア。top_mm: 上端の閾値（LIFT_TOP_MM と同じ値。-1 は未設定）
  LiftController(LiftHal* hal, int top_mm);

  // hello を受ける。/ws/ui で受けたら偽（呼び出し側はその接続を閉じること。
  // DetailedDesign.md §3.1）。2 度目以降の hello は無視する（role・ceiling_sensor・名前は
  // 最初のものから変えない）。真のときだけ接続として覚える。
  // 同じ名前の別の接続が繋がっていたら、その接続番号を *replaced_conn_id に入れる
  // （無ければ -1。呼び出し側がその接続を閉じること。名前の無い接続は置き換えない。
  // DetailedDesign-protocol-modules.md §1.2）
  bool on_hello(const ConnId& conn, const HelloMsg& hello, int* replaced_conn_id = nullptr);

  // ceiling（距離計を持つ上部モジュールが止まっている間も送る値。protocol-modules §2.3）。
  // 値の出どころになれる接続（hello で ceiling_sensor: true を明示した control の接続）の
  // ものだけ覚える。それ以外は捨てる。report.received_at_ms は受け取った時刻
  void on_ceiling(const ConnId& conn, const CeilingReport& report);

  // hold を受ける。持ち主のものだけ覚え、それ以外は無視する
  void on_hold(const ConnId& conn, const HoldMsg& hold, uint32_t now_ms);

  // release を受ける。持ち主からのものだけ止める
  void on_release(const ConnId& conn, int press);

  // 接続が閉じた。持ち主ならその場で止める（OWNER_GONE）
  void on_close(int conn_id);

  // /ws/ui に繋いでいる画面の数（state に載せるだけ。LIFT_UI_CLIENTS_MAX 超は
  // 呼び出し側で閉じること）
  void set_ui_clients(int count);

  // モータへの出力の上限 [%]（Spec-ui §2.1）。tick が SpeedLoop::update に渡す。
  // 動いている最中に変えたら次の周期から効く。範囲外は LIFT_DUTY_ABS_MAX_PCT に丸める
  void set_motor_max_pct(int pct);

  // 単体の操作（昇降部の画面・物理ボタン）でも天井で止めるか（設定 ceiling_standalone。
  // protocol-modules §2.5）。起動時と PUT /api/settings の成功時に呼ぶ。
  // 動いている最中に変えたら次の周期から効く。上部モジュールからの命令には効かない
  void set_ceiling_standalone(bool enabled);

  // 設定をまとめて効かせる（set_motor_max_pct と set_ceiling_standalone）。
  // main.cpp は起動時と PUT の成功時に、設定の項目を 1 つずつでなくこれを呼ぶ
  void apply_settings(const LiftSettings& settings);

  // 判定して、その結果をモータへ出して、状態を更新する
  LiftState step(uint32_t now_ms);

  // 直近の step() が出した状態（state メッセージの中身）
  const LiftState& state() const;

 private:
  // 持ち主の命令の中身（LiftArbiter が「誰のものか」だけを持ち、ここが中身を持つ）
  struct OwnerCmd {
    LiftDir dir = LiftDir::STOP;
    int speed_mm_s = 0;
    bool has_sensor = true;
    CeilingReport ceiling;
  };

  // 接続ごとの hello の記録（role・ceiling_sensor は 1 度だけ受け付ける）
  struct ConnInfo {
    bool used = false;
    int id = 0;
    ConnKind kind = ConnKind::UI;
    bool hello_seen = false;
    bool has_sensor = true;
    bool sensor_explicit = false;  // ceiling_sensor を hello で明示したか
    ModuleRole role = ModuleRole::CONTROL;
    ModuleOperator op = ModuleOperator::HUMAN;
    char name[32] = {};  // 名前が無ければ anon-<接続番号>
    bool named = false;  // hello の名前が形に合っていたか（置き換えの対象か）
    char busy_label[49] = {};
    char ip[64] = {};
    char fw[16] = {};
    // 最後に受け取った天井の値（hold・ceiling のどちらから来たものでも）。
    // 出どころになれる接続だけが持つ
    bool has_ceiling = false;
    CeilingReport ceiling;
  };
  static constexpr int kMaxConns = LIFT_CONN_SLOTS;

  ConnInfo* find_or_add(const ConnId& conn);
  ConnInfo* find(int conn_id);
  const ConnInfo* find(int conn_id) const;
  bool sensor_of(int conn_id, ConnKind kind) const;
  // 天井の値の出どころになれる接続か（hello で ceiling_sensor: true を明示した control。
  // protocol-modules §2.3）
  static bool is_ceiling_source(const ConnInfo& info);
  // 受け取った天井の値を、その接続が出どころになれるときだけ覚える
  void remember_ceiling(ConnInfo* info, const CeilingReport& report);
  // 出どころの接続の中で、いちばん厳しい（止める・近い）値を選ぶ。無ければ nullptr
  const ConnInfo* pick_ceiling_source(uint32_t now_ms) const;
  // 単体の操作で使う出どころ。出どころの接続のうち**新しい**値（ceiling_is_stale でない）の中で
  // 近い方（止める値 → 近い値）。古いものは無視する。新しい値が 1 つも無ければ nullptr
  const ConnInfo* pick_fresh_ceiling_source(uint32_t now_ms) const;
  const ConnInfo* pick_source(uint32_t now_ms, bool fresh_only) const;
  // observe の接続からの命令か（捨てる）
  bool is_observer(int conn_id) const;

  LiftHal* hal_;
  int top_mm_;
  LiftArbiter arbiter_;
  OwnerCmd owner_cmd_;
  bool has_owner_cmd_ = false;  // 持ち主の命令の中身を持っているか
  StopReason idle_reason_ = StopReason::CMD_TIMEOUT;  // 持ち主がいないときの停止理由
  bool had_owner_ = false;
  // 上部モジュール（MODULE）の最後の hold に載っていた天井の値。持ち主がいないときの表示用
  // （判定には使わない）。無ければ has_last_module_ceiling_ が偽
  CeilingReport last_module_ceiling_;
  bool has_last_module_ceiling_ = false;
  char last_module_name_[32] = {};  // その hold を送ってきた接続の名前
  ConnInfo conns_[kMaxConns];
  int ui_clients_ = 0;
  uint32_t run_travel_um_;  // 現在の方向について、回していた間に目標の速さで進んだとみなした距離 [µm]
  int run_speed_mm_s_;      // 直前の step で決めた目標の速さ（回していた間の足し込みに使う。測った速さは使わない）
  LiftDir run_dir_;     // run_travel_um_ を数えている方向（変われば 0 に戻す）
  bool turning_;        // 直前の step でモータを回していたか
  uint32_t run_at_ms_;  // 積算の起点（直前の step の時刻）
  // 速さの制御（DetailedDesign.md §4.1.2）
  SpeedLoop speed_loop_;
  bool ceiling_standalone_ = LIFT_CEILING_STANDALONE_DEFAULT;  // set_ceiling_standalone で持たせる
  int motor_max_pct_ = LIFT_MOTOR_MAX_PCT_DEFAULT;  // モータへの出力の上限 [%]。set_motor_max_pct で持たせる
  int last_duty_ = 0;          // 直前にモータへ出したデューティ（#8a の「動かしている」判定用）
  uint32_t loop_at_ms_ = 0;    // SpeedLoop を最後に進めた時刻
  bool loop_started_ = false;  // SpeedLoop を進めたことがあるか
  LiftDir loop_dir_ = LiftDir::STOP;  // SpeedLoop が持っている向き
  int loop_owner_ = -1;               // SpeedLoop が持っている持ち主の接続番号
  int loop_press_ = 0;               // 同じく press
  // 速さの測定（窓の起点。LIFT_SPEED_WINDOW_MS ごとに取り直す）
  int32_t speed_win_count_ = 0;
  uint32_t speed_win_ms_ = 0;
  bool speed_win_valid_ = false;
  float speed_up_mm_s_ = 0.0f;  // 測った速さ（上向きが正）
  // #8a の数え（DetailedDesign.md §4.1）。持ち主が替わっても数え直さない
  bool stalled_ = false;
  LiftDir stall_dir_ = LiftDir::STOP;  // 数えている方向（替われば取り直す）
  int32_t stall_anchor_ = 0;           // 起点の数
  uint32_t stall_at_ms_ = 0;           // 起点の時刻
  bool stall_valid_ = false;
  LiftState state_;
};
