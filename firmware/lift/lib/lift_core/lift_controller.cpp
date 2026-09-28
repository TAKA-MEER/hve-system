#include "lift_controller.h"

namespace {

// 「LIFT_MAX_RUN_MS を超えた」ことを残すので +1 で頭打ちにする。
// ちょうど LIFT_MAX_RUN_MS ではまだ動かせる（DetailedDesign.md §4.1 の表 8 は「超」）。
constexpr uint32_t kRunStopMs = LIFT_MAX_RUN_MS + 1;

}  // namespace

LiftController::LiftController(LiftHal* hal, int top_mm)
    : hal_(hal),
      top_mm_(top_mm),
      has_cmd_(false),
      cmd_(),
      cmd_received_at_ms_(0),
      run_ms_(0),
      run_dir_(LiftDir::STOP),
      turning_(false),
      run_at_ms_(0),
      state_() {
  state_.top_mm = top_mm_;
}

void LiftController::on_command(const LiftCmd& cmd, uint32_t now_ms) {
  cmd_ = cmd;
  has_cmd_ = true;
  cmd_received_at_ms_ = now_ms;
}

LiftState LiftController::step(uint32_t now_ms) {
  // 直前の step から今回までのあいだ、実際に回っていた時間を足してから判定する
  // （判定のあとに足すと、止まるまでが 1 周期ぶん延びる）。
  const uint32_t elapsed = now_ms - run_at_ms_;
  if (turning_ && elapsed > 0) {
    run_ms_ = (elapsed > kRunStopMs - run_ms_) ? kRunStopMs : run_ms_ + elapsed;
  }
  // 停止指令（指令を一度も受けていない場合もこれと同じ扱い）・方向の変化で 0 に戻す
  if (cmd_.dir == LiftDir::STOP || cmd_.dir != run_dir_) {
    run_ms_ = 0;
  }
  run_dir_ = cmd_.dir;

  LiftDecideInput in;
  in.has_cmd = has_cmd_;
  in.cmd = cmd_;
  in.cmd_received_at_ms = cmd_received_at_ms_;
  in.now_ms = now_ms;
  in.bottom_pressed = hal_->bottom_pressed();
  in.height_mm = hal_->height_mm();
  in.height_ok = hal_->height_ok();
  in.height_at_ms = hal_->height_at_ms();
  in.top_mm = top_mm_;
  in.run_ms = run_ms_;

  // 判定の結果をモータへ出す。指令をそのまま流さないこと（DetailedDesign.md §4.1）
  const LiftDecideResult decided = lift_decide(in);
  hal_->motor_set(decided.dir, decided.duty);

  turning_ = decided.reason == StopReason::NONE &&
             (decided.dir == LiftDir::UP || decided.dir == LiftDir::DOWN) &&
             decided.dir == cmd_.dir;
  run_at_ms_ = now_ms;

  state_.dir = decided.dir;
  state_.duty = decided.duty;
  state_.reason = decided.reason;
  state_.bottom = in.bottom_pressed;
  state_.height_mm = in.height_mm;
  state_.height_ok = in.height_ok &&
                     static_cast<int64_t>(now_ms) - static_cast<int64_t>(in.height_at_ms) <=
                         HEIGHT_STALE_MS;
  state_.top_mm = top_mm_;
  state_.cmd_age_ms = has_cmd_ ? now_ms - cmd_received_at_ms_ : 0;
  return state_;
}

const LiftState& LiftController::state() const { return state_; }
