// 判定そのもの。DetailedDesign.md §4.1 の表。
#include "lift_decide.h"

namespace {

// 時計が戻っても巨大な値にならないように符号付きで差を取る
int64_t elapsed_ms(uint32_t now_ms, uint32_t then_ms) {
  return static_cast<int64_t>(now_ms) - static_cast<int64_t>(then_ms);
}

int clamp_duty(int duty) {
  if (duty < 0) {
    return 0;
  }
  if (duty > LIFT_DUTY_ABS_MAX_PCT) {
    return LIFT_DUTY_ABS_MAX_PCT;
  }
  return duty;
}

LiftDecideResult stop_with(StopReason reason) {
  LiftDecideResult result;
  result.dir = LiftDir::STOP;
  result.duty = 0;
  result.reason = reason;
  return result;
}

}  // namespace

LiftDecideResult lift_decide(const LiftDecideInput& in) {
  // 表 1 最後の指令から LIFT_CMD_TIMEOUT_MS 超（指令を一度も受けていない場合も同じ扱い）
  if (!in.has_cmd) {
    return stop_with(StopReason::CMD_TIMEOUT);
  }
  if (elapsed_ms(in.now_ms, in.cmd_received_at_ms) > LIFT_CMD_TIMEOUT_MS) {
    return stop_with(StopReason::CMD_TIMEOUT);
  }

  // 表 2 指令が stop
  if (in.cmd.dir == LiftDir::STOP) {
    return stop_with(StopReason::CMD_STOP);
  }

  // 表 3 上昇 かつ ceil_ok が true で無い
  if (in.cmd.dir == LiftDir::UP && !in.cmd.ceil_ok) {
    return stop_with(StopReason::CEILING);
  }

  // 表 4 上昇 かつ 高さが読めない・HEIGHT_STALE_MS 超
  const bool height_usable =
      in.height_ok && elapsed_ms(in.now_ms, in.height_at_ms) <= HEIGHT_STALE_MS;
  if (in.cmd.dir == LiftDir::UP && !height_usable) {
    return stop_with(StopReason::HEIGHT_UNKNOWN);
  }

  // 表 6 上昇 かつ 上端の閾値が設定済み（-1 なら判定しない）かつ 高さ ≧ 閾値
  if (in.cmd.dir == LiftDir::UP && in.top_mm >= 0 && in.height_mm >= in.top_mm) {
    return stop_with(StopReason::TOP);
  }

  // 表 7 下降 かつ 下端スイッチが押されている
  if (in.cmd.dir == LiftDir::DOWN && in.bottom_pressed) {
    return stop_with(StopReason::BOTTOM);
  }

  // 表 8 同じ方向へ LIFT_MAX_RUN_MS 超 連続
  if (in.run_ms > LIFT_MAX_RUN_MS) {
    return stop_with(StopReason::MAX_RUN);
  }

  // 表 9 それ以外。指令どおりに動かして、デューティは 0〜LIFT_DUTY_ABS_MAX_PCT に丸める
  LiftDecideResult result;
  result.dir = in.cmd.dir;
  result.duty = clamp_duty(in.cmd.duty);
  result.reason = StopReason::NONE;
  return result;
}
