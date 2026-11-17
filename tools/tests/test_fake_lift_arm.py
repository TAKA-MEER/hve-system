"""偽の昇降部の測定機構（`role: arm`・`arm`・`arm_confirm`・#4e・再起動）の試験。

`WP-LIFT-18` の判定と同じ規則（docs/plan/detailed/DetailedDesign-protocol-arm.md §2〜§8）を
`FakeLift` を通して縛る。次の変異は赤になる:

- #4e で下降を止めない（上昇だけ止める）
- `/ws/module` から来た `arm_confirm` で解く
- 番号が違う `arm_confirm` で解く
- 置き換えを通信できないとみなす（古い接続が閉じたら止める）
"""

from __future__ import annotations

import json
import pathlib
import sys

from aiohttp import WSMsgType

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fake_lift_server as fake
from fake_lift_server import FakeLift, decode_hold

TIMEOUT = fake.LIFT_ARM_LINK_TIMEOUT_MS


def arm_conn(lift: FakeLift, name="fk-arm", **extra) -> int:
    conn = lift.add_conn("module", "192.168.5.20")
    body = {"t": "hello", "name": name, "role": "arm", "operator": "auto",
            "ceiling_sensor": False, **extra}
    assert lift.on_hello(conn, "module", body)
    return conn


def send_arm(lift: FakeLift, conn: int, now: int, out="__missing__", kind="module") -> None:
    body = {"t": "arm"} if out == "__missing__" else {"t": "arm", "out": out}
    lift.on_arm(conn, kind, body, now)


def hold(lift: FakeLift, conn: int, kind: str, now: int, direction="down", press=1) -> None:
    parsed = decode_hold({"t": "hold", "press": press, "dir": direction, "speed": 40,
                          "ceiling": {"status": "MEASURED", "mm": 2000, "age_ms": 50}}, now)
    assert parsed is not None
    lift.on_hold(conn, kind, parsed, now)


def stopped_by_link(lift: FakeLift, now: int) -> dict:
    state = lift.step(now)
    assert state["reason"] == "ARM_LINK" and state["dir"] == "stop", state
    return state


# --- state.arm の形（§7 の表）---


def test_before_any_arm_everything_is_unknown_and_nothing_stops():
    lift = FakeLift()
    arm_conn(lift)
    state = lift.step(1000)
    assert state["arm"] == {"attached": None, "link": None, "confirm": None, "out": None}
    assert state["reason"] != "ARM_LINK"


def test_arm_out_true_false_and_null_are_reported_as_given():
    lift = FakeLift()
    conn = arm_conn(lift)
    for out, want in ((True, True), (False, False), (None, True)):  # null は true に倒す
        send_arm(lift, conn, 1000, out)
        assert lift.step(1050)["arm"] == {"attached": True, "link": True, "confirm": None,
                                          "out": want}
    send_arm(lift, conn, 1100)  # out が欠けた arm は null として受ける（捨てない）
    assert lift.step(1150)["arm"]["out"] is True
    assert lift.arm_out_latched is True
    send_arm(lift, conn, 1200, "yes")  # 型違いも null
    assert lift.step(1250)["arm"]["out"] is True


def test_arm_from_a_non_arm_connection_is_dropped():
    lift = FakeLift()
    cam = lift.add_conn("module")
    lift.on_hello(cam, "module", {"t": "hello", "name": "hve-cam", "ceiling_sensor": True})
    ui = lift.add_conn("ui")
    send_arm(lift, cam, 1000, True)
    send_arm(lift, ui, 1000, True, kind="ui")
    assert lift.step(1050)["arm"]["attached"] is None
    assert lift.arm_out_latched is False


def test_arm_role_is_automatic_and_never_a_ceiling_source():
    lift = FakeLift()
    conn = arm_conn(lift, operator="human", ceiling_sensor=True)
    module = lift.step(1000)["modules"][0]
    assert (module["role"], module["operator"], module["ceiling_sensor"]) == ("arm", "auto", False)
    lift.on_ceiling(conn, {"status": "MEASURED", "mm": 2000, "age_ms": 0}, 1000)
    other = lift.add_conn("module")
    lift.on_hello(other, "module", {"t": "hello", "name": "ss", "ceiling_sensor": False})
    hold(lift, other, "module", 1010, direction="up")
    assert lift.step(1050)["reason"] == "CEILING_STALE"  # アームの値は天井の出どころにならない


def test_arm_owner_is_named_arm_with_the_connection_name():
    lift = FakeLift()
    conn = arm_conn(lift)
    hold(lift, conn, "module", 1000, direction="down")
    state = lift.step(1050)
    assert (state["owner"], state["owner_name"], state["owner_operator"]) == (
        "arm", "fk-arm", "auto")
    assert state["dir"] == "down"


# --- #4e ---


def test_link_lost_while_the_arm_is_out_stops_even_a_descent():
    lift = FakeLift()
    conn = arm_conn(lift)
    send_arm(lift, conn, 1000, True)
    ui = lift.add_conn("ui")
    hold(lift, ui, "ui", 1500, direction="down")
    assert lift.step(1500)["dir"] == "down"  # 通信できているあいだは動く
    hold(lift, ui, "ui", 1950, direction="down")  # 持ち主は命令を送り続けている
    state = stopped_by_link(lift, 1000 + TIMEOUT + 1)
    assert state["arm"] == {"attached": True, "link": False, "confirm": 1, "out": None}
    hold(lift, ui, "ui", 2100, direction="up", press=2)
    stopped_by_link(lift, 2110)  # 上昇も止まる


def test_idle_state_also_reports_arm_link_as_the_stop_reason():
    lift = FakeLift()
    conn = arm_conn(lift)
    send_arm(lift, conn, 1000, None)  # 分からないも「出ている」と同じ
    stopped_by_link(lift, 1000 + TIMEOUT + 1)


def test_link_lost_while_the_arm_is_stowed_does_not_stop():
    lift = FakeLift()
    conn = arm_conn(lift)
    send_arm(lift, conn, 1000, False)
    ui = lift.add_conn("ui")
    hold(lift, ui, "ui", 2500, direction="down")
    state = lift.step(2500)
    assert state["dir"] == "down" and state["reason"] != "ARM_LINK"
    assert state["arm"] == {"attached": True, "link": False, "confirm": None, "out": None}


def test_closing_the_arm_connection_loses_the_link_at_once():
    lift = FakeLift()
    conn = arm_conn(lift)
    send_arm(lift, conn, 1000, True)
    lift.drop_conn(conn)
    stopped_by_link(lift, 1010)


def test_stop_number_goes_up_every_time_the_stop_starts():
    lift = FakeLift()
    conn = arm_conn(lift)
    send_arm(lift, conn, 1000, True)
    assert stopped_by_link(lift, 3000)["arm"]["confirm"] == 1
    assert lift.step(3100)["arm"]["confirm"] == 1  # 止めている間は増えない
    send_arm(lift, conn, 3200, True)  # 戻る
    assert lift.step(3250)["arm"]["confirm"] is None
    assert stopped_by_link(lift, 3250 + TIMEOUT + 1)["arm"]["confirm"] == 2


def test_the_link_coming_back_clears_the_stop_without_an_answer():
    lift = FakeLift()
    conn = arm_conn(lift)
    send_arm(lift, conn, 1000, True)
    stopped_by_link(lift, 3000)
    send_arm(lift, conn, 3100, True)
    state = lift.step(3150)
    assert state["reason"] != "ARM_LINK"
    assert state["arm"] == {"attached": True, "link": True, "confirm": None, "out": True}


# --- 確認ダイアログ ---


def stopped_lift():
    lift = FakeLift()
    conn = arm_conn(lift)
    send_arm(lift, conn, 1000, True)
    stopped_by_link(lift, 3000)
    return lift, conn


def test_arm_confirm_from_the_ui_with_the_right_number_clears_the_stop():
    lift, _ = stopped_lift()
    ui = lift.add_conn("ui")
    lift.on_arm_confirm(ui, "ui", {"t": "arm_confirm", "stop": 1})
    state = lift.step(3100)
    assert state["reason"] != "ARM_LINK"
    assert state["arm"] == {"attached": None, "link": None, "confirm": None, "out": None}
    assert lift.arm_out_latched is False
    hold(lift, ui, "ui", 3150, direction="down")
    assert lift.step(3200)["dir"] == "down"  # 新しく押した操作で動ける


def test_arm_confirm_with_a_wrong_or_missing_number_is_dropped():
    lift, _ = stopped_lift()
    ui = lift.add_conn("ui")
    for body in ({"t": "arm_confirm", "stop": 2}, {"t": "arm_confirm", "stop": 0},
                 {"t": "arm_confirm"}, {"t": "arm_confirm", "stop": "1"},
                 {"t": "arm_confirm", "stop": True}):
        lift.on_arm_confirm(ui, "ui", body)
        assert lift.step(3100)["reason"] == "ARM_LINK", body


def test_arm_confirm_from_ws_module_is_dropped():
    lift, conn = stopped_lift()
    cam = lift.add_conn("module")
    lift.on_hello(cam, "module", {"t": "hello", "name": "hve-cam"})
    for who in (cam, conn):
        lift.on_arm_confirm(who, "module", {"t": "arm_confirm", "stop": 1})
    assert lift.step(3100)["reason"] == "ARM_LINK"


def test_a_late_confirm_for_an_old_number_does_not_clear_a_new_stop():
    lift, conn = stopped_lift()
    ui = lift.add_conn("ui")
    send_arm(lift, conn, 3100, True)
    lift.step(3150)
    stopped_by_link(lift, 3150 + TIMEOUT + 1)  # 2 度目（番号 2）
    lift.on_arm_confirm(ui, "ui", {"t": "arm_confirm", "stop": 1})
    assert lift.step(5000)["reason"] == "ARM_LINK"


# --- 再起動をまたいで覚える（H-X15）---


def test_a_remembered_out_arm_stops_after_a_restart_until_arm_comes_back():
    lift, _ = stopped_lift()
    lift.restart()
    ui = lift.add_conn("ui")
    hold(lift, ui, "ui", 100, direction="down")  # 原点の前の下降でも止まる
    state = stopped_by_link(lift, 150)
    assert state["arm"] == {"attached": None, "link": False, "confirm": 1, "out": None}
    conn = arm_conn(lift)
    send_arm(lift, conn, 200, False)  # 格納位置と知らされたら解け、覚えも下りる
    assert lift.step(250)["reason"] != "ARM_LINK"
    assert lift.arm_out_latched is False
    lift.restart()
    hold(lift, ui, "ui", 300, direction="down")
    assert lift.step(350)["reason"] != "ARM_LINK"  # 次の再起動では止まらない


def test_a_restart_with_a_stowed_arm_does_not_stop():
    lift = FakeLift()
    conn = arm_conn(lift)
    send_arm(lift, conn, 1000, False)
    lift.restart()
    assert lift.step(1100)["arm"]["confirm"] is None
    assert lift.step(1100)["reason"] != "ARM_LINK"


def test_answering_after_a_restart_clears_the_remembered_bit():
    lift, _ = stopped_lift()
    lift.restart()
    stopped_by_link(lift, 100)
    ui = lift.add_conn("ui")
    lift.on_arm_confirm(ui, "ui", {"t": "arm_confirm", "stop": 1})
    assert lift.step(150)["reason"] != "ARM_LINK"
    assert lift.arm_out_latched is False


# --- 1 本だけ ---


def test_a_new_arm_connection_replaces_the_old_one_and_keeps_the_last_out():
    lift = FakeLift()
    old = arm_conn(lift, name="fk-arm")
    send_arm(lift, old, 1000, True)
    new = arm_conn(lift, name="fk-arm2")  # 名前が違っても古い方を閉じる
    assert lift.replaced == [old]
    state = lift.step(1100)  # 置き換えは通信できないとみなさない
    assert state["reason"] != "ARM_LINK"
    assert state["arm"] == {"attached": True, "link": True, "confirm": None, "out": True}
    assert [m["name"] for m in state["modules"]] == ["fk-arm2"]
    stopped_by_link(lift, 1000 + TIMEOUT + 1)  # 新しい接続が arm を送らなければ引き継いだ時刻から数える
    send_arm(lift, new, 3000, True)
    assert lift.step(3050)["arm"]["link"] is True


def test_the_old_arm_connection_that_owned_the_lift_stops_it_when_replaced():
    lift = FakeLift()
    old = arm_conn(lift)
    send_arm(lift, old, 1000, False)
    hold(lift, old, "module", 1000, direction="down")
    assert lift.step(1050)["dir"] == "down"
    arm_conn(lift, name="fk-arm2")
    assert lift.step(1100)["reason"] == "OWNER_GONE"


# --- POST /api/fake と WS ---


async def recv_state(ws, pred, tries=60):
    for _ in range(tries):
        msg = await ws.receive()
        if msg.type != WSMsgType.TEXT:
            return None
        state = json.loads(msg.data)
        if pred(state):
            return state
    raise AssertionError("条件に合う state が来ない")


async def test_ws_arm_stop_confirm_and_restart(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(fake, "LIFT_ARM_LINK_TIMEOUT_MS", 250)
    client = await aiohttp_client(fake.make_app(tmp_path, FakeLift()))
    arm = await client.ws_connect("/ws/module")
    ui = await client.ws_connect("/ws/ui")
    await arm.send_str(json.dumps({"t": "hello", "name": "fk-arm", "role": "arm"}))
    await arm.send_str(json.dumps({"t": "arm", "out": True}))
    await recv_state(ui, lambda s: s["arm"]["link"] is True and s["arm"]["out"] is True)
    # arm を送らなければ #4e。/ws/module の arm_confirm では解けず、/ws/ui の答えで解ける
    state = await recv_state(ui, lambda s: s["reason"] == "ARM_LINK")
    number = state["arm"]["confirm"]
    assert isinstance(number, int)
    await arm.send_str(json.dumps({"t": "arm_confirm", "stop": number}))
    await ui.send_str(json.dumps({"t": "arm_confirm", "stop": number + 1}))
    for _ in range(5):
        assert (await recv_state(ui, lambda s: True))["reason"] == "ARM_LINK"
    await ui.send_str(json.dumps({"t": "arm_confirm", "stop": number}))
    await recv_state(ui, lambda s: s["reason"] != "ARM_LINK" and s["arm"]["confirm"] is None)
    await arm.close()
    await ui.close()


async def test_post_fake_restart_closes_connections_and_keeps_the_bit(aiohttp_client, tmp_path):
    lift = FakeLift()
    client = await aiohttp_client(fake.make_app(tmp_path, lift))
    arm = await client.ws_connect("/ws/module")
    await arm.send_str(json.dumps({"t": "hello", "name": "fk-arm", "role": "arm"}))
    await arm.send_str(json.dumps({"t": "arm", "out": True}))
    for _ in range(50):
        if lift.arm_out_latched:
            break
        await arm.receive()
    assert lift.arm_out_latched
    response = await client.post("/api/fake", json={"restart": True})
    assert response.status == 200
    msg = await arm.receive()
    while msg.type == WSMsgType.TEXT:
        msg = await arm.receive()
    assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
    assert lift.arm_out_latched
    assert lift.step(fake.now_ms())["reason"] == "ARM_LINK"


async def test_post_fake_arm_overrides_the_fields_and_null_clears_it(aiohttp_client, tmp_path):
    lift = FakeLift()
    client = await aiohttp_client(fake.make_app(tmp_path, lift))
    await client.post("/api/fake", json={"arm": {"link": False, "confirm": 3}})
    assert lift.step(1000)["arm"] == {"attached": None, "link": False, "confirm": 3, "out": None}
    await client.post("/api/fake", json={"arm": None})
    assert lift.step(1100)["arm"] == {"attached": None, "link": None, "confirm": None, "out": None}
