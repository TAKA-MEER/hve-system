"""ss-system の口（`/ws/module`・`aim`・`state`・`GET /api/snapshot`）の試験（`WP-CAM-14`）。

[DetailedDesign-protocol-ss.md](../../docs/plan/detailed/DetailedDesign-protocol-ss.md) を **本物の経路**
（aiohttp の WS クライアント → `app.py` → `ModulePort` → `ControlLoop` → 偽の UNO）で確かめる。
`fcntl` に頼らないので Windows でも回る。時計は `ManualClock`、制御ループは `rig.pump()` で 1 周期ずつ進める。

偽の UNO（`FakeHardware`）はヨーの速さに合わせて `Y` 行の累計を進めるので、
**`aim` のヨーが目標に着いて `ARRIVED` になるまで**を通しで確かめられる。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hve_camera.aim import YAW_SIGN, decode_aim, decode_hello, shortest_delta_deg, wrap_deg, yaw_deg_from_hs
from hve_camera.hw.base import POWER_ACTIVE
from hve_camera.hw.uno_hw import UnoHardware
from hve_camera.power import PowerState
from hve_camera.uno_link import parse_io_line
from tests.cam_support import wait_until
from tests.test_app import Rig
from tests.test_uno_hw import PARAMS as UNO_PARAMS
from tests.test_uno_hw import FakeSerial, ManualClock

HELLO = {
    "t": "hello",
    "name": "ss",
    "role": "control",
    "operator": "auto",
    "busy_label": "画像認識で操作中",
    "fw": "0.1.0",
}
#: 半ステップ → 度（試験の 1 周 4096）
HS_PER_DEG = 4096 / 360


@pytest.fixture
async def rig(loop) -> Any:
    r = Rig(use_fake_lift=True)
    # 途切れの試験以外は、送り続けなくても続くようにする（9 秒かかる回転を 1 回の `aim` で通す）
    r.params["aim_cmd_timeout_ms"] = 10**9
    await r.start()
    try:
        yield r
    finally:
        await r.stop()


# --- 道具 ----------------------------------------------------------------------------------------


async def connect(rig: Rig, hello: dict | None = HELLO) -> Any:
    assert rig.client is not None
    ws = await rig.client.ws_connect("/ws/module")
    if hello is not None:
        await ws.send_json(hello)
        await wait_until(lambda: any(c.hello_seen for c in rig.camera.port.conns.values()))
    return ws


async def settle() -> None:
    """送ったメッセージをアプリが読み終えるのを待つ（ローカルの WS なので短くてよい）。"""
    await asyncio.sleep(0.05)


async def send_aim(ws: Any, press: int, pitch: float | None = None, yaw: float | None = None) -> None:
    body: dict[str, Any] = {"t": "aim", "press": press}
    if pitch is not None:
        body["pitch_deg"] = pitch
    if yaw is not None:
        body["yaw_deg"] = yaw
    await ws.send_json(body)
    await settle()


async def zero_yaw(rig: Rig) -> Any:
    """画面（人）がヨーの正面を 0° にする。画面の WS を返す（開けたままにする）。"""
    ui = await rig.browser()
    await ui.send_json({"t": "yaw_zero"})
    await wait_until(lambda: rig.camera.yaw.zeroed())
    return ui


def record(rig: Rig) -> dict | None:
    return rig.camera.port.record


async def pump_until(rig: Rig, predicate: Callable[[], bool], steps: int = 400, ms: float = 100) -> int:
    for count in range(steps):
        if predicate():
            return count
        await rig.pump(ms)
    raise AssertionError("条件が %d 周期のあいだ成り立たなかった: record=%r" % (steps, record(rig)))


async def last_state(ws: Any) -> dict | None:
    """届いている `state` のうち最後のもの。"""
    last = None
    while True:
        try:
            message = await ws.receive_json(timeout=0.1)
        except asyncio.TimeoutError:
            return last
        if message.get("t") == "state":
            last = message


def teleport_yaw(rig: Rig, deg: float) -> None:
    """偽の UNO の累計を直接進める（回すのに時間がかかるのを省く。試験だけの手）。"""
    rig.hw._yaw_total += deg * HS_PER_DEG  # noqa: SLF001


def yaw_now(rig: Rig) -> float | None:
    return rig.camera.yaw.deg()


async def start_upstream(rig: Rig, *, status: int = 200) -> TestServer:
    """`hve_video` の `/snapshot` の偽物（`X-Frame-Age-Ms: 120`）。`video_port` を差し替える。"""

    async def handler(request: web.Request) -> web.Response:
        if status != 200:
            return web.json_response({"error": "none"}, status=status)
        return web.Response(
            body=b"\xff\xd8fake-jpeg\xff\xd9", content_type="image/jpeg", headers={"X-Frame-Age-Ms": "120"}
        )

    app = web.Application()
    app.router.add_get("/snapshot", handler)
    server = TestServer(app)
    await server.start_server()
    rig.params["video_port"] = server.port
    return server


# --- 純関数 ---------------------------------------------------------------------------------------


def test_wrap_and_shortest_delta() -> None:
    assert wrap_deg(180) == -180
    assert wrap_deg(-180) == -180
    assert wrap_deg(190) == -170
    assert wrap_deg(-190) == 170
    assert shortest_delta_deg(170, -170) == 20  # 近い回り方（左回り）
    assert shortest_delta_deg(-170, 170) == -20
    assert shortest_delta_deg(0, 90) == 90


def test_yaw_deg_from_hs_sign_and_wrap() -> None:
    assert YAW_SIGN == 1  # 累計が増える向き（`yaw_left` が送る正の `yaw_hsps`）が画面の左
    assert yaw_deg_from_hs(1024, 0, 4096) == 90
    assert yaw_deg_from_hs(-1024, 0, 4096) == -90
    assert yaw_deg_from_hs(2048 + 100, 100, 4096) == -180  # 半周は -180 に収める
    assert yaw_deg_from_hs(4096 + 1024, 0, 4096) == 90  # 一周を超えても -180 以上 180 未満


def test_decode_hello_and_aim() -> None:
    info = decode_hello({"t": "hello", "name": "SS!", "role": "x", "operator": "robot", "busy_label": "あ" * 17})
    assert info["name"] == ""  # 形に合わない名前は名前無し
    assert info["role"] == "observe"  # 知らない値は命じられない側に倒す
    assert info["operator"] == "human"
    assert info["busy_label"] is None  # 48 バイトを超えたら無し（切り詰めない）
    assert decode_hello({})["role"] == "control"
    assert decode_aim({"t": "aim", "press": 1, "pitch_deg": 1}) == {"press": 1, "pitch_deg": 1.0, "yaw_deg": None}
    assert decode_aim({"t": "aim", "press": 1}) is None  # どちらの軸も無い
    assert decode_aim({"t": "aim", "press": True, "pitch_deg": 1}) is None
    assert decode_aim({"t": "aim", "press": 1, "pitch_deg": "1"}) is None


# --- UART の `Y` 行と偽の UNO ----------------------------------------------------------------------


def test_parse_y_line() -> None:
    assert parse_io_line("Y 1200 -340\n") == ("Y", 1200, -340)
    assert parse_io_line("\x00\x00Y 5 0\n") == ("Y", 5, 0)
    assert parse_io_line("Y 1200\n") is None
    assert parse_io_line("Y 1 2147483648\n") is None  # 符号つき 32 bit の外
    assert parse_io_line("Y a b\n") is None


async def test_uno_hardware_reads_y_and_b() -> None:
    clock = ManualClock()
    serial = FakeSerial()
    hw = UnoHardware(UNO_PARAMS, clock, serial=serial)
    try:
        assert hw.yaw_hs_total() is None
        serial.feed("Y 100 40\nY 170 -25\n")
        clock.advance(50)
        # `Y` だけの回は天井の読み値を変えない（`READ_ERROR` にしない）
        assert await hw.read_ceiling() is None
        assert hw.yaw_hs_total() == -25
        assert hw.yaw_epoch() == 0
        serial.feed("B 10 0.3.0\nY 40 7\n")
        clock.advance(50)
        await hw.read_ceiling()
        assert hw.yaw_epoch() == 1
        assert hw.yaw_hs_total() == 7  # `B` のあとの `Y` は新しい数え始め
        serial.feed("Y 90 9\nB 5 0.3.0\n")
        clock.advance(50)
        await hw.read_ceiling()
        assert hw.yaw_epoch() == 2
        assert hw.yaw_hs_total() is None  # `B` のあと次の `Y` が来るまでは不明
        # 窓が空いて行を捨てる回でも、`B` の印は見落とさない
        serial.feed("B 5 0.3.0\n")
        clock.advance(5000)
        await hw.read_ceiling()
        assert hw.yaw_epoch() == 3
    finally:
        await hw.close()


async def test_fake_hardware_yaw_total_follows_the_speed(rig: Rig) -> None:
    hw = rig.hw
    assert hw.yaw_hs_total() == 0
    await hw.drive_yaw(100, "left")
    rig.clock.advance(1000)
    assert hw.yaw_hs_total() == 100
    await hw.drive_yaw(50, "right")
    rig.clock.advance(1000)
    assert hw.yaw_hs_total() == 50
    await hw.stop_yaw()
    rig.clock.advance(1000)
    assert hw.yaw_hs_total() == 50
    hw.reboot_uno()
    assert hw.yaw_hs_total() == 0 and hw.yaw_epoch() == 1


# --- 接続・state ----------------------------------------------------------------------------------


async def test_state_fields_over_the_wire(rig: Rig) -> None:
    ws = await connect(rig)
    await rig.pump()
    state = await last_state(ws)
    assert state is not None
    assert set(state) == {
        "t", "seq", "boot_id", "uptime_ms", "pitch_deg", "pitch_min_deg", "pitch_max_deg", "yaw_deg",
        "yaw_dps", "yaw_zeroed", "owner", "owner_name", "owner_operator", "power", "io", "aim",
    }
    assert isinstance(state["boot_id"], str) and state["boot_id"]
    assert state["uptime_ms"] == 100
    assert state["pitch_deg"] == 0 and state["pitch_min_deg"] == -45 and state["pitch_max_deg"] == 45
    assert state["yaw_deg"] is None and state["yaw_zeroed"] is False  # 原点の前
    assert state["yaw_dps"] == 10
    assert state["owner"] is None and state["owner_name"] is None and state["owner_operator"] is None
    assert state["power"] == "ACTIVE" and state["io"] == "ok" and state["aim"] is None
    await rig.pump()
    second = await last_state(ws)
    assert second["seq"] > state["seq"]
    assert second["boot_id"] == state["boot_id"]


async def test_connections_are_limited_and_the_new_one_is_closed(rig: Rig) -> None:
    kept = [await connect(rig, hello=None) for _ in range(4)]
    extra = await rig.client.ws_connect("/ws/module")  # type: ignore[union-attr]
    message = await extra.receive(timeout=2)
    assert message.type.name in ("CLOSE", "CLOSING", "CLOSED")  # 超えた新しい接続は閉じる
    assert all(not ws.closed for ws in kept)  # 古い方は閉じない
    assert len(rig.camera.port.conns) == 4


async def test_same_name_hello_closes_the_old_connection(rig: Rig) -> None:
    await zero_yaw(rig)
    old = await connect(rig)
    await send_aim(old, 1, yaw=30)
    assert record(rig)["status"] == "MOVING"
    new = await connect(rig)
    await wait_until(lambda: len(rig.camera.port.conns) == 1)
    message = await old.receive(timeout=2)
    assert message.type.name in ("CLOSE", "CLOSING", "CLOSED")
    assert record(rig)["status"] == "NOT_ARRIVED" and record(rig)["reason"] == "OWNER_GONE"
    assert rig.camera.port.owner is None
    assert not new.closed


# --- aim: ヨーが着くまで（通し）--------------------------------------------------------------------


async def test_aim_yaw_goes_to_the_target_and_arrives(rig: Rig) -> None:
    ui = await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 7, pitch=-20, yaw=30)

    # 動いている間: 持ち主は ss。画面には busy_label が出る
    assert record(rig) == {"by": "ss", "press": 7, "pitch_deg": -20.0, "yaw_deg": 30.0, "status": "MOVING", "reason": "NONE"}
    await rig.pump()
    assert rig.hw.pitch_deg == -17.0  # 行き過ぎ点（-20 + 3。起動時は向きが分からないので逆とみなす）
    module_state = rig.camera.build_module_state()
    assert module_state["pitch_deg"] == -20.0  # 報告する目標角は行き過ぎ点でない
    assert module_state["owner"] == "module" and module_state["owner_name"] == "ss"
    assert module_state["owner_operator"] == "auto"
    await rig.pump()
    ui_state = await last_state(ui)
    assert ui_state["busy_label"] == "画像認識で操作中"

    steps = await pump_until(rig, lambda: record(rig)["status"] != "MOVING")
    assert steps > 20  # 10 deg/s で 30° なので 3 秒ほどかかる（一瞬で着いたことにしない）
    assert record(rig)["status"] == "ARRIVED" and record(rig)["reason"] == "NONE"
    assert rig.hw.pitch_deg == -20.0  # 戻りの指令が効いたあと
    assert abs(yaw_now(rig) - 30) <= rig.params["aim_yaw_tol_deg"]
    assert rig.hw.yaw_direction in (None, "left")
    assert rig.hw.yaw_running is False  # 着いたら止める
    after = rig.camera.build_module_state()
    assert after["owner"] is None  # 着いたら持ち主を離す
    assert after["aim"]["status"] == "ARRIVED"
    assert after["pitch_deg"] == -20.0 and abs(after["yaw_deg"] - 30) <= 1
    ui_state = await rig.pump()
    assert ui_state["busy_label"] is None


async def test_arrived_press_does_not_move_again_but_a_new_press_does(rig: Rig) -> None:
    await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 3, yaw=10)
    await pump_until(rig, lambda: record(rig)["status"] == "ARRIVED")
    yaw_at_arrival = yaw_now(rig)
    drives = len(rig.hw.yaw_history)

    await send_aim(ws, 3, yaw=-60)  # 同じ `press`（着いたあと）
    await rig.pump()
    await rig.pump()
    assert record(rig)["status"] == "ARRIVED" and record(rig)["yaw_deg"] == 10.0
    assert rig.camera.port.owner is None
    assert yaw_now(rig) == yaw_at_arrival
    assert all(item is None for item in rig.hw.yaw_history[drives:]), "同じ press で動き出した"

    await send_aim(ws, 4, yaw=-60)  # 新しい `press`
    assert record(rig)["status"] == "MOVING" and record(rig)["press"] == 4
    await rig.pump()
    assert rig.hw.yaw_running and rig.hw.yaw_direction == "right"


async def test_pitch_only_aim_arrives_after_the_return_wait(rig: Rig) -> None:
    ws = await connect(rig)
    await send_aim(ws, 1, pitch=-30)
    assert record(rig)["status"] == "MOVING"  # 目標角を渡した周期では着いたことにしない
    await pump_until(rig, lambda: record(rig)["status"] == "ARRIVED")
    # 行き過ぎ点（-27）→ 戻り（-30）の順に指令が出た
    assert rig.hw.pitch_history == [-27.0, -30.0], rig.hw.pitch_history
    assert rig.hw.pitch_deg == -30
    assert rig.camera.port.owner is None
    assert rig.camera.build_module_state()["pitch_deg"] == -30


async def test_yaw_takes_the_shorter_way_round(rig: Rig) -> None:
    await zero_yaw(rig)
    teleport_yaw(rig, 170)
    await rig.pump(10)
    assert abs(yaw_now(rig) - 170) < 0.5
    ws = await connect(rig)
    await send_aim(ws, 1, yaw=-170)  # 左回りに 20°（右回りだと 340°）
    await rig.pump()
    assert rig.hw.yaw_direction == "left", "遠い回り方をした"
    steps = await pump_until(rig, lambda: record(rig)["status"] == "ARRIVED", steps=60)
    assert steps < 40
    assert abs(wrap_deg(yaw_now(rig) - (-170))) <= rig.params["aim_yaw_tol_deg"]
    assert -180 <= yaw_now(rig) < 180


async def test_yaw_speed_is_yaw_dps(rig: Rig) -> None:
    await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 1, yaw=90)
    await rig.pump()
    expected = 10 * 4096 / 360  # 設定のヨーの初期値 10 deg/s
    assert rig.hw.yaw_steps_per_s == pytest.approx(expected, rel=0.01)


# --- aim: 受け付けない・止まる ---------------------------------------------------------------------


async def test_yaw_before_zero_is_unknown_and_nothing_moves(rig: Rig) -> None:
    ws = await connect(rig)
    await send_aim(ws, 1, pitch=-10, yaw=20)
    assert record(rig)["status"] == "NOT_ARRIVED" and record(rig)["reason"] == "YAW_UNKNOWN"
    await rig.pump()
    await rig.pump()
    assert rig.hw.pitch_deg == 0.0, "受け付けないのにピッチを動かした"
    assert all(item is None for item in rig.hw.yaw_history), "原点の前にヨーを回した"
    state = rig.camera.build_module_state()
    assert state["yaw_deg"] is None and state["yaw_zeroed"] is False
    # 片方だけ（ピッチ）なら原点の前でも受け付ける
    await send_aim(ws, 2, pitch=-10)
    await pump_until(rig, lambda: record(rig)["status"] == "ARRIVED")


async def test_pitch_outside_the_range_is_rejected_not_rounded(rig: Rig) -> None:
    ws = await connect(rig)
    await send_aim(ws, 1, pitch=50)
    assert record(rig)["status"] == "NOT_ARRIVED" and record(rig)["reason"] == "TARGET_RANGE"
    assert rig.hw.pitch_deg == 0.0 and rig.hw.pitch_history == []  # 45 に丸めて動かさない
    await send_aim(ws, 2, pitch=-45.5)
    assert record(rig)["reason"] == "TARGET_RANGE" and rig.hw.pitch_history == []
    await send_aim(ws, 3, pitch=45)  # 境目は受け付ける
    assert record(rig)["status"] == "MOVING"
    await pump_until(rig, lambda: record(rig)["status"] == "ARRIVED")
    assert rig.hw.pitch_deg == 45
    await send_aim(ws, 4, pitch=-45)
    await pump_until(rig, lambda: record(rig)["status"] == "ARRIVED")
    assert rig.hw.pitch_deg == -45
    await zero_yaw(rig)
    await send_aim(ws, 5, yaw=180)  # -180 以上 180 未満
    assert record(rig)["reason"] == "TARGET_RANGE"
    assert all(item is None for item in rig.hw.yaw_history)


async def test_a_human_pressing_the_screen_preempts_the_aim(rig: Rig) -> None:
    ui = await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 1, yaw=90)
    await rig.pump()
    assert record(rig)["status"] == "MOVING" and rig.hw.yaw_running

    await ui.send_json({"t": "hold", "axis": "pitch_up", "speed": 10})
    await rig.wait_axis("pitch_up")
    assert record(rig)["status"] == "NOT_ARRIVED" and record(rig)["reason"] == "PREEMPTED"
    await rig.pump()
    state = rig.camera.build_module_state()
    assert state["owner"] == "ui" and state["owner_operator"] == "human"  # 人の割り込みが見分けられる
    assert rig.hw.yaw_running is False  # ss のヨーは止まる

    # 画面を離したあとも、同じ press の aim は動かさない。新しい press は取り返す
    await ui.send_json({"t": "release"})
    await settle()
    await send_aim(ws, 1, yaw=90)
    assert record(rig)["reason"] == "PREEMPTED"
    await send_aim(ws, 2, yaw=90)
    assert record(rig)["status"] == "MOVING"
    assert rig.camera.build_module_state()["owner"] == "module"


async def test_zeroing_the_yaw_while_ss_drives_preempts_it(rig: Rig) -> None:
    ui = await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 1, yaw=90)
    await rig.pump()
    await ui.send_json({"t": "yaw_zero"})
    await wait_until(lambda: record(rig)["status"] == "NOT_ARRIVED")
    assert record(rig)["reason"] == "PREEMPTED"
    assert rig.camera.port.owner is None
    await rig.pump()
    assert rig.hw.yaw_running is False


async def test_ss_cannot_zero_the_yaw(rig: Rig) -> None:
    ws = await connect(rig)
    await ws.send_json({"t": "yaw_zero"})
    await settle()
    await rig.pump()
    assert rig.camera.yaw.zeroed() is False  # 原点合わせは人だけ


async def test_a_cut_off_aim_stops_the_yaw_with_cmd_timeout(rig: Rig) -> None:
    rig.params["aim_cmd_timeout_ms"] = 600
    await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 1, yaw=90)
    for _ in range(5):  # 500 ms。まだ続く
        await rig.pump()
    assert record(rig)["status"] == "MOVING" and rig.hw.yaw_running
    await send_aim(ws, 1, yaw=90)  # 送り続けると続く
    for _ in range(5):
        await rig.pump()
    assert record(rig)["status"] == "MOVING"
    for _ in range(3):  # 途切れて 600 ms を超えた
        await rig.pump()
    assert record(rig)["status"] == "NOT_ARRIVED" and record(rig)["reason"] == "CMD_TIMEOUT"
    assert rig.hw.yaw_running is False
    assert rig.camera.port.owner is None


async def test_release_and_close_stop_the_aim(rig: Rig) -> None:
    await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 5, yaw=90)
    await rig.pump()
    await ws.send_json({"t": "release", "press": 4})  # 持ち主の press と違う release は効かない
    await settle()
    assert record(rig)["status"] == "MOVING"
    await ws.send_json({"t": "release", "press": 5})
    await settle()
    assert record(rig)["reason"] == "CMD_STOP" and rig.camera.port.owner is None
    await rig.pump()
    assert rig.hw.yaw_running is False

    await send_aim(ws, 6, yaw=-90)
    await rig.pump()
    assert rig.hw.yaw_running
    await ws.close()
    await wait_until(lambda: record(rig)["reason"] == "OWNER_GONE")
    await rig.pump()
    assert rig.hw.yaw_running is False


async def test_arduino_silence_ends_the_aim_with_io_lost(rig: Rig) -> None:
    rig.params["aim_cmd_timeout_ms"] = 10**9
    await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 1, yaw=90)
    await rig.pump()
    assert record(rig)["status"] == "MOVING"
    rig.hw.set_io_lost(True)  # 行が来なくなる
    await pump_until(rig, lambda: record(rig)["status"] != "MOVING", steps=20)
    assert record(rig)["reason"] == "IO_LOST" and rig.camera.port.owner is None
    assert rig.hw.yaw_running is False
    assert rig.camera.build_module_state()["io"] == "lost"


async def test_observe_connections_cannot_command(rig: Rig) -> None:
    ws = await connect(rig, {"t": "hello", "name": "viewer", "role": "observe"})
    await send_aim(ws, 1, pitch=-10)
    assert record(rig) is None and rig.hw.pitch_history == []
    await ws.send_json({"t": "release", "press": 1})
    await settle()
    assert rig.camera.port.owner is None
    # 状態は読める
    await rig.pump()
    assert (await last_state(ws)) is not None


async def test_lower_press_than_seen_is_dropped(rig: Rig) -> None:
    ws = await connect(rig)
    await send_aim(ws, 5, pitch=-10)
    await send_aim(ws, 4, pitch=-20)  # 古い press
    await rig.pump()
    assert record(rig)["press"] == 5 and rig.camera.control.pitch_deg == -10.0
    assert rig.hw.pitch_deg == -7.0  # 行き過ぎ点（古い press の -20 には行かない）


# --- ヨーの原点 ----------------------------------------------------------------------------------


async def test_yaw_deg_is_none_until_zeroed_and_increases_to_the_left(rig: Rig) -> None:
    ui = await rig.browser()
    await rig.pump()
    state = await last_state(ui)
    assert state["yaw_deg"] is None and state["yaw_zeroed"] is False
    await ui.send_json({"t": "hold", "axis": "yaw_left", "speed": 10})
    await settle()
    for _ in range(3):
        await rig.pump()
    assert rig.camera.yaw.deg() is None  # 回しても原点を合わせるまでは不明のまま

    for _ in range(5):  # 前の hold が途絶えて止まる
        await rig.pump()
    await ui.send_json({"t": "yaw_zero"})
    await wait_until(lambda: rig.camera.yaw.zeroed())
    assert yaw_now(rig) == 0
    await ui.send_json({"t": "hold", "axis": "yaw_left", "speed": 10})
    await settle()
    for _ in range(3):
        await rig.pump()
    assert 1.5 < yaw_now(rig) < 3.5  # 「左」で増える（約 0.2〜0.3 秒 × 10 deg/s）
    state = await last_state(ui)
    assert state["yaw_zeroed"] is True and isinstance(state["yaw_deg"], float)

    for _ in range(5):
        await rig.pump()
    await ui.send_json({"t": "yaw_zero"})
    await settle()
    await ui.send_json({"t": "hold", "axis": "yaw_right", "speed": 10})
    await settle()
    for _ in range(3):
        await rig.pump()
    assert -3.5 < yaw_now(rig) < -1.5  # 「右」で減る


async def test_a_uno_reboot_drops_the_origin(rig: Rig) -> None:
    await zero_yaw(rig)
    assert yaw_now(rig) == 0
    rig.hw.reboot_uno()  # `B` 行
    await rig.pump()
    state = rig.camera.build_module_state()
    assert state["yaw_zeroed"] is False and state["yaw_deg"] is None
    ws = await connect(rig)
    await send_aim(ws, 1, yaw=10)
    assert record(rig)["reason"] == "YAW_UNKNOWN"


async def test_a_uno_reboot_during_an_aim_stops_it(rig: Rig) -> None:
    await zero_yaw(rig)
    ws = await connect(rig)
    await send_aim(ws, 1, yaw=90)
    await rig.pump()
    rig.hw.reboot_uno()
    await rig.pump()
    assert record(rig)["status"] == "NOT_ARRIVED" and record(rig)["reason"] == "YAW_UNKNOWN"
    assert rig.hw.yaw_running is False


# --- touch（省電力）-------------------------------------------------------------------------------


async def go_saving(rig: Rig) -> None:
    await rig.pump(1_800_000)
    await rig.pump(1_500)
    assert rig.camera._idle.state is not PowerState.ACTIVE  # noqa: SLF001


async def test_aim_calls_touch_and_wakes_from_power_saving(rig: Rig) -> None:
    ws = await connect(rig)
    await go_saving(rig)
    await send_aim(ws, 1, pitch=-10)
    assert rig.camera._idle.state is PowerState.ACTIVE  # noqa: SLF001
    assert rig.hw.power_mode == POWER_ACTIVE  # ピッチの力を戻してから動く
    assert rig.camera.build_module_state()["power"] == "ACTIVE"


async def test_power_state_is_save_while_saving(rig: Rig) -> None:
    await connect(rig)
    await go_saving(rig)
    assert rig.camera.build_module_state()["power"] == "SAVE"


# --- 静止画 --------------------------------------------------------------------------------------


async def get(rig: Rig, path: str = "/api/snapshot?by=ss") -> Any:
    assert rig.client is not None
    return await rig.client.get(path)


async def test_snapshot_passes_the_image_and_adds_headers(rig: Rig) -> None:
    upstream = await start_upstream(rig)
    try:
        await zero_yaw(rig)
        ws = await connect(rig)
        await send_aim(ws, 1, pitch=-12)
        await rig.pump(1000)  # 行き過ぎ点と戻りの指令が両方出た周期
        await rig.pump(1000)  # ここから 1000 ms 静止
        response = await get(rig)
        assert response.status == 200
        assert await response.read() == b"\xff\xd8fake-jpeg\xff\xd9"
        assert response.headers["Content-Type"] == "image/jpeg"
        assert response.headers["X-Frame-Age-Ms"] == "120"  # `hve_video` の値をそのまま渡す
        assert response.headers["X-Boot-Id"] == rig.camera.build_module_state()["boot_id"]
        assert response.headers["X-Pitch-Deg"] == "-12.0"
        assert response.headers["X-Yaw-Deg"] == "0.0"
        assert int(response.headers["X-Still-Ms"]) == 1000
    finally:
        await upstream.close()


async def test_snapshot_without_a_yaw_origin_has_no_yaw_header(rig: Rig) -> None:
    upstream = await start_upstream(rig)
    try:
        response = await get(rig, "/api/snapshot")
        assert response.status == 200
        assert "X-Yaw-Deg" not in response.headers
        assert "X-Pitch-Deg" in response.headers and "X-Still-Ms" in response.headers
    finally:
        await upstream.close()


async def test_snapshot_is_503_when_hve_video_is_missing_or_empty(rig: Rig) -> None:
    rig.params["video_port"] = 1  # 誰も居ない
    assert (await get(rig)).status == 503
    upstream = await start_upstream(rig, status=503)
    try:
        assert (await get(rig, "/api/snapshot")).status == 503
    finally:
        await upstream.close()


async def test_snapshot_from_ss_is_limited_to_one_per_200_ms(rig: Rig) -> None:
    upstream = await start_upstream(rig)
    try:
        assert (await get(rig)).status == 200
        assert (await get(rig)).status == 429  # 同じ時刻にもう 1 枚
        await rig.pump(199)
        assert (await get(rig)).status == 429
        await rig.pump(1)
        assert (await get(rig)).status == 200  # 200 ms 経った
        # `by=ss` でない要求は数えない・縛らない
        assert (await get(rig, "/api/snapshot")).status == 200
        assert (await get(rig, "/api/snapshot")).status == 200
    finally:
        await upstream.close()


async def test_snapshot_calls_touch(rig: Rig) -> None:
    upstream = await start_upstream(rig)
    try:
        await go_saving(rig)
        assert (await get(rig)).status == 200
        assert rig.camera._idle.state is PowerState.ACTIVE  # noqa: SLF001
    finally:
        await upstream.close()
    await go_saving(rig)
    rig.params["video_port"] = 1  # 取れなくても要求は操作に数える
    assert (await get(rig)).status == 503
    assert rig.camera._idle.state is PowerState.ACTIVE  # noqa: SLF001


async def test_still_ms_restarts_when_the_pitch_or_yaw_moves(rig: Rig) -> None:
    upstream = await start_upstream(rig)
    try:
        await zero_yaw(rig)
        ws = await connect(rig)
        await rig.pump(2000)
        assert int((await get(rig, "/api/snapshot")).headers["X-Still-Ms"]) >= 2000

        # ピッチの指令が変わるたびに数え直す（行き過ぎ点・戻りの両方）
        await send_aim(ws, 1, pitch=-10)
        await rig.pump(300)
        assert int((await get(rig, "/api/snapshot")).headers["X-Still-Ms"]) == 0  # 行き過ぎ点の指令
        await rig.pump(1000)
        assert int((await get(rig, "/api/snapshot")).headers["X-Still-Ms"]) == 0  # 戻りの指令
        await rig.pump(500)
        assert int((await get(rig, "/api/snapshot")).headers["X-Still-Ms"]) == 500

        # ヨーが回っている間は、撮った 1 枚（`X-Frame-Age-Ms: 120`）より新しい動きが見える
        await send_aim(ws, 2, yaw=60)
        await rig.pump(100)
        await rig.pump(100)
        moving = int((await get(rig, "/api/snapshot")).headers["X-Still-Ms"])
        assert moving <= 100
        assert moving < 120  # ss は X-Still-Ms < X-Frame-Age-Ms なら撮り直す

        # 画面の操作でも数え直す
        ui = await rig.browser()
        await ui.send_json({"t": "hold", "axis": "pitch_up", "speed": 10})
        await rig.wait_axis("pitch_up")
        await rig.pump(100)
        assert int((await get(rig, "/api/snapshot")).headers["X-Still-Ms"]) == 0
    finally:
        await upstream.close()
