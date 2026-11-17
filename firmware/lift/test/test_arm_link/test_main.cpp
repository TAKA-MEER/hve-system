// 測定機構の #4e・確認・覚えておくこと（WP-LIFT-18。DetailedDesign-protocol-arm.md）の試験（env:native）。
// LiftController に偽 HAL を差し、arm / arm_confirm / hello(role: arm) / 接続の閉 → step のあと、
// 偽モータへ実際に出た値と state.arm・state.reason を確かめる（DetailedDesign.md DD-2）。
// NVS は main.cpp が loop() の中で書く。ここでは Rig::service() が同じ手順（take_arm_latch_write →
// 書く → on_arm_latch_written）を踏む。純関数（lift_decide）の試験だけで済ませない。
#include <unity.h>

#include <cstring>

#include "../test_lift_controller/fake_hal.h"
#include "button_input.h"
#include "cmd_codec.h"
#include "lift_controller.h"

namespace {

constexpr int kUi = 1;
constexpr int kCam = 2;
constexpr int kArm = 3;
constexpr int kArm2 = 4;
constexpr int kFk = 5;

ConnId conn_of(int id, ConnKind kind) {
  ConnId conn;
  conn.id = id;
  conn.kind = kind;
  return conn;
}
ConnId ui(int id = kUi) { return conn_of(id, ConnKind::UI); }
ConnId module(int id) { return conn_of(id, ConnKind::MODULE); }
ConnId button() { return conn_of(LIFT_BUTTON_CONN_ID, ConnKind::BUTTON); }

HelloMsg hello_named(const char* name, ModuleRole role, bool has_sensor, ModuleOperator op) {
  HelloMsg hello;
  hello.has_sensor = has_sensor;
  hello.sensor_explicit = true;
  hello.role = role;
  hello.op = op;
  std::strncpy(hello.name, name, sizeof(hello.name) - 1);
  return hello;
}
// アームの制御器の hello。わざと ceiling_sensor: true・operator: human を名乗る（固定されるか見る）
HelloMsg hello_arm(const char* name = "fk-arm") {
  return hello_named(name, ModuleRole::ARM, true, ModuleOperator::HUMAN);
}
HelloMsg hello_cam() {
  return hello_named("hve-cam", ModuleRole::CONTROL, true, ModuleOperator::HUMAN);
}
HelloMsg hello_fk() {
  return hello_named("fk", ModuleRole::CONTROL, false, ModuleOperator::AUTO);
}

HoldMsg hold_of(int press, LiftDir dir, int speed = 40) {
  HoldMsg hold;
  hold.press = press;
  hold.dir = dir;
  hold.speed = speed;
  hold.has_ceiling = false;
  return hold;
}

ArmMsg arm_out(ArmTri out) {
  ArmMsg msg;
  msg.out = out;
  return msg;
}

void assert_reason(StopReason expected, const LiftState& state) {
  TEST_ASSERT_EQUAL_INT(static_cast<int>(expected), static_cast<int>(state.reason));
}
void assert_tri(ArmTri expected, ArmTri actual) {
  TEST_ASSERT_EQUAL_INT(static_cast<int>(expected), static_cast<int>(actual));
}

// main.cpp の loop() と同じ順（イベント → NVS の書き込み → step）で進める。
// 高さは 500 mm・読めている・新しい（下端でも上端でもない・原点は取れている）
struct Rig {
  FakeHal hal;
  LiftController ctrl;
  uint32_t t = 0;
  bool manual_nvs = false;  // 真なら service() は何もしない（書き込みが終わる前を試験する）
  bool nvs_ok = true;       // 偽の NVS が書けるか
  bool nvs_value = false;   // 偽の NVS に入っている値
  int nvs_writes = 0;
  int nvs_false_writes = 0;
  LiftState st;

  // latched_at_boot: 起動したとき NVS に true が入っていた（main.cpp の load_arm_latch と同じ渡し方）
  explicit Rig(bool latched_at_boot = false) : ctrl(&hal, LIFT_TOP_MM) {
    hal.set_homed(true);
    hal.set_height(500, true, 0);
    if (latched_at_boot) {
      nvs_value = true;
      ctrl.restore_arm_out_latched(true);
    }
    st = ctrl.step(0);
  }

  void service() {
    if (manual_nvs) {
      return;
    }
    bool value = false;
    if (ctrl.take_arm_latch_write(&value)) {
      ++nvs_writes;
      if (!value) {
        ++nvs_false_writes;
      }
      if (nvs_ok) {
        nvs_value = value;
      }
      ctrl.on_arm_latch_written(value, nvs_ok);
    }
  }

  LiftState tick(uint32_t ms = 100) {
    t += ms;
    hal.set_height(500, true, t);
    service();
    st = ctrl.step(t);
    return st;
  }

  // 画面（/ws/ui）の hold を ms ぶん 100 ms ごとに出し直す
  LiftState hold_ui(LiftDir dir, int press, uint32_t ms) {
    for (uint32_t spent = 0; spent < ms; spent += 100) {
      ctrl.on_hold(ui(), hold_of(press, dir), t + 100);
      tick();
    }
    return st;
  }

  // role: arm の接続を繋ぎ、置き換えられた古い接続の番号を返す（無ければ -1）
  int join_arm(int id, const char* name = "fk-arm") {
    int replaced = -2;
    TEST_ASSERT_TRUE(ctrl.on_hello(module(id), hello_arm(name), &replaced));
    return replaced;
  }
  void arm(int id, ArmTri out) { ctrl.on_arm(module(id), arm_out(out), t); }
  bool confirm(int stop, ConnId from) {
    ArmConfirmMsg msg;
    msg.stop = stop;
    return ctrl.on_arm_confirm(from, msg);
  }
  // 腕が出ている（out: true）と知らせ、NVS に書けて state.arm.out が true になるまで進める
  void arm_out_and_settled(int id = kArm) {
    join_arm(id);
    arm(id, ArmTri::YES);
    tick();
    TEST_ASSERT_TRUE(nvs_value);
    assert_tri(ArmTri::YES, st.arm.out);
  }
  // 最後の arm から ms だけ何も来ない（接続は開いたまま）
  void silence(uint32_t ms) {
    const uint32_t end = t + ms;
    while (t < end) {
      tick(100);
    }
  }
};

}  // namespace

void setUp() {}
void tearDown() {}

// --- 何も知らない・格納位置 ---

void test_unknown_arm_does_not_stop_and_state_is_all_null() {
  Rig rig;
  rig.hold_ui(LiftDir::UP, 1, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.st);
  assert_tri(ArmTri::NUL, rig.st.arm.attached);
  assert_tri(ArmTri::NUL, rig.st.arm.link);
  assert_tri(ArmTri::NUL, rig.st.arm.out);
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
}

void test_hello_alone_does_not_attach() {
  Rig rig;
  rig.join_arm(kArm);
  rig.tick();
  assert_tri(ArmTri::NUL, rig.st.arm.attached);
  assert_tri(ArmTri::NUL, rig.st.arm.link);
}

void test_stowed_arm_is_attached_link_up_and_out_false() {
  Rig rig;
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::NO);
  rig.hold_ui(LiftDir::UP, 1, 200);
  assert_tri(ArmTri::YES, rig.st.arm.attached);
  assert_tri(ArmTri::YES, rig.st.arm.link);
  assert_tri(ArmTri::NO, rig.st.arm.out);
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  TEST_ASSERT_FALSE(rig.nvs_value);
  TEST_ASSERT_EQUAL_INT(0, rig.nvs_writes);  // 書くのは値が変わったときだけ
}

// --- 腕が出る → NVS に書けてから out を出す（変異 8） ---

void test_out_true_is_shown_only_after_nvs_write_is_acknowledged() {
  Rig rig;
  rig.manual_nvs = true;
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::YES);
  rig.tick();
  // まだ書いていない → true を出さない（アームの制御器はこれを読んでから腕を出す）
  assert_tri(ArmTri::NUL, rig.st.arm.out);
  bool value = false;
  TEST_ASSERT_TRUE(rig.ctrl.take_arm_latch_write(&value));
  TEST_ASSERT_TRUE(value);
  // 書いている間（結果を返す前）も出さない
  rig.arm(kArm, ArmTri::YES);
  rig.tick();
  assert_tri(ArmTri::NUL, rig.st.arm.out);
  TEST_ASSERT_FALSE(rig.ctrl.take_arm_latch_write(&value));  // 二重に頼まない
  rig.ctrl.on_arm_latch_written(true, true);
  rig.arm(kArm, ArmTri::YES);
  rig.tick();
  assert_tri(ArmTri::YES, rig.st.arm.out);
  assert_tri(ArmTri::YES, rig.st.arm.link);
}

void test_out_null_is_treated_as_true_and_written() {
  Rig rig;
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::NUL);
  rig.tick();
  TEST_ASSERT_TRUE(rig.nvs_value);
  assert_tri(ArmTri::YES, rig.st.arm.out);  // null は true に倒して出す
}

void test_out_is_written_once_per_change() {
  Rig rig;
  rig.join_arm(kArm);
  for (int i = 0; i < 5; ++i) {
    rig.arm(kArm, ArmTri::YES);
    rig.tick();
  }
  TEST_ASSERT_EQUAL_INT(1, rig.nvs_writes);
  rig.arm(kArm, ArmTri::NO);
  rig.tick();
  TEST_ASSERT_EQUAL_INT(2, rig.nvs_writes);
  TEST_ASSERT_FALSE(rig.nvs_value);
  assert_tri(ArmTri::NO, rig.st.arm.out);
}

// --- #4e で止まる（変異 1〜6） ---

void test_lost_link_with_arm_out_stops_up_and_down_with_arm_link() {
  Rig rig;
  rig.arm_out_and_settled();
  // 通信できているあいだは動く
  rig.hold_ui(LiftDir::UP, 1, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  // arm が来なくなる（接続は開いたまま）
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  // 上昇
  rig.hold_ui(LiftDir::UP, 2, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  // 下降（変異 1: 下降を止めない）
  rig.hold_ui(LiftDir::DOWN, 3, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_tri(ArmTri::YES, rig.st.arm.attached);
  assert_tri(ArmTri::NO, rig.st.arm.link);
  assert_tri(ArmTri::NUL, rig.st.arm.out);
  TEST_ASSERT_EQUAL_INT(1, rig.st.arm.confirm);
}

void test_arm_link_is_reported_even_without_an_owner() {
  Rig rig;
  rig.arm_out_and_settled();
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  assert_reason(StopReason::ARM_LINK, rig.st);  // 持ち主がいなくても理由は ARM_LINK
}

void test_link_timeout_boundary_is_1000ms_after_last_arm() {
  Rig rig;
  rig.arm_out_and_settled();
  const uint32_t last = rig.t - 100;  // arm を受けた時刻（arm_out_and_settled は受けてから 100 ms 進める）
  rig.t = last + LIFT_ARM_LINK_TIMEOUT_MS - 100;
  rig.ctrl.on_hold(ui(), hold_of(1, LiftDir::UP), rig.t);
  rig.tick(100);  // ちょうど 1000 ms: まだ通信できている
  TEST_ASSERT_EQUAL_UINT32(last + LIFT_ARM_LINK_TIMEOUT_MS, rig.t);
  assert_tri(ArmTri::YES, rig.st.arm.link);
  assert_reason(StopReason::NONE, rig.st);
  rig.ctrl.on_hold(ui(), hold_of(1, LiftDir::UP), rig.t + 1);
  rig.tick(1);  // 1001 ms
  assert_tri(ArmTri::NO, rig.st.arm.link);
  assert_reason(StopReason::ARM_LINK, rig.st);
}

void test_arm_link_stops_descent_while_height_is_unknown_before_homing() {
  // 変異 2: 原点の前（高さが読めない間）の下降。起動したとき覚えていた。
  // 対照: 覚えていなければ、高さが読めなくても下降は動く（上昇だけ止まる）
  {
    Rig free_rig;
    free_rig.hal.set_homed(false);
    free_rig.t += 100;
    free_rig.hal.set_height(0, false, free_rig.t);
    free_rig.ctrl.on_hold(ui(), hold_of(1, LiftDir::DOWN), free_rig.t);
    free_rig.st = free_rig.ctrl.step(free_rig.t);
    TEST_ASSERT_TRUE(free_rig.hal.motor_moving());
  }
  Rig rig(true);
  rig.hal.set_homed(false);
  rig.t += 100;
  rig.hal.set_height(0, false, rig.t);
  rig.ctrl.on_hold(ui(), hold_of(1, LiftDir::DOWN), rig.t);
  rig.st = rig.ctrl.step(rig.t);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_FALSE(rig.st.height_ok);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
}

void test_arm_link_stops_the_physical_button_both_ways() {
  // 変異 3: 物理ボタン（BUTTON の接続）も止める
  Rig rig;
  rig.arm_out_and_settled();
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  for (int i = 0; i < 2; ++i) {
    const LiftDir dir = i == 0 ? LiftDir::UP : LiftDir::DOWN;
    rig.ctrl.on_hold(button(), hold_of(10 + i, dir), rig.t + 100);
    rig.tick();
    assert_reason(StopReason::ARM_LINK, rig.st);
    TEST_ASSERT_FALSE(rig.hal.motor_moving());
    rig.ctrl.on_release(button(), 10 + i);
  }
}

void test_arm_link_stops_a_real_button_press_through_button_step() {
  // 実際の経路（button_step が on_hold を呼ぶ）でも止まる。対照に、覚えていなければ動く
  for (int latched = 0; latched < 2; ++latched) {
    FakeHal hal;
    hal.set_homed(true);
    LiftController ctrl(&hal, LIFT_TOP_MM);
    if (latched) {
      ctrl.restore_arm_out_latched(true);
    }
    ButtonInput button_in;
    LiftSettings settings;
    uint32_t t = 1000;  // 反転の停止の間（ManualSwitch）を 0 から数えないように
    LiftState st;
    for (int i = 0; i < 30; ++i) {  // 300 ms。最初の 10 回は離れている（エンコーダは回さないので長くは動かさない）
      t += 10;
      hal.set_height(500, true, t);
      const int up_level = i < 10 ? 1 : 0;  // 1 = HIGH（離れている）・0 = LOW（押している）
      button_step(button_in, ctrl, up_level, 1, settings, t);
      st = ctrl.step(t);
    }
    if (latched) {
      assert_reason(StopReason::ARM_LINK, st);
      TEST_ASSERT_FALSE(hal.motor_moving());
    } else {
      TEST_ASSERT_TRUE(hal.motor_moving());
    }
  }
}

void test_out_false_then_lost_link_does_not_stop() {
  // 変異 4
  Rig rig;
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::NO);
  rig.tick();
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 500);
  rig.hold_ui(LiftDir::UP, 1, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.st);
  assert_tri(ArmTri::YES, rig.st.arm.attached);
  assert_tri(ArmTri::NO, rig.st.arm.link);
  assert_tri(ArmTri::NUL, rig.st.arm.out);
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
  rig.hold_ui(LiftDir::DOWN, 2, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.st);
  // 接続が閉じても（最後は格納位置）止めない
  rig.ctrl.on_close(kArm);
  rig.hold_ui(LiftDir::UP, 3, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.st);
}

void test_out_null_then_lost_link_stops_like_true() {
  // 変異 5: null を false と同じに扱わない
  Rig rig;
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::NUL);
  rig.tick();
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  rig.hold_ui(LiftDir::UP, 1, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
}

void test_open_connection_without_arm_messages_is_no_link() {
  // 変異 6: 接続が開いているだけでは通信できていない（on_close しない）
  Rig rig;
  rig.arm_out_and_settled();
  for (int i = 0; i < 12; ++i) {
    rig.ctrl.on_hold(ui(), hold_of(1, LiftDir::UP), rig.t + 100);
    rig.tick();
  }
  assert_reason(StopReason::ARM_LINK, rig.st);
  assert_tri(ArmTri::NO, rig.st.arm.link);
}

void test_closed_arm_connection_is_no_link_at_once() {
  Rig rig;
  rig.arm_out_and_settled();
  rig.ctrl.on_close(kArm);
  rig.hold_ui(LiftDir::UP, 1, 100);
  assert_reason(StopReason::ARM_LINK, rig.st);  // 時間切れを待たない
  assert_tri(ArmTri::NO, rig.st.arm.link);
}

// --- 起動したとき覚えている（変異 7） ---

void test_remembered_arm_stops_before_any_arm_message() {
  Rig rig(true);
  TEST_ASSERT_EQUAL_INT(1, rig.st.arm.confirm);  // 最初の step から確認待ち
  rig.hold_ui(LiftDir::UP, 1, 300);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  rig.hold_ui(LiftDir::DOWN, 2, 300);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_tri(ArmTri::NUL, rig.st.arm.attached);
  assert_tri(ArmTri::NO, rig.st.arm.link);
  assert_tri(ArmTri::NUL, rig.st.arm.out);  // 覚えていた値を out に出さない
  TEST_ASSERT_EQUAL_INT(1, rig.st.arm.confirm);
}

void test_remembered_arm_is_released_by_arm_out_false_and_written_back() {
  Rig rig(true);
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::NO);
  rig.tick();
  TEST_ASSERT_FALSE(rig.nvs_value);  // 次の再起動では止まらない
  rig.hold_ui(LiftDir::UP, 1, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.st);
  assert_tri(ArmTri::NO, rig.st.arm.out);
}

void test_remembered_arm_with_out_true_arm_message_resumes_without_a_write() {
  Rig rig(true);
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::YES);
  rig.tick();
  TEST_ASSERT_EQUAL_INT(0, rig.nvs_writes);  // もう NVS に true が入っている
  assert_tri(ArmTri::YES, rig.st.arm.out);
  rig.hold_ui(LiftDir::UP, 1, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());  // 通信が戻れば確認なしで動かせる
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
}

// --- NVS に書けなかった（変異 9） ---

void test_nvs_write_failure_stops_like_4e_and_hides_out() {
  Rig rig;
  rig.nvs_ok = false;
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::YES);
  rig.tick();  // 書けなかった
  rig.hold_ui(LiftDir::UP, 1, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);  // 通信できていても止める（覚えられないまま腕を出させない）
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  assert_tri(ArmTri::NUL, rig.st.arm.out);  // out: true を出さない（アームの制御器は腕を出さない）
  TEST_ASSERT_TRUE(rig.st.arm.confirm > 0);
  rig.hold_ui(LiftDir::DOWN, 2, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
}

void test_nvs_write_failure_clears_after_a_successful_retry() {
  Rig rig;
  rig.nvs_ok = false;
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::YES);
  rig.tick();
  rig.hold_ui(LiftDir::UP, 1, 100);
  assert_reason(StopReason::ARM_LINK, rig.st);
  rig.nvs_ok = true;
  rig.arm(kArm, ArmTri::YES);  // 次の arm で書き直す
  rig.tick();
  TEST_ASSERT_TRUE(rig.nvs_value);
  assert_tri(ArmTri::YES, rig.st.arm.out);
  rig.hold_ui(LiftDir::UP, 2, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.st);
}

void test_nvs_failure_to_lower_does_not_stop() {
  Rig rig(true);
  rig.nvs_ok = false;
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::NO);  // 格納位置。下ろす書き込みが失敗しても、止める理由は無い
  rig.tick();
  rig.hold_ui(LiftDir::UP, 1, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

// --- 覚えたものは接続の閉・時間切れでは下ろさない（変異 10） ---

void test_close_and_timeout_never_lower_the_latch() {
  Rig rig;
  rig.arm_out_and_settled();
  const int writes = rig.nvs_writes;
  rig.ctrl.on_close(kArm);
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 500);
  rig.hold_ui(LiftDir::UP, 1, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_TRUE(rig.nvs_value);
  TEST_ASSERT_EQUAL_INT(0, rig.nvs_false_writes);
  TEST_ASSERT_EQUAL_INT(writes, rig.nvs_writes);
  // 繋ぎ直して out: true と知らせても、覚えは変わらず書き直さない
  rig.join_arm(kArm2);
  rig.arm(kArm2, ArmTri::YES);
  rig.tick();
  TEST_ASSERT_EQUAL_INT(writes, rig.nvs_writes);
  TEST_ASSERT_TRUE(rig.nvs_value);
  assert_tri(ArmTri::YES, rig.st.arm.out);
}

void test_timeout_alone_keeps_the_latch_and_stop_number_counts_each_stop() {
  Rig rig;
  rig.arm_out_and_settled();
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  TEST_ASSERT_EQUAL_INT(1, rig.st.arm.confirm);
  rig.arm(kArm, ArmTri::YES);  // 通信が戻った
  rig.tick();
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  TEST_ASSERT_EQUAL_INT(2, rig.st.arm.confirm);  // 止め始めるたびに番号が増える
  TEST_ASSERT_TRUE(rig.nvs_value);
  TEST_ASSERT_EQUAL_INT(0, rig.nvs_false_writes);
}

// --- 確認（変異 11〜13） ---

void test_confirm_from_module_connection_is_dropped() {
  Rig rig(true);
  rig.tick();
  TEST_ASSERT_FALSE(rig.confirm(rig.st.arm.confirm, module(kFk)));
  TEST_ASSERT_FALSE(rig.confirm(rig.st.arm.confirm, module(kCam)));
  TEST_ASSERT_FALSE(rig.confirm(rig.st.arm.confirm, module(kArm)));
  TEST_ASSERT_FALSE(rig.confirm(rig.st.arm.confirm, button()));
  rig.hold_ui(LiftDir::UP, 1, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_TRUE(rig.st.arm.confirm > 0);
  TEST_ASSERT_TRUE(rig.nvs_value);
}

void test_confirm_with_wrong_number_is_dropped_and_right_number_clears() {
  Rig rig(true);
  rig.tick();
  const int shown = rig.st.arm.confirm;
  TEST_ASSERT_EQUAL_INT(1, shown);
  TEST_ASSERT_FALSE(rig.confirm(shown + 1, ui()));
  TEST_ASSERT_FALSE(rig.confirm(shown - 1, ui()));
  TEST_ASSERT_FALSE(rig.confirm(0, ui()));
  rig.hold_ui(LiftDir::UP, 1, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_TRUE(rig.confirm(shown, ui()));
  rig.hold_ui(LiftDir::UP, 2, 200);  // 新しく押し始めた操作で動く
  assert_reason(StopReason::NONE, rig.st);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

void test_late_confirm_for_an_old_stop_does_not_clear_a_new_stop() {
  // 止め直したあと、前の番号の確認が遅れて届く（変異 12）
  Rig rig;
  rig.arm_out_and_settled();
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  TEST_ASSERT_EQUAL_INT(1, rig.st.arm.confirm);
  rig.arm(kArm, ArmTri::YES);  // 通信が戻って解けた（確認なし）
  rig.tick();
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
  // 解けたあとに、古いダイアログの押下が届く: 確認待ちでないので捨てる（腕が出たままの覚えを消さない）
  TEST_ASSERT_FALSE(rig.confirm(1, ui()));
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  TEST_ASSERT_EQUAL_INT(2, rig.st.arm.confirm);  // 覚えが消えていないので、また止まる
  TEST_ASSERT_FALSE(rig.confirm(1, ui()));       // 前の番号
  TEST_ASSERT_TRUE(rig.nvs_value);
  rig.hold_ui(LiftDir::UP, 5, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_TRUE(rig.confirm(2, ui()));
}

void test_confirm_lowers_latch_resets_attached_and_writes_false() {
  // 変異 13
  Rig rig;
  rig.arm_out_and_settled();
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 200);
  const int shown = rig.st.arm.confirm;
  TEST_ASSERT_TRUE(shown > 0);
  TEST_ASSERT_TRUE(rig.confirm(shown, ui()));
  rig.tick();
  TEST_ASSERT_FALSE(rig.nvs_value);  // 覚えを下ろし、NVS にも書く
  TEST_ASSERT_EQUAL_INT(1, rig.nvs_false_writes);
  assert_tri(ArmTri::NUL, rig.st.arm.attached);  // 付いているとみなすのを「分からない」に戻す
  assert_tri(ArmTri::NUL, rig.st.arm.link);
  assert_tri(ArmTri::NUL, rig.st.arm.out);
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
  // 次に arm を受けるまで、止めない（通信できなくても。一度も通信できていないときと同じ）
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 500);
  rig.hold_ui(LiftDir::UP, 9, 200);
  assert_reason(StopReason::NONE, rig.st);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  rig.hold_ui(LiftDir::DOWN, 10, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

void test_confirm_is_not_repeatable() {
  Rig rig(true);
  rig.tick();
  const int shown = rig.st.arm.confirm;
  TEST_ASSERT_TRUE(rig.confirm(shown, ui()));
  TEST_ASSERT_FALSE(rig.confirm(shown, ui()));  // 確認待ちでなくなった
}

void test_returning_arm_message_clears_4e_without_confirm() {
  // 変異 14
  Rig rig(true);
  rig.hold_ui(LiftDir::UP, 1, 200);
  assert_reason(StopReason::ARM_LINK, rig.st);
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::YES);
  rig.ctrl.on_hold(ui(), hold_of(1, LiftDir::UP), rig.t + 100);
  rig.tick();
  assert_reason(StopReason::NONE, rig.st);
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_tri(ArmTri::YES, rig.st.arm.link);
}

// --- 1 本だけ・置き換え（変異 15・16） ---

void test_second_arm_connection_replaces_the_first_even_with_another_name() {
  Rig rig;
  TEST_ASSERT_EQUAL_INT(-1, rig.join_arm(kArm, "fk-arm"));
  TEST_ASSERT_EQUAL_INT(kArm, rig.join_arm(kArm2, "fk-arm2"));  // 名前が違っても古い方を閉じさせる
}

void test_replacing_the_arm_connection_is_not_a_link_loss() {
  // 新しい接続がすぐ arm を送る（古い接続の close が先 / 後の 2 通り）
  for (int close_first = 0; close_first < 2; ++close_first) {
    Rig rig;
    rig.arm_out_and_settled(kArm);
    rig.silence(300);
    const int replaced = rig.join_arm(kArm2);
    TEST_ASSERT_EQUAL_INT(kArm, replaced);
    if (close_first) {
      rig.ctrl.on_close(replaced);
    }
    // 置き換えた瞬間。新しい接続の最初の arm より前でも止めない（引き継いだ時刻から数える）
    rig.hold_ui(LiftDir::UP, 1, 100);
    assert_reason(StopReason::NONE, rig.st);
    TEST_ASSERT_TRUE(rig.hal.motor_moving());
    assert_tri(ArmTri::YES, rig.st.arm.link);
    if (!close_first) {
      rig.ctrl.on_close(replaced);
    }
    rig.hold_ui(LiftDir::UP, 1, 100);
    assert_reason(StopReason::NONE, rig.st);
    assert_tri(ArmTri::YES, rig.st.arm.link);
    rig.arm(kArm2, ArmTri::YES);
    rig.hold_ui(LiftDir::UP, 1, 100);
    assert_reason(StopReason::NONE, rig.st);
    TEST_ASSERT_TRUE(rig.hal.motor_moving());
  }
}

void test_replaced_connection_carries_the_clock_and_times_out_if_silent() {
  Rig rig;
  rig.arm_out_and_settled(kArm);
  const int replaced = rig.join_arm(kArm2);
  rig.ctrl.on_close(replaced);
  rig.silence(LIFT_ARM_LINK_TIMEOUT_MS + 300);  // 新しい接続が arm を送らなければ時間切れ
  rig.hold_ui(LiftDir::UP, 1, 100);
  assert_reason(StopReason::ARM_LINK, rig.st);
}

void test_old_replaced_connection_cannot_report_arm() {
  Rig rig;
  rig.arm_out_and_settled(kArm);
  rig.join_arm(kArm2);
  // 古い接続から out: false が遅れて届いても受けない（覚えを下ろさない）
  rig.arm(kArm, ArmTri::NO);
  rig.tick();
  TEST_ASSERT_TRUE(rig.nvs_value);
  TEST_ASSERT_EQUAL_INT(0, rig.nvs_false_writes);
}

void test_arm_from_non_arm_role_or_ui_connection_is_dropped() {
  // 変異 16
  Rig rig;
  rig.ctrl.on_hello(module(kCam), hello_cam(), nullptr);
  rig.ctrl.on_hello(module(kFk), hello_fk(), nullptr);
  rig.join_arm(kArm);
  rig.ctrl.on_arm(module(kCam), arm_out(ArmTri::YES), rig.t);
  rig.ctrl.on_arm(module(kFk), arm_out(ArmTri::YES), rig.t);
  rig.ctrl.on_arm(ui(), arm_out(ArmTri::YES), rig.t);
  rig.ctrl.on_arm(ui(kArm), arm_out(ArmTri::YES), rig.t);  // /ws/ui で同じ番号の接続（口が違う）
  rig.ctrl.on_arm(module(99), arm_out(ArmTri::YES), rig.t);  // hello の無い接続
  rig.ctrl.on_arm(button(), arm_out(ArmTri::YES), rig.t);
  rig.tick();
  assert_tri(ArmTri::NUL, rig.st.arm.attached);
  TEST_ASSERT_FALSE(rig.nvs_value);
  TEST_ASSERT_EQUAL_INT(0, rig.nvs_writes);
}

void test_arm_without_any_arm_role_connection_is_dropped() {
  Rig rig;
  rig.ctrl.on_hello(module(kCam), hello_cam(), nullptr);
  rig.ctrl.on_arm(module(kCam), arm_out(ArmTri::YES), rig.t);
  rig.ctrl.on_arm(button(), arm_out(ArmTri::YES), rig.t);  // 番号 -1（接続なし）と取り違えない
  rig.tick();
  assert_tri(ArmTri::NUL, rig.st.arm.attached);
  TEST_ASSERT_EQUAL_INT(0, rig.nvs_writes);
}

// --- アームの制御器が昇降を命じる（変異 17・18） ---

void test_arm_owner_is_arm_named_fk_arm_and_auto() {
  // 変異 18
  Rig rig;
  rig.join_arm(kArm, "fk-arm");
  rig.arm(kArm, ArmTri::NO);
  rig.ctrl.on_hold(module(kArm), hold_of(1, LiftDir::DOWN), rig.t + 100);
  rig.tick();
  TEST_ASSERT_TRUE(rig.st.has_owner);
  TEST_ASSERT_EQUAL_INT(3, rig.st.owner_kind);
  TEST_ASSERT_EQUAL_STRING("fk-arm", rig.st.owner_name);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO),
                        static_cast<int>(rig.st.owner_operator));
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  // state の JSON にも出る
  char json[LIFT_STATE_TEXT_MAX];
  TEST_ASSERT_GREATER_THAN(0, state_encode(rig.st, json, sizeof(json)));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner\":\"arm\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner_name\":\"fk-arm\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"owner_operator\":\"auto\""));
  TEST_ASSERT_NOT_NULL(std::strstr(json, "\"role\":\"arm\""));
  // 名乗った operator: human・ceiling_sensor: true は固定される（modules の欄）
  TEST_ASSERT_EQUAL_INT(1, rig.st.module_count);
  TEST_ASSERT_FALSE(rig.st.modules[0].ceiling_sensor);
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleOperator::AUTO),
                        static_cast<int>(rig.st.modules[0].op));
  TEST_ASSERT_EQUAL_INT(static_cast<int>(ModuleRole::ARM), static_cast<int>(rig.st.modules[0].role));
}

void test_arm_climb_is_stopped_by_the_camera_ceiling_not_its_own_value() {
  // 変異 17: カメラモジュールが近い天井を知らせているとき、アームの上昇は止まる
  Rig rig;
  rig.ctrl.on_hello(module(kCam), hello_cam(), nullptr);
  rig.join_arm(kArm);
  rig.arm(kArm, ArmTri::NO);
  CeilingReport near_ceiling;
  near_ceiling.status = CeilingStatus::MEASURED;
  near_ceiling.mm = 300;  // 閾値 500 mm より近い
  near_ceiling.age_ms = 0;
  near_ceiling.received_at_ms = rig.t + 100;
  rig.ctrl.on_ceiling(module(kCam), near_ceiling);
  // アーム自身の hold には「遠い」値を載せる（使われてはいけない）
  HoldMsg hold = hold_of(1, LiftDir::UP);
  hold.has_ceiling = true;
  hold.ceiling.status = CeilingStatus::MEASURED;
  hold.ceiling.mm = 1450;
  hold.ceiling.age_ms = 0;
  hold.ceiling.received_at_ms = rig.t + 100;
  rig.ctrl.on_hold(module(kArm), hold, rig.t + 100);
  rig.tick();
  assert_reason(StopReason::CEILING_NEAR, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  // 下降は天井に関係なく動く
  rig.ctrl.on_hold(module(kArm), hold_of(2, LiftDir::DOWN), rig.t + 100);
  rig.tick();
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

void test_arm_climb_without_camera_ceiling_stops_with_stale_even_if_it_sends_one() {
  Rig rig;
  rig.join_arm(kArm);  // ceiling_sensor: true を名乗っても出どころにならない
  rig.arm(kArm, ArmTri::NO);
  HoldMsg hold = hold_of(1, LiftDir::UP);
  hold.has_ceiling = true;
  hold.ceiling.status = CeilingStatus::MEASURED;
  hold.ceiling.mm = 1450;
  hold.ceiling.age_ms = 0;
  hold.ceiling.received_at_ms = rig.t + 100;
  rig.ctrl.on_hold(module(kArm), hold, rig.t + 100);
  rig.ctrl.on_ceiling(module(kArm), hold.ceiling);
  rig.tick();
  assert_reason(StopReason::CEILING_STALE, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
}

void test_arm_own_command_is_stopped_by_4e_too() {
  Rig rig(true);
  rig.join_arm(kArm);
  rig.ctrl.on_hold(module(kArm), hold_of(1, LiftDir::DOWN), rig.t + 100);
  rig.tick();  // 通信できる前（arm を送る前）のアームの制御器自身の命令も #4e で止まる
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
}

void test_goto_while_4e_reports_arm_link_not_height_unknown() {
  Rig rig(true);
  rig.hal.set_homed(false);
  rig.t += 100;
  rig.hal.set_height(0, false, rig.t);
  GotoMsg go;
  go.press = 1;
  go.target_mm = 800;
  go.speed = 40;
  go.has_ceiling = false;
  rig.ctrl.on_goto(module(kFk), go, rig.t);  // fk が goto（高さが読めない・#4e）
  rig.st = rig.ctrl.step(rig.t);
  assert_reason(StopReason::ARM_LINK, rig.st);  // goto の HEIGHT_UNKNOWN で上書きしない
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
}

// --- #4e が立ち始めたとき持ち主を離す（解けたあとは新しく押し始めた操作でなければ動かない。Spec-module §4）---

// 画面が hold up を送り続けている間に #4e が立つ（arm が来なくなる）。戻り値は #4e が立った周期の confirm
int hold_up_until_4e(Rig* rig, int press) {
  for (int i = 0; i < 20; ++i) {
    rig->hold_ui(LiftDir::UP, press, 100);
    if (rig->st.arm.confirm > 0) {
      return rig->st.arm.confirm;
    }
  }
  TEST_FAIL_MESSAGE("#4e が立たなかった");
  return 0;
}

void test_same_press_does_not_resume_after_confirm_but_new_press_does() {
  Rig rig;
  rig.arm_out_and_settled();
  rig.hold_ui(LiftDir::UP, 1, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  const int shown = hold_up_until_4e(&rig, 1);
  assert_reason(StopReason::ARM_LINK, rig.st);
  TEST_ASSERT_TRUE(rig.confirm(shown, ui()));
  rig.hold_ui(LiftDir::UP, 1, 300);  // 同じ press を送り続けても動かない
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  TEST_ASSERT_FALSE(rig.st.has_owner);
  assert_reason(StopReason::CMD_STOP, rig.st);
  rig.hold_ui(LiftDir::UP, 2, 200);  // 新しく押し始めれば動く
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
  assert_reason(StopReason::NONE, rig.st);
}

void test_same_press_does_not_resume_after_link_returns_but_new_press_does() {
  Rig rig;
  rig.arm_out_and_settled();
  rig.hold_ui(LiftDir::UP, 1, 200);
  hold_up_until_4e(&rig, 1);
  rig.arm(kArm, ArmTri::YES);  // 通信が戻って解ける
  rig.hold_ui(LiftDir::UP, 1, 300);
  TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
  TEST_ASSERT_FALSE(rig.hal.motor_moving());
  TEST_ASSERT_FALSE(rig.st.has_owner);
  rig.arm(kArm, ArmTri::YES);
  rig.hold_ui(LiftDir::UP, 2, 200);
  TEST_ASSERT_TRUE(rig.hal.motor_moving());
}

void test_held_physical_button_stops_in_4e_and_needs_a_new_press_after() {
  // button_step 経由。押し続けている間に #4e → 解けても、押し直すまで動かない
  Rig rig;
  rig.join_arm(kArm);
  ButtonInput button_in;
  LiftSettings settings;
  uint32_t t = 1000;
  bool up_low = false;
  int phase_checked = 0;
  for (int i = 0; i < 400; ++i) {
    t += 10;
    rig.t = t;
    rig.hal.set_height(500, true, t);
    if (i % 10 == 0 && (t < 1500 || t >= 3500)) {
      rig.ctrl.on_arm(module(kArm), arm_out(ArmTri::YES), t);  // 1500 ms〜3500 ms は来ない
    }
    up_low = i >= 10 && !(i >= 300 && i < 320);  // 押す・4000 ms ごろ一度離す・また押す
    button_step(button_in, rig.ctrl, up_low ? 0 : 1, 1, settings, t);
    rig.service();
    rig.st = rig.ctrl.step(t);
    if (t > 2800 && t < 3000) {  // #4e の間（押し続けている）
      assert_reason(StopReason::ARM_LINK, rig.st);
      TEST_ASSERT_FALSE(rig.hal.motor_moving());
      phase_checked |= 1;
    }
    if (t > 3600 && t < 3990) {  // 通信が戻り、解けた。まだ押し続けている（同じ押し始め）
      if (i < 300) {
        TEST_ASSERT_EQUAL_INT(0, rig.st.arm.confirm);
        TEST_ASSERT_FALSE(rig.hal.motor_moving());
        phase_checked |= 2;
      }
    }
    if (i >= 335 && i < 345) {  // 4000〜4200 ms に一度離して押し直した。押し直したあとは動く
      TEST_ASSERT_TRUE(rig.hal.motor_moving());
      phase_checked |= 4;
    }
  }
  TEST_ASSERT_EQUAL_INT(7, phase_checked);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_unknown_arm_does_not_stop_and_state_is_all_null);
  RUN_TEST(test_hello_alone_does_not_attach);
  RUN_TEST(test_stowed_arm_is_attached_link_up_and_out_false);
  RUN_TEST(test_out_true_is_shown_only_after_nvs_write_is_acknowledged);
  RUN_TEST(test_out_null_is_treated_as_true_and_written);
  RUN_TEST(test_out_is_written_once_per_change);
  RUN_TEST(test_lost_link_with_arm_out_stops_up_and_down_with_arm_link);
  RUN_TEST(test_arm_link_is_reported_even_without_an_owner);
  RUN_TEST(test_link_timeout_boundary_is_1000ms_after_last_arm);
  RUN_TEST(test_arm_link_stops_descent_while_height_is_unknown_before_homing);
  RUN_TEST(test_arm_link_stops_the_physical_button_both_ways);
  RUN_TEST(test_arm_link_stops_a_real_button_press_through_button_step);
  RUN_TEST(test_out_false_then_lost_link_does_not_stop);
  RUN_TEST(test_out_null_then_lost_link_stops_like_true);
  RUN_TEST(test_open_connection_without_arm_messages_is_no_link);
  RUN_TEST(test_closed_arm_connection_is_no_link_at_once);
  RUN_TEST(test_remembered_arm_stops_before_any_arm_message);
  RUN_TEST(test_remembered_arm_is_released_by_arm_out_false_and_written_back);
  RUN_TEST(test_remembered_arm_with_out_true_arm_message_resumes_without_a_write);
  RUN_TEST(test_nvs_write_failure_stops_like_4e_and_hides_out);
  RUN_TEST(test_nvs_write_failure_clears_after_a_successful_retry);
  RUN_TEST(test_nvs_failure_to_lower_does_not_stop);
  RUN_TEST(test_close_and_timeout_never_lower_the_latch);
  RUN_TEST(test_timeout_alone_keeps_the_latch_and_stop_number_counts_each_stop);
  RUN_TEST(test_confirm_from_module_connection_is_dropped);
  RUN_TEST(test_confirm_with_wrong_number_is_dropped_and_right_number_clears);
  RUN_TEST(test_late_confirm_for_an_old_stop_does_not_clear_a_new_stop);
  RUN_TEST(test_confirm_lowers_latch_resets_attached_and_writes_false);
  RUN_TEST(test_confirm_is_not_repeatable);
  RUN_TEST(test_returning_arm_message_clears_4e_without_confirm);
  RUN_TEST(test_second_arm_connection_replaces_the_first_even_with_another_name);
  RUN_TEST(test_replacing_the_arm_connection_is_not_a_link_loss);
  RUN_TEST(test_replaced_connection_carries_the_clock_and_times_out_if_silent);
  RUN_TEST(test_old_replaced_connection_cannot_report_arm);
  RUN_TEST(test_arm_from_non_arm_role_or_ui_connection_is_dropped);
  RUN_TEST(test_arm_without_any_arm_role_connection_is_dropped);
  RUN_TEST(test_arm_owner_is_arm_named_fk_arm_and_auto);
  RUN_TEST(test_arm_climb_is_stopped_by_the_camera_ceiling_not_its_own_value);
  RUN_TEST(test_arm_climb_without_camera_ceiling_stops_with_stale_even_if_it_sends_one);
  RUN_TEST(test_arm_own_command_is_stopped_by_4e_too);
  RUN_TEST(test_goto_while_4e_reports_arm_link_not_height_unknown);
  RUN_TEST(test_same_press_does_not_resume_after_confirm_but_new_press_does);
  RUN_TEST(test_same_press_does_not_resume_after_link_returns_but_new_press_does);
  RUN_TEST(test_held_physical_button_stops_in_4e_and_needs_a_new_press_after);
  return UNITY_END();
}
