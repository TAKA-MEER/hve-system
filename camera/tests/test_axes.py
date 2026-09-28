"""ピッチ・ヨーの計算と速度の丸めの試験。"""

import pytest

from hve_camera.axes import clamp_speed, pitch_step, yaw_step_rate
from hve_camera.params import load_params

PARAMS = load_params()
PITCH_MIN = PARAMS["pitch_min_deg"]
PITCH_MAX = PARAMS["pitch_max_deg"]
STEPS_PER_REV = PARAMS["yaw_steps_per_rev"]


# --- ピッチ: 積分 ------------------------------------------------------------------------------


def test_pitch_moves_up_by_the_speed_times_the_time():
    assert pitch_step(0.0, 10.0, 1000.0, PARAMS) == (10.0, "NONE")


def test_pitch_moves_down_by_the_speed_times_the_time():
    assert pitch_step(0.0, -10.0, 1000.0, PARAMS) == (-10.0, "NONE")


def test_pitch_uses_ms_not_seconds():
    assert pitch_step(0.0, 10.0, 100.0, PARAMS) == (1.0, "NONE")


def test_pitch_does_not_move_without_speed():
    assert pitch_step(12.0, 0.0, 1000.0, PARAMS) == (12.0, "NONE")


def test_pitch_stays_inside_the_range_step_by_step():
    """100 回積和法律、可動範囲から外へ出ない。`AXIS_LIMIT` は端で止まったときだけ。"""
    angle = 0.0
    for _ in range(100):
        angle, reason = pitch_step(angle, 10.0, 100.0, PARAMS)
        assert PITCH_MIN <= angle <= PITCH_MAX
        if reason == "AXIS_LIMIT":
            assert angle in (PITCH_MIN, PITCH_MAX)


# --- ピッチ: 可動範囲（変異 5 の芯）------------------------------------------------------------


def test_pitch_is_stopped_at_the_upper_limit():
    assert pitch_step(PITCH_MAX - 1.0, 30.0, 1000.0, PARAMS) == (PITCH_MAX, "AXIS_LIMIT")


def test_pitch_is_stopped_at_the_lower_limit():
    assert pitch_step(PITCH_MIN + 1.0, -30.0, 1000.0, PARAMS) == (PITCH_MIN, "AXIS_LIMIT")


def test_pitch_never_exceeds_the_upper_limit():
    assert pitch_step(0.0, 1000.0, 10_000.0, PARAMS) == (PITCH_MAX, "AXIS_LIMIT")


def test_pitch_never_goes_below_the_lower_limit():
    assert pitch_step(0.0, -1000.0, 10_000.0, PARAMS) == (PITCH_MIN, "AXIS_LIMIT")


def test_pitch_landing_exactly_on_the_limit_is_not_a_limit_hit():
    """境界。端にちょうど着くのは止めではない（越えない）。"""
    assert pitch_step(PITCH_MAX - 10.0, 10.0, 1000.0, PARAMS) == (PITCH_MAX, "NONE")
    assert pitch_step(PITCH_MIN + 10.0, -10.0, 1000.0, PARAMS) == (PITCH_MIN, "NONE")


def test_pitch_one_step_past_the_limit_is_a_limit_hit():
    assert pitch_step(PITCH_MAX - 10.0, 10.5, 1000.0, PARAMS) == (PITCH_MAX, "AXIS_LIMIT")
    assert pitch_step(PITCH_MIN + 10.0, -10.5, 1000.0, PARAMS) == (PITCH_MIN, "AXIS_LIMIT")


def test_pitch_at_the_limit_can_still_move_back():
    assert pitch_step(PITCH_MAX, -10.0, 1000.0, PARAMS) == (PITCH_MAX - 10.0, "NONE")


def test_pitch_range_comes_from_the_parameters():
    params = dict(PARAMS, pitch_min_deg=-10.0, pitch_max_deg=10.0)
    assert pitch_step(0.0, 100.0, 1000.0, params) == (10.0, "AXIS_LIMIT")
    assert pitch_step(0.0, -100.0, 1000.0, params) == (-10.0, "AXIS_LIMIT")


def test_pitch_reason_is_a_name_from_names_section_3():
    assert pitch_step(PITCH_MAX, 30.0, 1000.0, PARAMS)[1] in {"NONE", "AXIS_LIMIT"}


# --- ヨー: 角度を持たない ------------------------------------------------------------------------


def test_yaw_converts_deg_per_s_to_half_steps_per_s():
    """1 回転 = `yaw_steps_per_rev` ステップ = 360 deg。"""
    assert yaw_step_rate(360.0, "yaw_left", PARAMS) == (float(STEPS_PER_REV), "left")


def test_yaw_step_rate_is_proportional_to_the_speed():
    slow, _ = yaw_step_rate(10.0, "yaw_left", PARAMS)
    fast, _ = yaw_step_rate(20.0, "yaw_left", PARAMS)
    assert fast == pytest.approx(slow * 2)


def test_yaw_normalises_the_direction():
    assert yaw_step_rate(10.0, "yaw_right", PARAMS)[1] == "right"
    assert yaw_step_rate(10.0, "yaw_left", PARAMS)[1] == "left"


def test_yaw_ignores_the_sign_of_the_speed_because_the_direction_carries_it():
    assert yaw_step_rate(-10.0, "yaw_left", PARAMS)[0] == yaw_step_rate(
        10.0, "yaw_left", PARAMS
    )[0]


def test_yaw_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        yaw_step_rate(10.0, "pitch_up", PARAMS)


@pytest.mark.parametrize("speed", [0.0, 1.0, 30.0, 60.0, 360.0, 3600.0])
def test_yaw_has_no_range_limit(speed):
    """360° 的回し続けるを妨げない（spec Spec-ui.md §1.4）。"""
    steps_per_s, direction = yaw_step_rate(speed, "yaw_right", PARAMS)
    assert steps_per_s >= 0.0
    assert direction == "right"


def test_yaw_step_rate_uses_the_steps_per_rev_parameter():
    params = dict(PARAMS, yaw_steps_per_rev=1000)
    assert yaw_step_rate(36.0, "yaw_left", params) == (100.0, "left")


# --- 速度の丸め ----------------------------------------------------------------------------------


def test_clamp_speed_passes_a_value_inside_the_range():
    assert clamp_speed(25, {"min": 10, "max": 60}) == 25


def test_clamp_speed_raises_a_value_below_the_minimum():
    assert clamp_speed(3, {"min": 10, "max": 60}) == 10


def test_clamp_speed_lowers_a_value_above_the_maximum():
    assert clamp_speed(99, {"min": 10, "max": 60}) == 60


def test_clamp_speed_at_the_boundaries():
    assert clamp_speed(10, {"min": 10, "max": 60}) == 10
    assert clamp_speed(60, {"min": 10, "max": 60}) == 60


def test_clamp_speed_works_for_a_degenerate_range():
    assert clamp_speed(50, {"min": 30, "max": 30}) == 30


def test_clamp_speed_keeps_the_type_of_a_float():
    assert clamp_speed(12.5, {"min": 10, "max": 60}) == 12.5
