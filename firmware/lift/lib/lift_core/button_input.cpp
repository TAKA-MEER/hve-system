#include "button_input.h"

namespace {
constexpr int kHigh = 1;  // Arduino の HIGH（Arduino.h は include しない）
}  // namespace

ManualDir ButtonInput::update(int up_level, int down_level, uint32_t now_ms) {
  const ManualDir before = dir_;
  if (!armed_) {
    if (up_level == kHigh && down_level == kHigh) {
      armed_ = true;
    }
  }
  if (armed_) {
    dir_ = switch_.update(up_level, down_level, now_ms);
  } else {
    // 離れるまでは何を読んでも止まり。ManualSwitch には「離れている」と渡しておく
    dir_ = switch_.update(kHigh, kHigh, now_ms);
  }
  if (before == ManualDir::STOP && dir_ != ManualDir::STOP) {
    ++press_;
  }
  just_stopped_ = before != ManualDir::STOP && dir_ == ManualDir::STOP;
  return dir_;
}

void button_step(ButtonInput& button, LiftController& controller, int up_level, int down_level,
                 const LiftSettings& settings, uint32_t now_ms) {
  const ManualDir dir = button.update(up_level, down_level, now_ms);
  ConnId conn;
  conn.id = LIFT_BUTTON_CONN_ID;
  conn.kind = ConnKind::BUTTON;
  if (dir != ManualDir::STOP) {
    HoldMsg hold;
    hold.press = button.press();
    hold.dir = dir == ManualDir::UP ? LiftDir::UP : LiftDir::DOWN;
    hold.speed = dir == ManualDir::UP ? settings.up.init_mm_s : settings.down.init_mm_s;
    hold.has_ceiling = false;
    controller.on_hold(conn, hold, now_ms);
  } else if (button.just_stopped()) {
    controller.on_release(conn, button.press());
  }
}
