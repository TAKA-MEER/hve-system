#include "lift_controller.h"

#include <cstring>

#include "hal_core.h"  // ENC_UP_SIGN・LIFT_UM_PER_COUNT（速さの測定・#8a の数え用）

namespace {

// 「LIFT_MAX_RUN_MS を超えた」ことを残すので +1 で頭打ちにする。
// ちょうど LIFT_MAX_RUN_MS ではまだ動かせる（DetailedDesign.md §4.1 の表 8 は「超」）。
constexpr uint32_t kRunStopMs = LIFT_MAX_RUN_MS + 1;

void copy_name(char* out, size_t out_size, const char* in) {
  if (out_size == 0) {
    return;
  }
  out[0] = '\0';
  if (in == nullptr) {
    return;
  }
  std::strncpy(out, in, out_size - 1);
  out[out_size - 1] = '\0';
}

// 進行方向へ進んだ数。上昇なら上向きが正、下降なら下向きが正。
// 逆向きに進んだら負になり、「進んでいない」と数える（§4.1 #8a）。
int32_t forward_counts(int32_t now_count, int32_t anchor_count, LiftDir dir) {
  const int32_t delta = now_count - anchor_count;
  if (dir == LiftDir::DOWN) {
    return -delta * static_cast<int32_t>(ENC_UP_SIGN);
  }
  return delta * static_cast<int32_t>(ENC_UP_SIGN);
}

}  // namespace

LiftController::LiftController(LiftHal* hal, int top_mm)
    : hal_(hal),
      top_mm_(top_mm),
      arbiter_(),
      owner_cmd_(),
      has_owner_cmd_(false),
      idle_reason_(StopReason::CMD_TIMEOUT),
      had_owner_(false),
      conns_(),
      ui_clients_(0),
      run_ms_(0),
      run_dir_(LiftDir::STOP),
      turning_(false),
      run_at_ms_(0),
      state_() {
  state_.top_detect = LIFT_TOP_DETECT_ENABLED;
}

LiftController::ConnInfo* LiftController::find_or_add(const ConnId& conn) {
  ConnInfo* free_slot = nullptr;
  for (int i = 0; i < kMaxConns; ++i) {
    if (conns_[i].used && conns_[i].id == conn.id) {
      return &conns_[i];
    }
    if (!conns_[i].used && free_slot == nullptr) {
      free_slot = &conns_[i];
    }
  }
  if (free_slot == nullptr) {
    return nullptr;
  }
  free_slot->used = true;
  free_slot->id = conn.id;
  free_slot->kind = conn.kind;
  free_slot->hello_seen = false;
  // 欠けている・読めないときは厳しい側（持つ）に倒す（DetailedDesign.md §3.1）
  free_slot->has_sensor = true;
  free_slot->name[0] = '\0';
  free_slot->ip[0] = '\0';
  return free_slot;
}

LiftController::ConnInfo* LiftController::find(int conn_id) {
  for (int i = 0; i < kMaxConns; ++i) {
    if (conns_[i].used && conns_[i].id == conn_id) {
      return &conns_[i];
    }
  }
  return nullptr;
}

bool LiftController::sensor_of(int conn_id, ConnKind kind) const {
  // /ws/ui（画面）の命令に天井の値は使わない（spec #4c）
  if (kind == ConnKind::UI) {
    return false;
  }
  for (int i = 0; i < kMaxConns; ++i) {
    if (conns_[i].used && conns_[i].id == conn_id) {
      return conns_[i].has_sensor;
    }
  }
  // hello をまだ受けていない・読めないときは持つとみなす（§3.1）
  return true;
}

bool LiftController::on_hello(const ConnId& conn, const HelloMsg& hello) {
  // /ws/ui で hello を受けたら、その接続を閉じる（§3.1）
  if (conn.kind == ConnKind::UI) {
    return false;
  }
  ConnInfo* info = find_or_add(conn);
  if (info == nullptr) {
    return true;
  }
  // ceiling_sensor は接続ごとに 1 度だけ受け付ける（2 度目以降は無視する）
  if (!info->hello_seen) {
    info->hello_seen = true;
    info->has_sensor = hello.has_sensor;
    copy_name(info->name, sizeof(info->name), hello.name);
  }
  return true;
}

void LiftController::on_hold(const ConnId& conn, const HoldMsg& hold, uint32_t now_ms) {
  if (!arbiter_.on_hold(conn, hold.press, now_ms)) {
    return;
  }
  owner_cmd_.dir = hold.dir;
  owner_cmd_.speed_mm_s = hold.speed;
  owner_cmd_.has_sensor = sensor_of(conn.id, conn.kind);
  if (hold.has_ceiling) {
    owner_cmd_.ceiling = hold.ceiling;
  } else {
    // 型の違う中身・欠けは MISSING として受け取り、その場で止める。
    // 前の hold の値で動き続けない（DetailedDesign-protocol.md §2.1）
    owner_cmd_.ceiling.status = CeilingStatus::MISSING;
    owner_cmd_.ceiling.mm = 0;
    owner_cmd_.ceiling.age_ms = 0;
    owner_cmd_.ceiling.received_at_ms = now_ms;
  }
  if (conn.kind == ConnKind::MODULE) {
    last_module_ceiling_ = owner_cmd_.ceiling;
    has_last_module_ceiling_ = true;
  }
  has_owner_cmd_ = true;
  had_owner_ = true;
}

void LiftController::on_release(const ConnId& conn, int press) {
  if (arbiter_.on_release(conn, press)) {
    has_owner_cmd_ = false;
    owner_cmd_.dir = LiftDir::STOP;
    owner_cmd_.speed_mm_s = 0;
    idle_reason_ = StopReason::CMD_STOP;
    had_owner_ = false;
  }
}

void LiftController::on_close(int conn_id) {
  ConnId owner;
  int press = 0;
  const bool was_owner = arbiter_.owner(&owner, &press) && owner.id == conn_id;
  arbiter_.on_close(conn_id);
  if (was_owner) {
    // 持ち主の接続が閉じたらその場で止める（DetailedDesign-protocol.md §1）
    has_owner_cmd_ = false;
    owner_cmd_.dir = LiftDir::STOP;
    owner_cmd_.speed_mm_s = 0;
    idle_reason_ = StopReason::OWNER_GONE;
    had_owner_ = false;
  }
  ConnInfo* info = find(conn_id);
  if (info != nullptr) {
    info->used = false;
  }
}

void LiftController::set_ui_clients(int count) { ui_clients_ = count < 0 ? 0 : count; }

LiftState LiftController::step(uint32_t now_ms) {
  arbiter_.tick(now_ms);
  ConnId owner;
  int owner_press = 0;
  const bool has_owner = arbiter_.owner(&owner, &owner_press);
  if (had_owner_ && !has_owner) {
    // release でも close でもなく持ち主がいなくなった = hold の途絶
    idle_reason_ = StopReason::CMD_TIMEOUT;
    has_owner_cmd_ = false;
  }
  had_owner_ = has_owner;

  // 直前の step から今回までのあいだ、実際に回っていた時間を足してから判定する
  // （判定のあとに足すと、止まるまでが 1 周期ぶん延びる）。
  const int32_t elapsed = elapsed_ms(now_ms, run_at_ms_);
  if (turning_ && elapsed > 0) {
    const uint32_t step_ms = static_cast<uint32_t>(elapsed);
    run_ms_ = (step_ms > kRunStopMs - run_ms_) ? kRunStopMs : run_ms_ + step_ms;
  }
  // 停止指令・方向の変化で 0 に戻す。持ち主が替わっても数え直さない（§4.1 #8）
  const LiftDir cmd_dir = has_owner_cmd_ ? owner_cmd_.dir : LiftDir::STOP;
  if (cmd_dir == LiftDir::STOP || cmd_dir != run_dir_) {
    run_ms_ = 0;
  }
  run_dir_ = cmd_dir;

  // 速さを測る（窓の起点から LIFT_SPEED_WINDOW_MS 以上経てば取り直す。原点に依らない）
  const int32_t enc_now = hal_->encoder_count();
  if (!speed_win_valid_) {
    speed_win_count_ = enc_now;
    speed_win_ms_ = now_ms;
    speed_win_valid_ = true;
    speed_up_mm_s_ = 0.0f;
  } else if (elapsed_ms(now_ms, speed_win_ms_) >=
             static_cast<int32_t>(LIFT_SPEED_WINDOW_MS)) {
    speed_up_mm_s_ = speed_from_counts(speed_win_count_, enc_now, speed_win_ms_, now_ms,
                                       LIFT_UM_PER_COUNT, ENC_UP_SIGN);
    speed_win_count_ = enc_now;
    speed_win_ms_ = now_ms;
  }

  // #8a の数え（§4.1）。命令が stop になるか向きが替わるまで続ける。
  // 持ち主が替わっても数え直さない
  if (cmd_dir == LiftDir::STOP || cmd_dir != stall_dir_) {
    stalled_ = false;
    stall_dir_ = cmd_dir;
    stall_anchor_ = enc_now;
    stall_at_ms_ = now_ms;
    stall_valid_ = true;
  } else if (last_duty_ > 0) {
    // 動かしている（出力が 0 より大きい）間だけ数える
    if (!stall_valid_) {
      stall_anchor_ = enc_now;
      stall_at_ms_ = now_ms;
      stall_valid_ = true;
    } else if (forward_counts(enc_now, stall_anchor_, cmd_dir) >= LIFT_STALL_MIN_COUNTS) {
      // 進行方向へ進んだら起点を取り直す。逆向きは「進んでいない」
      stall_anchor_ = enc_now;
      stall_at_ms_ = now_ms;
    } else if (elapsed_ms(now_ms, stall_at_ms_) > static_cast<int32_t>(LIFT_STALL_MS)) {
      stalled_ = true;
    }
  } else {
    // 出していない間は起点を取り直す（止まっていた時間を数えない）
    stall_anchor_ = enc_now;
    stall_at_ms_ = now_ms;
    stall_valid_ = true;
  }

  LiftDecideInput in;
  in.has_cmd = has_owner;
  in.owner_empty_reason = idle_reason_;
  in.dir = cmd_dir;
  in.speed_mm_s = owner_cmd_.speed_mm_s;
  in.stalled = stalled_;
  in.ceiling = owner_cmd_.ceiling;
  // hello が hold のあとに届いても、その時点の値を向き先で使う
  const bool owner_has_sensor = has_owner ? sensor_of(owner.id, owner.kind) : false;
  in.connection_has_sensor = owner_has_sensor;
  in.now_ms = now_ms;
  in.bottom_pressed = hal_->bottom_pressed();
  in.height_mm = hal_->height_mm();
  in.height_ok = hal_->height_ok();
  in.height_at_ms = hal_->height_at_ms();
  in.top_mm = top_mm_;
  in.run_ms = run_ms_;

  // 判定の結果をモータへ出す。指令をそのまま流さないこと（§4.1）
  const LiftDecideResult decided = lift_decide(in);
  const bool moving =
      has_owner && decided.reason == StopReason::NONE &&
      (decided.dir == LiftDir::UP || decided.dir == LiftDir::DOWN) && decided.dir == cmd_dir;
  int duty = 0;
  if (moving) {
    // 止まった・向きが替わった・持ち主が替わったら積分を 0 に戻す（§4.1.2）
    if (!loop_started_ || decided.dir != loop_dir_ || owner.id != loop_owner_ ||
        owner_press != loop_press_) {
      speed_loop_.reset();
    }
    // LIFT_SPEED_CTRL_MS ごとに進める。loop が詰まったら経過時間で割る
    const int32_t loop_elapsed = loop_started_ ? elapsed_ms(now_ms, loop_at_ms_) :
                                                 static_cast<int32_t>(LIFT_SPEED_CTRL_MS);
    if (!loop_started_ || loop_elapsed >= static_cast<int32_t>(LIFT_SPEED_CTRL_MS)) {
      const float measured_fwd =
          (decided.dir == LiftDir::DOWN) ? -speed_up_mm_s_ : speed_up_mm_s_;
      const uint32_t dt = loop_elapsed <= 0 ? 0u : static_cast<uint32_t>(loop_elapsed);
      duty = speed_loop_.update(decided.speed_mm_s, measured_fwd, decided.dir, dt);
      loop_at_ms_ = now_ms;
      loop_started_ = true;
      loop_dir_ = decided.dir;
      loop_owner_ = owner.id;
      loop_press_ = owner_press;
    } else {
      duty = last_duty_;
    }
    hal_->motor_set(decided.dir, duty);
  } else {
    hal_->motor_set(LiftDir::STOP, 0);
    speed_loop_.reset();
    loop_started_ = false;
    loop_dir_ = LiftDir::STOP;
    duty = 0;
  }
  last_duty_ = duty;

  turning_ = moving && duty > 0;
  run_at_ms_ = now_ms;

  state_.dir = moving ? decided.dir : LiftDir::STOP;
  state_.duty = duty;
  state_.reason = decided.reason;
  // 止めるときの dir は STOP（指令ではなく実際に出している方向）
  if (!moving) {
    state_.dir = LiftDir::STOP;
    state_.duty = 0;
  }
  state_.speed = decided.speed_mm_s;
  state_.speed_meas = static_cast<int>(speed_up_mm_s_ >= 0.0f ? speed_up_mm_s_ + 0.5f
                                                              : speed_up_mm_s_ - 0.5f);
  state_.bottom = in.bottom_pressed;
  state_.height_mm = in.height_mm;
  // 高さは表示と配信だけに使い続ける（W-1）。鮮度の判断は height_is_fresh の 1 か所
  state_.height_ok = in.height_ok && height_is_fresh(now_ms, in.height_at_ms);
  state_.top_detect = LIFT_TOP_DETECT_ENABLED;
  // 天井は持ち主の最後の hold の値。持ち主がいなければ上部モジュールの
  // 最後の hold の値（無ければ null）
  state_.ceiling_used = has_owner && owner_has_sensor;
  const bool show_last = !has_owner_cmd_ && has_last_module_ceiling_;
  state_.ceiling_present = has_owner_cmd_ || show_last;
  if (state_.ceiling_present) {
    // 持ち主がいなければ上部モジュールの最後の hold の値（古さは増え続ける）
    const CeilingReport& shown = has_owner_cmd_ ? owner_cmd_.ceiling : last_module_ceiling_;
    state_.ceiling_status = shown.status;
    state_.ceiling_mm = shown.mm;
    const int32_t transit = elapsed_ms(now_ms, shown.received_at_ms);
    const int64_t age = static_cast<int64_t>(shown.age_ms) + (transit > 0 ? transit : 0);
    state_.ceiling_age_ms = age < 0 ? 0u : static_cast<uint32_t>(age);
    if (has_owner_cmd_) {
      const CeilingVerdict verdict =
          ceiling_check(owner_cmd_.ceiling, now_ms, has_owner ? owner_has_sensor : false);
      state_.ceiling_ok = verdict.ok;
      state_.ceiling_reason = verdict.reason;
    } else {
      // 表示だけ。持ち主がいないので判定は無い（上部モジュールは owner が module の
      // ときだけ ok/reason を使う）
      state_.ceiling_ok = false;
      state_.ceiling_reason = StopReason::CEILING_STALE;
    }
  }
  state_.has_owner = has_owner;
  state_.owner_kind = has_owner && owner.kind == ConnKind::MODULE ? 1 : 0;
  state_.ui_clients = ui_clients_;
  // 上部モジュールの接続（/ws/module で hello を受けたもの）
  state_.module_connected = false;
  state_.module_has_sensor = true;
  state_.module_ip[0] = '\0';
  state_.module_name[0] = '\0';
  for (int i = 0; i < kMaxConns; ++i) {
    if (conns_[i].used && conns_[i].kind == ConnKind::MODULE && conns_[i].hello_seen) {
      state_.module_connected = true;
      state_.module_has_sensor = conns_[i].has_sensor;
      copy_name(state_.module_ip, sizeof(state_.module_ip), conns_[i].ip);
      copy_name(state_.module_name, sizeof(state_.module_name), conns_[i].name);
      break;
    }
  }
  // 指令の年齢も elapsed_ms で数える。負や一周したときは 0 として出す
  const int32_t cmd_age =
      has_owner ? elapsed_ms(now_ms, owner_cmd_.ceiling.received_at_ms) : 0;
  state_.cmd_age_ms = cmd_age > 0 ? static_cast<uint32_t>(cmd_age) : 0u;
  return state_;
}

const LiftState& LiftController::state() const { return state_; }
