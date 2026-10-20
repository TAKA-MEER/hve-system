// 操作の持ち主（最後の操作が勝つ）。Arduino.h は include しない
// （docs/plan/detailed/DetailedDesign-names.md §1）。
// 規則は docs/plan/detailed/DetailedDesign.md §3.3。
#pragma once

#include <cstdint>

#include "lift_decide.h"  // LIFT_CMD_TIMEOUT_MS・elapsed_ms

// 接続の種類（DetailedDesign.md §3.1）。/ws/ui = 画面・/ws/module = 上部モジュール。
enum class ConnKind : uint8_t {
  UI = 0,
  MODULE = 1,
};

// 接続の識別子（DetailedDesign-names.md §1）。id は WS の接続ごとの番号。
struct ConnId {
  int id = 0;
  ConnKind kind = ConnKind::UI;
};

// 持ち主の規則（DetailedDesign.md §3.3）をまとめた純ロジック。
// タイムアウト（LIFT_CMD_TIMEOUT_MS）の時計もここが持つ。呼び出し側は step ごとに
// tick(now) を呼ぶこと。
class LiftArbiter {
 public:
  LiftArbiter();

  // hold を受け取る。その接続の新しい押し始めなら持ち主を替え、
  // 持ち主の押し続けなら受け取った時刻を更新する。持ち主の hold として
  // 受け付けたら true（呼び出し側は命令の中身を覚えてよい）。
  bool on_hold(const ConnId& conn, int press, uint32_t now_ms);

  // release を受け取る。持ち主からなら持ち主を空にして true。
  bool on_release(const ConnId& conn, int press);

  // 接続が閉じた。その場で持ち主なら空にする（DetailedDesign-protocol.md §1）。
  void on_close(int conn_id);

  // 持ち主の hold が途絶えていたら持ち主を空にする。step ごとに呼ぶ。
  void tick(uint32_t now_ms);

  // 持ち主がいればその接続と press を出して true。いなければ false。
  bool owner(ConnId* out_conn, int* out_press) const;

 private:
  // WTO: 接続ごとの「前に見た press」。止まったあとに遅れて届いた古い hold が
  // 持ち主にならないようにする（§3.3）。接続は画面が数個なので固定長の表で足りる。
  static constexpr int kMaxConns = 8;
  struct Seen {
    bool used = false;
    int id = 0;
    int32_t last_press = INT32_MIN;
  };
  Seen seen_[kMaxConns];

  bool has_owner_ = false;
  ConnId owner_;
  int owner_press_ = 0;
  uint32_t owner_at_ms_ = 0;  // 持ち主の最後の hold を受け取った時刻

  Seen* find_or_add(int conn_id);
};
