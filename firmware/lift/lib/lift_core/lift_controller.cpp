#include "lift_controller.h"

#include <climits>
#include <cstdio>
#include <cstring>

#include "hal_core.h"  // ENC_UP_SIGN・LIFT_UM_PER_COUNT（速さの測定・#8a の数え用）

namespace {

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
      run_travel_um_(0),
      run_speed_mm_s_(0),
      run_dir_(LiftDir::STOP),
      turning_(false),
      run_at_ms_(0),
      state_() {
  state_.top_detect = LIFT_TOP_DETECT_ENABLED && top_mm_ >= 0;
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
  free_slot->sensor_explicit = false;
  free_slot->role = ModuleRole::CONTROL;
  free_slot->op = ModuleOperator::HUMAN;
  free_slot->name[0] = '\0';
  free_slot->named = false;
  free_slot->busy_label[0] = '\0';
  free_slot->ip[0] = '\0';
  free_slot->fw[0] = '\0';
  free_slot->has_ceiling = false;
  free_slot->ceiling = CeilingReport();
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

const LiftController::ConnInfo* LiftController::find(int conn_id) const {
  for (int i = 0; i < kMaxConns; ++i) {
    if (conns_[i].used && conns_[i].id == conn_id) {
      return &conns_[i];
    }
  }
  return nullptr;
}

bool LiftController::is_observer(int conn_id) const {
  const ConnInfo* info = find(conn_id);
  return info != nullptr && info->kind == ConnKind::MODULE && info->hello_seen &&
         info->role == ModuleRole::OBSERVE;
}

bool LiftController::is_ceiling_source(const ConnInfo& info) {
  return info.used && info.kind == ConnKind::MODULE && info.hello_seen && info.sensor_explicit &&
         info.has_sensor && info.role == ModuleRole::CONTROL;
}

void LiftController::remember_ceiling(ConnInfo* info, const CeilingReport& report) {
  if (info == nullptr || !is_ceiling_source(*info)) {
    return;
  }
  info->has_ceiling = true;
  info->ceiling = report;
}

const LiftController::ConnInfo* LiftController::pick_ceiling_source(uint32_t now_ms) const {
  // 厳しい側（止める値 → 近い値）を選ぶ。古い値も選ぶ（止めるため）。2 台以上のときの規則は
  // spec H-M5 が未定なので、近い方を使う案（protocol-modules §2.4）
  return pick_source(now_ms, false);
}

const LiftController::ConnInfo* LiftController::pick_fresh_ceiling_source(uint32_t now_ms) const {
  // 単体の操作（§2.5）: 古い値は無視して、新しい値の中で厳しい方を選ぶ
  return pick_source(now_ms, true);
}

const LiftController::ConnInfo* LiftController::pick_source(uint32_t now_ms,
                                                            bool fresh_only) const {
  const ConnInfo* best = nullptr;
  int best_key = INT_MAX;
  for (int i = 0; i < kMaxConns; ++i) {
    const ConnInfo& info = conns_[i];
    if (!is_ceiling_source(info) || !info.has_ceiling) {
      continue;
    }
    if (fresh_only && ceiling_is_stale(info.ceiling, now_ms)) {
      continue;
    }
    const CeilingVerdict verdict = ceiling_check(info.ceiling, now_ms, true, ceiling_margin_mm_);
    int key = 0;
    if (verdict.ok) {
      key = info.ceiling.status == CeilingStatus::MEASURED ? info.ceiling.mm : INT_MAX - 1;
      if (key < 0) {
        key = 0;
      }
    } else {
      key = -1;
    }
    if (best == nullptr || key < best_key) {
      best = &info;
      best_key = key;
    }
  }
  return best;
}

bool LiftController::sensor_of(int conn_id, ConnKind kind) const {
  // /ws/ui（画面）の命令に天井の値は使わない（spec #4c）
  if (kind == ConnKind::UI) {
    return false;
  }
  // 物理ボタンも距離計を持たない（spec #4c。DetailedDesign.md §4.1.3）
  if (kind == ConnKind::BUTTON) {
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

bool LiftController::on_hello(const ConnId& conn, const HelloMsg& hello, int* replaced_conn_id) {
  if (replaced_conn_id != nullptr) {
    *replaced_conn_id = -1;
  }
  // /ws/ui で hello を受けたら、その接続を閉じる（§3.1）
  if (conn.kind == ConnKind::UI) {
    return false;
  }
  ConnInfo* info = find_or_add(conn);
  if (info == nullptr) {
    return true;
  }
  // role・ceiling_sensor・名前は接続ごとに 1 度だけ受け付ける（2 度目以降は無視する。
  // 途中で役割を変えて守りを外させない。protocol-modules §1.1）
  if (!info->hello_seen) {
    info->hello_seen = true;
    info->has_sensor = hello.has_sensor;
    info->sensor_explicit = hello.sensor_explicit;
    info->role = hello.role;
    info->op = hello.op;
    copy_name(info->busy_label, sizeof(info->busy_label), hello.busy_label);
    copy_name(info->ip, sizeof(info->ip), hello.ip);
    copy_name(info->fw, sizeof(info->fw), hello.fw);
    info->named = hello.name[0] != '\0';
    if (info->named) {
      copy_name(info->name, sizeof(info->name), hello.name);
    } else {
      std::snprintf(info->name, sizeof(info->name), "anon-%d", conn.id);
    }
    if (info->role == ModuleRole::ARM) {
      // アームの制御器（protocol-arm §2.1）。operator は自動に、ceiling_sensor は false に固定する
      // （アームの制御器の値を天井の出どころにしない。先端の距離計は天面を測るもので天井ではない）
      info->op = ModuleOperator::AUTO;
      info->has_sensor = false;
      info->sensor_explicit = false;
      // role: arm の接続は 1 本だけ（§2.2）。新しい接続が来たら、名前が違っても古い方を閉じさせる。
      // 置き換えは「通信できない」とみなさない: 最後の out と受けた時刻は引き継ぐ（接続の番号だけ替える）
      if (arm_conn_id_ >= 0 && arm_conn_id_ != conn.id && find(arm_conn_id_) != nullptr &&
          replaced_conn_id != nullptr) {
        *replaced_conn_id = arm_conn_id_;
      }
      arm_conn_id_ = conn.id;
    }
    // 同じ名前の別の接続は古い接続。呼び出し側が閉じる（名前の無い接続は置き換えない）
    if (info->named && conn.kind == ConnKind::MODULE && replaced_conn_id != nullptr &&
        *replaced_conn_id < 0) {
      for (int i = 0; i < kMaxConns; ++i) {
        const ConnInfo& other = conns_[i];
        if (other.used && other.id != conn.id && other.kind == ConnKind::MODULE &&
            other.hello_seen && other.named && std::strcmp(other.name, info->name) == 0) {
          *replaced_conn_id = other.id;
          break;
        }
      }
    }
  }
  return true;
}

void LiftController::on_ceiling(const ConnId& conn, const CeilingReport& report) {
  if (conn.kind != ConnKind::MODULE) {
    return;
  }
  remember_ceiling(find(conn.id), report);
}

bool LiftController::accept_command(const ConnId& conn, int press, bool has_ceiling,
                                    const CeilingReport& ceiling_in, uint32_t now_ms,
                                    bool* new_owner) {
  ConnId prev;
  int prev_press = 0;
  const bool had = arbiter_.owner(&prev, &prev_press);
  const bool accepted = arbiter_.on_hold(conn, press, now_ms);
  CeilingReport report;
  if (has_ceiling) {
    report = ceiling_in;
  } else {
    // 型の違う中身・欠けは MISSING として受け取り、その場で止める。
    // 前の hold の値で動き続けない（DetailedDesign-protocol.md §2.1）
    report.status = CeilingStatus::MISSING;
    report.mm = 0;
    report.age_ms = 0;
    report.received_at_ms = now_ms;
  }
  // 値は持ち主でない接続の hold に載っていても新しい測定なので、出どころなら覚える。
  // 載っていない hold は、持ち主のものだけ MISSING として覚える（古い hold で汚さない）
  if (conn.kind == ConnKind::MODULE && (has_ceiling || accepted)) {
    remember_ceiling(find(conn.id), report);
  }
  if (!accepted) {
    return false;
  }
  ConnId now_owner;
  int now_press = 0;
  arbiter_.owner(&now_owner, &now_press);
  *new_owner = !had || prev.id != now_owner.id || prev_press != now_press;
  owner_cmd_.has_sensor = sensor_of(conn.id, conn.kind);
  owner_cmd_.ceiling = report;
  if (conn.kind == ConnKind::MODULE) {
    last_module_ceiling_ = owner_cmd_.ceiling;
    has_last_module_ceiling_ = true;
    const ConnInfo* info = find(conn.id);
    copy_name(last_module_name_, sizeof(last_module_name_), info != nullptr ? info->name : "");
  }
  has_owner_cmd_ = true;
  had_owner_ = true;
  return true;
}

void LiftController::end_goto(StopReason reason) {
  if (goto_live_) {
    state_.goto_info.status = GotoStatus::NOT_ARRIVED;
    state_.goto_info.reason = reason;
  }
  goto_live_ = false;
}

bool LiftController::height_usable(uint32_t now_ms) const {
  return hal_->height_ok() && height_is_fresh(now_ms, hal_->height_at_ms());
}

void LiftController::on_hold(const ConnId& conn, const HoldMsg& hold, uint32_t now_ms) {
  // observe の接続からは昇降を命じられない（捨てる。protocol-modules §2.1）
  if (conn.kind == ConnKind::MODULE && is_observer(conn.id)) {
    return;
  }
  bool new_owner = false;
  if (!accept_command(conn, hold.press, hold.has_ceiling, hold.ceiling, now_ms, &new_owner)) {
    return;
  }
  // goto の持ち主が hold に替えた（取って代わられたか、同じ持ち主が替えたか）
  end_goto(new_owner ? StopReason::PREEMPTED : StopReason::CMD_STOP);
  owner_cmd_.is_goto = false;
  owner_cmd_.blocked = false;
  owner_cmd_.dir = hold.dir;
  owner_cmd_.speed_mm_s = hold.speed;
}

void LiftController::on_goto(const ConnId& conn, const GotoMsg& go, uint32_t now_ms) {
  if (conn.kind == ConnKind::MODULE && is_observer(conn.id)) {
    return;  // observe の接続からは昇降を命じられない
  }
  bool new_owner = false;
  if (!accept_command(conn, go.press, go.has_ceiling, go.ceiling, now_ms, &new_owner)) {
    return;
  }
  // 新しい goto（持ち主が替わった・hold から goto に替えた）。state.goto を作り直す。
  // 前の goto が別の goto に取って代わられたときは、古い記録が新しい goto で置き換わる
  // （PREEMPTED が state に残るのは、hold などの goto でない操作に取って代わられたとき）
  if (new_owner || !owner_cmd_.is_goto) {
    GotoInfo& info = state_.goto_info;
    info.present = true;
    const ConnInfo* conn_info = find(conn.id);
    if (conn_info != nullptr) {
      copy_name(info.by, sizeof(info.by), conn_info->name);
    } else {
      std::snprintf(info.by, sizeof(info.by), "anon-%d", conn.id);
    }
    info.press = go.press;
    info.target_mm = go.target_mm;
    info.status = GotoStatus::MOVING;
    info.reason = StopReason::NONE;
    goto_live_ = true;
    owner_cmd_.blocked = false;
    owner_cmd_.block_reason = StopReason::NONE;
    if (!height_usable(now_ms)) {
      owner_cmd_.blocked = true;
      owner_cmd_.block_reason = StopReason::HEIGHT_UNKNOWN;
    } else if (go.target_mm < 0 || (top_mm_ >= 0 && go.target_mm > top_mm_)) {
      owner_cmd_.blocked = true;
      owner_cmd_.block_reason = StopReason::TARGET_RANGE;
    }
    if (owner_cmd_.blocked) {
      info.status = GotoStatus::NOT_ARRIVED;
      info.reason = owner_cmd_.block_reason;
      goto_live_ = false;
    }
  }
  owner_cmd_.is_goto = true;
  owner_cmd_.target_mm = go.target_mm;
  owner_cmd_.dir = LiftDir::STOP;  // 向きは step が決める
  owner_cmd_.speed_mm_s = go.speed;
}

void LiftController::on_release(const ConnId& conn, int press) {
  if (conn.kind == ConnKind::MODULE && is_observer(conn.id)) {
    return;  // observe の接続からは何も命じられない
  }
  if (arbiter_.on_release(conn, press)) {
    has_owner_cmd_ = false;
    owner_cmd_.dir = LiftDir::STOP;
    owner_cmd_.speed_mm_s = 0;
    idle_reason_ = StopReason::CMD_STOP;
    had_owner_ = false;
    end_goto(StopReason::CMD_STOP);
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
    end_goto(StopReason::OWNER_GONE);
  }
  // いまのアームの接続が閉じたら、その場で「通信できない」。覚えている腕の状態（arm_out_latched_）には
  // 触れない（接続が閉じても腕は出たまま。protocol-arm §5）。置き換えられた古い接続は番号が違うので何も起きない
  if (conn_id == arm_conn_id_) {
    arm_conn_id_ = -1;
  }
  ConnInfo* info = find(conn_id);
  if (info != nullptr) {
    info->used = false;
  }
}

bool LiftController::arm_link_ok(uint32_t now_ms) const {
  // 「通信できている」= いまのアームの接続から、最後の arm を LIFT_ARM_LINK_TIMEOUT_MS 以内に受けた。
  // 接続が開いているかだけでは判定しない（相手が黙って死ぬと TCP は閉じない。protocol-arm §4）
  return arm_conn_id_ >= 0 && arm_attached_ &&
         elapsed_ms(now_ms, arm_last_at_ms_) <= static_cast<int32_t>(LIFT_ARM_LINK_TIMEOUT_MS);
}

bool LiftController::arm_stop_active(uint32_t now_ms) const {
  // #4e: 腕が格納位置にない・分からないと最後に知っていて通信できない（起動したとき覚えていた場合を含む）。
  // 覚えられなかったとき（NVS に書けなかった）も同じに止める（覚えられないまま腕を出させない）
  return (arm_out_latched_ && !arm_link_ok(now_ms)) || arm_write_failed_;
}

void LiftController::on_arm(const ConnId& conn, const ArmMsg& arm, uint32_t now_ms) {
  // いまの role: arm の接続からのものだけ受ける（/ws/ui・role が arm でない接続・置き換えられた古い接続は捨てる）
  if (conn.kind != ConnKind::MODULE || conn.id != arm_conn_id_) {
    return;
  }
  const ConnInfo* info = find(conn.id);
  if (info == nullptr || !info->hello_seen || info->role != ModuleRole::ARM) {
    return;
  }
  arm_attached_ = true;
  arm_last_out_ = arm.out;
  arm_last_at_ms_ = now_ms;
  if (arm.out == ArmTri::NO) {
    // 格納位置にあると知らされた。覚えたものを下ろす（もう覚えるものが無いので、書けなかった印も消す）
    arm_out_latched_ = false;
    arm_write_failed_ = false;
  } else {
    // true と null（アームの制御器自身が分からない）は同じに扱う（spec #4e「格納位置にない、または分からない」）
    arm_out_latched_ = true;
  }
  // 通信が戻った。確認待ちだったなら、答えなくても解ける（番号も無効にする。遅れて届いた古い確認で
  // 腕が出たままの覚えを消さない）。書けなかった印があるときは、書き直せるまで解けない
  if (!arm_write_failed_) {
    arm_confirm_shown_ = 0;
  }
  if (arm_out_latched_ != arm_nvs_value_) {
    arm_write_wanted_ = true;  // 値が変わった（書けなかったときの頼み直しもここ）
  }
}

bool LiftController::on_arm_confirm(const ConnId& conn, const ArmConfirmMsg& confirm) {
  // 確認できるのは昇降部の画面（/ws/ui）だけ。確認待ちで、番号が直前の step が出したものと同じときだけ
  // 受ける（古いダイアログの遅れた押下で、新しい止めを解かない。press と同じ考え）
  if (conn.kind != ConnKind::UI) {
    return false;
  }
  if (arm_confirm_shown_ == 0 || confirm.stop != arm_confirm_shown_) {
    return false;
  }
  // 覚えたものを消し、付いているとみなすのを「分からない」に戻す（次の arm を受けるまで）
  arm_out_latched_ = false;
  arm_attached_ = false;
  arm_last_out_ = ArmTri::NUL;
  arm_write_failed_ = false;
  arm_confirm_shown_ = 0;
  if (arm_nvs_value_) {
    arm_write_wanted_ = true;  // NVS の覚えも下ろす
  }
  return true;
}

void LiftController::restore_arm_out_latched(bool latched) {
  arm_out_latched_ = latched;
  arm_nvs_value_ = latched;
}

bool LiftController::take_arm_latch_write(bool* value) {
  if (arm_write_inflight_ || !arm_write_wanted_) {
    return false;
  }
  arm_write_wanted_ = false;
  if (arm_out_latched_ == arm_nvs_value_) {
    return false;  // 書くのは値が変わったときだけ
  }
  *value = arm_out_latched_;
  arm_write_inflight_ = true;
  return true;
}

void LiftController::on_arm_latch_written(bool value, bool ok) {
  arm_write_inflight_ = false;
  if (ok) {
    arm_nvs_value_ = value;
    if (value == arm_out_latched_) {
      arm_write_failed_ = false;
    } else {
      arm_write_wanted_ = true;  // 書いている間に覚えの値が変わった
    }
    return;
  }
  // 書けなかった。覚えようとしていた値（true）のときだけ、止める印を立てる。
  // いまの覚えがもう違う値なら、古い書き込みの結果なので何もしない
  if (value && arm_out_latched_) {
    arm_write_failed_ = true;
  }
}

void LiftController::set_ui_clients(int count) { ui_clients_ = count < 0 ? 0 : count; }

void LiftController::set_motor_max_pct(int pct) {
  if (pct < 0) {
    pct = 0;
  }
  if (pct > LIFT_DUTY_ABS_MAX_PCT) {
    pct = LIFT_DUTY_ABS_MAX_PCT;
  }
  motor_max_pct_ = pct;
}

void LiftController::set_ceiling_standalone(bool enabled) { ceiling_standalone_ = enabled; }

void LiftController::set_ceiling_margin_mm(int mm) { ceiling_margin_mm_ = mm; }

void LiftController::apply_settings(const LiftSettings& settings) {
  set_motor_max_pct(settings.motor_max_pct);
  set_ceiling_standalone(settings.ceiling_standalone);
  set_ceiling_margin_mm(settings.ceiling_margin_mm);
}

LiftState LiftController::step(uint32_t now_ms) {
  arbiter_.tick(now_ms);
  ConnId owner;
  int owner_press = 0;
  bool has_owner = arbiter_.owner(&owner, &owner_press);
  if (had_owner_ && !has_owner) {
    // release でも close でもなく持ち主がいなくなった = hold の途絶
    idle_reason_ = StopReason::CMD_TIMEOUT;
    has_owner_cmd_ = false;
    end_goto(StopReason::CMD_TIMEOUT);
  }
  had_owner_ = has_owner;

  // goto: 目標といまの高さから向きを決める。着いたら止めて、その周期で持ち主を離す
  // （同じ press の goto は、持ち主の規則が「前に見た press 以下」として受け付けない。§2.2）
  StopReason goto_force = StopReason::NONE;  // goto が止める理由（受け付けなかった・高さが読めない）
  if (has_owner && has_owner_cmd_ && owner_cmd_.is_goto) {
    owner_cmd_.dir = LiftDir::STOP;
    if (owner_cmd_.blocked) {
      goto_force = owner_cmd_.block_reason;
    } else if (!height_usable(now_ms)) {
      goto_force = StopReason::HEIGHT_UNKNOWN;
    } else {
      const int diff = owner_cmd_.target_mm - hal_->height_mm();
      if (diff <= LIFT_GOTO_TOL_MM && diff >= -LIFT_GOTO_TOL_MM) {
        state_.goto_info.status = GotoStatus::ARRIVED;
        state_.goto_info.reason = StopReason::NONE;
        goto_live_ = false;
        arbiter_.on_release(owner, owner_press);
        has_owner = false;
        had_owner_ = false;
        has_owner_cmd_ = false;
        owner_cmd_.is_goto = false;
        owner_cmd_.speed_mm_s = 0;
        idle_reason_ = StopReason::ARRIVED;
      } else {
        owner_cmd_.dir = diff > 0 ? LiftDir::UP : LiftDir::DOWN;
      }
    }
  }

  // 直前の step から今回までのあいだ、実際に回っていた時間を足してから判定する
  // （判定のあとに足すと、止まるまでが 1 周期ぶん延びる）。
  const int32_t elapsed = elapsed_ms(now_ms, run_at_ms_);
  if (turning_ && elapsed > 0) {
    // 直前の step で決めた目標の速さ [mm/s] × 経過 [ms] = µm。測った速さは使わない。
    // 「上限を超えた」ことを残すので、上限の 1 つ上で頭打ちにする
    // （ちょうど上限ではまだ動かせる。DetailedDesign.md §4.1 の表 8 は「超」）
    const uint32_t stop_um = lift_max_run_um(top_mm_) + 1;
    const uint64_t add_um = static_cast<uint64_t>(run_speed_mm_s_ > 0 ? run_speed_mm_s_ : 0) *
                            static_cast<uint64_t>(elapsed);
    run_travel_um_ = (add_um > stop_um - run_travel_um_) ? stop_um : run_travel_um_ + static_cast<uint32_t>(add_um);
  }
  // 停止指令・方向の変化で 0 に戻す。持ち主が替わっても数え直さない（§4.1 #8）
  const LiftDir cmd_dir = has_owner_cmd_ ? owner_cmd_.dir : LiftDir::STOP;
  if (cmd_dir == LiftDir::STOP || cmd_dir != run_dir_) {
    run_travel_um_ = 0;
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

  // 測定機構の #4e（protocol-arm §5・§8）。止め始めるたびに確認の番号を 1 増やす
  const bool arm_stop = arm_stop_active(now_ms);
  if (arm_stop && !arm_stop_prev_) {
    ++arm_stop_;
  }
  arm_stop_prev_ = arm_stop;
  arm_confirm_shown_ = arm_stop ? arm_stop_ : 0;

  LiftDecideInput in;
  in.arm_link_stop = arm_stop;
  in.has_cmd = has_owner;
  in.owner_empty_reason = idle_reason_;
  in.dir = cmd_dir;
  in.speed_mm_s = owner_cmd_.speed_mm_s;
  in.ceiling_margin_mm = ceiling_margin_mm_;
  in.stalled = stalled_;
  // hello が hold のあとに届いても、その時点の値を向き先で使う
  const bool owner_has_sensor = has_owner ? sensor_of(owner.id, owner.kind) : false;
  // 距離計を持たない上部モジュール（fk・ss）の命令には、距離計を持つ上部モジュールの
  // 値を使う（protocol-modules §2.4）。無い・古いときは MISSING で CEILING_STALE
  const bool borrows_ceiling =
      has_owner && owner.kind == ConnKind::MODULE && !owner_has_sensor;
  // 単体の操作（画面・物理ボタン）の天井（§2.5）。周期ごとに決め直す。
  // 持ち主が誰でも「いま単体で上げたらどうなるか」を state に出す
  const ConnInfo* standalone_source = nullptr;
  if (!ceiling_standalone_) {
    state_.ceiling_standalone = StandaloneCeiling::SETTING_OFF;
  } else {
    standalone_source = pick_fresh_ceiling_source(now_ms);
    state_.ceiling_standalone =
        standalone_source != nullptr ? StandaloneCeiling::USE : StandaloneCeiling::NO_SOURCE;
  }
  const bool standalone_owner =
      has_owner && (owner.kind == ConnKind::UI || owner.kind == ConnKind::BUTTON);
  const bool standalone_use = standalone_owner && standalone_source != nullptr;
  // 出どころの値で判定している持ち主（距離計を持たない上部モジュール・単体の操作で USE）
  const bool uses_source = borrows_ceiling || standalone_use;
  const ConnInfo* ceiling_source = borrows_ceiling ? pick_ceiling_source(now_ms)
                                   : standalone_use ? standalone_source
                                   : !has_owner_cmd_ ? pick_ceiling_source(now_ms)
                                                     : nullptr;
  if (standalone_use) {
    in.ceiling = standalone_source->ceiling;
    in.connection_has_sensor = true;  // READ_ERROR・MISSING は CEILING_STALE で止める
  } else if (borrows_ceiling) {
    if (ceiling_source != nullptr) {
      in.ceiling = ceiling_source->ceiling;
    } else {
      in.ceiling = CeilingReport();  // MISSING
      in.ceiling.received_at_ms = now_ms;
    }
    in.connection_has_sensor = true;
  } else {
    in.ceiling = owner_cmd_.ceiling;
    in.connection_has_sensor = owner_has_sensor;
  }
  in.now_ms = now_ms;
  in.bottom_pressed = hal_->bottom_pressed();
  in.height_mm = hal_->height_mm();
  in.height_ok = hal_->height_ok();
  in.height_at_ms = hal_->height_at_ms();
  in.top_mm = top_mm_;
  in.run_travel_um = run_travel_um_;

  // 判定の結果をモータへ出す。指令をそのまま流さないこと（§4.1）
  LiftDecideResult decided = lift_decide(in);
  if (goto_force != StopReason::NONE && !arm_stop) {  // #4e は goto の理由で上書きしない
    decided = LiftDecideResult();  // 止める。理由は goto の受け付けなかった理由・高さが読めない
    decided.reason = goto_force;
  }
  if (goto_live_) {
    // 実行中の goto: 動いている間 MOVING、安全の判定などで止まっている間は NOT_ARRIVED と理由
    state_.goto_info.status =
        decided.reason == StopReason::NONE ? GotoStatus::MOVING : GotoStatus::NOT_ARRIVED;
    state_.goto_info.reason = decided.reason;
  }
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
      duty = speed_loop_.update(decided.speed_mm_s, measured_fwd, decided.dir, dt,
                                motor_max_pct_);
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
  run_speed_mm_s_ = decided.speed_mm_s;
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
  // 鮮度の判断は height_is_fresh の 1 か所（判定 #4 と共有）
  state_.height_ok = in.height_ok && height_is_fresh(now_ms, in.height_at_ms);
  state_.top_detect = LIFT_TOP_DETECT_ENABLED && top_mm_ >= 0;
  // 天井は持ち主の最後の hold の値。持ち主がいなければ上部モジュールの
  // 最後の hold の値（無ければ null）
  state_.ceiling_used = has_owner && (owner_has_sensor || uses_source);
  state_.ceiling_from[0] = '\0';
  const bool show_last = !has_owner_cmd_ && (has_last_module_ceiling_ || ceiling_source != nullptr);
  state_.ceiling_present = has_owner_cmd_ || show_last;
  if (state_.ceiling_present) {
    // 判定に使った値（借りるときは出どころの値）。持ち主がいなければ、出どころの接続の
    // 最新の値（無ければ上部モジュールの最後の hold の値）。古さは増え続ける
    const CeilingReport& shown = uses_source ? in.ceiling
                                 : has_owner_cmd_ ? owner_cmd_.ceiling
                                 : ceiling_source != nullptr ? ceiling_source->ceiling
                                                             : last_module_ceiling_;
    if (uses_source || !has_owner_cmd_) {
      if (ceiling_source != nullptr) {
        copy_name(state_.ceiling_from, sizeof(state_.ceiling_from), ceiling_source->name);
      } else if (!uses_source) {
        copy_name(state_.ceiling_from, sizeof(state_.ceiling_from), last_module_name_);
      }
    } else if (owner.kind == ConnKind::MODULE) {
      const ConnInfo* owner_info = find(owner.id);
      if (owner_info != nullptr) {
        copy_name(state_.ceiling_from, sizeof(state_.ceiling_from), owner_info->name);
      }
    }
    state_.ceiling_status = shown.status;
    state_.ceiling_mm = shown.mm;
    const int32_t transit = elapsed_ms(now_ms, shown.received_at_ms);
    const int64_t age = static_cast<int64_t>(shown.age_ms) + (transit > 0 ? transit : 0);
    state_.ceiling_age_ms = age < 0 ? 0u : static_cast<uint32_t>(age);
    if (has_owner_cmd_) {
      const CeilingVerdict verdict = ceiling_check(
          in.ceiling, now_ms, has_owner ? (owner_has_sensor || uses_source) : false,
          ceiling_margin_mm_);
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
  state_.owner_kind = !has_owner ? 0 : owner.kind == ConnKind::MODULE ? 1 : owner.kind == ConnKind::BUTTON ? 2 : 0;
  // アームの制御器（role: arm の接続）が持ち主なら arm（protocol-arm §3）
  if (has_owner && owner.kind == ConnKind::MODULE) {
    const ConnInfo* arm_owner = find(owner.id);
    if (arm_owner != nullptr && arm_owner->role == ModuleRole::ARM) {
      state_.owner_kind = 3;
    }
  }
  state_.ui_clients = ui_clients_;
  // 持ち主の名前・人か自動か（module のときはその接続の hello の値）
  state_.owner_name[0] = '\0';
  state_.owner_operator = ModuleOperator::HUMAN;
  if (has_owner && owner.kind == ConnKind::MODULE) {
    const ConnInfo* owner_info = find(owner.id);
    if (owner_info != nullptr) {
      copy_name(state_.owner_name, sizeof(state_.owner_name), owner_info->name);
      state_.owner_operator = owner_info->op;
    }
  }
  // state.arm（protocol-arm §7 の表）。out は NVS に書けたと知らされてから true を出す
  {
    const bool link = arm_link_ok(now_ms);
    const bool known = arm_attached_ || arm_out_latched_;  // 覚えているものがあれば、通信できないことも出す
    ArmInfo& arm = state_.arm;
    arm.attached = arm_attached_ ? ArmTri::YES : ArmTri::NUL;
    arm.link = !known ? ArmTri::NUL : link ? ArmTri::YES : ArmTri::NO;
    arm.confirm = arm_confirm_shown_;
    arm.out = ArmTri::NUL;
    if (arm_attached_ && link) {
      if (arm_last_out_ == ArmTri::NO) {
        arm.out = ArmTri::NO;
      } else if (arm_out_latched_ && arm_nvs_value_ && !arm_write_failed_) {
        arm.out = ArmTri::YES;  // true と null は true に倒して出す。NVS に書けたあとだけ
      }
    }
  }
  state_.homed = hal_->homed();
  // 繋がっている上部モジュール（/ws/module で hello を受けたもの。observe を含む）
  state_.module_count = 0;
  for (int i = 0; i < kMaxConns && state_.module_count < LIFT_MODULE_LIST_MAX; ++i) {
    const ConnInfo& info = conns_[i];
    if (!info.used || info.kind != ConnKind::MODULE || !info.hello_seen) {
      continue;
    }
    ModuleInfo& item = state_.modules[state_.module_count++];
    copy_name(item.name, sizeof(item.name), info.name);
    item.role = info.role;
    item.op = info.op;
    item.ceiling_sensor = info.has_sensor;
    copy_name(item.busy_label, sizeof(item.busy_label), info.busy_label);
    copy_name(item.ip, sizeof(item.ip), info.ip);
    copy_name(item.fw, sizeof(item.fw), info.fw);
  }
  // 指令の年齢も elapsed_ms で数える。負や一周したときは 0 として出す
  const int32_t cmd_age =
      has_owner ? elapsed_ms(now_ms, owner_cmd_.ceiling.received_at_ms) : 0;
  state_.cmd_age_ms = cmd_age > 0 ? static_cast<uint32_t>(cmd_age) : 0u;
  return state_;
}

const LiftState& LiftController::state() const { return state_; }
