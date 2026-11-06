// Unity の試験（env:native）。LiftController に偽 HAL を差し、hello / hold /
// release / close → step のあと偽モータへ実際に出た値を確かめる
// （DetailedDesign.md DD-2）。純関数だけでは不可。
// 速さの制御は SpeedLoop の出力（デューティ）をモータで見る（§4.1.2）。
#include <unity.h>

#include <cstring>
#include <string>

#include "fake_hal.h"
#include "hal_core.h"  // LIFT_STALL_MIN_COUNTS・LIFT_UM_PER_COUNT
#include "lift_controller.h"
#include "speed_loop.h"

namespace {

ConnId ui(int id) {
  ConnId conn;
  conn.id = id;
  conn.kind = ConnKind::UI;
  return conn;
}

ConnId module(int id) {
  ConnId conn;
  conn.id = id;
  conn.kind = ConnKind::MODULE;
  return conn;
}

ConnId button(int id) {
  ConnId conn;
  conn.id = id;
  conn.kind = ConnKind::BUTTON;
  return conn;
}

HelloMsg hello_with_sensor(bool has_sensor) {
  HelloMsg hello;
  hello.has_sensor = has_sensor;
  hello.sensor_explicit = true;  // 実際の hello は ceiling_sensor を明示する
  std::strncpy(hello.name, "hve-cam", sizeof(hello.name) - 1);
  return hello;
}

// 名前・役割・人か自動か・距離計を名乗った hello（sensor_explicit は真。既定はカメラモジュール）
HelloMsg hello_as(const char* name, bool has_sensor, ModuleRole role = ModuleRole::CONTROL,
                  ModuleOperator op = ModuleOperator::HUMAN) {
  HelloMsg hello;
  hello.has_sensor = has_sensor;
  hello.sensor_explicit = true;
  hello.role = role;
  hello.op = op;
  std::strncpy(hello.name, name, sizeof(hello.name) - 1);
  return hello;
}

HelloMsg hello_cam() { return hello_as("hve-cam", true); }
HelloMsg hello_fk() {
  return hello_as("fk", false, ModuleRole::CONTROL, ModuleOperator::AUTO);
}

CeilingReport ceiling_at(CeilingStatus status, int mm, uint32_t age_ms, uint32_t at_ms) {
  CeilingReport report;
  report.status = status;
  report.mm = mm;
  report.age_ms = age_ms;
  report.received_at_ms = at_ms;
  return report;
}

CeilingReport measured(int mm, uint32_t at_ms, uint32_t age_ms = 0) {
  return ceiling_at(CeilingStatus::MEASURED, mm, age_ms, at_ms);
}

// 距離計を持たない上部モジュール（fk）の上昇。ceiling は載せない
HoldMsg fk_up(int press, int speed = 40) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = LiftDir::UP;
  hold.speed = speed;
  hold.has_ceiling = false;
  return hold;
}

// 距離計を持つ接続からの上昇の hold（天井 MEASURED・十分遠い・送った瞬間は新しい）
HoldMsg up_hold(int press, int speed, uint32_t at_ms, int age_ms = 80) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = LiftDir::UP;
  hold.speed = speed;
  hold.has_ceiling = true;
  hold.ceiling.status = CeilingStatus::MEASURED;
  hold.ceiling.mm = 1450;
  hold.ceiling.age_ms = static_cast<uint32_t>(age_ms);
  hold.ceiling.received_at_ms = at_ms;
  return hold;
}

HoldMsg down_hold(int press, int speed = 40) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = LiftDir::DOWN;
  hold.speed = speed;
  hold.has_ceiling = false;
  return hold;
}

// エンコーダ 1 周のカウント（ENC_PPR 360 の 4 逓倍）。設計値で 37.5 mm。
constexpr int32_t kCountsPerRev = 4 * 360;

void assert_reason(StopReason expected, const LiftState& state) {
  TEST_ASSERT_EQUAL_INT(static_cast<int>(expected), static_cast<int>(state.reason));
}

// 連続駆動の上限（#4b）の試験用。100 ms ごとに hold を出し直して step し、偽 HAL のエンコーダを
// 「目標とは別の」速さ enc_mm_s で進める（モータが回っているときだけ。向きは dir に合わせる）。
// 高さは 500 mm に置く（上端の閾値 TOP に当たらない）。最初に MAX_RUN 以外で止まった時刻を覚える。
struct RunRig {
  FakeHal hal;
  LiftController ctrl;
  double count_acc = 0.0;
  LiftState last;
  explicit RunRig(int top_mm) : ctrl(&hal, top_mm) {}

  // t_ms を start..end（含む）で 100 ms 刻み。戻り値は最後の状態。
  // ceiling_mm はモジュールの天井の距離（モジュールの接続のときだけ使う）
  LiftState run(uint32_t start_ms, uint32_t end_ms, const ConnId& conn, int press, LiftDir dir,
                int target_mm_s, double enc_mm_s, int ceiling_mm = 1450) {
    for (uint32_t t = start_ms; t <= end_ms; t += 100) {
      if (hal.motor_moving()) {
        const double um = enc_mm_s * 100.0;  // 100 ms ぶん [µm]
        count_acc += (dir == LiftDir::UP ? ENC_UP_SIGN : -ENC_UP_SIGN) * um / LIFT_UM_PER_COUNT;
      }
      hal.set_encoder_count(static_cast<int32_t>(count_acc));
      hal.set_height(500, true, t);
      HoldMsg hold = up_hold(press, target_mm_s, t);
      hold.dir = dir;
      hold.ceiling.mm = ceiling_mm;
      if (conn.kind == ConnKind::UI) {
        hold.has_ceiling = false;
      }
      ctrl.on_hold(conn, hold, t);
      last = ctrl.step(t);
    }
    return last;
  }
};

// #8a で止まらない程度にエンコーダを進めながら回すための、100 ms あたりのカウント。
// 500 ms で LIFT_STALL_MIN_COUNTS 以上になる（切り上げ）。
constexpr int32_t kAdvanceCountsPer100Ms = (LIFT_STALL_MIN_COUNTS + 4) / 5;

int32_t advance_count_at(uint32_t t_ms) {
  return kAdvanceCountsPer100Ms * static_cast<int32_t>(t_ms / 100);
}

// 目標（既定 40 mm/s）より十分遅い速さでエンコーダを進めながら上昇させる。
// 出力を上限まで育てたいので、#8a で止まらないことを優先する。係数の値に依らない。
void drive_up_with_progress(LiftController* ctrl, FakeHal* hal, uint32_t end_ms,
                            const ConnId& conn, int press, int speed = 40) {
  for (uint32_t t = 0; t <= end_ms; t += 100) {
    hal->set_height(500, true, t);
    hal->set_encoder_count(advance_count_at(t));
    ctrl->on_hold(conn, up_hold(press, speed, t), t);
    ctrl->step(t);
  }
}

}  // namespace

// --- 起動直後とウォッチドッグ ---

void test_motor_stays_stopped_before_any_command() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(state.dir));
  assert_reason(StopReason::CMD_TIMEOUT, state);
  TEST_ASSERT_FALSE(hal.motor_moving());
}

void test_ui_up_without_ceiling_reaches_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  // /ws/ui の上昇では天井の値を求めない（spec #4c）。ceiling 無しで動く
  HoldMsg hold;
  hold.press = 1;
  hold.dir = LiftDir::UP;
  hold.speed = 40;
  hold.has_ceiling = false;
  ctrl.on_hold(ui(1), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
  TEST_ASSERT_TRUE(hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_INT(40, state.speed);
  TEST_ASSERT_TRUE(state.has_owner);
  TEST_ASSERT_EQUAL_INT(0, state.owner_kind);  // ui
  TEST_ASSERT_FALSE(state.ceiling_used);
}

void test_watchdog_stops_motor_at_600ms() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  TEST_ASSERT_TRUE(hal.motor_moving());

  // エンコーダも進める（進めないと #8a が先に止める）
  hal.set_encoder_count(120);
  ctrl.step(600);
  TEST_ASSERT_TRUE(hal.motor_moving());  // ちょうど 600 ms までは動かしてよい

  const LiftState state = ctrl.step(601);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::CMD_TIMEOUT, state);
  TEST_ASSERT_FALSE(state.has_owner);
}

// --- hello と距離計の有無 ---

void test_module_hold_without_hello_is_treated_as_having_sensor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  // hello を受けていない /ws/module の接続は「距離計を持つ」とみなす。
  // ceiling の無い hold では上昇させない
  HoldMsg hold;
  hold.press = 1;
  hold.dir = LiftDir::UP;
  hold.speed = 40;
  hold.has_ceiling = false;
  ctrl.on_hold(module(2), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_module_hold_without_ceiling_stops_with_stale() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  HoldMsg hold;
  hold.press = 1;
  hold.dir = LiftDir::UP;
  hold.speed = 40;
  hold.has_ceiling = false;  // ceiling を持つ接続の ceiling 無し hold
  ctrl.on_hold(module(2), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_FALSE(state.ceiling_ok);
}

void test_second_hello_with_no_sensor_is_ignored() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  // 2 度目の hello の ceiling_sensor: false は受け付けない
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(false)));
  HoldMsg hold;
  hold.press = 1;
  hold.dir = LiftDir::UP;
  hold.speed = 40;
  hold.has_ceiling = false;
  ctrl.on_hold(module(2), hold, 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_ui_hello_closes_connection() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  // /ws/ui で hello を受けたら、その接続を閉じる（偽を返す）
  TEST_ASSERT_FALSE(ctrl.on_hello(ui(1), hello_with_sensor(true)));
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
}

void test_read_error_stops_but_no_echo_moves() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));

  HoldMsg error = up_hold(1, 40, 0);
  error.ceiling.status = CeilingStatus::READ_ERROR;
  ctrl.on_hold(module(2), error, 0);
  assert_reason(StopReason::CEILING_STALE, ctrl.step(0));
  TEST_ASSERT_FALSE(hal.motor_moving());

  HoldMsg echo = up_hold(2, 40, 100);
  echo.ceiling.status = CeilingStatus::NO_ECHO;
  ctrl.on_hold(module(2), echo, 100);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_TRUE(hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::OUT_OF_RANGE),
                        static_cast<int>(state.ceiling_reason));
}

// --- 持ち主の切り替えと停止 ---

void test_old_press_from_non_owner_does_not_steal() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 1000);
  ctrl.on_hold(ui(1), up_hold(5, 20, 1000), 1000);
  ctrl.on_hold(module(2), up_hold(3, 60, 1100), 1100);
  // 取って代わられた側の古い press では持ち主は替わらない（持ち主は module のまま）
  HoldMsg old = up_hold(5, 20, 1200);
  ctrl.on_hold(ui(1), old, 1200);
  hal.set_height(500, true, 1200);
  const LiftState state = ctrl.step(1200);
  TEST_ASSERT_TRUE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(1, state.owner_kind);  // module
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_INT(60, state.speed);
}

void test_owner_close_stops_immediately_with_owner_gone() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  TEST_ASSERT_TRUE(ctrl.step(0).dir == LiftDir::UP);
  ctrl.on_close(1);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::OWNER_GONE, state);
  TEST_ASSERT_FALSE(state.has_owner);
}

// 持ち主が替わっても数え直さない（同じ方向が続く限り数え続ける。上限を逃れられない）。
// 30 mm/s・全行程 1800 mm の上限は 90 秒
void test_owner_change_does_not_reset_max_run() {
  RunRig rig(LIFT_TOP_MM);
  rig.run(0, 60000, ui(1), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  // 持ち主が替わる。ここで数え直すなら、90 秒を過ぎても止まらない
  rig.run(60100, 90000, module(2), 9, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  const LiftState state = rig.run(90100, 90100, module(2), 9, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

// 1. 30 mm/s・top 1800 で上げ続けて、60 秒では止まらず、90 秒を超えたら MAX_RUN
//    （固定 10 秒なら 10 秒で止まる）
void test_max_run_30mm_s_full_stroke_stops_just_after_90s() {
  RunRig rig(1800);
  rig.run(0, 60000, ui(1), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  rig.run(60100, 90000, ui(1), 1, LiftDir::UP, 30, 30.0);  // ちょうど 90 秒ぶんはまだ動かせる
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  const LiftState state = rig.run(90100, 90100, ui(1), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

// 2. 偽 HAL がエンコーダを目標の半分の速さで進めても、止まる時刻は 1 と同じ
//    （測った速さで数えていない）
void test_max_run_ignores_measured_speed() {
  RunRig rig(1800);
  rig.run(0, 90000, ui(1), 1, LiftDir::UP, 30, 15.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  const LiftState state = rig.run(90100, 90100, ui(1), 1, LiftDir::UP, 30, 15.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

// 3. 10 mm/s で長く動いたあと 60 mm/s に上げても、その場では止まらない。
//    止まる時刻は、進んだとみなした距離の合計どおり。
//    10 mm/s で 60 秒（601 000 µm。最初の 100 ms は回す前）→ 60 mm/s で残りを進む
//    （経過時間 × いまの目標の速さなら 60 100 ms × 60 mm/s = 3 606 000 µm で、上げた途端に止まる）
void test_max_run_sums_distance_when_target_speed_changes() {
  RunRig rig(1800);
  rig.run(0, 60000, ui(1), 1, LiftDir::UP, 10, 10.0);
  assert_reason(StopReason::NONE, rig.last);
  // 上げた直後の step は直前の目標（10）で足すので 601 000 µm。止まらない
  rig.run(60100, 60100, ui(1), 1, LiftDir::UP, 60, 60.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  // 601 000 + 60 × (t - 60 100) が 2 700 000 を超える最初の 100 ms 刻みは t = 95 100
  rig.run(60200, 95000, ui(1), 1, LiftDir::UP, 60, 60.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  const LiftState state = rig.run(95100, 95100, ui(1), 1, LiftDir::UP, 60, 60.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

// 4. top_mm 400 で作り、100 mm/s で下降し続けると 6 秒を超えて MAX_RUN
//    （全行程を 1800 や 1900 で数えると止まらない。上昇だと高さ 400 の TOP が先に当たるので下降）
void test_max_run_uses_top_mm_as_stroke_through_controller() {
  RunRig rig(400);
  rig.ctrl.set_motor_max_pct(LIFT_DUTY_ABS_MAX_PCT);
  rig.run(0, 6000, ui(1), 1, LiftDir::DOWN, 100, 100.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  const LiftState state = rig.run(6100, 6100, ui(1), 1, LiftDir::DOWN, 100, 100.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

// 6. 上昇を CEILING_NEAR で止めている間は足さない（回していないので）。
//    30 mm/s で 45 秒回し、天井が近くて 100 秒止まり、天井が離れたら残りの 45 秒で止まる
void test_max_run_does_not_count_while_stopped_by_ceiling_near() {
  RunRig rig(1800);
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(2), hello_with_sensor(true)));
  rig.run(0, 45000, module(2), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  // 天井が近い（CEILING_MARGIN_MM_DEFAULT 以下）。止まったまま 100 秒
  const LiftState near_state = rig.run(45100, 145000, module(2), 1, LiftDir::UP, 30, 30.0, 300);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, near_state);
  // 天井が離れた。止まっていた間を数えていれば、すぐ MAX_RUN になる
  rig.run(145100, 145100, module(2), 1, LiftDir::UP, 30, 30.0);
  assert_reason(StopReason::NONE, rig.last);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  // 回した距離は 45 秒 + 再開後。再開後の 45 秒ぶん（合計 90 秒）までは動かせる
  rig.run(145200, 190000, module(2), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  const LiftState state = rig.run(190100, 190200, module(2), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

// 6b. 命令は動かせる（止める理由は無い）が出力が 0 で回っていない間も足さない。
//     出力の上限を 0 % にすると duty が 0 になる。判定の速さは 30 のままなので、
//     「回していた間だけ」の条件が無いと、止まっていた 100 秒ぶんが足されてすぐ MAX_RUN になる
void test_max_run_does_not_count_while_duty_is_zero() {
  RunRig rig(1800);
  rig.run(0, 45000, ui(1), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  rig.ctrl.set_motor_max_pct(0);
  rig.run(45100, 145000, ui(1), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  rig.ctrl.set_motor_max_pct(LIFT_MOTOR_MAX_PCT_DEFAULT);
  rig.run(145100, 190000, ui(1), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  const LiftState state = rig.run(190100, 190100, ui(1), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

// 向きを替えたら数え直す（release を挟まない）。MAX_RUN で止まったあと、
// 別の接続が新しい press で下降を送ると（最後の操作が勝つ）、下降で動く
void test_max_run_then_opposite_direction_from_other_connection_moves() {
  RunRig rig(1800);
  rig.run(0, 90100, ui(1), 1, LiftDir::UP, 30, 30.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, rig.last);
  rig.run(90200, 90300, ui(2), 2, LiftDir::DOWN, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(rig.hal.motor_dir()));
  assert_reason(StopReason::NONE, rig.last);
}

// 上昇で途中まで数えたあと離さずに下降へ切り替えると、下降は下降の距離だけで上限まで動ける
void test_direction_switch_restarts_count_for_full_limit() {
  RunRig rig(1800);
  rig.run(0, 60000, ui(1), 1, LiftDir::UP, 30, 30.0);  // 1 800 000 µm
  assert_reason(StopReason::NONE, rig.last);
  rig.run(60100, 60100 + 90000, ui(2), 2, LiftDir::DOWN, 30, 30.0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
  const LiftState state = rig.run(60100 + 90100, 60100 + 90100, ui(2), 2, LiftDir::DOWN, 30, 30.0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::MAX_RUN, state);
}

void test_same_press_after_release_does_not_restart() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  TEST_ASSERT_TRUE(ctrl.step(0).dir == LiftDir::UP);
  ctrl.on_release(ui(1), 1);
  assert_reason(StopReason::CMD_STOP, ctrl.step(100));
  // 同じ接続の同じ press では動き出さない
  ctrl.on_hold(ui(1), up_hold(1, 40, 200), 200);
  const LiftState state = ctrl.step(200);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CMD_STOP, state);
}

void test_same_press_after_timeout_does_not_restart() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  assert_reason(StopReason::CMD_TIMEOUT, ctrl.step(1000));
  ctrl.on_hold(ui(1), up_hold(1, 40, 1100), 1100);
  const LiftState state = ctrl.step(1100);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CMD_TIMEOUT, state);
}

void test_same_press_after_close_does_not_restart() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.on_close(1);
  assert_reason(StopReason::OWNER_GONE, ctrl.step(100));
  ctrl.on_hold(ui(1), up_hold(1, 40, 200), 200);
  const LiftState state = ctrl.step(200);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::OWNER_GONE, state);
}

void test_wrong_typed_ceiling_stops_at_once() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  ctrl.on_hold(module(2), up_hold(1, 40, 0), 0);
  TEST_ASSERT_TRUE(ctrl.step(0).dir == LiftDir::UP);
  // 型の違う ceiling の hold は捨てず MISSING として受ける。前の値で動き続けない
  HoldMsg broken = up_hold(1, 40, 100);
  broken.has_ceiling = false;
  ctrl.on_hold(module(2), broken, 100);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_ceiling_age_counts_transit_through_controller() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 1000);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  // 送る瞬間に 590 ms 古い値。受け取ってから 20 ms 経てば CEILING_STALE_MS 超
  ctrl.on_hold(module(2), up_hold(1, 40, 1000, 590), 1000);
  TEST_ASSERT_TRUE(ctrl.step(1000).dir == LiftDir::UP);
  hal.set_height(500, true, 1020);  // 高さは新しいまま（#4 で止めない）
  const LiftState state = ctrl.step(1020);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

// --- 速さのフィードバック（§4.1.2。モータに出した値で縛る） ---

// (a) 目標に届かないとモータへ出したデューティが上がっていく（上限を緩めた状態で見る）
void test_duty_rises_while_speed_stays_below_target() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  ctrl.set_motor_max_pct(LIFT_DUTY_ABS_MAX_PCT);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);  // 進まない（#8a の 500 ms より短く回す）
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  const int d0 = ctrl.step(0).duty;
  ctrl.on_hold(ui(1), up_hold(1, 40, 100), 100);
  const int d1 = ctrl.step(100).duty;
  ctrl.on_hold(ui(1), up_hold(1, 40, 200), 200);
  const int d2 = ctrl.step(200).duty;
  TEST_ASSERT_TRUE(hal.motor_moving());
  TEST_ASSERT_GREATER_THAN(d0, d1);
  TEST_ASSERT_GREATER_THAN(d1, d2);
}

// (a2) 既定の上限（50 %）では出力が 50 を超えない（Spec-ui §2.1）。
// 上限に当たるまでの時間は係数の値で変わるので、十分に長く回して縛る。
void test_duty_never_exceeds_default_motor_cap() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);  // 既定 50 %
  // 上限を外した場合の出力。上限が効いていることを係数の値に依らず示す
  SpeedLoop uncapped;
  bool uncapped_exceeds_cap = false;
  for (uint32_t t = 0; t <= 2000; t += 100) {
    hal.set_height(500, true, t);
    hal.set_encoder_count(advance_count_at(t));
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    const LiftState state = ctrl.step(t);
    const float measured = (t == 0) ? 0.0f : static_cast<float>(kAdvanceCountsPer100Ms) *
                                                 static_cast<float>(LIFT_UM_PER_COUNT) / 100.0f;
    const uint32_t dt = (t == 0) ? LIFT_SPEED_CTRL_MS : 100;
    if (uncapped.update(40, measured, LiftDir::UP, dt, LIFT_DUTY_ABS_MAX_PCT) >
        LIFT_MOTOR_MAX_PCT_DEFAULT) {
      uncapped_exceeds_cap = true;
    }
    TEST_ASSERT_TRUE(hal.motor_moving());
    TEST_ASSERT_LESS_OR_EQUAL_INT(LIFT_MOTOR_MAX_PCT_DEFAULT, hal.motor_duty());
    TEST_ASSERT_LESS_OR_EQUAL_INT(LIFT_MOTOR_MAX_PCT_DEFAULT, state.duty);
  }
  // 上限が無ければ既定の上限を超える状況まで回している（空振りの試験にしない）
  TEST_ASSERT_TRUE(uncapped_exceeds_cap);
}

// (a3) 動いている最中に上限を下げたら次の周期から効く
void test_lowering_cap_mid_motion_applies_on_next_cycle() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  drive_up_with_progress(&ctrl, &hal, 1500, ui(1), 1);
  const int grown = hal.motor_duty();
  // 新しい上限（30）より上まで育っていることを確かめてから下げる
  TEST_ASSERT_GREATER_THAN(30, grown);
  TEST_ASSERT_LESS_OR_EQUAL_INT(LIFT_MOTOR_MAX_PCT_DEFAULT, grown);
  ctrl.set_motor_max_pct(30);
  hal.set_height(500, true, 1600);
  hal.set_encoder_count(advance_count_at(1600));
  ctrl.on_hold(ui(1), up_hold(1, 40, 1600), 1600);
  const LiftState state = ctrl.step(1600);
  TEST_ASSERT_TRUE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(30, hal.motor_duty());
  TEST_ASSERT_EQUAL_INT(30, state.duty);
}

// (b) 数が進まなければ LIFT_STALL_MS 後に ENCODER_STALL でモータが 0
void test_stall_when_encoder_never_moves() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  for (uint32_t t = 0; t <= 500; t += 100) {
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    const LiftState state = ctrl.step(t);
    TEST_ASSERT_TRUE(hal.motor_moving());
    assert_reason(StopReason::NONE, state);
  }
  ctrl.on_hold(ui(1), up_hold(1, 40, 600), 600);
  const LiftState stalled = ctrl.step(600);
  TEST_ASSERT_FALSE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(0, hal.motor_duty());
  assert_reason(StopReason::ENCODER_STALL, stalled);
}

// (c) 逆向きに進んでも (b) と同じ
void test_stall_when_encoder_runs_backwards() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  for (uint32_t t = 0; t <= 500; t += 100) {
    // 上昇の命令なのに数は減っていく（逆向き）
    hal.set_encoder_count(-static_cast<int32_t>(t / 5));
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    TEST_ASSERT_TRUE(ctrl.step(t).dir == LiftDir::UP);
  }
  hal.set_encoder_count(-120);
  ctrl.on_hold(ui(1), up_hold(1, 40, 600), 600);
  const LiftState stalled = ctrl.step(600);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::ENCODER_STALL, stalled);
}

// (c2) 500 ms で 0.8 mm に満たない進み（20 カウント ≈ 520 µm）は #8a で止まる。
// 仮の 100 µm のままだと 20 カウント＝2 mm で「進んだ」と数えて止まらない。
// 閾値を 8 カウントに戻しても、20 カウントで起点を取り直して止まらない。
void test_stall_when_progress_is_under_800_um() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  for (uint32_t t = 0; t <= 500; t += 100) {
    // 500 ms で 20 カウントだけ進める
    hal.set_encoder_count(static_cast<int32_t>(20 * t / 500));
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    TEST_ASSERT_TRUE(ctrl.step(t).dir == LiftDir::UP);
  }
  hal.set_encoder_count(20);
  ctrl.on_hold(ui(1), up_hold(1, 40, 600), 600);
  const LiftState stalled = ctrl.step(600);
  TEST_ASSERT_FALSE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(0, hal.motor_duty());
  assert_reason(StopReason::ENCODER_STALL, stalled);
}

// (c3) 800 µm 以上進んでいれば止まらない（100 ms ごとに 20 カウント ≈ 520 µm）
void test_no_stall_while_progress_reaches_800_um() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  for (uint32_t t = 0; t <= 600; t += 100) {
    hal.set_encoder_count(static_cast<int32_t>(20 * (t / 100)));
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    const LiftState state = ctrl.step(t);
    assert_reason(StopReason::NONE, state);
  }
  TEST_ASSERT_TRUE(hal.motor_moving());
}

// (c4) 速さの測定は LIFT_UM_PER_COUNT を使う。1440 カウント / 100 ms は
// 37.5 mm / 100 ms ＝ 約 374 mm/s。100 µm で測ると 1440 mm/s になる。
void test_speed_meas_uses_um_per_count() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  hal.set_encoder_count(kCountsPerRev);
  ctrl.on_hold(ui(1), up_hold(1, 40, 100), 100);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_EQUAL_INT(374, state.speed_meas);
}

// (d) 止まって押し直すと積分が残っていない（最初の出力が前向き＋比例だけ）
void test_integral_is_reset_after_stop_and_repress() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  // 目標に届かない状態で十分に回して積分を育てる（係数の値に依らず上限近くまで上がる）
  drive_up_with_progress(&ctrl, &hal, 1500, ui(1), 1);
  const int grown = hal.motor_duty();
  TEST_ASSERT_GREATER_THAN(30, grown);
  // エンコーダを止めて 100 ms 待ち、測った速さを 0 に戻す
  hal.set_height(500, true, 1600);
  hal.set_encoder_count(advance_count_at(1500));
  ctrl.on_hold(ui(1), up_hold(1, 40, 1600), 1600);
  ctrl.step(1600);
  // 離して押し直す（新しい press）
  ctrl.on_release(ui(1), 1);
  assert_reason(StopReason::CMD_STOP, ctrl.step(1700));
  hal.set_height(500, true, 1800);
  hal.set_encoder_count(advance_count_at(1500));
  ctrl.on_hold(ui(1), up_hold(2, 40, 1800), 1800);
  const int fresh = ctrl.step(1800).duty;
  // 測った速さ 0・経過 20 ms の前向き＋比例だけ。積分が残っていれば grown 並みに大きい
  SpeedLoop expect;
  TEST_ASSERT_EQUAL_INT(
      expect.update(40, 0.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS, LIFT_MOTOR_MAX_PCT_DEFAULT),
      fresh);
  TEST_ASSERT_LESS_THAN(grown, fresh);
}

// (d2) 持ち主が同じ press のまま押し続けていて、条件で止まり、条件が戻って再開したときも
// 積分を持ち越さない（止まったときの reset を縛る）
void test_integral_is_reset_after_stop_while_owner_keeps_holding() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  // 距離計を持つ接続で目標に届かない状態を作り、積分を育てる
  for (uint32_t t = 0; t <= 1500; t += 100) {
    hal.set_height(500, true, t);
    hal.set_encoder_count(advance_count_at(t));
    ctrl.on_hold(module(2), up_hold(1, 40, t), t);
    ctrl.step(t);
  }
  TEST_ASSERT_GREATER_THAN(30, hal.motor_duty());
  // エンコーダを止めて、天井の値が古い hold で止める（持ち主と press はそのまま）
  hal.set_height(500, true, 1600);
  hal.set_encoder_count(advance_count_at(1500));
  ctrl.on_hold(module(2), up_hold(1, 40, 1600, 1000), 1600);
  const LiftState stopped = ctrl.step(1600);
  assert_reason(StopReason::CEILING_STALE, stopped);
  TEST_ASSERT_FALSE(hal.motor_moving());
  // 同じ press のまま新しい天井の値で再開する。積分が残っていれば grown 並みに大きい
  hal.set_height(500, true, 1700);
  hal.set_encoder_count(advance_count_at(1500));
  ctrl.on_hold(module(2), up_hold(1, 40, 1700), 1700);
  const int resumed = ctrl.step(1700).duty;
  SpeedLoop expect;
  TEST_ASSERT_EQUAL_INT(
      expect.update(40, 0.0f, LiftDir::UP, LIFT_SPEED_CTRL_MS, LIFT_MOTOR_MAX_PCT_DEFAULT),
      resumed);
}

// (e) stalled のあと反対方向へは動かせる
void test_opposite_direction_moves_after_stall() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_encoder_count(0);
  for (uint32_t t = 0; t <= 600; t += 100) {
    ctrl.on_hold(ui(1), up_hold(1, 40, t), t);
    ctrl.step(t);
  }
  assert_reason(StopReason::ENCODER_STALL, ctrl.state());
  // 反対方向（下降）へは動かせる
  ctrl.on_hold(ui(1), down_hold(2, 40), 700);
  const LiftState state = ctrl.step(700);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(hal.motor_dir()));
  assert_reason(StopReason::NONE, state);
}

// 持ち主が替わっても積分は持ち越さない
void test_integral_is_reset_when_owner_changes() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  // 目標に届かない状態で十分に回して積分を育てる（係数の値に依らず上限近くまで上がる）
  drive_up_with_progress(&ctrl, &hal, 1500, ui(1), 1);
  const int grown = hal.motor_duty();
  TEST_ASSERT_GREATER_THAN(30, grown);
  // エンコーダを止めて 100 ms 待ち、測った速さを 0 に戻してから別の持ち主へ引き継ぐ。
  // press は同じにして、持ち主の接続が替わったことだけを積分のリセットの条件にする
  hal.set_height(500, true, 1600);
  hal.set_encoder_count(advance_count_at(1500));
  ctrl.on_hold(module(2), up_hold(1, 40, 1600), 1600);
  const LiftState state = ctrl.step(1600);
  assert_reason(StopReason::NONE, state);
  SpeedLoop expect;
  TEST_ASSERT_EQUAL_INT(expect.update(40, 0.0f, LiftDir::UP, 100, LIFT_MOTOR_MAX_PCT_DEFAULT),
                        hal.motor_duty());
  TEST_ASSERT_LESS_THAN(grown, hal.motor_duty());
}

// --- release と下端・速さの丸め ---

void test_release_from_owner_stops_with_cmd_stop() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.on_release(ui(1), 1);
  const LiftState state = ctrl.step(100);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CMD_STOP, state);
}

void test_release_from_non_owner_is_ignored() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.on_release(module(2), 1);
  TEST_ASSERT_TRUE(ctrl.step(100).dir == LiftDir::UP);
}

void test_bottom_pressed_stops_down_motor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  hal.set_bottom(true);
  ctrl.on_hold(ui(1), down_hold(1), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::BOTTOM, state);
  TEST_ASSERT_TRUE(state.bottom);
}

void test_speed_is_clamped_before_speed_loop() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  // LIFT_SPEED_ABS_MAX_MM_S を超える速さは SpeedLoop に渡す前に丸める。
  // 出力の上限は別の試験が縛る（ここでは係数の値に依らないこれ 1 点だけ）
  ctrl.on_hold(ui(1), up_hold(1, LIFT_SPEED_ABS_MAX_MM_S + 50, 0), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(LIFT_SPEED_ABS_MAX_MM_S, state.speed);
  TEST_ASSERT_TRUE(hal.motor_moving());
}

void test_motor_is_written_on_every_step() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0), 0);
  ctrl.step(0);
  ctrl.step(100);
  ctrl.step(200);
  TEST_ASSERT_EQUAL_INT(3, hal.motor_calls());
}

// --- state の中身 ---

void test_state_reports_top_detect_true_and_module() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(812, true, 900);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  ctrl.set_ui_clients(1);
  ctrl.on_hold(module(2), up_hold(17, 40, 1000), 1000);
  const LiftState state = ctrl.step(1035);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(state.dir));
  TEST_ASSERT_TRUE(state.duty > 0);
  TEST_ASSERT_EQUAL_INT(40, state.speed);
  TEST_ASSERT_EQUAL_INT(0, state.speed_meas);
  TEST_ASSERT_TRUE(state.top_detect);  // LIFT_TOP_MM が設定済み
  TEST_ASSERT_TRUE(state.ceiling_present);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MEASURED),
                        static_cast<int>(state.ceiling_status));
  TEST_ASSERT_EQUAL_INT(1450, state.ceiling_mm);
  TEST_ASSERT_EQUAL_INT(80 + 35, state.ceiling_age_ms);
  TEST_ASSERT_TRUE(state.ceiling_ok);
  TEST_ASSERT_EQUAL_INT(1, state.module_count);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.modules[0].name);
  TEST_ASSERT_TRUE(state.modules[0].ceiling_sensor);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.owner_name);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.ceiling_from);
  TEST_ASSERT_EQUAL_INT(1, state.ui_clients);
  TEST_ASSERT_EQUAL_INT(35, state.cmd_age_ms);
  TEST_ASSERT_EQUAL_INT(812, state.height_mm);
  TEST_ASSERT_TRUE(state.height_ok);
}

void test_state_keeps_last_module_ceiling_after_release() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(812, true, 900);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  ctrl.on_hold(module(2), up_hold(17, 40, 1000), 1000);
  ctrl.step(1035);
  ctrl.on_release(module(2), 17);
  const LiftState state = ctrl.step(1200);
  TEST_ASSERT_FALSE(state.has_owner);
  TEST_ASSERT_TRUE(state.ceiling_present);
  TEST_ASSERT_FALSE(state.ceiling_used);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(CeilingStatus::MEASURED),
                        static_cast<int>(state.ceiling_status));
  TEST_ASSERT_EQUAL_INT(1450, state.ceiling_mm);
  TEST_ASSERT_EQUAL_INT(80 + 200, state.ceiling_age_ms);
  // 古さは増え続ける
  const LiftState later = ctrl.step(5000);
  TEST_ASSERT_TRUE(later.ceiling_present);
  TEST_ASSERT_EQUAL_INT(80 + 4000, later.ceiling_age_ms);
  // 止める判断は変えない
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(later.dir));
}

void test_state_keeps_last_module_ceiling_after_hold_timeout() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(812, true, 900);
  TEST_ASSERT_TRUE(ctrl.on_hello(module(2), hello_with_sensor(true)));
  ctrl.on_hold(module(2), up_hold(17, 40, 1000), 1000);
  ctrl.step(1035);
  const LiftState state = ctrl.step(9000);
  TEST_ASSERT_FALSE(state.has_owner);
  TEST_ASSERT_TRUE(state.ceiling_present);
  TEST_ASSERT_EQUAL_INT(1450, state.ceiling_mm);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(state.dir));
}

void test_state_ceiling_null_when_no_module_hold_ever() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(812, true, 900);
  // 画面（UI）の hold と release だけ。上部モジュールの値は無い
  ctrl.on_hold(ui(1), down_hold(1), 1000);
  ctrl.step(1035);
  ctrl.on_release(ui(1), 1);
  const LiftState state = ctrl.step(1200);
  TEST_ASSERT_FALSE(state.ceiling_present);
}

void test_state_before_step_is_stopped() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  const LiftState& state = ctrl.state();
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::STOP), static_cast<int>(state.dir));
  TEST_ASSERT_EQUAL_INT(0, state.duty);
  TEST_ASSERT_TRUE(state.top_detect);
  TEST_ASSERT_FALSE(state.ceiling_present);
}

// 上端が未設定（-1。#2a）のときは top_detect が偽で、高さが大きくても上昇できる
void test_top_unset_reports_top_detect_false_and_still_climbs() {
  FakeHal hal;
  LiftController ctrl(&hal, -1);
  TEST_ASSERT_FALSE(ctrl.state().top_detect);
  hal.set_height(5000, true, 1000);
  hal.set_encoder_count(40);
  ctrl.on_hold(ui(1), up_hold(1, 40, 1000), 1000);
  const LiftState state = ctrl.step(1000);
  TEST_ASSERT_FALSE(state.top_detect);
  TEST_ASSERT_TRUE(hal.motor_moving());
  assert_reason(StopReason::NONE, state);
}

// --- millis() が一周したあと ---

void test_cmd_timeout_survives_millis_wrap() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0x00000400u);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0xFFFFFF00u), 0xFFFFFF00u);
  const LiftState state = ctrl.step(0x00000400u);  // 経過 1280 ms
  TEST_ASSERT_FALSE(hal.motor_moving());
  assert_reason(StopReason::CMD_TIMEOUT, state);
}

void test_height_ok_in_state_survives_millis_wrap_when_fresh() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0xFFFFFF00u);
  hal.set_encoder_count(0x00000100);
  ctrl.on_hold(ui(1), up_hold(1, 40, 0x00000100u), 0x00000100u);
  const LiftState state = ctrl.step(0x00000100u);  // 経過 512 ms
  TEST_ASSERT_TRUE(state.height_ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::UP), static_cast<int>(hal.motor_dir()));
}

void test_height_unknown_shows_in_state() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  // 原点を取る前（height_ok が偽）は state.height_ok も偽。下降はできる
  hal.set_height(0, false, 0);
  ctrl.on_hold(ui(1), down_hold(1), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(state.height_ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(hal.motor_dir()));
}

// --- 下端スイッチの一瞬の誤り（WP-LIFT-10。EncoderHeight を載せた偽 HAL を通す）---

namespace {

// 10 ms ごとに poll し、100 ms ごとに持ち主の上昇の hold と step を回す。
// 上昇は約 99 mm/s（10 ms で 38 カウント）。end_ms まで進めて最後の step の状態を返す。
struct EncoderRig {
  EncoderFakeHal hal;
  LiftController ctrl{&hal, LIFT_TOP_MM};
  uint32_t t = 0;
  LiftState last;

  void run_to(uint32_t end_ms, int32_t counts_per_10ms) {
    for (; t <= end_ms; t += 10) {
      hal.set_count(hal.encoder_count() + counts_per_10ms);
      hal.poll(t);
      if (t % 100 == 0) {
        ctrl.on_hold(module(2), up_hold(1, 100, t), t);
        last = ctrl.step(t);
      }
    }
  }

  // 下端に着けて原点を取り、離れる
  void land_and_leave() {
    hal.set_raw_pressed(true);
    for (uint32_t i = 0; i < 6; ++i, t += 10) {
      hal.poll(t);
    }
    hal.set_raw_pressed(false);
    hal.poll(t);
    t = ((t / 100) + 1) * 100;
    ctrl.on_hello(module(2), hello_with_sensor(true));
  }
};

}  // namespace

void test_glitch_during_climb_does_not_reset_height() {
  EncoderRig rig;
  rig.land_and_leave();
  // 高いところまで上がった状態にする（上端 LIFT_TOP_MM の手前。連続駆動の上限があるので、数だけ進めておく）
  rig.hal.set_count(1000 * 1000 / LIFT_UM_PER_COUNT);
  rig.hal.poll(rig.t);
  rig.run_to(rig.t + 1000, 38);
  TEST_ASSERT_TRUE(rig.last.height_ok);
  TEST_ASSERT_TRUE(rig.last.height_mm > 1000);
  TEST_ASSERT_TRUE(rig.last.height_mm < LIFT_TOP_MM - 300);
  // 上昇中に 1 回だけ「押された」と読めた
  rig.hal.set_raw_pressed(true);
  rig.hal.set_count(rig.hal.encoder_count() + 38);
  rig.hal.poll(rig.t);
  rig.t += 10;
  rig.hal.set_raw_pressed(false);
  rig.run_to(rig.t + 300, 38);
  TEST_ASSERT_TRUE(rig.last.height_ok);
  TEST_ASSERT_TRUE(rig.last.height_mm > 1000);  // 0 に戻らない
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.last);
}

void test_long_press_while_high_makes_height_unknown() {
  EncoderRig rig;
  rig.land_and_leave();
  rig.run_to(5000, 38);
  TEST_ASSERT_TRUE(rig.last.height_ok);
  const int before = rig.last.height_mm;
  TEST_ASSERT_TRUE(before > 300);
  rig.hal.set_raw_pressed(true);
  rig.run_to(rig.t + 300, 38);
  // 原点にしない（0 に戻らない）。高さは読めないことになる
  TEST_ASSERT_FALSE(rig.last.height_ok);
  TEST_ASSERT_TRUE(rig.last.height_mm >= before);
  // 判定 #4 で上昇が止まる
  assert_reason(StopReason::HEIGHT_UNKNOWN, rig.last);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
}

// 下降の BOTTOM は読んだそのまま。1 回読めただけでも止まる（時間の続きを掛けない）
void test_bottom_stops_down_on_a_single_raw_read() {
  EncoderRig rig;
  rig.land_and_leave();
  rig.run_to(3000, 38);
  rig.ctrl.on_hold(module(2), down_hold(2), rig.t);
  rig.hal.set_raw_pressed(true);
  rig.hal.poll(rig.t);  // 押されたと読めたのはこの 1 回だけ
  const LiftState state = rig.ctrl.step(rig.t);
  assert_reason(StopReason::BOTTOM, state);
  TEST_ASSERT_TRUE(state.bottom);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
}

// --- 上端の検知と高さが読めないとき（W-1 を解いた。判定 #2・#4。モータに出た値で縛る）---

// 原点を取る前（エンコーダの高さが無い）は上昇しない。下降はできる
void test_up_before_origin_is_refused_but_down_works() {
  EncoderRig rig;  // 下端に着けていない
  rig.ctrl.on_hello(module(2), hello_with_sensor(true));
  rig.hal.poll(0);
  rig.ctrl.on_hold(module(2), up_hold(1, 40, 100), 100);
  rig.hal.poll(100);
  const LiftState up = rig.ctrl.step(100);
  TEST_ASSERT_FALSE(up.height_ok);
  assert_reason(StopReason::HEIGHT_UNKNOWN, up);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  rig.ctrl.on_hold(module(2), down_hold(2), 200);
  rig.hal.poll(200);
  const LiftState down = rig.ctrl.step(200);
  assert_reason(StopReason::NONE, down);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(rig.hal.motor_dir()));
}

// 下端に着けて原点を取ると上昇できる
void test_up_after_landing_on_bottom_works() {
  EncoderRig rig;
  rig.land_and_leave();
  rig.run_to(rig.t + 500, 38);
  assert_reason(StopReason::NONE, rig.last);
  TEST_ASSERT_TRUE(rig.last.height_ok);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

// 高さが LIFT_TOP_MM に届く手前は上昇でき、ちょうど届いたら止まる（境界）
void test_up_below_top_moves_and_at_top_stops() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(LIFT_TOP_MM - 1, true, 1000);
  hal.set_encoder_count(40);
  ctrl.on_hold(ui(1), up_hold(1, 40, 1000), 1000);
  const LiftState below = ctrl.step(1000);
  assert_reason(StopReason::NONE, below);
  TEST_ASSERT_TRUE(hal.motor_moving());
  hal.set_height(LIFT_TOP_MM, true, 1100);
  ctrl.on_hold(ui(1), up_hold(1, 40, 1100), 1100);
  const LiftState at = ctrl.step(1100);
  assert_reason(StopReason::TOP, at);
  TEST_ASSERT_FALSE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(0, at.duty);
}

// 上端を超えていても上昇しない。下降はできる
void test_above_top_refuses_up_but_down_works() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(LIFT_TOP_MM + 50, true, 1000);
  ctrl.on_hold(ui(1), up_hold(1, 40, 1000), 1000);
  assert_reason(StopReason::TOP, ctrl.step(1000));
  TEST_ASSERT_FALSE(hal.motor_moving());
  hal.set_height(LIFT_TOP_MM + 50, true, 1100);
  ctrl.on_hold(ui(1), down_hold(2), 1100);
  const LiftState down = ctrl.step(1100);
  assert_reason(StopReason::NONE, down);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(hal.motor_dir()));
  TEST_ASSERT_TRUE(hal.motor_moving());
}

// 高さの読み値が HEIGHT_STALE_MS を超えて古いと上昇しない（天井は新しいまま）。ちょうどなら動く
void test_up_with_stale_height_is_refused() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 1000);
  hal.set_encoder_count(40);
  ctrl.on_hold(ui(1), up_hold(1, 40, 1000 + HEIGHT_STALE_MS), 1000 + HEIGHT_STALE_MS);
  const LiftState edge = ctrl.step(1000 + HEIGHT_STALE_MS);  // 経過がちょうど HEIGHT_STALE_MS
  assert_reason(StopReason::NONE, edge);
  TEST_ASSERT_TRUE(hal.motor_moving());
  const uint32_t late = 1000 + HEIGHT_STALE_MS + 1;
  ctrl.on_hold(ui(1), up_hold(1, 40, late), late);
  const LiftState stale = ctrl.step(late);
  assert_reason(StopReason::HEIGHT_UNKNOWN, stale);
  TEST_ASSERT_FALSE(stale.height_ok);
  TEST_ASSERT_FALSE(hal.motor_moving());
}

// --- 上部モジュールの複数対応（WP-LIFT-13。protocol-modules §1・§2） ---

// 高さが読めている状態で、fk の上昇を time に step する。ceiling の値は呼び出し側が先に入れる
struct MultiRig {
  FakeHal hal;
  LiftController ctrl;
  MultiRig() : ctrl(&hal, LIFT_TOP_MM) { hal.set_height(500, true, 0); }
  // 時刻 t に高さを新しくし、fk（conn 3）が press で上昇を送り、step する
  LiftState fk_step(uint32_t t, int press = 1, int conn = 3) {
    hal.set_height(500, true, t);
    ctrl.on_hold(module(conn), fk_up(press), t);
    return ctrl.step(t);
  }
};

// 変異 1・4: カメラモジュールが止まっている間に ceiling で送った値で、fk の上昇が止まる
void test_fk_climb_stops_at_camera_ceiling_sent_by_ceiling_message() {
  MultiRig rig;
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(2), hello_cam()));
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(3), hello_fk()));
  // カメラモジュールは昇降を命じていない。ceiling だけ送っている
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.ceiling_from);
  TEST_ASSERT_EQUAL_INT(300, state.ceiling_mm);
  TEST_ASSERT_EQUAL_STRING("fk", state.owner_name);
}

void test_fk_climb_moves_when_camera_ceiling_is_far() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_TRUE(state.ceiling_ok);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.ceiling_from);
  // 近づいたら次の周期から止まる（止まっている間も送り続ける値が効く）
  rig.ctrl.on_ceiling(module(2), measured(400, 100));
  const LiftState near = rig.fk_step(100);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, near);
}

// 変異 4 の相方: hold に載った値（カメラモジュールが持ち主だったときの最後の値）も使う
void test_fk_uses_camera_hold_ceiling_too() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_hello(module(3), hello_fk());
  // カメラモジュールが下降の hold に近い天井を載せて送った（持ち主になって release した）
  HoldMsg cam_hold = up_hold(1, 40, 0);
  cam_hold.dir = LiftDir::DOWN;
  cam_hold.ceiling.mm = 300;
  rig.ctrl.on_hold(module(2), cam_hold, 0);
  rig.ctrl.step(0);
  rig.ctrl.on_release(module(2), 1);
  const LiftState state = rig.fk_step(10);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
}

// 変異 2: カメラモジュールが繋がっていない・値が古いとき、fk の上昇を許さない
void test_fk_climb_refused_without_camera() {
  MultiRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_FALSE(state.ceiling_ok);
}

void test_fk_climb_refused_when_camera_value_is_old() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  TEST_ASSERT_TRUE(rig.fk_step(0).dir == LiftDir::UP);
  // カメラモジュールが黙った。CEILING_STALE_MS を超えたら止まる（fk の hold は続いている）
  const uint32_t late = CEILING_STALE_MS + 1;
  const LiftState state = rig.fk_step(late - 300);  // まだ新しい
  TEST_ASSERT_TRUE(state.dir == LiftDir::UP);
  rig.fk_step(late - 100);
  const LiftState stale = rig.fk_step(late);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, stale);
  TEST_ASSERT_EQUAL_INT(late, stale.ceiling_age_ms);
}

void test_fk_climb_stops_when_camera_connection_closes() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  TEST_ASSERT_TRUE(rig.fk_step(0).dir == LiftDir::UP);
  rig.ctrl.on_close(2);  // カメラモジュールの電源が落ちた
  const LiftState state = rig.fk_step(10);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

// 変異 3: 値の古さに、受け取ってからの経過を足す
void test_fk_ceiling_age_counts_transit() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_hello(module(3), hello_fk());
  // 送る瞬間に 590 ms 古い値。受け取ってから 20 ms 経てば CEILING_STALE_MS 超
  rig.ctrl.on_ceiling(module(2), measured(1450, 1000, 590));
  TEST_ASSERT_TRUE(rig.fk_step(1000).dir == LiftDir::UP);
  const LiftState state = rig.fk_step(1020);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
  TEST_ASSERT_EQUAL_INT(610, state.ceiling_age_ms);
}

// カメラモジュールが 2 台のときは近い方（厳しい側）
void test_fk_uses_nearest_of_two_sensor_modules() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_hello(module(4), hello_as("hve-cam2", true));
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  rig.ctrl.on_ceiling(module(4), measured(400, 0));
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  TEST_ASSERT_EQUAL_STRING("hve-cam2", state.ceiling_from);
}

// カメラモジュールの下降には fk の天井の判定は関係ない
void test_fk_down_does_not_need_ceiling() {
  MultiRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  HoldMsg down = down_hold(1);
  rig.ctrl.on_hold(module(3), down, 0);
  const LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, state);
}

// 変異 5: observe の接続の hold・release では動かない・止められない
void test_observe_connection_hold_is_dropped() {
  MultiRig rig;
  rig.ctrl.on_hello(module(5), hello_as("viewer", true, ModuleRole::OBSERVE));
  rig.ctrl.on_hold(module(5), up_hold(1, 40, 0), 0);
  const LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  TEST_ASSERT_FALSE(state.has_owner);
  assert_reason(StopReason::CMD_TIMEOUT, state);
}

void test_observe_connection_release_does_not_stop_owner() {
  MultiRig rig;
  rig.ctrl.on_hello(module(5), hello_as("viewer", true, ModuleRole::OBSERVE));
  rig.ctrl.on_hold(ui(1), up_hold(7, 40, 0), 0);
  TEST_ASSERT_TRUE(rig.ctrl.step(0).dir == LiftDir::UP);
  rig.ctrl.on_release(module(5), 7);  // observe の release は捨てる
  rig.hal.set_height(500, true, 100);
  rig.ctrl.on_hold(ui(1), up_hold(7, 40, 100), 100);
  TEST_ASSERT_TRUE(rig.ctrl.step(100).dir == LiftDir::UP);
}

// 変異 6: role の知らない値は observe。decode_hello を通した実際の経路で縛る
void test_unknown_role_hello_cannot_command() {
  MultiRig rig;
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello(
      "{\"t\":\"hello\",\"name\":\"odd\",\"role\":\"superuser\",\"ceiling_sensor\":true}", &hello));
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(5), hello));
  rig.ctrl.on_hold(module(5), up_hold(1, 40, 0), 0);
  const LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  TEST_ASSERT_FALSE(state.has_owner);
}

void test_missing_role_hello_can_command() {
  MultiRig rig;
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"name\":\"hve-cam\",\"ceiling_sensor\":true}", &hello));
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(2), hello));
  rig.ctrl.on_hold(module(2), up_hold(1, 40, 0), 0);
  rig.ctrl.step(0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

// 変異 7: 同じ名前で繋ぎ直したら古い接続の番号を返す（呼び出し側が閉じる）。名前無しは置き換えない
void test_same_name_hello_returns_old_connection() {
  MultiRig rig;
  int replaced = -2;
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(3), hello_fk(), &replaced));
  TEST_ASSERT_EQUAL_INT(-1, replaced);
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(2), hello_cam(), &replaced));
  TEST_ASSERT_EQUAL_INT(-1, replaced);  // 別の名前
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(4), hello_fk(), &replaced));
  TEST_ASSERT_EQUAL_INT(3, replaced);
}

void test_nameless_hellos_do_not_replace_each_other() {
  MultiRig rig;
  HelloMsg nameless;  // 名前無し（形に合わない名前も decode が空にする）
  nameless.sensor_explicit = true;
  int replaced = -2;
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(3), nameless, &replaced));
  TEST_ASSERT_TRUE(rig.ctrl.on_hello(module(4), nameless, &replaced));
  TEST_ASSERT_EQUAL_INT(-1, replaced);
  const LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(2, state.module_count);
  TEST_ASSERT_EQUAL_STRING("anon-3", state.modules[0].name);
  TEST_ASSERT_EQUAL_STRING("anon-4", state.modules[1].name);
}

void test_replacing_owner_stops_it_when_old_connection_closes() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  rig.ctrl.on_hello(module(3), hello_fk());
  TEST_ASSERT_TRUE(rig.fk_step(0).dir == LiftDir::UP);
  int replaced = -1;
  rig.ctrl.on_hello(module(6), hello_fk(), &replaced);
  TEST_ASSERT_EQUAL_INT(3, replaced);
  rig.ctrl.on_close(replaced);  // 呼び出し側が古い接続を閉じた（その場で止まる）
  const LiftState state = rig.ctrl.step(10);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::OWNER_GONE, state);
  // 新しい接続の press は新しい接続の分として数える（press 1 から動ける）
  rig.ctrl.on_ceiling(module(2), measured(1450, 20));
  const LiftState moved = rig.fk_step(20, 1, 6);
  TEST_ASSERT_TRUE(moved.dir == LiftDir::UP);
}

// 変異 8: 2 度目の hello で role・ceiling_sensor・名前は変わらない
void test_second_hello_does_not_change_role_or_sensor() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  rig.ctrl.on_hello(module(3), hello_fk());
  // fk が 2 度目に observe・距離計あり・別の名前を名乗る。どれも受け付けない
  int replaced = -1;
  rig.ctrl.on_hello(module(3), hello_as("other", true, ModuleRole::OBSERVE), &replaced);
  TEST_ASSERT_EQUAL_INT(-1, replaced);
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_TRUE(state.dir == LiftDir::UP);  // role は control のまま（observe なら hold は捨てられる）
  TEST_ASSERT_TRUE(state.ceiling_used);        // 距離計なしのまま（あるなら自分の MISSING で止まる）
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.ceiling_from);
  TEST_ASSERT_EQUAL_STRING("fk", state.owner_name);
  // 距離計ありと名乗り直した fk の ceiling は出どころにならない
  rig.ctrl.on_ceiling(module(3), measured(100, 10));
  const LiftState later = rig.fk_step(10);
  TEST_ASSERT_TRUE(later.dir == LiftDir::UP);
  TEST_ASSERT_EQUAL_STRING("hve-cam", later.ceiling_from);
}

void test_second_hello_cannot_downgrade_camera_to_observer() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_hello(module(2), hello_as("hve-cam", true, ModuleRole::OBSERVE));
  rig.ctrl.on_hold(module(2), up_hold(1, 40, 0), 0);
  rig.ctrl.step(0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

// 変異 9: 持ち主が fk のとき owner_name・owner_operator が fk・auto になる
void test_state_owner_name_and_operator() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  rig.ctrl.on_hello(module(3), hello_fk());
  const LiftState fk = rig.fk_step(0);
  TEST_ASSERT_TRUE(fk.has_owner);
  TEST_ASSERT_EQUAL_INT(1, fk.owner_kind);
  TEST_ASSERT_EQUAL_STRING("fk", fk.owner_name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO), static_cast<int>(fk.owner_operator));
  // カメラモジュールが新しく押し始めたら、持ち主はカメラモジュール（人）
  rig.ctrl.on_hold(module(2), up_hold(1, 40, 10), 10);
  const LiftState cam = rig.ctrl.step(10);
  TEST_ASSERT_EQUAL_STRING("hve-cam", cam.owner_name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::HUMAN), static_cast<int>(cam.owner_operator));
  // 画面が押したら名前は空・人
  rig.ctrl.on_hold(ui(1), up_hold(1, 40, 20), 20);
  const LiftState ui_state = rig.ctrl.step(20);
  TEST_ASSERT_EQUAL_INT(0, ui_state.owner_kind);
  TEST_ASSERT_EQUAL_STRING("", ui_state.owner_name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::HUMAN), static_cast<int>(ui_state.owner_operator));
}

// 変異 10: operator が欠けた hello は human（decode_hello を通した実際の経路）
void test_hello_without_operator_is_human_owner() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"name\":\"fk\",\"ceiling_sensor\":false}", &hello));
  rig.ctrl.on_hello(module(3), hello);
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_EQUAL_STRING("fk", state.owner_name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::HUMAN), static_cast<int>(state.owner_operator));
  // 名乗った auto は auto
  rig.ctrl.on_close(3);
  HelloMsg auto_hello;
  TEST_ASSERT_TRUE(decode_hello(
      "{\"t\":\"hello\",\"name\":\"ss\",\"operator\":\"auto\",\"ceiling_sensor\":false}", &auto_hello));
  rig.ctrl.on_hello(module(4), auto_hello);
  rig.ctrl.on_ceiling(module(2), measured(1450, 10));
  const LiftState ss = rig.fk_step(10, 1, 4);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO), static_cast<int>(ss.owner_operator));
}

// 変異 12: hello を送っていない・ceiling_sensor が欠けた接続の ceiling は出どころにならない
void test_ceiling_from_connection_without_hello_is_not_a_source() {
  MultiRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  // カメラモジュールは繋がっていない。名乗っていない接続が NO_ECHO を送るだけ
  rig.ctrl.on_ceiling(module(9), ceiling_at(CeilingStatus::NO_ECHO, 0, 0, 0));
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_ceiling_from_hello_without_sensor_field_is_not_a_source() {
  MultiRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello("{\"t\":\"hello\",\"name\":\"x\"}", &hello));  // ceiling_sensor が欠けた
  rig.ctrl.on_hello(module(8), hello);
  rig.ctrl.on_ceiling(module(8), ceiling_at(CeilingStatus::NO_ECHO, 0, 0, 0));
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
  // 同じ接続の hold に載った値も出どころにならない
  rig.ctrl.on_hold(module(8), up_hold(1, 40, 10), 10);
  rig.ctrl.on_release(module(8), 1);
  const LiftState again = rig.fk_step(10, 2);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, again);
}

// 変異 13: observe の接続・ceiling_sensor: false の接続の ceiling は fk の判定に使わない
void test_ceiling_from_observer_is_not_a_source() {
  MultiRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.on_hello(module(5), hello_as("viewer", true, ModuleRole::OBSERVE));
  rig.ctrl.on_ceiling(module(5), measured(1450, 0));
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
  // カメラモジュールの値が有効なら、observe の近い値に引きずられない
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_ceiling(module(2), measured(1450, 10));
  rig.ctrl.on_ceiling(module(5), measured(100, 10));
  const LiftState ok = rig.fk_step(10);
  TEST_ASSERT_TRUE(ok.dir == LiftDir::UP);
  TEST_ASSERT_EQUAL_STRING("hve-cam", ok.ceiling_from);
}

void test_ceiling_from_no_sensor_connection_is_not_a_source() {
  MultiRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.on_hello(module(4), hello_as("ss", false, ModuleRole::CONTROL, ModuleOperator::AUTO));
  rig.ctrl.on_ceiling(module(4), measured(1450, 0));  // 距離計を持たないと名乗った接続の値は捨てる
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_ceiling_from_ui_connection_is_ignored() {
  MultiRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.on_ceiling(ui(1), measured(1450, 0));
  const LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

// 変異 14: busy_label は 48 バイトまで。超えたら切り詰めず無し（壊れた UTF-8 を state に載せない）
void test_busy_label_over_48_bytes_is_dropped_not_truncated() {
  MultiRig rig;
  std::string text = "{\"t\":\"hello\",\"name\":\"fk\",\"busy_label\":\"a";
  for (int i = 0; i < 16; ++i) {
    text += "\xe6\xb8\xac";
  }
  text += "\"}";
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello(text.c_str(), &hello));
  rig.ctrl.on_hello(module(3), hello);
  const LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(1, state.module_count);
  TEST_ASSERT_EQUAL_STRING("", state.modules[0].busy_label);
  char json[LIFT_STATE_TEXT_MAX];
  TEST_ASSERT_GREATER_THAN(0, state_encode(state, json, sizeof(json)));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"busy_label\":null"));
}

void test_busy_label_48_bytes_reaches_state() {
  MultiRig rig;
  std::string label;
  for (int i = 0; i < 16; ++i) {
    label += "\xe6\xb8\xac";
  }
  const std::string text = "{\"t\":\"hello\",\"name\":\"fk\",\"busy_label\":\"" + label + "\"}";
  HelloMsg hello;
  TEST_ASSERT_TRUE(decode_hello(text.c_str(), &hello));
  rig.ctrl.on_hello(module(3), hello);
  const LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_EQUAL_STRING(label.c_str(), state.modules[0].busy_label);
}

// state.modules・homed・ceiling.from
void test_state_modules_list_includes_observer_and_hello_fields() {
  MultiRig rig;
  HelloMsg cam = hello_cam();
  std::strncpy(cam.fw, "0.2.0", sizeof(cam.fw) - 1);
  std::strncpy(cam.ip, "192.168.5.23", sizeof(cam.ip) - 1);
  rig.ctrl.on_hello(module(2), cam);
  HelloMsg fk = hello_fk();
  std::strncpy(fk.busy_label, "fk-busy", sizeof(fk.busy_label) - 1);
  rig.ctrl.on_hello(module(3), fk);
  rig.ctrl.on_hello(module(5), hello_as("viewer", true, ModuleRole::OBSERVE));
  // hello を送っていない接続は並べない
  rig.ctrl.on_ceiling(module(9), measured(1, 0));
  const LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(3, state.module_count);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.modules[0].name);
  TEST_ASSERT_EQUAL_STRING("192.168.5.23", state.modules[0].ip);
  TEST_ASSERT_EQUAL_STRING("0.2.0", state.modules[0].fw);
  TEST_ASSERT_EQUAL_STRING("", state.modules[0].busy_label);
  TEST_ASSERT_EQUAL_STRING("fk", state.modules[1].name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO), static_cast<int>(state.modules[1].op));
  TEST_ASSERT_FALSE(state.modules[1].ceiling_sensor);
  TEST_ASSERT_EQUAL_STRING("fk-busy", state.modules[1].busy_label);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::OBSERVE), static_cast<int>(state.modules[2].role));
  rig.ctrl.on_close(3);
  TEST_ASSERT_EQUAL_INT(2, rig.ctrl.step(10).module_count);
}

void test_state_modules_is_capped_at_list_max() {
  MultiRig rig;
  const char* names[] = {"a", "b", "c", "d", "e", "f"};
  for (int i = 0; i < LIFT_MODULE_LIST_MAX; ++i) {
    rig.ctrl.on_hello(module(10 + i), hello_as(names[i], false));
  }
  TEST_ASSERT_EQUAL_INT(LIFT_MODULE_LIST_MAX, rig.ctrl.step(0).module_count);
}

void test_state_homed_follows_hal() {
  MultiRig rig;
  TEST_ASSERT_FALSE(rig.ctrl.step(0).homed);
  rig.hal.set_homed(true);
  TEST_ASSERT_TRUE(rig.ctrl.step(10).homed);
}

void test_state_ceiling_from_while_idle_follows_ceiling_messages() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  // 昇降を命じていなくても、ceiling が届いている間は state.ceiling が新しい
  rig.ctrl.on_ceiling(module(2), measured(1450, 1000, 50));
  const LiftState state = rig.ctrl.step(1100);
  TEST_ASSERT_FALSE(state.has_owner);
  TEST_ASSERT_TRUE(state.ceiling_present);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.ceiling_from);
  TEST_ASSERT_EQUAL_INT(150, state.ceiling_age_ms);
  TEST_ASSERT_FALSE(state.ceiling_used);
}

// 持ち主が距離計を持つ接続のときは、今までどおり自分の hold の値。ceiling.from は自分
void test_camera_owner_uses_own_hold_ceiling_and_from() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_hold(module(2), up_hold(1, 40, 0), 0);
  const LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.ceiling_from);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

// 設定 ceiling_standalone が false のときは、画面・物理ボタンは天井を使わない（WP-LIFT-12 の
// 「天井を使わない」を、false の場合の試験に書き換えたもの。WP-LIFT-15）
void test_ui_and_button_climb_ignore_camera_ceiling_when_setting_is_off() {
  MultiRig rig;
  rig.ctrl.set_ceiling_standalone(false);
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  HoldMsg hold = fk_up(1);
  rig.ctrl.on_hold(ui(1), hold, 0);
  LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  TEST_ASSERT_FALSE(state.ceiling_used);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::SETTING_OFF),
                        static_cast<int>(state.ceiling_standalone));

  rig.ctrl.on_release(ui(1), 1);
  rig.ctrl.step(10);
  rig.ctrl.on_hold(button(9), fk_up(1), 20);
  rig.hal.set_height(500, true, 20);
  state = rig.ctrl.step(20);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(2, state.owner_kind);  // button
  TEST_ASSERT_FALSE(state.ceiling_used);
}

// --- WP-LIFT-15: 単体の操作でも天井で止める（protocol-modules §2.5）---

struct StandaloneRig : MultiRig {
  StandaloneRig() { ctrl.on_hello(module(2), hello_cam()); }
  // 時刻 t に高さを新しくし、conn が press で上昇の hold を送って step する（天井は載せない）
  LiftState climb(const ConnId& conn, uint32_t t, int press = 1) {
    hal.set_height(500, true, t);
    ctrl.on_hold(conn, fk_up(press), t);
    return ctrl.step(t);
  }
};

// 変異 1: 設定が true でカメラモジュールが MEASURED 300 mm を送っている間、ui の上昇が止まらない
void test_ui_climb_stops_at_camera_ceiling_when_setting_is_on() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  const LiftState state = rig.climb(ui(1), 0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  TEST_ASSERT_EQUAL_INT(0, state.owner_kind);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_FALSE(state.ceiling_ok);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.ceiling_from);
  TEST_ASSERT_EQUAL_INT(300, state.ceiling_mm);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::USE),
                        static_cast<int>(state.ceiling_standalone));
}

// 変異 2: 同じ条件で物理ボタンの上昇が止まらない
void test_button_climb_stops_at_camera_ceiling_when_setting_is_on() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  const LiftState state = rig.climb(button(9), 0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  TEST_ASSERT_EQUAL_INT(2, state.owner_kind);
  TEST_ASSERT_TRUE(state.ceiling_used);
}

void test_standalone_climb_moves_when_camera_ceiling_is_far_and_down_needs_none() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  LiftState state = rig.climb(ui(1), 0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_TRUE(state.ceiling_used);
  TEST_ASSERT_TRUE(state.ceiling_ok);
  // 下降は天井を見ない
  rig.ctrl.on_ceiling(module(2), measured(300, 100));
  rig.hal.set_height(500, true, 100);
  rig.ctrl.on_hold(ui(1), down_hold(2), 100);
  state = rig.ctrl.step(100);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(LiftDir::DOWN), static_cast<int>(state.dir));
}

// 変異 3: 設定が false でも天井で止まる
void test_setting_off_lets_ui_and_button_climb_even_when_ceiling_is_near() {
  StandaloneRig rig;
  rig.ctrl.set_ceiling_standalone(false);
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  LiftState state = rig.climb(ui(1), 0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  rig.ctrl.on_close(1);
  rig.ctrl.step(10);
  state = rig.climb(button(9), 20);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, state);
}

// 変異 4: カメラモジュールが繋がっていない・値が古いとき、ui の上昇を CEILING_STALE で止める
void test_standalone_climb_is_allowed_without_source_or_with_stale_value() {
  {
    FakeHal hal;
    LiftController ctrl(&hal, LIFT_TOP_MM);  // 上部モジュールが 1 つも繋がっていない
    hal.set_height(500, true, 0);
    ctrl.on_hold(ui(1), fk_up(1), 0);
    const LiftState state = ctrl.step(0);
    TEST_ASSERT_TRUE(hal.motor_moving());
    assert_reason(StopReason::NONE, state);
    TEST_ASSERT_FALSE(state.ceiling_used);
    TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::NO_SOURCE),
                          static_cast<int>(state.ceiling_standalone));
  }
  {
    // 繋がっているが、値が CEILING_STALE_MS（600 ms）より古い（近い値でも使わない）
    StandaloneRig rig;
    rig.ctrl.on_ceiling(module(2), measured(300, 0));
    const LiftState state = rig.climb(ui(1), 601);
    TEST_ASSERT_TRUE(rig.hal.motor_moving());
    assert_reason(StopReason::NONE, state);
    TEST_ASSERT_FALSE(state.ceiling_used);
    TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::NO_SOURCE),
                          static_cast<int>(state.ceiling_standalone));
  }
  {
    // 送るときの古さ（age_ms）が大きいときも古い
    StandaloneRig rig;
    rig.ctrl.on_ceiling(module(2), measured(300, 0, 700));
    const LiftState state = rig.climb(button(9), 0);
    TEST_ASSERT_TRUE(rig.hal.motor_moving());
    TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::NO_SOURCE),
                          static_cast<int>(state.ceiling_standalone));
  }
  {
    // ちょうど CEILING_STALE_MS までは新しい
    StandaloneRig rig;
    rig.ctrl.on_ceiling(module(2), measured(300, 0));
    const LiftState state = rig.climb(ui(1), 600);
    TEST_ASSERT_FALSE(rig.hal.motor_moving());
    assert_reason(StopReason::CEILING_NEAR, state);
  }
}

// 変異 5: 上昇の最中に値が古くなっても、前の判定のまま止め続ける（周期ごとに決め直さない）
void test_standalone_decision_is_remade_every_cycle() {
  StandaloneRig rig;
  // 近い値で止まっている。press は同じまま、hold を出し続ける
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  TEST_ASSERT_FALSE(rig.climb(ui(1), 0, 7).dir == LiftDir::UP);
  TEST_ASSERT_FALSE(rig.climb(ui(1), 300, 7).dir == LiftDir::UP);
  assert_reason(StopReason::CEILING_NEAR, rig.climb(ui(1), 600, 7));
  // カメラモジュールが黙って値が古くなる。次の周期から使わず、上昇を許す
  LiftState state = rig.climb(ui(1), 700, 7);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::NO_SOURCE),
                        static_cast<int>(state.ceiling_standalone));
  // 逆に、上昇中に近い値が届いたら次の周期から止める
  rig.ctrl.on_ceiling(module(2), measured(300, 800));
  state = rig.climb(ui(1), 800, 7);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
}

// 変異 6: 新しい値が READ_ERROR のとき ui の上昇を許す（通信はできていて距離計が読めない → 止める）
void test_standalone_climb_stops_with_stale_reason_when_fresh_value_is_read_error() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), ceiling_at(CeilingStatus::READ_ERROR, 0, 0, 0));
  LiftState state = rig.climb(ui(1), 0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::USE),
                        static_cast<int>(state.ceiling_standalone));
  // MISSING（新しいが読めない）も止める
  rig.ctrl.on_close(1);
  rig.ctrl.step(10);
  rig.ctrl.on_ceiling(module(2), ceiling_at(CeilingStatus::MISSING, 0, 0, 20));
  state = rig.climb(button(9), 20);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
}

void test_standalone_no_echo_and_too_near() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), ceiling_at(CeilingStatus::NO_ECHO, 0, 0, 0));
  LiftState state = rig.climb(ui(1), 0);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StopReason::OUT_OF_RANGE),
                        static_cast<int>(state.ceiling_reason));
  rig.ctrl.on_ceiling(module(2), ceiling_at(CeilingStatus::TOO_NEAR, 0, 0, 100));
  state = rig.climb(ui(1), 100);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
}

// 変異 7: 設定が上部モジュールからの命令に効く（false にすると fk の上昇が天井で止まらない）
void test_setting_does_not_affect_commands_from_modules() {
  StandaloneRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.set_ceiling_standalone(false);
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  LiftState state = rig.fk_step(0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  // 値が古いときは、設定にかかわらず CEILING_STALE で止める（単体の操作と逆）
  state = rig.fk_step(1000, 2);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_STALE, state);
  // 設定が true でも同じ
  rig.ctrl.set_ceiling_standalone(true);
  state = rig.fk_step(1100, 3);
  assert_reason(StopReason::CEILING_STALE, state);
}

// 変異 11: 出どころが 2 台（古い方・新しい方）のとき、古い方を選ぶ
void test_standalone_ignores_stale_source_and_uses_fresh_one() {
  StandaloneRig rig;
  rig.ctrl.on_hello(module(4), hello_as("hve-cam2", true));
  // 古い方は近い値・新しい方は遠い値 → 古い方を無視して上昇を許す
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  rig.ctrl.on_ceiling(module(4), measured(1450, 1000));
  LiftState state = rig.climb(ui(1), 1100);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, state);
  TEST_ASSERT_EQUAL_STRING("hve-cam2", state.ceiling_from);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::USE),
                        static_cast<int>(state.ceiling_standalone));
}

void test_standalone_uses_fresh_near_source_even_if_other_is_stale_and_far() {
  StandaloneRig rig;
  rig.ctrl.on_hello(module(4), hello_as("hve-cam2", true));
  // 古い方は遠い値（または READ_ERROR）・新しい方は MEASURED 300 mm → 新しい方で止める
  rig.ctrl.on_ceiling(module(2), ceiling_at(CeilingStatus::READ_ERROR, 0, 0, 0));
  rig.ctrl.on_ceiling(module(4), measured(300, 1000));
  const LiftState state = rig.climb(button(9), 1100);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  TEST_ASSERT_EQUAL_STRING("hve-cam2", state.ceiling_from);
}

void test_standalone_uses_nearest_of_two_fresh_sources() {
  StandaloneRig rig;
  rig.ctrl.on_hello(module(4), hello_as("hve-cam2", true));
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  rig.ctrl.on_ceiling(module(4), measured(300, 0));
  const LiftState state = rig.climb(ui(1), 100);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  TEST_ASSERT_EQUAL_STRING("hve-cam2", state.ceiling_from);
}

// 変異 10: state.ceiling.standalone（持ち主が誰でも・いなくても毎周期出す）
void test_state_ceiling_standalone_reports_setting_off_no_source_and_use() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  // 出どころが無い（持ち主もいない）
  LiftState state = ctrl.step(0);
  TEST_ASSERT_FALSE(state.has_owner);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::NO_SOURCE),
                        static_cast<int>(state.ceiling_standalone));
  // 新しい値が来ると、押す前から USE
  ctrl.on_hello(module(2), hello_cam());
  ctrl.on_ceiling(module(2), measured(1450, 10));
  state = ctrl.step(20);
  TEST_ASSERT_FALSE(state.has_owner);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::USE),
                        static_cast<int>(state.ceiling_standalone));
  // 古くなると NO_SOURCE に戻る
  state = ctrl.step(700);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::NO_SOURCE),
                        static_cast<int>(state.ceiling_standalone));
  // 設定が切なら、出どころの有無に関係なく SETTING_OFF（出どころが無くても NO_SOURCE にならない）
  ctrl.set_ceiling_standalone(false);
  state = ctrl.step(710);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::SETTING_OFF),
                        static_cast<int>(state.ceiling_standalone));
  ctrl.on_ceiling(module(2), measured(1450, 720));
  state = ctrl.step(720);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::SETTING_OFF),
                        static_cast<int>(state.ceiling_standalone));
  // 持ち主が上部モジュールでも出す
  ctrl.set_ceiling_standalone(true);
  ctrl.on_hello(module(3), hello_fk());
  hal.set_height(500, true, 730);
  ctrl.on_hold(module(3), fk_up(1), 730);
  state = ctrl.step(730);
  TEST_ASSERT_EQUAL_INT(1, state.owner_kind);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::USE),
                        static_cast<int>(state.ceiling_standalone));
}

void test_standalone_source_must_be_an_explicit_control_sensor() {
  FakeHal hal;
  LiftController ctrl(&hal, LIFT_TOP_MM);
  hal.set_height(500, true, 0);
  // ceiling_sensor を名乗っていない・observe の接続の値は出どころにならない（§2.3）
  ctrl.on_hello(module(2), hello_as("watcher", true, ModuleRole::OBSERVE));
  ctrl.on_ceiling(module(2), measured(300, 0));
  ctrl.on_hold(ui(1), fk_up(1), 0);
  const LiftState state = ctrl.step(0);
  TEST_ASSERT_TRUE(hal.motor_moving());
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::NO_SOURCE),
                        static_cast<int>(state.ceiling_standalone));
}

// 設定をまとめて効かせる口（main.cpp が起動時と PUT の成功時に呼ぶ）。
// 変異 12: 起動時に NVS の設定から ceiling_standalone を渡さない
void test_apply_settings_hands_ceiling_standalone_to_controller() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  LiftSettings settings;
  settings.ceiling_standalone = false;
  rig.ctrl.apply_settings(settings);
  TEST_ASSERT_TRUE(rig.climb(ui(1), 0).dir == LiftDir::UP);
  rig.ctrl.on_close(1);
  rig.ctrl.step(10);
  settings.ceiling_standalone = true;
  rig.ctrl.apply_settings(settings);
  assert_reason(StopReason::CEILING_NEAR, rig.climb(ui(1), 20, 2));
}

// NVS に保存した JSON から読んだ設定が、そのまま LiftController の動きになる（起動時の経路）
void test_saved_false_makes_ui_climb_ignore_ceiling_after_boot() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), measured(300, 0));
  bool using_defaults = true;
  const char* saved =
      "{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
      "\"lift_down\":{\"min\":10,\"max\":60,\"init\":30},"
      "\"motor_max_pct\":50,\"ceiling_standalone\":false}";
  rig.ctrl.apply_settings(lift_settings_from_saved(saved, &using_defaults));
  TEST_ASSERT_FALSE(using_defaults);
  TEST_ASSERT_TRUE(rig.climb(ui(1), 0).dir == LiftDir::UP);
}

// --- WP-LIFT-16: 天井の閾値を設定に（ceiling_margin_mm。protocol §3）---

namespace {
LiftSettings settings_with_margin(int margin_mm) {
  LiftSettings settings;
  settings.ceiling_margin_mm = margin_mm;
  return settings;
}
}  // namespace

// 変異 1・3: 判定が設定ではなく固定の 500 を使う／apply_settings が閾値を渡さない。
// 閾値 1000・カメラモジュールが 800 mm を送っている間、fk の上昇が止まる（既定の 500 なら動く）
void test_fk_climb_stops_at_800mm_when_margin_setting_is_1000() {
  StandaloneRig rig;
  rig.ctrl.on_hello(module(3), hello_fk());
  rig.ctrl.on_ceiling(module(2), measured(800, 0));
  // 既定（500）では 800 mm は遠い
  TEST_ASSERT_TRUE(rig.fk_step(0).dir == LiftDir::UP);
  rig.ctrl.apply_settings(settings_with_margin(1000));
  const LiftState state = rig.fk_step(10);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  TEST_ASSERT_FALSE(state.ceiling_ok);
  TEST_ASSERT_EQUAL_INT(
      static_cast<int>(StopReason::CEILING_NEAR), static_cast<int>(state.ceiling_reason));
  // 小さくすれば同じ値で動く（150 の下端。800 は遠い）
  rig.ctrl.apply_settings(settings_with_margin(CEILING_MARGIN_MM_MIN));
  TEST_ASSERT_TRUE(rig.fk_step(20, 2).dir == LiftDir::UP);
}

// 変異 2: 単体の操作（ui・button）の判定だけ固定の 500 を使う
void test_ui_and_button_climb_stop_at_800mm_when_margin_setting_is_1000() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), measured(800, 0));
  rig.ctrl.apply_settings(settings_with_margin(1000));
  LiftState state = rig.climb(ui(1), 0);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  TEST_ASSERT_FALSE(state.ceiling_ok);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(StandaloneCeiling::USE),
                        static_cast<int>(state.ceiling_standalone));
  rig.ctrl.on_close(1);
  rig.ctrl.step(10);
  state = rig.climb(button(9), 20);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
  // 既定に戻せば同じ 800 mm で上がる
  rig.ctrl.apply_settings(settings_with_margin(CEILING_MARGIN_MM_DEFAULT));
  rig.ctrl.on_close(9);
  rig.ctrl.step(30);
  TEST_ASSERT_TRUE(rig.climb(ui(1), 40, 2).dir == LiftDir::UP);
}

// PUT が通った次の周期から新しい値で判定する（動いている最中に大きくしたら、その場で止まる）
void test_raising_margin_while_climbing_stops_from_next_cycle() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), measured(800, 0));
  TEST_ASSERT_TRUE(rig.climb(ui(1), 0).dir == LiftDir::UP);
  rig.ctrl.apply_settings(settings_with_margin(1000));
  rig.ctrl.on_ceiling(module(2), measured(800, 0));
  const LiftState state = rig.climb(ui(1), 10);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_reason(StopReason::CEILING_NEAR, state);
}

// 出どころ選び（pick_source）の判定も同じ閾値: 2 台とも閾値以下のときは止める値が同列（先の接続）、
// 500 のままなら遠い方（800）は ok で、近い方（600）が選ばれる
void test_source_pick_uses_margin_setting() {
  StandaloneRig rig;
  rig.ctrl.on_hello(module(4), hello_as("cam2", true));
  rig.ctrl.on_ceiling(module(2), measured(800, 0));
  rig.ctrl.on_ceiling(module(4), measured(600, 0));
  rig.ctrl.on_hold(ui(1), fk_up(1), 0);
  rig.hal.set_height(500, true, 0);
  LiftState state = rig.ctrl.step(0);
  TEST_ASSERT_EQUAL_INT(600, state.ceiling_mm);
  TEST_ASSERT_EQUAL_STRING("cam2", state.ceiling_from);
  rig.ctrl.apply_settings(settings_with_margin(1000));
  rig.ctrl.on_ceiling(module(2), measured(800, 10));
  rig.ctrl.on_ceiling(module(4), measured(600, 10));
  rig.hal.set_height(500, true, 10);
  state = rig.ctrl.step(10);
  TEST_ASSERT_EQUAL_INT(800, state.ceiling_mm);
  TEST_ASSERT_EQUAL_STRING("hve-cam", state.ceiling_from);
}

// 起動時: NVS に保存した JSON から読んだ閾値が、そのまま LiftController の動きになる。
// 欄の無い古い形は 500 で読む（動く）
void test_saved_margin_applies_after_boot_and_missing_means_500() {
  StandaloneRig rig;
  rig.ctrl.on_ceiling(module(2), measured(800, 0));
  bool using_defaults = true;
  const char* saved =
      "{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
      "\"lift_down\":{\"min\":10,\"max\":60,\"init\":30},"
      "\"motor_max_pct\":50,\"ceiling_standalone\":true,\"ceiling_margin_mm\":1000}";
  rig.ctrl.apply_settings(lift_settings_from_saved(saved, &using_defaults));
  TEST_ASSERT_FALSE(using_defaults);
  TEST_ASSERT_FALSE(rig.climb(ui(1), 0).dir == LiftDir::UP);
  rig.ctrl.on_close(1);
  rig.ctrl.step(10);
  const char* old_saved =
      "{\"lift_up\":{\"min\":10,\"max\":60,\"init\":30},"
      "\"lift_down\":{\"min\":10,\"max\":60,\"init\":30},"
      "\"motor_max_pct\":50,\"ceiling_standalone\":true}";
  rig.ctrl.apply_settings(lift_settings_from_saved(old_saved, &using_defaults));
  TEST_ASSERT_FALSE(using_defaults);
  TEST_ASSERT_TRUE(rig.climb(ui(1), 20, 2).dir == LiftDir::UP);
}

// 接続を何度も繋ぎ直しても hold を受け付け続ける（閉じた接続の番号の欄を使い回す）
void test_many_reconnects_still_accept_holds() {
  MultiRig rig;
  rig.ctrl.on_hello(module(2), hello_cam());
  rig.ctrl.on_ceiling(module(2), measured(1450, 0));
  uint32_t t = 0;
  for (int id = 100; id < 140; ++id) {
    rig.ctrl.on_hello(module(id), hello_fk());
    const LiftState state = rig.fk_step(t, 1, id);
    TEST_ASSERT_TRUE(state.dir == LiftDir::UP);
    rig.ctrl.on_ceiling(module(2), measured(1450, t + 10));
    rig.ctrl.on_close(id);
    t += 10;
    rig.ctrl.step(t);
  }
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();

  RUN_TEST(test_motor_stays_stopped_before_any_command);
  RUN_TEST(test_ui_up_without_ceiling_reaches_motor);
  RUN_TEST(test_watchdog_stops_motor_at_600ms);

  RUN_TEST(test_module_hold_without_hello_is_treated_as_having_sensor);
  RUN_TEST(test_module_hold_without_ceiling_stops_with_stale);
  RUN_TEST(test_second_hello_with_no_sensor_is_ignored);
  RUN_TEST(test_ui_hello_closes_connection);
  RUN_TEST(test_read_error_stops_but_no_echo_moves);

  RUN_TEST(test_old_press_from_non_owner_does_not_steal);
  RUN_TEST(test_owner_close_stops_immediately_with_owner_gone);
  RUN_TEST(test_owner_change_does_not_reset_max_run);
  RUN_TEST(test_max_run_30mm_s_full_stroke_stops_just_after_90s);
  RUN_TEST(test_max_run_ignores_measured_speed);
  RUN_TEST(test_max_run_sums_distance_when_target_speed_changes);
  RUN_TEST(test_max_run_uses_top_mm_as_stroke_through_controller);
  RUN_TEST(test_max_run_does_not_count_while_stopped_by_ceiling_near);
  RUN_TEST(test_max_run_does_not_count_while_duty_is_zero);
  RUN_TEST(test_max_run_then_opposite_direction_from_other_connection_moves);
  RUN_TEST(test_direction_switch_restarts_count_for_full_limit);
  RUN_TEST(test_same_press_after_release_does_not_restart);
  RUN_TEST(test_same_press_after_timeout_does_not_restart);
  RUN_TEST(test_same_press_after_close_does_not_restart);
  RUN_TEST(test_wrong_typed_ceiling_stops_at_once);
  RUN_TEST(test_ceiling_age_counts_transit_through_controller);

  RUN_TEST(test_duty_rises_while_speed_stays_below_target);
  RUN_TEST(test_duty_never_exceeds_default_motor_cap);
  RUN_TEST(test_lowering_cap_mid_motion_applies_on_next_cycle);
  RUN_TEST(test_stall_when_encoder_never_moves);
  RUN_TEST(test_stall_when_encoder_runs_backwards);
  RUN_TEST(test_stall_when_progress_is_under_800_um);
  RUN_TEST(test_no_stall_while_progress_reaches_800_um);
  RUN_TEST(test_speed_meas_uses_um_per_count);
  RUN_TEST(test_integral_is_reset_after_stop_and_repress);
  RUN_TEST(test_integral_is_reset_after_stop_while_owner_keeps_holding);
  RUN_TEST(test_opposite_direction_moves_after_stall);
  RUN_TEST(test_integral_is_reset_when_owner_changes);

  RUN_TEST(test_release_from_owner_stops_with_cmd_stop);
  RUN_TEST(test_release_from_non_owner_is_ignored);
  RUN_TEST(test_bottom_pressed_stops_down_motor);
  RUN_TEST(test_speed_is_clamped_before_speed_loop);
  RUN_TEST(test_motor_is_written_on_every_step);

  RUN_TEST(test_state_reports_top_detect_true_and_module);
  RUN_TEST(test_state_keeps_last_module_ceiling_after_release);
  RUN_TEST(test_state_keeps_last_module_ceiling_after_hold_timeout);
  RUN_TEST(test_state_ceiling_null_when_no_module_hold_ever);
  RUN_TEST(test_state_before_step_is_stopped);
  RUN_TEST(test_top_unset_reports_top_detect_false_and_still_climbs);

  RUN_TEST(test_cmd_timeout_survives_millis_wrap);
  RUN_TEST(test_height_ok_in_state_survives_millis_wrap_when_fresh);
  RUN_TEST(test_height_unknown_shows_in_state);
  RUN_TEST(test_up_before_origin_is_refused_but_down_works);
  RUN_TEST(test_up_after_landing_on_bottom_works);
  RUN_TEST(test_up_below_top_moves_and_at_top_stops);
  RUN_TEST(test_above_top_refuses_up_but_down_works);
  RUN_TEST(test_up_with_stale_height_is_refused);

  RUN_TEST(test_glitch_during_climb_does_not_reset_height);
  RUN_TEST(test_long_press_while_high_makes_height_unknown);
  RUN_TEST(test_bottom_stops_down_on_a_single_raw_read);


  RUN_TEST(test_fk_climb_stops_at_camera_ceiling_sent_by_ceiling_message);
  RUN_TEST(test_fk_climb_moves_when_camera_ceiling_is_far);
  RUN_TEST(test_fk_uses_camera_hold_ceiling_too);
  RUN_TEST(test_fk_climb_refused_without_camera);
  RUN_TEST(test_fk_climb_refused_when_camera_value_is_old);
  RUN_TEST(test_fk_climb_stops_when_camera_connection_closes);
  RUN_TEST(test_fk_ceiling_age_counts_transit);
  RUN_TEST(test_fk_uses_nearest_of_two_sensor_modules);
  RUN_TEST(test_fk_down_does_not_need_ceiling);
  RUN_TEST(test_observe_connection_hold_is_dropped);
  RUN_TEST(test_observe_connection_release_does_not_stop_owner);
  RUN_TEST(test_unknown_role_hello_cannot_command);
  RUN_TEST(test_missing_role_hello_can_command);
  RUN_TEST(test_same_name_hello_returns_old_connection);
  RUN_TEST(test_nameless_hellos_do_not_replace_each_other);
  RUN_TEST(test_replacing_owner_stops_it_when_old_connection_closes);
  RUN_TEST(test_second_hello_does_not_change_role_or_sensor);
  RUN_TEST(test_second_hello_cannot_downgrade_camera_to_observer);
  RUN_TEST(test_state_owner_name_and_operator);
  RUN_TEST(test_hello_without_operator_is_human_owner);
  RUN_TEST(test_ceiling_from_connection_without_hello_is_not_a_source);
  RUN_TEST(test_ceiling_from_hello_without_sensor_field_is_not_a_source);
  RUN_TEST(test_ceiling_from_observer_is_not_a_source);
  RUN_TEST(test_ceiling_from_no_sensor_connection_is_not_a_source);
  RUN_TEST(test_ceiling_from_ui_connection_is_ignored);
  RUN_TEST(test_busy_label_over_48_bytes_is_dropped_not_truncated);
  RUN_TEST(test_busy_label_48_bytes_reaches_state);
  RUN_TEST(test_state_modules_list_includes_observer_and_hello_fields);
  RUN_TEST(test_state_modules_is_capped_at_list_max);
  RUN_TEST(test_state_homed_follows_hal);
  RUN_TEST(test_state_ceiling_from_while_idle_follows_ceiling_messages);
  RUN_TEST(test_camera_owner_uses_own_hold_ceiling_and_from);
  RUN_TEST(test_ui_and_button_climb_ignore_camera_ceiling_when_setting_is_off);
  RUN_TEST(test_ui_climb_stops_at_camera_ceiling_when_setting_is_on);
  RUN_TEST(test_button_climb_stops_at_camera_ceiling_when_setting_is_on);
  RUN_TEST(test_standalone_climb_moves_when_camera_ceiling_is_far_and_down_needs_none);
  RUN_TEST(test_setting_off_lets_ui_and_button_climb_even_when_ceiling_is_near);
  RUN_TEST(test_standalone_climb_is_allowed_without_source_or_with_stale_value);
  RUN_TEST(test_standalone_decision_is_remade_every_cycle);
  RUN_TEST(test_standalone_climb_stops_with_stale_reason_when_fresh_value_is_read_error);
  RUN_TEST(test_standalone_no_echo_and_too_near);
  RUN_TEST(test_setting_does_not_affect_commands_from_modules);
  RUN_TEST(test_standalone_ignores_stale_source_and_uses_fresh_one);
  RUN_TEST(test_standalone_uses_fresh_near_source_even_if_other_is_stale_and_far);
  RUN_TEST(test_standalone_uses_nearest_of_two_fresh_sources);
  RUN_TEST(test_state_ceiling_standalone_reports_setting_off_no_source_and_use);
  RUN_TEST(test_standalone_source_must_be_an_explicit_control_sensor);
  RUN_TEST(test_apply_settings_hands_ceiling_standalone_to_controller);
  RUN_TEST(test_fk_climb_stops_at_800mm_when_margin_setting_is_1000);
  RUN_TEST(test_ui_and_button_climb_stop_at_800mm_when_margin_setting_is_1000);
  RUN_TEST(test_raising_margin_while_climbing_stops_from_next_cycle);
  RUN_TEST(test_source_pick_uses_margin_setting);
  RUN_TEST(test_saved_margin_applies_after_boot_and_missing_means_500);
  RUN_TEST(test_saved_false_makes_ui_climb_ignore_ceiling_after_boot);
  RUN_TEST(test_many_reconnects_still_accept_holds);

  return UNITY_END();
}
