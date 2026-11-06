"""`tools/lift_probe.py`（v2）の純関数の試験（ネットワーク無し）。"""

from __future__ import annotations

import importlib.util
import pathlib

import aiohttp
import pytest

_PROBE_PATH = pathlib.Path(__file__).resolve().parents[1] / "lift_probe.py"
_spec = importlib.util.spec_from_file_location("lift_probe", _PROBE_PATH)
assert _spec is not None and _spec.loader is not None
lift_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lift_probe)

key_action = lift_probe.key_action
clamp_speed = lift_probe.clamp_speed
apply_action = lift_probe.apply_action
initial_state = lift_probe.initial_state
build_hello = lift_probe.build_hello
build_hold = lift_probe.build_hold
build_release = lift_probe.build_release
ceiling_of = lift_probe.ceiling_of
format_state = lift_probe.format_state
parse_args = lift_probe.parse_args

STEP = lift_probe.lift_probe_speed_step_mm_s
PERIOD = lift_probe.lift_probe_period_ms


# --- 立ち上がりは安全側 -------------------------------------------------


def test_initial_state_is_stopped_with_near_ceiling():
    """起動直後は止まっていて、天井は近すぎ（上昇しない側）のこと。"""
    state = initial_state()
    assert state == {"dir": "stop", "speed_mm_s": 40, "press": 0, "ceiling_near": True,
                     "goto_mm": 1000}


# --- キーの解釈 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "action"),
    [
        ("u", "up"),
        ("d", "down"),
        ("s", "stop"),
        ("c", "toggle_ceiling"),
        ("+", "speed_up"),
        ("=", "speed_up"),
        ("-", "speed_down"),
        ("g", "goto"),
        ("]", "goto_up"),
        ("[", "goto_down"),
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


def test_stop_keeps_speed_and_press():
    state = apply_action(initial_state(), "up")
    stopped = apply_action(state, "stop")
    assert stopped["dir"] == "stop"
    assert stopped["speed_mm_s"] == state["speed_mm_s"]
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
    assert (hello["name"], hello["role"], hello["operator"]) == ("lift-probe", "control", "human")
    assert "busy_label" not in hello


def test_build_hello_carries_the_given_identity():
    """名乗りが `hello` に載る（protocol-modules §1.1）。"""
    hello = build_hello("fk", "control", "auto", False, "測定操作中")
    assert hello == {"t": "hello", "name": "fk", "role": "control", "operator": "auto",
                     "ceiling_sensor": False, "busy_label": "測定操作中",
                     "fw": lift_probe.lift_probe_fw}
    assert build_hello("v", "observe")["role"] == "observe"


def test_hello_from_args_reads_every_identity_option():
    args = parse_args(["ws://x/ws/module", "--name", "ss", "--role", "control",
                       "--operator", "auto", "--no-ceiling-sensor",
                       "--busy-label", "画像認識で操作中"])
    hello = lift_probe.hello_from_args(args)
    assert (hello["name"], hello["operator"], hello["ceiling_sensor"], hello["busy_label"]) == (
        "ss", "auto", False, "画像認識で操作中")
    default = lift_probe.hello_from_args(parse_args(["ws://x/ws/module"]))
    assert default == build_hello()


def test_bad_identity_options_are_refused():
    for bad in (["--name", "Bad Name"], ["--name", "x" * 17], ["--name", ""],
                ["--role", "admin"], ["--operator", "robot"],
                ["--busy-label", ""], ["--busy-label", "あ" * 17]):
        with pytest.raises(SystemExit):
            parse_args(["ws://x/ws/module", *bad])


def test_sensorless_hold_and_goto_carry_no_ceiling():
    state = apply_action(initial_state(), "up")
    assert "ceiling" not in lift_probe.build_hold(state, ceiling_sensor=False)
    goto_state = apply_action(initial_state(), "goto")
    assert "ceiling" not in lift_probe.build_goto(goto_state, ceiling_sensor=False)
    assert "ceiling" in lift_probe.build_goto(goto_state)


def test_goto_message_and_press():
    state = apply_action(initial_state(), "goto")
    assert state["press"] == 1 and state["dir"] == "goto"
    assert apply_action(state, "goto")["press"] == 1  # 続けても増えない
    goto = lift_probe.build_goto(state)
    assert goto["t"] == "goto" and goto["press"] == 1 and goto["target_mm"] == 1000
    assert goto["speed"] == 40 and goto["ceiling"]["status"] == "TOO_NEAR"
    assert lift_probe.command_message(state)["t"] == "goto"
    moved = apply_action(apply_action(state, "goto_up"), "goto_up")
    assert moved["goto_mm"] == 1200 and moved["press"] == 1
    assert apply_action(moved, "up")["press"] == 2  # goto から up は押し直し
    assert apply_action(initial_state(goto_mm=50), "goto_down")["goto_mm"] == 0


def test_stopped_probe_sends_no_command_but_a_sensor_probe_sends_ceiling():
    stopped = initial_state()
    assert lift_probe.command_message(stopped) is None
    ceiling = lift_probe.build_ceiling(apply_action(stopped, "toggle_ceiling"))
    assert ceiling == {"t": "ceiling", "status": "MEASURED", "mm": lift_probe.lift_probe_far_mm,
                       "age_ms": 50}


def test_probe_apply_releases_a_goto():
    probe = lift_probe.Probe("ws://x", initial_state(), True, 100)
    assert probe.apply("goto")["t"] == "goto"
    assert probe.apply("stop") == {"t": "release", "press": 1}
    assert probe.apply("stop") is None


def test_build_hold_carries_press_dir_speed_and_ceiling():
    state = apply_action(initial_state(), "up")
    hold = build_hold(state)
    assert hold["t"] == "hold"
    assert hold["press"] == 1
    assert hold["dir"] == "up"
    assert hold["speed"] == 40
    assert "duty" not in hold  # 旧版の duty は送らない
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


def test_build_hold_clamps_speed():
    hold = build_hold({"dir": "up", "speed_mm_s": 140, "press": 1, "ceiling_near": True})
    assert hold["speed"] == 100


# --- state の表示 -------------------------------------------------------


def test_format_state_is_one_line_and_shows_the_numbers():
    line = format_state(
        {
            "t": "state",
            "seq": 3,
            "dir": "up",
            "duty": 40,
            "speed": 40,
            "speed_meas": 39,
            "reason": "NONE",
            "bottom": False,
            "height_mm": 1234,
            "height_ok": True,
            "top_detect": False,
            "ceiling": {"status": "MEASURED", "reason": "NONE"},
            "owner": "module",
            "owner_name": "fk",
            "owner_operator": "auto",
            "modules": [{"name": "hve-cam"}, {"name": "fk"}],
            "goto": {"status": "MOVING"},
            "arm": {"out": None},
            "ui_clients": 1,
            "cmd_age_ms": 12,
            "fw": "test",
        }
    )
    assert "\n" not in line
    for want in ("seq=3", "dir=up", "duty=40", "speed=40", "speed_meas=39", "height_mm=1234", "owner=module/fk/auto",
                 "modules=hve-cam,fk", "goto=MOVING"):
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


# --- 偽の昇降部へ実際に繋ぐ（名乗りが hello に載って届く・天井を貸す）---


async def _states(ws, pred, tries=40):
    import json as _json

    for _ in range(tries):
        msg = await ws.receive()
        if msg.type != aiohttp.WSMsgType.TEXT:
            break
        state = _json.loads(msg.data)
        if pred(state):
            return state
    raise AssertionError("条件に合う state が来ない")


async def test_probe_identity_reaches_the_lift_and_sensor_probe_lends_its_ceiling(
        aiohttp_client, tmp_path):
    import asyncio

    import sys as _sys
    _sys.path.insert(0, str(_PROBE_PATH.parent))
    import fake_lift_server as fake

    client = await aiohttp_client(fake.make_app(tmp_path, fake.FakeLift()))
    far = initial_state()
    far = apply_action(far, "toggle_ceiling")  # 遠い
    cam = lift_probe.Probe("x", far, True, 20, build_hello("hve-cam", "control", "human", True))
    fk = lift_probe.Probe("x", apply_action(far, "up"), True, 20,
                          build_hello("fk", "control", "auto", False, "測定操作中"))
    ws_cam = await client.ws_connect("/ws/module")
    ws_fk = await client.ws_connect("/ws/module")
    tasks = [asyncio.create_task(cam.send_loop(ws_cam)), asyncio.create_task(fk.send_loop(ws_fk))]
    try:
        state = await _states(ws_fk, lambda s: s.get("owner_name") == "fk" and s.get("dir") == "up")
    finally:
        cam.quitting = fk.quitting = True
        for task in tasks:
            task.cancel()
    names = {m["name"]: m for m in state["modules"]}
    assert names["fk"]["operator"] == "auto" and names["fk"]["busy_label"] == "測定操作中"
    assert names["fk"]["ceiling_sensor"] is False
    assert names["hve-cam"]["operator"] == "human"
    assert state["owner_operator"] == "auto" and state["ceiling"]["from"] == "hve-cam"
    await ws_cam.close()
    await ws_fk.close()
