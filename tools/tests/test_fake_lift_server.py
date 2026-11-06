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


def good_settings() -> dict:
    return {"lift_up": {"min": 10, "max": 60, "init": 30},
            "lift_down": {"min": 10, "max": 60, "init": 30},
            "motor_max_pct": 50}


def test_settings_validation():
    assert validate_settings(good_settings()) == []
    bad_axis = good_settings()
    bad_axis["lift_up"] = {"min": 70, "max": 60, "init": 30}
    assert validate_settings(bad_axis) != []


def test_settings_validation_ceiling_standalone_is_optional_but_must_be_bool():
    assert validate_settings(good_settings()) == []
    for good in (True, False):
        body = good_settings()
        body["ceiling_standalone"] = good
        assert validate_settings(body) == []
    for bad in ("true", 1, 0, None, "yes"):
        body = good_settings()
        body["ceiling_standalone"] = bad
        assert validate_settings(body) != []


def test_settings_validation_rejects_missing_or_out_of_range_motor_max_pct():
    """`motor_max_pct` の欠けた PUT・9・101 は通さない（protocol §3）。"""
    missing = {"lift_up": {"min": 10, "max": 60, "init": 30},
               "lift_down": {"min": 10, "max": 60, "init": 30}}
    assert validate_settings(missing) != []
    for bad in (9, 101, "50", True, 50.0, None):
        body = good_settings()
        body["motor_max_pct"] = bad
        assert validate_settings(body) != []
    for good in (10, 50, 100):
        body = good_settings()
        body["motor_max_pct"] = good
        assert validate_settings(body) == []


# --- HTTP・WS の端（aiohttp_client）---


async def test_settings_round_trip(aiohttp_client, tmp_path):
    app = fake.make_app(tmp_path, FakeLift())
    client = await aiohttp_client(app)
    body = await (await client.get("/api/settings")).json()
    assert body["settings"]["lift_up"]["init"] == 30
    assert body["settings"]["motor_max_pct"] == 50
    bad = await client.put("/api/settings", json={"lift_up": {"min": 70, "max": 60, "init": 30},
                                                  "lift_down": {"min": 10, "max": 60, "init": 30},
                                                  "motor_max_pct": 50})
    assert bad.status == 400
    missing = await client.put("/api/settings", json={"lift_up": {"min": 10, "max": 60, "init": 30},
                                                      "lift_down": {"min": 10, "max": 60, "init": 30}})
    assert missing.status == 400
    kept = await (await client.get("/api/settings")).json()
    assert kept["settings"]["lift_up"] == {"min": 10, "max": 60, "init": 30}
    assert kept["settings"]["motor_max_pct"] == 50
    ok = await client.put("/api/settings", json=good_settings())
    assert ok.status == 200
    saved = await (await client.get("/api/settings")).json()
    assert saved["settings"]["motor_max_pct"] == 50


async def test_settings_ceiling_standalone_round_trip(aiohttp_client, tmp_path):
    """既定 true。false を保存したあと、欠けた PUT は今の値を保つ（true に戻さない）。"""
    app = fake.make_app(tmp_path, FakeLift())
    client = await aiohttp_client(app)
    body = await (await client.get("/api/settings")).json()
    assert body["settings"]["ceiling_standalone"] is True
    put = good_settings()
    put["ceiling_standalone"] = False
    assert (await client.put("/api/settings", json=put)).status == 200
    saved = await (await client.get("/api/settings")).json()
    assert saved["settings"]["ceiling_standalone"] is False
    assert (await client.put("/api/settings", json=good_settings())).status == 200  # 欠けた PUT
    kept = await (await client.get("/api/settings")).json()
    assert kept["settings"]["ceiling_standalone"] is False
    bad = good_settings()
    bad["ceiling_standalone"] = "no"
    assert (await client.put("/api/settings", json=bad)).status == 400
    kept = await (await client.get("/api/settings")).json()
    assert kept["settings"]["ceiling_standalone"] is False


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


def _hold_up(lift: FakeLift, conn: int, now: int, speed: int, press: int = 1) -> None:
    hold = decode_hold(hold_text(press=press, speed=speed, ceiling=FAR), now)
    assert hold is not None
    lift.on_hold(conn, "module", hold, now)


def _run(lift: FakeLift, conn: int, start: int, end: int, speed: int) -> str:
    reason = "NONE"
    for now in range(start, end + 1, 100):
        _hold_up(lift, conn, now, speed)
        reason = lift.step(now)["reason"]
    return reason


def test_max_run_at_30_mm_s_stops_just_after_90_s():
    """連続駆動の上限は全行程 1800 mm の 150 %（目標の速さで数える。30 mm/s なら 90 秒）。"""
    lift = FakeLift()
    conn = module_conn(lift)
    assert _run(lift, conn, 1000, 1000 + 60000, 30) == "NONE"  # 固定 10 秒なら止まっている
    assert _run(lift, conn, 1000 + 60100, 1000 + 90000, 30) == "NONE"  # ちょうど上限は動かせる
    assert _run(lift, conn, 1000 + 90100, 1000 + 90100, 30) == "MAX_RUN"


def test_max_run_sums_distance_when_target_speed_changes():
    """10 mm/s で 60 秒のあと 60 mm/s に上げても、その場では止まらない。"""
    lift = FakeLift()
    conn = module_conn(lift)
    assert _run(lift, conn, 1000, 1000 + 60000, 10) == "NONE"
    # 601 000 + 6000 × (n) が 2 700 000 を超える最初の 100 ms 刻みは 95 100 ms
    assert _run(lift, conn, 1000 + 60100, 1000 + 95000, 60) == "NONE"
    assert _run(lift, conn, 1000 + 95100, 1000 + 95100, 60) == "MAX_RUN"


def test_max_run_allows_the_opposite_direction():
    lift = FakeLift()
    conn = module_conn(lift)
    assert _run(lift, conn, 1000, 1000 + 90100, 30) == "MAX_RUN"
    down = decode_hold(hold_text(press=2, direction="down", speed=30, ceiling=FAR), 1000 + 90200)
    assert down is not None
    lift.on_hold(conn, "module", down, 1000 + 90200)
    assert lift.step(1000 + 90300)["dir"] == "down"


def test_provisional_names_max_run_pct():
    assert "LIFT_MAX_RUN_PCT" in fake.PROVISIONAL
    assert [n for n in fake.PROVISIONAL if "MAX_RUN" in n] == ["LIFT_MAX_RUN_PCT"]


# --- 複数の上部モジュール（protocol-modules §1〜§4。WP-LIFTUI-03）---


def hello_of(name=None, role=None, operator=None, sensor=None, busy=None) -> dict:
    body: dict = {"t": "hello"}
    for key, value in (("name", name), ("role", role), ("operator", operator),
                       ("ceiling_sensor", sensor), ("busy_label", busy)):
        if value is not None:
            body[key] = value
    return body


def named_conn(lift: FakeLift, **hello) -> int:
    conn = lift.add_conn("module", "192.168.5.9")
    assert lift.on_hello(conn, "module", hello_of(**hello))
    return conn


def go(lift: FakeLift, conn: int, now: int, press=1, direction="up", speed=40, ceiling=None):
    hold = decode_hold(hold_text(press=press, direction=direction, speed=speed, ceiling=ceiling), now)
    assert hold is not None
    lift.on_hold(conn, "module", hold, now)


def test_decode_hello_reads_the_new_fields():
    ok, info = fake.decode_hello(hello_of("fk", "control", "auto", False, "測定操作中"))
    assert ok
    assert (info["name"], info["role"], info["operator"], info["busy_label"]) == (
        "fk", "control", "auto", "測定操作中")
    assert info["has_sensor"] is False and info["explicit_sensor"] is False


def test_decode_hello_defaults_lean_to_the_safe_side():
    _ok, info = fake.decode_hello({"t": "hello"})
    assert (info["role"], info["operator"], info["has_sensor"]) == ("control", "human", True)
    assert info["explicit_sensor"] is False  # 名乗っていない天井は他の接続に貸さない
    _ok, info = fake.decode_hello(hello_of(role="admin", operator="robot", name="Bad Name"))
    assert info["role"] == "observe" and info["operator"] == "human" and info["name"] == ""


def test_busy_label_over_48_bytes_or_empty_is_dropped_not_truncated():
    assert fake.decode_hello(hello_of(busy="あ" * 16))[1]["busy_label"] == "あ" * 16  # 48 バイト
    assert fake.decode_hello(hello_of(busy="あ" * 17))[1]["busy_label"] is None
    assert fake.decode_hello(hello_of(busy=""))[1]["busy_label"] is None


def test_state_lists_all_modules_and_names_the_owner():
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", operator="human", sensor=True)
    fk = named_conn(lift, name="fk", operator="auto", sensor=False, busy="測定操作中")
    state = lift.step(1000)
    assert [m["name"] for m in state["modules"]] == ["hve-cam", "fk"]
    assert state["modules"][1]["busy_label"] == "測定操作中"
    assert state["modules"][1]["ip"] == "192.168.5.9"
    assert "module" not in state
    assert state["arm"] == {"attached": None, "link": None, "confirm": None,
                            "out": None, "drum": None}
    go(lift, cam, 1000, ceiling=FAR)
    state = lift.step(1050)
    assert (state["owner"], state["owner_name"], state["owner_operator"]) == (
        "module", "hve-cam", "human")
    go(lift, fk, 1100, ceiling=None)
    state = lift.step(1150)
    assert (state["owner_name"], state["owner_operator"]) == ("fk", "auto")


def test_same_name_replaces_the_old_connection_and_stops_the_owner():
    lift = FakeLift()
    old = named_conn(lift, name="hve-cam", sensor=True)
    go(lift, old, 1000, ceiling=FAR)
    assert lift.step(1050)["dir"] == "up"
    named_conn(lift, name="hve-cam", sensor=True)
    assert lift.replaced == [old]
    state = lift.step(1100)
    assert state["reason"] == "OWNER_GONE" and state["dir"] == "stop"
    assert [m["name"] for m in state["modules"]] == ["hve-cam"]


def test_anonymous_connections_are_not_replaced():
    lift = FakeLift()
    named_conn(lift)
    named_conn(lift)
    assert lift.replaced == []
    assert len(lift.step(1000)["modules"]) == 2


def test_second_hello_cannot_change_role_or_name():
    lift = FakeLift()
    conn = named_conn(lift, name="fk", role="control", sensor=False)
    lift.on_hello(conn, "module", hello_of(name="other", role="observe", sensor=True))
    state = lift.step(1000)
    assert state["modules"][0]["name"] == "fk" and state["modules"][0]["role"] == "control"


def test_observe_connection_cannot_command():
    lift = FakeLift()
    conn = named_conn(lift, name="viewer", role="observe", sensor=True)
    go(lift, conn, 1000, ceiling=FAR)
    assert lift.step(1050)["dir"] == "stop"
    lift.on_goto(conn, "module", fake.decode_goto(
        {"t": "goto", "press": 2, "target_mm": 1000, "speed": 40, "ceiling": FAR}, 1100), 1100)
    state = lift.step(1150)
    assert state["dir"] == "stop" and state["goto"] is None
    assert state["modules"][0]["role"] == "observe"  # 一覧には出る


# --- 距離計を持たない上部モジュールの天井（§2.3・§2.4）---


def test_module_without_sensor_cannot_go_up_when_no_source_is_connected():
    lift = FakeLift()
    fk = named_conn(lift, name="fk", operator="auto", sensor=False)
    go(lift, fk, 1000)
    state = lift.step(1050)
    assert state["dir"] == "stop" and state["reason"] == "CEILING_STALE"


def test_module_without_sensor_uses_the_sensor_modules_ceiling():
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", sensor=True)
    fk = named_conn(lift, name="fk", operator="auto", sensor=False)
    lift.on_ceiling(cam, dict(FAR), 1000)
    go(lift, fk, 1000)
    state = lift.step(1050)
    assert state["dir"] == "up"
    assert state["ceiling"]["from"] == "hve-cam" and state["ceiling"]["used"] is True
    lift.on_ceiling(cam, dict(NEAR), 1100)
    go(lift, fk, 1100)
    state = lift.step(1150)
    assert state["dir"] == "stop" and state["reason"] == "CEILING_NEAR"
    # 下降は天井で止めない
    go(lift, fk, 1200, press=2, direction="down")
    assert lift.step(1250)["dir"] == "down"


def test_module_without_sensor_stops_when_the_sensor_value_goes_stale():
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", sensor=True)
    fk = named_conn(lift, name="fk", sensor=False)
    lift.on_ceiling(cam, dict(FAR), 1000)
    go(lift, fk, 1000)
    assert lift.step(1050)["dir"] == "up"
    go(lift, fk, 1000 + fake.CEILING_STALE_MS + 100)
    state = lift.step(1000 + fake.CEILING_STALE_MS + 150)
    assert state["reason"] == "CEILING_STALE" and state["dir"] == "stop"


def test_read_error_from_the_source_stops_the_other_module():
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", sensor=True)
    fk = named_conn(lift, name="fk", sensor=False)
    lift.on_ceiling(cam, {"status": "READ_ERROR", "age_ms": 10}, 1000)
    go(lift, fk, 1000)
    assert lift.step(1050)["reason"] == "CEILING_STALE"


def test_unnamed_sensor_claim_is_not_a_ceiling_source():
    """`hello` を送らない・`ceiling_sensor` を明示しない・observe の接続の値は貸さない。"""
    lift = FakeLift()
    fk = named_conn(lift, name="fk", sensor=False)
    silent = lift.add_conn("module")  # hello 無し
    implicit = named_conn(lift, name="implicit")  # ceiling_sensor が欠けている
    viewer = named_conn(lift, name="viewer", role="observe", sensor=True)
    for conn in (silent, implicit, viewer):
        lift.on_ceiling(conn, dict(FAR), 1000)
    go(lift, fk, 1000)
    assert lift.step(1050)["reason"] == "CEILING_STALE"


def test_ceiling_from_a_module_that_says_no_sensor_is_discarded():
    lift = FakeLift()
    liar = named_conn(lift, name="liar", sensor=False)
    fk = named_conn(lift, name="fk", sensor=False)
    lift.on_ceiling(liar, dict(FAR), 1000)
    go(lift, fk, 1000)
    assert lift.step(1050)["reason"] == "CEILING_STALE"


def test_ceiling_carried_by_hold_also_updates_the_source():
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", sensor=True)
    fk = named_conn(lift, name="fk", sensor=False)
    go(lift, cam, 1000, ceiling=FAR)
    lift.on_release(cam, 1)
    go(lift, fk, 1050)
    assert lift.step(1100)["dir"] == "up"


def test_nearest_of_two_sources_is_used():
    lift = FakeLift()
    a = named_conn(lift, name="cam-a", sensor=True)
    b = named_conn(lift, name="cam-b", sensor=True)
    fk = named_conn(lift, name="fk", sensor=False)
    lift.on_ceiling(a, dict(FAR), 1000)
    lift.on_ceiling(b, dict(NEAR), 1000)
    go(lift, fk, 1000)
    assert lift.step(1050)["reason"] == "CEILING_NEAR"


# --- 単体の操作の天井（protocol-modules §2.5。WP-LIFTUI-04）---


def ui_up(lift: FakeLift, now: int, kind="ui") -> None:
    conn = lift.add_conn(kind)
    lift.on_hold(conn, kind, decode_hold(hold_text(), now), now)


@pytest.mark.parametrize("kind", ["ui", "button"])
def test_standalone_stops_at_a_fresh_near_ceiling(kind):
    """設定が入で、距離計を持つ上部モジュールの新しい値が近いなら、画面・物理ボタンの上昇も止める。"""
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", sensor=True)
    lift.on_ceiling(cam, dict(NEAR), 1000)
    ui_up(lift, 1000, kind)
    state = lift.step(1050)
    assert (state["dir"], state["reason"]) == ("stop", "CEILING_NEAR")
    assert state["ceiling"]["standalone"] == "USE" and state["ceiling"]["used"] is True
    assert state["ceiling"]["from"] == "hve-cam"


def test_standalone_setting_off_does_not_use_the_ceiling():
    lift = FakeLift()
    lift.settings["ceiling_standalone"] = False
    cam = named_conn(lift, name="hve-cam", sensor=True)
    lift.on_ceiling(cam, dict(NEAR), 1000)
    ui_up(lift, 1000)
    state = lift.step(1050)
    assert state["dir"] == "up" and state["reason"] == "NONE"
    assert state["ceiling"]["standalone"] == "SETTING_OFF"


def test_standalone_no_source_allows_without_stale_stop():
    """出どころが無い・全部古いときは天井を使わず許す（CEILING_STALE にしない）。"""
    lift = FakeLift()
    ui_up(lift, 1000)
    state = lift.step(1050)
    assert (state["dir"], state["reason"]) == ("up", "NONE")
    assert state["ceiling"]["standalone"] == "NO_SOURCE"
    # 値はあるが古い
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", sensor=True)
    lift.on_ceiling(cam, dict(NEAR), 1000)
    ui_up(lift, 1000 + 2000)
    state = lift.step(1000 + 2050)
    assert (state["dir"], state["reason"]) == ("up", "NONE")
    assert state["ceiling"]["standalone"] == "NO_SOURCE"


def test_standalone_ignores_a_stale_source_when_another_is_fresh():
    """古い出どころは無視し、新しい値の中で判定する（どちらの向きの取り違えも赤）。"""
    lift = FakeLift()
    a = named_conn(lift, name="cam-a", sensor=True)
    b = named_conn(lift, name="cam-b", sensor=True)
    lift.on_ceiling(a, dict(NEAR), 1000)  # 古くなる
    lift.on_ceiling(b, dict(FAR), 3000)   # 新しい
    ui_up(lift, 3000)
    state = lift.step(3050)
    assert (state["dir"], state["reason"]) == ("up", "NONE")
    assert state["ceiling"]["standalone"] == "USE" and state["ceiling"]["from"] == "cam-b"

    lift = FakeLift()
    a = named_conn(lift, name="cam-a", sensor=True)
    b = named_conn(lift, name="cam-b", sensor=True)
    lift.on_ceiling(a, dict(FAR), 1000)   # 古くなる
    lift.on_ceiling(b, dict(NEAR), 3000)  # 新しい
    ui_up(lift, 3000)
    state = lift.step(3050)
    assert (state["dir"], state["reason"]) == ("stop", "CEILING_NEAR")
    assert state["ceiling"]["from"] == "cam-b"


def test_standalone_uses_the_nearer_of_fresh_sources():
    lift = FakeLift()
    a = named_conn(lift, name="cam-a", sensor=True)
    b = named_conn(lift, name="cam-b", sensor=True)
    lift.on_ceiling(a, {"status": "MEASURED", "mm": 2000, "age_ms": 50}, 1000)
    lift.on_ceiling(b, {"status": "MEASURED", "mm": 300, "age_ms": 50}, 1000)
    ui_up(lift, 1000)
    state = lift.step(1050)
    assert (state["dir"], state["reason"]) == ("stop", "CEILING_NEAR")
    assert state["ceiling"]["from"] == "cam-b"


def test_standalone_read_error_stops_with_ceiling_stale():
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", sensor=True)
    lift.on_ceiling(cam, {"status": "READ_ERROR", "age_ms": 50}, 1000)
    ui_up(lift, 1000)
    state = lift.step(1050)
    assert (state["dir"], state["reason"]) == ("stop", "CEILING_STALE")
    assert state["ceiling"]["standalone"] == "USE"


def test_standalone_is_reported_without_an_owner_and_does_not_affect_modules():
    lift = FakeLift()
    assert lift.step(1000)["ceiling"]["standalone"] == "NO_SOURCE"
    lift.settings["ceiling_standalone"] = False
    assert lift.step(1010)["ceiling"]["standalone"] == "SETTING_OFF"
    # 上部モジュールからの命令には設定が効かない（いつも天井を使う）
    cam = named_conn(lift, name="hve-cam", sensor=True)
    lift.on_ceiling(cam, dict(NEAR), 1100)
    fk = named_conn(lift, name="fk", sensor=False)
    go(lift, fk, 1100)
    assert lift.step(1150)["reason"] == "CEILING_NEAR"


# --- goto（§2.2）---


def goto_msg(press=1, target=900, speed=40, ceiling=None):
    body = {"t": "goto", "press": press, "target_mm": target, "speed": speed}
    if ceiling is not None:
        body["ceiling"] = ceiling
    return body


def send_goto(lift, conn, now, **kw):
    goto = fake.decode_goto(goto_msg(**kw), now)
    assert goto is not None
    lift.on_goto(conn, "module", goto, now)


def test_goto_moves_to_the_target_and_arrives_without_restarting():
    lift = FakeLift()
    lift.height_mm = 800
    ss = named_conn(lift, name="ss", operator="auto", sensor=True)
    lift.step(1000)
    now = 1000
    send_goto(lift, ss, now, target=830, ceiling=FAR)
    state = lift.step(now + 50)
    assert state["dir"] == "up" and state["goto"]["status"] == "MOVING"
    assert state["goto"]["by"] == "ss" and state["goto"]["target_mm"] == 830
    for _ in range(40):
        now += 100
        send_goto(lift, ss, now, target=830, ceiling=FAR)
        state = lift.step(now)
        if state["goto"]["status"] == "ARRIVED":
            break
    assert state["goto"]["status"] == "ARRIVED" and state["goto"]["reason"] == "NONE"
    assert state["dir"] == "stop" and state["reason"] == "ARRIVED"
    assert abs(state["height_mm"] - 830) <= fake.LIFT_GOTO_TOL_MM
    # 同じ press の goto では動き出さない。新しい press なら動く
    send_goto(lift, ss, now + 100, target=1200, ceiling=FAR)
    assert lift.step(now + 150)["dir"] == "stop"
    send_goto(lift, ss, now + 200, press=2, target=1200, ceiling=FAR)
    assert lift.step(now + 250)["dir"] == "up"


def test_goto_goes_down_to_a_lower_target():
    lift = FakeLift()
    lift.height_mm = 800
    ss = named_conn(lift, name="ss", sensor=True)
    send_goto(lift, ss, 1000, target=500, ceiling=FAR)
    assert lift.step(1050)["dir"] == "down"


def test_goto_refuses_unknown_height_and_out_of_range_targets():
    lift = FakeLift()
    ss = named_conn(lift, name="ss", sensor=True)
    send_goto(lift, ss, 1000, target=-5, ceiling=FAR)
    state = lift.step(1050)
    assert state["dir"] == "stop" and state["goto"]["status"] == "NOT_ARRIVED"
    assert state["goto"]["reason"] == "TARGET_RANGE"
    send_goto(lift, ss, 1100, press=2, target=fake.LIFT_TOP_MM + 1, ceiling=FAR)
    assert lift.step(1150)["goto"]["reason"] == "TARGET_RANGE"
    lift.height_ok = False
    send_goto(lift, ss, 1200, press=3, target=900, ceiling=FAR)
    state = lift.step(1250)
    assert state["dir"] == "stop" and state["goto"]["reason"] == "HEIGHT_UNKNOWN"


def test_goto_up_obeys_the_ceiling_like_hold():
    lift = FakeLift()
    lift.height_mm = 800
    ss = named_conn(lift, name="ss", sensor=True)
    send_goto(lift, ss, 1000, target=1200, ceiling=NEAR)
    state = lift.step(1050)
    assert state["dir"] == "stop" and state["reason"] == "CEILING_NEAR"
    assert state["goto"]["status"] == "NOT_ARRIVED" and state["goto"]["reason"] == "CEILING_NEAR"


def test_goto_of_a_module_without_sensor_uses_the_source():
    lift = FakeLift()
    cam = named_conn(lift, name="hve-cam", sensor=True)
    ss = named_conn(lift, name="ss", sensor=False)
    send_goto(lift, ss, 1000, target=1200)
    assert lift.step(1050)["reason"] == "CEILING_STALE"
    lift.on_ceiling(cam, dict(FAR), 1100)
    send_goto(lift, ss, 1100, target=1200)
    assert lift.step(1150)["dir"] == "up"


def test_goto_is_preempted_by_a_newer_press_from_someone_else():
    lift = FakeLift()
    ss = named_conn(lift, name="ss", sensor=True)
    cam = named_conn(lift, name="hve-cam", sensor=True)
    send_goto(lift, ss, 1000, target=1500, ceiling=FAR)
    assert lift.step(1050)["goto"]["status"] == "MOVING"
    go(lift, cam, 1100, ceiling=FAR, direction="down")
    state = lift.step(1150)
    assert state["goto"]["status"] == "NOT_ARRIVED" and state["goto"]["reason"] == "PREEMPTED"
    assert state["owner_name"] == "hve-cam"


def test_goto_release_and_timeout_report_not_arrived():
    lift = FakeLift()
    ss = named_conn(lift, name="ss", sensor=True)
    send_goto(lift, ss, 1000, target=1500, ceiling=FAR)
    lift.step(1050)
    lift.on_release(ss, 1)
    state = lift.step(1100)
    assert state["goto"]["status"] == "NOT_ARRIVED" and state["goto"]["reason"] == "CMD_STOP"
    send_goto(lift, ss, 1200, press=2, target=1500, ceiling=FAR)
    lift.step(1250)
    state = lift.step(1200 + fake.LIFT_CMD_TIMEOUT_MS + 10)
    assert state["goto"]["reason"] == "CMD_TIMEOUT"


def test_goto_with_broken_ceiling_is_kept_and_stops_as_missing():
    lift = FakeLift()
    ss = named_conn(lift, name="ss", sensor=True)
    goto = fake.decode_goto(goto_msg(ceiling={"status": "MEASURED", "age_ms": 5}), 1000)
    assert goto is not None and goto["has_ceiling"] is False
    lift.on_goto(ss, "module", goto, 1000)
    assert lift.step(1050)["reason"] == "CEILING_STALE"


def test_decode_goto_drops_unreadable_messages():
    assert fake.decode_goto({"t": "goto", "press": 1, "speed": 40}, 0) is None
    assert fake.decode_goto({"t": "goto", "press": 1, "target_mm": "x", "speed": 40}, 0) is None
    assert fake.decode_goto({"t": "goto", "press": True, "target_mm": 1, "speed": 40}, 0) is None


# --- WS の端 ---


async def recv_state(ws, pred, tries=30):
    for _ in range(tries):
        msg = await ws.receive()
        if msg.type != WSMsgType.TEXT:
            return None
        state = json.loads(msg.data)
        if pred(state):
            return state
    raise AssertionError("条件に合う state が来ない")


async def test_ws_ceiling_message_lets_a_sensorless_module_go_up(aiohttp_client, tmp_path):
    client = await aiohttp_client(fake.make_app(tmp_path, FakeLift()))
    cam = await client.ws_connect("/ws/module")
    fk = await client.ws_connect("/ws/module")
    await cam.send_str(json.dumps(hello_of("hve-cam", "control", "human", True)))
    await fk.send_str(json.dumps(hello_of("fk", "control", "auto", False, "測定操作中")))
    await fk.send_str(json.dumps(hold_text(ceiling=None)))
    state = await recv_state(fk, lambda s: s.get("owner_name") == "fk")
    assert state["reason"] == "CEILING_STALE"
    await cam.send_str(json.dumps({"t": "ceiling", **FAR}))
    await fk.send_str(json.dumps(hold_text(press=2, ceiling=None)))
    state = await recv_state(fk, lambda s: s.get("dir") == "up")
    assert state["owner_name"] == "fk" and state["ceiling"]["from"] == "hve-cam"
    assert [m["name"] for m in state["modules"]] == ["hve-cam", "fk"]
    await cam.close()
    await fk.close()


async def test_ws_same_name_closes_the_old_connection(aiohttp_client, tmp_path):
    client = await aiohttp_client(fake.make_app(tmp_path, FakeLift()))
    old = await client.ws_connect("/ws/module")
    await old.send_str(json.dumps(hello_of("fk", sensor=False)))
    await recv_state(old, lambda s: len(s["modules"]) == 1)
    new = await client.ws_connect("/ws/module")
    await new.send_str(json.dumps(hello_of("fk", sensor=False)))
    for _ in range(30):
        msg = await old.receive()
        if msg.type != WSMsgType.TEXT:
            break
    else:
        raise AssertionError("古い接続が閉じない")
    state = await recv_state(new, lambda s: len(s["modules"]) == 1)
    assert state["modules"][0]["name"] == "fk"
    await new.close()


async def test_ws_over_the_connection_limit_is_closed_without_closing_the_old(aiohttp_client, tmp_path):
    client = await aiohttp_client(fake.make_app(tmp_path, FakeLift()))
    first = await client.ws_connect("/ws/module")
    others = [await client.ws_connect("/ws/module") for _ in range(fake.LIFT_MODULE_MAX_CONN - 1)]
    extra = await client.ws_connect("/ws/module")
    msg = await extra.receive()
    assert msg.type != WSMsgType.TEXT  # 新しい接続が閉じられる
    state = await recv_state(first, lambda s: len(s["modules"]) == fake.LIFT_MODULE_MAX_CONN)
    assert state is not None
    for ws in [first, *others]:
        await ws.close()


async def test_ws_goto_over_websocket(aiohttp_client, tmp_path):
    lift = FakeLift()
    lift.height_mm = 800
    client = await aiohttp_client(fake.make_app(tmp_path, lift))
    ws = await client.ws_connect("/ws/module")
    await ws.send_str(json.dumps(hello_of("ss", "control", "auto", True, "画像認識で操作中")))
    await ws.send_str(json.dumps(goto_msg(target=1200, ceiling=FAR)))
    state = await recv_state(ws, lambda s: s.get("goto") is not None)
    assert state["goto"]["by"] == "ss" and state["dir"] == "up"
    assert state["modules"][0]["busy_label"] == "画像認識で操作中"
    await ws.close()
