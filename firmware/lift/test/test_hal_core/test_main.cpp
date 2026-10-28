// Unity の試験（env:native）。WP-LIFT-05 で足した EncoderHeight の純関数の試験。
// ここで縛るのは「原点・読めるか・高さの式・PCNT の差の足し込み」こと。
// hal_esp32 がこれらを実際に使っているかはこの試験では見られないので、
// 変異チェックでは hal_esp32 側の配線も手で確かめること。
#include <unity.h>

#include "hal_core.h"

namespace {

// Arduino の HIGH = 1 / LOW = 0（Arduino.h は include しないので手で書く）
constexpr int kHigh = 1;
constexpr int kLow = 0;

// PWM の分解能。names.md §5 の PWM_RESOLUTION（昇降部 config.h / 8 bit）と同じ値。
// ここでは引数として渡すだけなので config.h ではなく試験側で持つ。
constexpr int kResolution = 8;
constexpr uint32_t kFullScale = (1u << kResolution) - 1;

// エンコーダ 1 周のカウント（ENC_PPR 360 の 4 逓倍）。設計値で 37.5 mm。
constexpr int32_t kCountsPerRev = 4 * 360;

// 高さ [mm] 以上になる最小のカウント数。height_mm() は µm を 1000 で割って
// 切り捨てる（負は 0 の向き）ので、切り上げてその mm に届かせる。
constexpr int32_t counts_for_mm(int mm) {
  const int32_t um = (mm < 0 ? -mm : mm) * 1000;
  const int32_t n = (um + LIFT_UM_PER_COUNT - 1) / LIFT_UM_PER_COUNT;
  return mm < 0 ? -n : n;
}

// その µm 以下に収まる最大のカウント数（切り捨て）。閾値の手前を作るのに使う。
constexpr int32_t counts_under_um(int32_t um) { return um / LIFT_UM_PER_COUNT; }

// 試験の時計。press() は押された状態を LIFT_BOTTOM_DEBOUNCE_MS 続けて読ませる
// （EncoderHeight に「押された」とみなされる）。release() は離れた状態を 1 回読ませる。
uint32_t g_now = 1000;

void press(EncoderHeight* h, int32_t count, int up_sign = ENC_UP_SIGN) {
  h->update(count, true, g_now, up_sign);
  g_now += LIFT_BOTTOM_DEBOUNCE_MS;
  h->update(count, true, g_now, up_sign);
}

void release(EncoderHeight* h, int32_t count, int up_sign = ENC_UP_SIGN) {
  g_now += 10;
  h->update(count, false, g_now, up_sign);
}

}  // namespace

void setUp() { g_now = 1000; }

void tearDown() {}

// --- 起動直後 ---

void test_not_pressed_at_boot_is_unknown() {
  EncoderHeight h;
  release(&h, 1234);
  TEST_ASSERT_FALSE(h.ok());
}

void test_pressed_at_boot_takes_home() {
  EncoderHeight h;
  press(&h, 1234);
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
}

// --- 押された瞬間に原点を取り直す ---

void test_second_landing_retakes_home() {
  EncoderHeight h;
  press(&h, 1000);
  release(&h, 1000 + counts_for_mm(10));  // 上げる（10 mm）
  TEST_ASSERT_EQUAL_INT(10, h.height_mm());
  // 2 回目の着地で原点を取り直す
  press(&h, 500);
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
  // 離れてから上げると高さが増える
  release(&h, 500 + counts_for_mm(10));
  TEST_ASSERT_EQUAL_INT(10, h.height_mm());
}

void test_height_grows_when_raising_after_release() {
  EncoderHeight h;
  press(&h, 0);
  release(&h, counts_for_mm(10));
  TEST_ASSERT_EQUAL_INT(10, h.height_mm());
  TEST_ASSERT_TRUE(h.ok());
}

// エンコーダ 1 周で 37 mm 上がる（設計値 37.5 mm。360 P/R × 4 = 1440 カウント）。
// 仮の 100 µm に戻すと 144 mm になり、カウント → mm の換算が落ちる。
void test_one_encoder_revolution_is_37_mm() {
  EncoderHeight h;
  press(&h, 0);
  release(&h, kCountsPerRev);
  TEST_ASSERT_EQUAL_INT(37, h.height_mm());
}

// --- 押されている間の下降は原点を下げる ---

void test_pressed_going_down_lowers_home() {
  EncoderHeight h;
  press(&h, 1000);
  // 押されている間に下降側へ 5 mm 進むと原点が下がる（高さは 0 のまま）
  press(&h, 1000 - counts_for_mm(5));
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
  // 離れてから 15 mm 上げると、ずらした原点からの高さになる
  release(&h, 1000 - counts_for_mm(5) + counts_for_mm(15));
  TEST_ASSERT_EQUAL_INT(15, h.height_mm());
}

// --- 押されている間の上昇は原点を動かさない ---

void test_pressed_going_up_does_not_move_home() {
  EncoderHeight h;
  press(&h, 1000);
  // 押されている間に上昇側へ進んでも原点は動かない（高さが付く）
  press(&h, 1000 + counts_for_mm(5));
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(5, h.height_mm());
  // 離れて戻しても原点は最初のまま
  release(&h, 1000);
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
}

// --- HOME_SLACK_MM を超えたら故障 ---

void test_pressed_within_slack_stays_ok() {
  EncoderHeight h;
  press(&h, 1000);
  press(&h, 1000 + counts_under_um(HOME_SLACK_MM * 1000));
  TEST_ASSERT_TRUE(h.ok());
}

void test_pressed_beyond_slack_is_fault() {
  EncoderHeight h;
  press(&h, 1000);
  press(&h, 1000 + counts_under_um(HOME_SLACK_MM * 1000) + 1);
  TEST_ASSERT_FALSE(h.ok());
}

void test_fault_stays_after_release_until_repress() {
  EncoderHeight h;
  press(&h, 1000);
  press(&h, 1000 + counts_under_um(HOME_SLACK_MM * 1000) + 1);
  TEST_ASSERT_FALSE(h.ok());
  // 離れても偽のまま
  release(&h, 1000);
  TEST_ASSERT_FALSE(h.ok());
  // 押し直したら原点を取り直して真に戻る
  press(&h, 900);
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
}

// --- 原点より下（ずれ）は負のまま ---

void test_below_home_is_negative() {
  EncoderHeight h;
  press(&h, 1000);
  release(&h, 1000 - counts_for_mm(10));  // 離れている間の下降側へのずれ
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(-10, h.height_mm());
}

// --- PCNT の A/B 4 逓倍 ---

namespace {
// (A,B) の列を 1 段ずつ進めた合計
int run_quad_steps(const PcntQuadConfig& cfg, const int* ab, int n) {
  int total = 0;
  for (int i = 1; i < n; ++i) {
    total += pcnt_quad_step(cfg, ab[2 * (i - 1)], ab[2 * (i - 1) + 1], ab[2 * i], ab[2 * i + 1]);
  }
  return total;
}
}  // namespace

void test_quad_forward_cycle_is_minus4() {
  // (A,B) = 00→10→11→01→00 の 1 周（A が B より進む向き）で −4
  const int ab[] = {0, 0, 1, 0, 1, 1, 0, 1, 0, 0};
  TEST_ASSERT_EQUAL_INT(-4, run_quad_steps(kPcntQuadEncoder, ab, 5));
}

void test_quad_backward_cycle_is_plus4() {
  // 逆向きの 1 周で +4
  const int ab[] = {0, 0, 0, 1, 1, 1, 1, 0, 0, 0};
  TEST_ASSERT_EQUAL_INT(4, run_quad_steps(kPcntQuadEncoder, ab, 5));
}

void test_quad_two_cycles_are_plusminus8() {
  // 2 周で ±8（1 周の 2 倍）
  const int fwd[] = {0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0};
  TEST_ASSERT_EQUAL_INT(-8, run_quad_steps(kPcntQuadEncoder, fwd, 9));
}

void test_quad_no_edge_is_zero() {
  // 変化が無ければ 0（両方同時に変わることは無い）
  TEST_ASSERT_EQUAL_INT(0, pcnt_quad_step(kPcntQuadEncoder, 0, 0, 0, 0));
  TEST_ASSERT_EQUAL_INT(0, pcnt_quad_step(kPcntQuadEncoder, 1, 1, 1, 1));
}

// --- 溢れの累積 + 今のカウンタ ---

namespace {
// ENC_PCNT_LIMIT（src/config.h）と同じ値
constexpr int32_t kLimit = 30000;
}  // namespace

void test_encoder_total_adds_overflow_and_raw() {
  TEST_ASSERT_EQUAL_INT32(10000, encoder_total(10000, 0));
  TEST_ASSERT_EQUAL_INT32(9999, encoder_total(10000, -1));
  TEST_ASSERT_EQUAL_INT32(-10000, encoder_total(-10000, 0));
  TEST_ASSERT_EQUAL_INT32(42, encoder_total(0, 42));
}

void test_guarded_holds_on_upward_wrap_race() {
  // 上限を超えてハードウェアが 0 に戻し、まだ割り込みが足していない瞬間:
  // overflow=0・raw=5・前回 29998 → 前回のまま
  TEST_ASSERT_EQUAL_INT32(29998, encoder_total_guarded(29998, 0, 5, kLimit));
  // 次の回に割り込みが足した値で読み直す
  TEST_ASSERT_EQUAL_INT32(30010, encoder_total_guarded(29998, 30000, 10, kLimit));
}

void test_guarded_holds_on_downward_wrap_race() {
  // 下限側も同じ
  TEST_ASSERT_EQUAL_INT32(-29998, encoder_total_guarded(-29998, 0, -5, kLimit));
  TEST_ASSERT_EQUAL_INT32(-30010, encoder_total_guarded(-29998, -30000, -10, kLimit));
}

void test_guarded_passes_normal_steps() {
  TEST_ASSERT_EQUAL_INT32(105, encoder_total_guarded(100, 0, 105, kLimit));
  TEST_ASSERT_EQUAL_INT32(-105, encoder_total_guarded(-100, 0, -105, kLimit));
  TEST_ASSERT_EQUAL_INT32(30100, encoder_total_guarded(30050, 30000, 100, kLimit));
}

void test_guarded_allows_exactly_half_limit() {
  // ちょうど limit / 2 の飛びは通す（超えたら止める）
  TEST_ASSERT_EQUAL_INT32(15000, encoder_total_guarded(0, 0, 15000, kLimit));
  TEST_ASSERT_EQUAL_INT32(0, encoder_total_guarded(0, 0, 15001, kLimit));
}

// --- 向きの符号 ---

void test_up_sign_minus_one_reverses() {
  EncoderHeight h;
  press(&h, 1000, -1);
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
  // +1 のとき下げ方向の進みが、-1 では上げ方向になる
  release(&h, 1000 - counts_for_mm(10), -1);
  TEST_ASSERT_EQUAL_INT(10, h.height_mm());
  TEST_ASSERT_TRUE(h.ok());
}

// --- 下端スイッチ ---

void test_bottom_high_means_pressed() {
  // NC 配線（hardware §1）。HIGH = 開いている = 押されている / 断線
  TEST_ASSERT_TRUE(bottom_pressed_from_level(kHigh));
}

void test_bottom_low_means_not_pressed() {
  TEST_ASSERT_FALSE(bottom_pressed_from_level(kLow));
}

void test_bottom_pressed_level_is_high() {
  // 配線を LOW に変えたときは定数も変えるので、ここで表に固定する
  TEST_ASSERT_EQUAL_INT(kHigh, BOTTOM_PRESSED_LEVEL);
}

void test_bottom_accepts_only_the_pressed_level() {
  TEST_ASSERT_EQUAL_INT(1, bottom_pressed_from_level(kHigh));
  TEST_ASSERT_EQUAL_INT(0, bottom_pressed_from_level(kLow));
}

// --- モータのデューティ -> PWM ---

void test_duty_zero_gives_zero_pwm() {
  // 停止が 0 でなければ「止まれ」が全開になる
  TEST_ASSERT_EQUAL_UINT32(0, motor_duty_to_pwm(0, kResolution));
}

void test_duty_negative_gives_zero_pwm() {
  TEST_ASSERT_EQUAL_UINT32(0, motor_duty_to_pwm(-10, kResolution));
}

void test_duty_full_gives_full_scale() {
  TEST_ASSERT_EQUAL_UINT32(kFullScale, motor_duty_to_pwm(100, kResolution));
}

void test_duty_over_full_is_clamped() {
  TEST_ASSERT_EQUAL_UINT32(kFullScale, motor_duty_to_pwm(250, kResolution));
}

void test_duty_is_linear_in_pwm() {
  // 8 bit なら 40% で 102。比例の関係が崩れていないことを見る
  TEST_ASSERT_EQUAL_UINT32(kFullScale * 40 / 100, motor_duty_to_pwm(40, kResolution));
  TEST_ASSERT_EQUAL_UINT32(kFullScale / 4, motor_duty_to_pwm(25, kResolution));
}

void test_duty_uses_abs_max_as_full_scale_not_motor_cap() {
  // 100 % = PWM の最大が基準（LIFT_DUTY_ABS_MAX_PCT で割る）。
  // 上限の値（50）で割ると 50 % が PWM の最大（= 24 V そのまま）になるので、
  // 50 % はフルスケールの半分であることを縛る（Spec-ui §2.1）
  TEST_ASSERT_EQUAL_UINT32(kFullScale / 2, motor_duty_to_pwm(50, kResolution));
  TEST_ASSERT_EQUAL_UINT32(2047, motor_duty_to_pwm(50, 12));  // 4095 × 50 / 100 = 2047（切り捨て）
}

void test_pwm_stays_inside_the_channel() {
  for (int duty = -20; duty <= 120; duty += 7) {
    TEST_ASSERT_TRUE(motor_duty_to_pwm(duty, kResolution) <= kFullScale);
  }
}

void test_pwm_works_at_12_bit_too() {
  const uint32_t full = (1u << 12) - 1;
  TEST_ASSERT_EQUAL_UINT32(0, motor_duty_to_pwm(0, 12));
  TEST_ASSERT_EQUAL_UINT32(full, motor_duty_to_pwm(100, 12));
}

// --- 下端スイッチの一瞬の誤り（2026-10-28。LIFT_BOTTOM_DEBOUNCE_MS・LIFT_HOME_ACCEPT_MM）---

namespace {
// 原点を取ってから離れ、mm だけ上げた状態にする。そのときの数を返す
int32_t home_then_raise(EncoderHeight* h, int mm) {
  press(h, 0);
  const int32_t count = counts_for_mm(mm);
  release(h, count);
  return count;
}

// 読んだ値を押されのまま ms だけ見せる（1 ms ごと）。最後の時刻で終わる
void hold_raw_pressed(EncoderHeight* h, int32_t count, uint32_t ms) {
  for (uint32_t i = 0; i <= ms; ++i) {
    h->update(count, true, g_now, ENC_UP_SIGN);
    ++g_now;
  }
  --g_now;
}
}  // namespace

void test_single_pressed_read_does_not_take_home() {
  EncoderHeight h;
  h.update(1234, true, g_now, ENC_UP_SIGN);
  TEST_ASSERT_FALSE(h.ok());
  // 短く押されてすぐ離れた
  g_now += LIFT_BOTTOM_DEBOUNCE_MS - 1;
  h.update(1234, true, g_now, ENC_UP_SIGN);
  TEST_ASSERT_FALSE(h.ok());
  g_now += 1;
  h.update(1234, false, g_now, ENC_UP_SIGN);
  TEST_ASSERT_FALSE(h.ok());
}

void test_pressed_counts_after_exactly_debounce_ms() {
  EncoderHeight h;
  h.update(1234, true, g_now, ENC_UP_SIGN);
  g_now += LIFT_BOTTOM_DEBOUNCE_MS - 1;
  h.update(1234, true, g_now, ENC_UP_SIGN);
  TEST_ASSERT_FALSE(h.ok());
  g_now += 1;
  h.update(1234, true, g_now, ENC_UP_SIGN);
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
}

void test_release_in_between_restarts_the_debounce() {
  EncoderHeight h;
  h.update(10, true, g_now, ENC_UP_SIGN);
  g_now += LIFT_BOTTOM_DEBOUNCE_MS - 1;
  h.update(10, false, g_now, ENC_UP_SIGN);  // 続かずに離れた
  g_now += 1;
  h.update(10, true, g_now, ENC_UP_SIGN);  // 押し直し。ここから数え直す
  g_now += LIFT_BOTTOM_DEBOUNCE_MS - 1;
  h.update(10, true, g_now, ENC_UP_SIGN);
  TEST_ASSERT_FALSE(h.ok());
  g_now += 1;
  h.update(10, true, g_now, ENC_UP_SIGN);
  TEST_ASSERT_TRUE(h.ok());
}

void test_debounce_survives_millis_wrap() {
  EncoderHeight h;
  g_now = 0xFFFFFFF0u;
  h.update(10, true, g_now, ENC_UP_SIGN);
  g_now += LIFT_BOTTOM_DEBOUNCE_MS - 1;  // 折り返して小さい値になる
  h.update(10, true, g_now, ENC_UP_SIGN);
  TEST_ASSERT_TRUE(g_now < 0x1000u);
  TEST_ASSERT_FALSE(h.ok());
  g_now += 1;
  h.update(10, true, g_now, ENC_UP_SIGN);
  TEST_ASSERT_TRUE(h.ok());
}

void test_short_glitch_while_raised_keeps_home_and_height() {
  EncoderHeight h;
  const int32_t count = home_then_raise(&h, 1900);
  TEST_ASSERT_EQUAL_INT(1900, h.height_mm());
  // 10 ms だけ押されたと読めて離れた
  g_now += 5;
  h.update(count, true, g_now, ENC_UP_SIGN);
  g_now += 5;
  h.update(count, false, g_now, ENC_UP_SIGN);
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(1900, h.height_mm());
}

void test_long_press_above_accept_is_rejected() {
  EncoderHeight h;
  const int32_t count = home_then_raise(&h, 500);
  hold_raw_pressed(&h, count, LIFT_BOTTOM_DEBOUNCE_MS + 20);
  TEST_ASSERT_FALSE(h.ok());
  // 原点にしていない（高さは 0 に戻らない）
  TEST_ASSERT_EQUAL_INT(500, h.height_mm());
}

void test_accept_boundary_is_inclusive() {
  EncoderHeight h;
  // ちょうど LIFT_HOME_ACCEPT_MM の高さ（µm で見て以下）なら原点にする
  const int32_t under = counts_under_um(LIFT_HOME_ACCEPT_MM * 1000);
  press(&h, 0);
  release(&h, under);
  press(&h, under);
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
  // 1 カウント超えたら拒否する
  EncoderHeight h2;
  press(&h2, 0);
  release(&h2, under + 1);
  press(&h2, under + 1);
  TEST_ASSERT_FALSE(h2.ok());
}

void test_rejected_press_takes_home_when_it_comes_down_within_accept() {
  EncoderHeight h;
  const int32_t count = home_then_raise(&h, 500);
  hold_raw_pressed(&h, count, LIFT_BOTTOM_DEBOUNCE_MS + 5);
  TEST_ASSERT_FALSE(h.ok());
  // 押されたまま下がる。まだ高い間は偽のまま
  h.update(counts_for_mm(300), true, ++g_now, ENC_UP_SIGN);
  TEST_ASSERT_FALSE(h.ok());
  // LIFT_HOME_ACCEPT_MM 以下へ下がったら、そこで原点を取って真に戻る
  const int32_t low = counts_under_um(LIFT_HOME_ACCEPT_MM * 1000);
  h.update(low, true, ++g_now, ENC_UP_SIGN);
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
}

void test_rejected_stays_false_after_release_and_repress_is_judged_again() {
  EncoderHeight h;
  const int32_t count = home_then_raise(&h, 500);
  hold_raw_pressed(&h, count, LIFT_BOTTOM_DEBOUNCE_MS + 5);
  TEST_ASSERT_FALSE(h.ok());
  // 離れても偽のまま
  release(&h, count);
  TEST_ASSERT_FALSE(h.ok());
  // 高い所で押し直しても偽のまま
  press(&h, count);
  TEST_ASSERT_FALSE(h.ok());
  release(&h, count);
  // 下げてから押し直せば原点を取って真に戻る
  press(&h, 5);
  TEST_ASSERT_TRUE(h.ok());
  TEST_ASSERT_EQUAL_INT(0, h.height_mm());
}

void test_rejected_press_does_not_trigger_slack_fault_path() {
  // 拒否された押下で上へ進んでも、HOME_SLACK_MM の故障の経路には入らない（高さは数え続ける）
  EncoderHeight h;
  const int32_t count = home_then_raise(&h, 500);
  hold_raw_pressed(&h, count, LIFT_BOTTOM_DEBOUNCE_MS + 5);
  h.update(count + counts_for_mm(50), true, ++g_now, ENC_UP_SIGN);
  TEST_ASSERT_FALSE(h.ok());
  TEST_ASSERT_EQUAL_INT(550, h.height_mm());
}

int main(int /*argc*/, char** /*argv*/) {
  UNITY_BEGIN();
  RUN_TEST(test_not_pressed_at_boot_is_unknown);
  RUN_TEST(test_pressed_at_boot_takes_home);
  RUN_TEST(test_second_landing_retakes_home);
  RUN_TEST(test_height_grows_when_raising_after_release);
  RUN_TEST(test_one_encoder_revolution_is_37_mm);
  RUN_TEST(test_pressed_going_down_lowers_home);
  RUN_TEST(test_pressed_going_up_does_not_move_home);
  RUN_TEST(test_pressed_within_slack_stays_ok);
  RUN_TEST(test_pressed_beyond_slack_is_fault);
  RUN_TEST(test_fault_stays_after_release_until_repress);
  RUN_TEST(test_below_home_is_negative);
  RUN_TEST(test_single_pressed_read_does_not_take_home);
  RUN_TEST(test_pressed_counts_after_exactly_debounce_ms);
  RUN_TEST(test_release_in_between_restarts_the_debounce);
  RUN_TEST(test_debounce_survives_millis_wrap);
  RUN_TEST(test_short_glitch_while_raised_keeps_home_and_height);
  RUN_TEST(test_long_press_above_accept_is_rejected);
  RUN_TEST(test_accept_boundary_is_inclusive);
  RUN_TEST(test_rejected_press_takes_home_when_it_comes_down_within_accept);
  RUN_TEST(test_rejected_stays_false_after_release_and_repress_is_judged_again);
  RUN_TEST(test_rejected_press_does_not_trigger_slack_fault_path);
  RUN_TEST(test_quad_forward_cycle_is_minus4);
  RUN_TEST(test_quad_backward_cycle_is_plus4);
  RUN_TEST(test_quad_two_cycles_are_plusminus8);
  RUN_TEST(test_quad_no_edge_is_zero);
  RUN_TEST(test_encoder_total_adds_overflow_and_raw);
  RUN_TEST(test_guarded_holds_on_upward_wrap_race);
  RUN_TEST(test_guarded_holds_on_downward_wrap_race);
  RUN_TEST(test_guarded_passes_normal_steps);
  RUN_TEST(test_guarded_allows_exactly_half_limit);
  RUN_TEST(test_up_sign_minus_one_reverses);
  RUN_TEST(test_bottom_high_means_pressed);
  RUN_TEST(test_bottom_low_means_not_pressed);
  RUN_TEST(test_bottom_pressed_level_is_high);
  RUN_TEST(test_bottom_accepts_only_the_pressed_level);
  RUN_TEST(test_duty_zero_gives_zero_pwm);
  RUN_TEST(test_duty_negative_gives_zero_pwm);
  RUN_TEST(test_duty_full_gives_full_scale);
  RUN_TEST(test_duty_over_full_is_clamped);
  RUN_TEST(test_duty_is_linear_in_pwm);
  RUN_TEST(test_duty_uses_abs_max_as_full_scale_not_motor_cap);
  RUN_TEST(test_pwm_stays_inside_the_channel);
  RUN_TEST(test_pwm_works_at_12_bit_too);
  return UNITY_END();
}
