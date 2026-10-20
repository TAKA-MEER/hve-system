// 持ち主の規則そのもの。docs/plan/detailed/DetailedDesign.md §3.3 の表。
#include "lift_arbiter.h"

#include <cstdint>

LiftArbiter::LiftArbiter() : seen_(), has_owner_(false), owner_(), owner_press_(0), owner_at_ms_(0) {}

LiftArbiter::Seen* LiftArbiter::find_or_add(int conn_id) {
  Seen* free_slot = nullptr;
  for (int i = 0; i < kMaxConns; ++i) {
    if (seen_[i].used && seen_[i].id == conn_id) {
      return &seen_[i];
    }
    if (!seen_[i].used && free_slot == nullptr) {
      free_slot = &seen_[i];
    }
  }
  if (free_slot == nullptr) {
    return nullptr;  // 置き場が無い接続の hold は受け付けない（安全側＝止める）
  }
  free_slot->used = true;
  free_slot->id = conn_id;
  free_slot->last_press = INT32_MIN;
  return free_slot;
}

bool LiftArbiter::on_hold(const ConnId& conn, int press, uint32_t now_ms) {
  Seen* seen = find_or_add(conn.id);
  if (seen == nullptr) {
    return false;
  }
  if (press < seen->last_press) {
    // 持ち主でない接続の古い press（取って代わられた側が押し続けているだけ）。
    // 持ち主が空のときも、前に見た press 以下では持ち主になれない
    // （止まったあとに遅れて届いても動き出さない）
    return false;
  }
  if (press > seen->last_press) {
    // その接続で新しい押し始め。最後の操作が勝つので持ち主を替える
    seen->last_press = press;
    has_owner_ = true;
    owner_ = conn;
    owner_press_ = press;
    owner_at_ms_ = now_ms;
    return true;
  }
  // press == last_press: 押し続け。持ち主のものだけ受け取った時刻を更新する
  if (has_owner_ && owner_.id == conn.id && owner_press_ == press) {
    owner_at_ms_ = now_ms;
    return true;
  }
  return false;
}

bool LiftArbiter::on_release(const ConnId& conn, int press) {
  // 持ち主からのものだけ止める。持ち主でないもの・古い press のものは無視する
  if (has_owner_ && owner_.id == conn.id && press == owner_press_) {
    has_owner_ = false;
    return true;
  }
  return false;
}

void LiftArbiter::on_close(int conn_id) {
  // 持ち主の接続が閉じたらその場で空にする
  if (has_owner_ && owner_.id == conn_id) {
    has_owner_ = false;
  }
}

void LiftArbiter::tick(uint32_t now_ms) {
  // 持ち主の hold が LIFT_CMD_TIMEOUT_MS 届かなければ空にする
  if (has_owner_ && elapsed_ms(now_ms, owner_at_ms_) > static_cast<int32_t>(LIFT_CMD_TIMEOUT_MS)) {
    has_owner_ = false;
  }
}

bool LiftArbiter::owner(ConnId* out_conn, int* out_press) const {
  if (!has_owner_) {
    return false;
  }
  if (out_conn != nullptr) {
    *out_conn = owner_;
  }
  if (out_press != nullptr) {
    *out_press = owner_press_;
  }
  return true;
}
