"""`tools/fake_lift_server.py` の試験。

純関数（`ceiling_check`・`decode_hold`・設定の検証）だけでなく、
`FakeLift`（持ち主・判定をモータ相当へ効かせる呼び出し側）を通した試験で縛る。
`lift_core` と同じ規則（DetailedDesign.md §3・§4.1）なので、次の変異は赤になる:

- `/ws/module` の `hold` で天井が `TOO_NEAR` なのに上昇を許す
- 離したときに止めない（`release` を無視する）
- 古い `press` の `hold` で動き出す
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from aiohttp import WSMsgType

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fake_lift_server as fake

from fake_lift_server import FakeLift, ceiling_check, decode_hold, validate_settings


def hold_text(press=1, direction="up", speed=40, ceiling=None):
    body = {"t": "hold", "press": press, "dir": direction, "speed": speed}
    if ceiling is not None:
        body["ceiling"] = ceiling
    return body


FAR = {"status": "MEASURED", "mm": 2000, "age_ms": 50}
NEAR = {"status": "TOO_NEAR", "age_ms": 50}


# --- ceiling_check（lift_core/ceiling_check.cpp と同じ規則）---


def test_far_measured_is_ok():
    from fake_lift_server import CeilingReport

    ok, reason = ceiling_check(CeilingReport("MEASURED", 2000, 50, 1000), 1050, True)
    assert (ok, reason) == (True, "NONE")


def test_near_measured_is_not_ok():
    from fake_lift_server import CeilingReport

    ok, reason = ceiling_check(CeilingReport("MEASURED", 400, 50, 1000), 1050, True)
    assert (ok, reason) == (False, "CEILING_NEAR")


def test_too_near_is_not_ok():
    from fake_lift_server import CeilingReport

    ok, reason = ceiling_check(CeilingReport("TOO_NEAR", 0, 50, 1000), 1050, True)
    assert (ok, reason) == (False, "CEILING_NEAR")


def test_no_echo_is_ok_with_out_of_range():
    from fake_lift_server import CeilingReport

    ok, reason = ceiling_check(CeilingReport("NO_ECHO", 0, 50, 1000), 1050, True)
    assert (ok, reason) == (True, "OUT_OF_RANGE")


def test_read_error_is_stale():
    from fake_lift_server import CeilingReport

    ok, reason = ceiling_check(CeilingReport("READ_ERROR", 0, 50, 1000), 1050, True)
    assert (ok, reason) == (False, "CEILING_STALE")


def test_stale_age_is_not_ok():
    from fake_lift_server import CeilingReport

    ok, reason = ceiling_check(CeilingReport("MEASURED", 2000, 700, 1000), 1050, True)
    assert (ok, reason) == (False, "CEILING_STALE")


def test_without_sensor_everything_is_ok():
    from fake_lift_server import CeilingReport

    ok, _ = ceiling_check(CeilingReport("MISSING", 0, 0, 1000), 1050, False)
    assert ok is True


# --- decode_hold（protocol §2.1）---


def test_decode_hold_reads_a_full_hold():
    hold = decode_hold(hold_text(ceiling=FAR), 1000)
    assert hold is not None
    assert hold["press"] == 1 and hold["dir"] == "up" and hold["speed"] == 40
    assert hold["has_ceiling"] is True
    assert hold["ceiling"].status == "MEASURED"


def test_decode_hold_drops_a_duty_only_hold():
    """`duty` だけの hold（`speed` が無い）は読めない hold として捨てる。"""
    assert decode_hold({"t": "hold", "press": 1, "dir": "up", "duty": 40,
                        "ceiling": FAR}, 1000) is None
    assert decode_hold({"t": "hold", "press": 1, "dir": "up"}, 1000) is None


def test_decode_hold_clamps_speed_to_abs_max():
    """`speed` は 0〜LIFT_SPEED_ABS_MAX_MM_S（100）に丸める。"""
    hold = decode_hold(hold_text(speed=140, ceiling=FAR), 1000)
    assert hold is not None
    assert hold["speed"] == 100


def test_decode_hold_drops_a_hold_with_a_broken_speed():
    assert decode_hold(hold_text(speed="fast", ceiling=FAR), 1000) is None
    assert decode_hold(hold_text(speed=True, ceiling=FAR), 1000) is None


def test_decode_hold_keeps_broken_ceiling_as_missing_without_dropping():
    """型の違う ceiling は hold を捨てず MISSING として受け取る（その場で止める）。"""
    hold = decode_hold(hold_text(ceiling={"status": "MEASURED", "age_ms": 50}), 1000)
    assert hold is not None
    assert hold["has_ceiling"] is False


def test_decode_hold_drops_a_hold_with_a_broken_dir():
    assert decode_hold(hold_text(direction="sideways", ceiling=FAR), 1000) is None


# --- FakeLift を通した判定 ---


def module_conn(lift: FakeLift) -> int:
    conn = lift.add_conn("module")
    assert lift.on_hello(conn, "module", {"t": "hello", "ceiling_sensor": True})
    return conn


def test_ui_can_go_up_without_a_ceiling():
    """画面（/ws/ui）の上昇に天井の値は要らない（spec #4c）。"""
    lift = FakeLift()
    conn = lift.add_conn("ui")
    hold = decode_hold(hold_text(), 1000)
    assert hold is not None
    lift.on_hold(conn, "ui", hold, 1000)
    assert lift.step(1050)["dir"] == "up"


def test_module_with_too_near_cannot_go_up_but_can_go_down():
    """/ws/module の hold で天井が TOO_NEAR なのに上昇を許さない。"""
    lift = FakeLift()
    conn = module_conn(lift)
    hold = decode_hold(hold_text(direction="up", ceiling=NEAR), 1000)
    assert hold is not None
    lift.on_hold(conn, "module", hold, 1000)
    state = lift.step(1050)
    assert state["dir"] == "stop"
    assert state["reason"] == "CEILING_NEAR"

    down = decode_hold(hold_text(press=2, direction="down", ceiling=NEAR), 1100)
    assert down is not None
    lift.on_hold(conn, "module", down, 1100)
    assert lift.step(1150)["dir"] == "down"


def test_module_with_far_ceiling_can_go_up():
    lift = FakeLift()
    conn = module_conn(lift)
    hold = decode_hold(hold_text(ceiling=FAR), 1000)
    assert hold is not None
    lift.on_hold(conn, "module", hold, 1000)
    assert lift.step(1050)["dir"] == "up"


def test_module_without_ceiling_cannot_go_up():
    """ceiling が欠けた hold（距離計を持つ接続）で上昇を許さない。"""
    lift = FakeLift()
    conn = module_conn(lift)
    hold = decode_hold(hold_text(), 1000)  # ceiling 無し → MISSING
    assert hold is not None
    assert hold["has_ceiling"] is False
    lift.on_hold(conn, "module", hold, 1000)
    state = lift.step(1050)
    assert state["dir"] == "stop"
    assert state["reason"] == "CEILING_STALE"


def test_state_carries_speed_and_measured_speed():
    """`state` に目標の速さ・測った速さ・モータへの出力を載せる。"""
    lift = FakeLift()
    conn = module_conn(lift)
    hold = decode_hold(hold_text(speed=40, ceiling=FAR), 1000)
    assert hold is not None
    lift.on_hold(conn, "module", hold, 1000)
    state = lift.step(1050)
    assert state["dir"] == "up"
    assert state["speed"] == 40
    assert state["speed_meas"] == 40
    assert state["duty"] == 40


def test_height_moves_with_speed_times_elapsed():
    """偽の高さは `speed` [mm/s] × 経過時間で動く。"""
    lift = FakeLift()
    lift.height_mm = 800
    conn = module_conn(lift)
    lift.step(1000)
    lift.on_hold(conn, "module", decode_hold(hold_text(speed=40, ceiling=FAR), 1000), 1000)
    lift.on_hold(conn, "module", decode_hold(hold_text(speed=40, ceiling=FAR), 1900), 1900)
    state = lift.step(2000)  # 速さ 40 mm/s × 1 秒
    assert state["dir"] == "up"
    assert state["height_mm"] == 840


def test_stall_stops_with_encoder_stall_until_stop_or_reverse():
    """`stall` が真の間は動かそうとすると `ENCODER_STALL` で止まる。"""
    lift = FakeLift()
    lift.stall = True
    conn = module_conn(lift)
    now = 1000
    lift.step(now)
    lift.on_hold(conn, "module", decode_hold(hold_text(speed=40, ceiling=FAR), now), now)
    # 押し続け（100 ms ごと）で 600 ms 動かす。動き出しの遅れの間は動く
    for now in (1100, 1200, 1300, 1400, 1500, 1600):
        assert lift.step(now)["dir"] == "up"
        lift.on_hold(conn, "module", decode_hold(hold_text(speed=40, ceiling=FAR), now), now)
    stalled = lift.step(1700)
    assert stalled["dir"] == "stop"
    assert stalled["reason"] == "ENCODER_STALL"
    # 押し続けている間は続ける
    lift.on_hold(conn, "module", decode_hold(hold_text(speed=40, ceiling=FAR), 1700), 1700)
    assert lift.step(1800)["reason"] == "ENCODER_STALL"
    # 向きを替えれば反対方向へは動ける
    down = decode_hold(hold_text(press=2, direction="down", speed=40, ceiling=FAR), 1850)
    assert down is not None
    lift.on_hold(conn, "module", down, 1850)
    assert lift.step(1900)["dir"] == "down"


def test_height_unknown_is_shown_without_stopping():
    """`height_ok` が偽でも止めない（表示だけ。`W-1` の間は昇降部も止めない）。"""
    lift = FakeLift()
    lift.height_ok = False
    conn = module_conn(lift)
    lift.step(1000)
    hold = decode_hold(hold_text(speed=40, ceiling=FAR), 1000)
    assert hold is not None
    lift.on_hold(conn, "module", hold, 1000)
    state = lift.step(1050)
    assert state["dir"] == "up"
    assert state["reason"] == "HEIGHT_UNKNOWN"


def test_release_stops_at_once():
    """離したら CMD_STOP で止まる（CMD_TIMEOUT まで動かない）。"""
    lift = FakeLift()
    conn = lift.add_conn("ui")
    hold = decode_hold(hold_text(), 1000)
    assert hold is not None
    lift.on_hold(conn, "ui", hold, 1000)
    assert lift.step(1050)["dir"] == "up"
    lift.on_release(conn, 1)
    state = lift.step(1100)
    assert state["dir"] == "stop"
    assert state["reason"] == "CMD_STOP"


def test_timeout_stops_when_hold_stops_coming():
    """hold が途絶えたら CMD_TIMEOUT で止まる。"""
    lift = FakeLift()
    conn = lift.add_conn("ui")
    hold = decode_hold(hold_text(), 1000)
    assert hold is not None
    lift.on_hold(conn, "ui", hold, 1000)
    assert lift.step(1050)["dir"] == "up"
    state = lift.step(1000 + fake.LIFT_CMD_TIMEOUT_MS + 1)
    assert state["dir"] == "stop"
    assert state["reason"] == "CMD_TIMEOUT"


def test_stale_press_does_not_restart_after_a_stop():
    """止まったあと、同じ press の hold では動き出さない。"""
    lift = FakeLift()
    conn = lift.add_conn("ui")
    hold = decode_hold(hold_text(press=1), 1000)
    assert hold is not None
    lift.on_hold(conn, "ui", hold, 1000)
    assert lift.step(1050)["dir"] == "up"
    lift.on_release(conn, 1)
    assert lift.step(1100)["dir"] == "stop"
    again = decode_hold(hold_text(press=1), 1200)
    assert again is not None
    lift.on_hold(conn, "ui", again, 1200)
    assert lift.step(1250)["dir"] == "stop"


def test_close_of_the_owner_stops_at_once():
    """持ち主の接続が閉じたらその場で止まる（OWNER_GONE）。"""
    lift = FakeLift()
    conn = lift.add_conn("ui")
    hold = decode_hold(hold_text(), 1000)
    assert hold is not None
    lift.on_hold(conn, "ui", hold, 1000)
    assert lift.step(1050)["dir"] == "up"
    lift.drop_conn(conn)
    state = lift.step(1100)
    assert state["dir"] == "stop"
    assert state["reason"] == "OWNER_GONE"


def test_hello_on_ui_is_rejected():
    """/ws/ui で hello を受けたらその接続を閉じる（False を返す）。"""
    lift = FakeLift()
    conn = lift.add_conn("ui")
    assert lift.on_hello(conn, "ui", {"t": "hello"}) is False


def test_second_hello_does_not_remove_the_sensor():
    """2 度目の hello の ceiling_sensor: false は受け付けない。"""
    lift = FakeLift()
    conn = module_conn(lift)
    assert lift.on_hello(conn, "module", {"t": "hello", "ceiling_sensor": False}) is True
    assert lift.sensor_of(conn, "module") is True


# --- 設定 API の検証 ---


def test_settings_validation():
    assert validate_settings({"lift_up": {"min": 10, "max": 60, "init": 30},
                              "lift_down": {"min": 10, "max": 60, "init": 30}}) == []
    assert validate_settings({"lift_up": {"min": 70, "max": 60, "init": 30},
                              "lift_down": {"min": 10, "max": 60, "init": 30}}) != []


# --- HTTP・WS の端（aiohttp_client）---


async def test_settings_round_trip(aiohttp_client, tmp_path):
    app = fake.make_app(tmp_path, FakeLift())
    client = await aiohttp_client(app)
    body = await (await client.get("/api/settings")).json()
    assert body["settings"]["lift_up"]["init"] == 30
    bad = await client.put("/api/settings", json={"lift_up": {"min": 70, "max": 60, "init": 30},
                                                  "lift_down": {"min": 10, "max": 60, "init": 30}})
    assert bad.status == 400
    kept = await (await client.get("/api/settings")).json()
    assert kept["settings"]["lift_up"] == {"min": 10, "max": 60, "init": 30}


async def test_module_hold_over_websocket(aiohttp_client, tmp_path):
    """/ws/module の TOO_NEAR では上昇しない（端を通した変異の縛り）。"""
    app = fake.make_app(tmp_path, FakeLift())
    client = await aiohttp_client(app)
    ws = await client.ws_connect("/ws/module")
    await ws.send_str(json.dumps({"t": "hello", "ceiling_sensor": True}))
    await ws.send_str(json.dumps(hold_text(ceiling=NEAR)))
    for _ in range(10):
        msg = await ws.receive()
        state = json.loads(msg.data)
        if state.get("owner") == "module":
            break
    assert state["dir"] == "stop"
    assert state["reason"] == "CEILING_NEAR"
    await ws.close()


async def test_ui_hello_closes_the_connection(aiohttp_client, tmp_path):
    app = fake.make_app(tmp_path, FakeLift())
    client = await aiohttp_client(app)
    ws = await client.ws_connect("/ws/ui")
    await ws.send_str(json.dumps({"t": "hello"}))
    msg = await ws.receive()
    assert msg.type != WSMsgType.TEXT  # サーバーが接続を閉じる
    await ws.close()
