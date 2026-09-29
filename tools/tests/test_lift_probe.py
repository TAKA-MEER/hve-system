"""`tools/lift_probe.py` の純関数の試験（ネットワーク無し）。

`lift_probe.py` を読み込むのに aiohttp いるので、`:func:`main` の試験は
このリポジトリの `.venv` で走らせること（CLAUDE.md「ビルドとテスト」）。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_PROBE_PATH = pathlib.Path(__file__).resolve().parents[1] / "lift_probe.py"
_spec = importlib.util.spec_from_file_location("lift_probe", _PROBE_PATH)
assert _spec is not None and _spec.loader is not None
lift_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lift_probe)

build_cmd = lift_probe.build_cmd
clamp_duty = lift_probe.clamp_duty
apply_action = lift_probe.apply_action
format_state = lift_probe.format_state
initial_state = lift_probe.initial_state
key_action = lift_probe.key_action
main = lift_probe.main
parse_args = lift_probe.parse_args

STEP = lift_probe.lift_probe_duty_step_pct
PERIOD = lift_probe.lift_probe_period_ms


# --- 立ち上がりは安全側 -------------------------------------------------


def test_initial_state_is_stopped_without_ceiling_permission():
    """起動直後は止まっていて、上昇の許可は外れていること。"""
    state = initial_state()
    assert state == {"dir": "stop", "duty_pct": 40, "ceil_ok": False}


def test_ceil_ok_is_false_unless_asked_for():
    """`--ceil-ok` を明示したときだけ上昇の許可を出すこと。"""
    assert parse_args(["ws://x/ws"]).ceil_ok is False
    assert parse_args(["ws://x/ws", "--ceil-ok"]).ceil_ok is True
    assert initial_state(ceil_ok=parse_args(["ws://x/ws", "--ceil-ok"]).ceil_ok)["ceil_ok"] is True


# --- キーの解釈 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "action"),
    [
        ("u", "up"),
        ("d", "down"),
        ("s", "stop"),
        ("c", "toggle_ceil_ok"),
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


def test_plus_key_is_accepted_in_both_forms():
    """`=` だけだと Shift なしで + を押せないので、両方を受けること。"""
    assert key_action("+") == key_action("=")


# --- デューティの丸め ---------------------------------------------------


@pytest.mark.parametrize(
    ("given", "want"),
    [(-1, 0), (0, 0), (40, 40), (100, 100), (101, 100), (1000, 100)],
)
def test_clamp_duty_keeps_zero_to_one_hundred(given, want):
    assert clamp_duty(given) == want


# --- 操作の適用 ---------------------------------------------------------


def test_up_and_down_change_direction():
    state = initial_state()
    assert apply_action(state, "up")["dir"] == "up"
    assert apply_action(state, "down")["dir"] == "down"


def test_stop_keeps_duty_and_permission():
    """`s` はデューティも ceil_ok も保持すること。0 に戻すと次に `u` で消える。"""
    state = apply_action(apply_action(initial_state(), "toggle_ceil_ok"), "up")
    state = apply_action(state, "duty_up")
    stopped = apply_action(state, "stop")
    assert stopped["dir"] == "stop"
    assert stopped["duty_pct"] == state["duty_pct"] == 40 + STEP
    assert stopped["ceil_ok"] is True


def test_toggle_ceil_ok_flips_both_ways():
    """`c` は押すたびにON/OFF。1 回で false に戻ること。"""
    state = initial_state()
    assert state["ceil_ok"] is False
    on = apply_action(state, "toggle_ceil_ok")
    assert on["ceil_ok"] is True
    assert apply_action(on, "toggle_ceil_ok")["ceil_ok"] is False


def test_duty_keys_step_and_stop_at_the_ends():
    state = initial_state()
    assert apply_action(state, "duty_up")["duty_pct"] == 40 + STEP
    assert apply_action(state, "duty_down")["duty_pct"] == 40 - STEP
    top = initial_state(duty_pct=100)
    assert apply_action(top, "duty_up")["duty_pct"] == 100
    bottom = initial_state(duty_pct=0)
    assert apply_action(bottom, "duty_down")["duty_pct"] == 0


def test_apply_action_does_not_change_the_given_state():
    state = initial_state()
    apply_action(state, "up")
    assert state["dir"] == "stop"


def test_apply_action_ignores_unknown_action():
    state = initial_state()
    assert apply_action(state, "teleport") == state


# --- cmd の組み立て -----------------------------------------------------


def test_build_cmd_carries_seq_dir_duty_and_permission():
    state = apply_action(initial_state(), "up")
    cmd = build_cmd(7, state)
    assert cmd == {"t": "cmd", "seq": 7, "dir": "up", "duty": 40, "ceil_ok": False}


@pytest.mark.parametrize("dir_", ["up", "down", "stop"])
def test_build_cmd_always_carries_ceil_ok(dir_):
    """移動していないときでも `ceil_ok` を必ず載せる（詳細設計 §3 の協定）。"""
    cmd = build_cmd(0, {"dir": dir_, "duty_pct": 40, "ceil_ok": True})
    assert "ceil_ok" in cmd
    assert cmd["ceil_ok"] is True


def test_build_cmd_carries_ceil_ok_false_when_stopped():
    """既定のまま送ると `ceil_ok` が false で着く（上昇しない）。"""
    cmd = build_cmd(0, initial_state())
    assert cmd["ceil_ok"] is False
    assert cmd["dir"] == "stop"


def test_build_cmd_clamps_duty():
    cmd = build_cmd(0, {"dir": "up", "duty_pct": 140, "ceil_ok": False})
    assert cmd["duty"] == 100


# --- state の表示 -------------------------------------------------------


def test_format_state_is_one_line_and_shows_the_numbers():
    line = format_state(
        {
            "t": "state",
            "seq": 3,
            "dir": "up",
            "duty": 40,
            "reason": 0,
            "bottom": False,
            "height_mm": 1234,
            "height_ok": True,
            "top_mm": None,
            "cmd_age_ms": 12,
            "fw": "test",
        }
    )
    assert "\n" not in line
    for want in ("seq=3", "dir=up", "duty=40", "height_mm=1234", "height_ok=True", "top_mm=null"):
        assert want in line


def test_format_state_survives_missing_keys():
    """壊れた state でも例外を出さないこと。"""
    assert isinstance(format_state({}), str)


# --- 引数 ---------------------------------------------------------------


def test_help_works(capsys):
    """`--help` が 0 で終わること。"""
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--help"])
    assert exit_info.value.code == 0
    assert "ceil_ok" in capsys.readouterr().out


def test_stop_sending_flag():
    assert parse_args(["ws://x/ws"]).stop_sending is False
    assert parse_args(["ws://x/ws", "--stop-sending"]).stop_sending is True


def test_default_period_is_the_documented_one():
    assert parse_args(["ws://x/ws"]).period_ms == PERIOD == 100
