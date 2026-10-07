"""`tools/lift_probe.py`（v2）の純関数の試験（ネットワーク無し）。"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_PROBE_PATH = pathlib.Path(__file__).resolve().parents[1] / "lift_probe.py"
_spec = importlib.util.spec_from_file_location("lift_probe", _PROBE_PATH)
assert _spec is not None and _spec.loader is not None
lift_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lift_probe)

key_action = lift_probe.key_action
clamp_duty = lift_probe.clamp_duty
apply_action = lift_probe.apply_action
initial_state = lift_probe.initial_state
build_hello = lift_probe.build_hello
build_hold = lift_probe.build_hold
build_release = lift_probe.build_release
ceiling_of = lift_probe.ceiling_of
format_state = lift_probe.format_state
parse_args = lift_probe.parse_args

STEP = lift_probe.lift_probe_duty_step_pct
PERIOD = lift_probe.lift_probe_period_ms


# --- 立ち上がりは安全側 -------------------------------------------------


def test_initial_state_is_stopped_with_near_ceiling():
    """起動直後は止まっていて、天井は近すぎ（上昇しない側）のこと。"""
    state = initial_state()
    assert state == {"dir": "stop", "duty_pct": 40, "press": 0, "ceiling_near": True}


# --- キーの解釈 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "action"),
    [
        ("u", "up"),
        ("d", "down"),
        ("s", "stop"),
        ("c", "toggle_ceiling"),
        ("+", "duty_up"),
        ("=", "duty_up"),
        ("-", "duty_down"),
        ("x", "toggle_sending"),
        ("q", "quit"),
    ],
)
def test_key_action_knows_every_documented_key(key, action):
    assert key_action(key) == action


def test_unknown_key_does_nothing():
    assert key_action("z") is None
    assert key_action("") is None


# --- press の採番 -------------------------------------------------------


def test_press_increases_on_new_press_only():
    """押し始め（stop→up・up→down）で増え、押し続けでは増えないこと。"""
    state = initial_state()
    first = apply_action(state, "up")
    assert first["press"] == 1
    assert apply_action(first, "up")["press"] == 1  # 押し続け
    switched = apply_action(first, "down")
    assert switched["press"] == 2  # 入れ替え
    stopped = apply_action(switched, "stop")
    assert stopped["press"] == 2  # 離すときは増えない
    again = apply_action(stopped, "up")
    assert again["press"] == 3


def test_stop_keeps_duty_and_press():
    state = apply_action(initial_state(), "up")
    stopped = apply_action(state, "stop")
    assert stopped["dir"] == "stop"
    assert stopped["duty_pct"] == state["duty_pct"]
    assert stopped["press"] == state["press"]


def test_toggle_ceiling_flips_both_ways():
    state = initial_state()
    assert state["ceiling_near"] is True
    far = apply_action(state, "toggle_ceiling")
    assert far["ceiling_near"] is False
    assert apply_action(far, "toggle_ceiling")["ceiling_near"] is True


def test_apply_action_does_not_change_the_given_state():
    state = initial_state()
    apply_action(state, "up")
    assert state["dir"] == "stop"


# --- メッセージの組み立て -------------------------------------------------


def test_build_hello_claims_a_sensor_once():
    hello = build_hello()
    assert hello["t"] == "hello"
    assert hello["ceiling_sensor"] is True


def test_build_hold_carries_press_dir_duty_and_ceiling():
    state = apply_action(initial_state(), "up")
    hold = build_hold(state)
    assert hold["t"] == "hold"
    assert hold["press"] == 1
    assert hold["dir"] == "up"
    assert hold["duty"] == 40
    assert hold["ceiling"]["status"] == "TOO_NEAR"  # 立ち上がりは近すぎ


def test_build_hold_carries_far_ceiling_after_toggle():
    state = apply_action(apply_action(initial_state(), "toggle_ceiling"), "up")
    hold = build_hold(state)
    assert hold["ceiling"]["status"] == "MEASURED"
    assert hold["ceiling"]["mm"] == lift_probe.lift_probe_far_mm


def test_build_release_carries_the_same_press():
    state = apply_action(initial_state(), "up")
    release = build_release(state)
    assert release == {"t": "release", "press": 1}


def test_build_hold_clamps_duty():
    hold = build_hold({"dir": "up", "duty_pct": 140, "press": 1, "ceiling_near": True})
    assert hold["duty"] == 100


# --- state の表示 -------------------------------------------------------


def test_format_state_is_one_line_and_shows_the_numbers():
    line = format_state(
        {
            "t": "state",
            "seq": 3,
            "dir": "up",
            "duty": 40,
            "reason": "NONE",
            "bottom": False,
            "height_mm": 1234,
            "height_ok": True,
            "top_detect": False,
            "ceiling": {"status": "MEASURED", "reason": "NONE"},
            "owner": "module",
            "ui_clients": 1,
            "cmd_age_ms": 12,
            "fw": "test",
        }
    )
    assert "\n" not in line
    for want in ("seq=3", "dir=up", "duty=40", "height_mm=1234", "owner=module"):
        assert want in line


def test_format_state_survives_missing_keys():
    """壊れた state でも例外を出さないこと。"""
    assert isinstance(format_state({}), str)


# --- 引数 ---------------------------------------------------------------


def test_default_period_is_the_documented_one():
    assert parse_args(["ws://x/ws/module"]).period_ms == PERIOD == 100


def test_stop_sending_flag():
    assert parse_args(["ws://x/ws/module"]).stop_sending is False
    assert parse_args(["ws://x/ws/module", "--stop-sending"]).stop_sending is True
