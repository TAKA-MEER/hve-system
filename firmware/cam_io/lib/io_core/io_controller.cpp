// IoController（DetailedDesign.md §4.5）。判定の結果だけをハードウェアへ出す。
#include "io_controller.h"

namespace {

// 経過時間 [ms]。millis() は約 49.7 日で一周するので uint32 のまま引いてから int32 に直す
// （一周をまたいでも正しい値が出る。lift_core の elapsed_ms と同じ式）。
// now より少し新しい時刻には小さな負の値になるので、誤停止しない
int32_t elapsed_ms(uint32_t now_ms, uint32_t then_ms) {
  return static_cast<int32_t>(now_ms - then_ms);
}

int32_t clamp_pitch_ddeg(int32_t pitch_ddeg) {
  const int32_t lo = (int32_t)PITCH_MIN_DEG * 10;
  const int32_t hi = (int32_t)PITCH_MAX_DEG * 10;
  if (pitch_ddeg < lo) {
    return lo;
  }
  if (pitch_ddeg > hi) {
    return hi;
  }
  return pitch_ddeg;
}

int32_t clamp_yaw_hsps(int32_t yaw_hsps) {
  if (yaw_hsps < -YAW_HSPS_ABS_MAX) {
    return -YAW_HSPS_ABS_MAX;
  }
  if (yaw_hsps > YAW_HSPS_ABS_MAX) {
    return YAW_HSPS_ABS_MAX;
  }
  return yaw_hsps;
}

}  // namespace

IoController::IoController(IoHal* hal)
    : hal_(hal), has_cmd_(false), pitch_ddeg_(PITCH_INITIAL_DEG * 10), yaw_hsps_(0), cmd_at_ms_(0) {}

void IoController::on_command(const IoCmd& cmd, uint32_t now_ms) {
  pitch_ddeg_ = clamp_pitch_ddeg(cmd.pitch_ddeg);
  yaw_hsps_ = clamp_yaw_hsps(cmd.yaw_hsps);
  has_cmd_ = true;
  cmd_at_ms_ = now_ms;
}

void IoController::tick(uint32_t now_ms) {
  // ちょうど IO_CMD_TIMEOUT_MS までは動かしてよい（「超」で止める）
  const bool fresh =
      has_cmd_ && elapsed_ms(now_ms, cmd_at_ms_) <= static_cast<int32_t>(IO_CMD_TIMEOUT_MS);
  const int32_t yaw = fresh ? yaw_hsps_ : 0;
  if (yaw == 0) {
    hal_->yaw_coils_off();  // 止めている間はコイルの電流を切る（発熱・電池）
  } else {
    hal_->yaw_set_hsps(yaw);
  }
  // ピッチは途絶えてもパルスを止めない（止めると脱力してカメラが倒れる）
  hal_->servo_set_ddeg(pitch_ddeg_);
}
