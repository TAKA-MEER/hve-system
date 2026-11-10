"""外し忘れの省電力・電源断を **アプリを通して** 縛る試験（`WP-CAM-13`）。

`IdlePower` の純関数の試験は `test_power.py`。ここでは **`app.py`・`uno_hw.py` の呼び出し側の数行**
（`touch()` の入口・`step()` に渡す値・段階の伝え方・`poweroff()` を呼ぶ回数・`R` を送る切り替え）が
効いていることを、画面の WS・`POST /api/fake`・制御ループ 1 回（`pump`）を通して確かめる。

**開発機を落とさない。**この試験では実物の `/sbin/poweroff`（`subprocess.run`・`PowerHw.poweroff`）が
呼ばれたら即失敗する（`_never_really_power_off`）。
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from hve_camera import __main__ as cli
from hve_camera.app import create_app
from hve_camera.hw.base import POWER_ACTIVE, POWER_SAVING, POWER_SETTLING
from hve_camera.hw.fake_power import FakePower
from hve_camera.hw.power_hw import PowerHw
from hve_camera.hw.uno_hw import UnoHardware
from tests.cam_support import PARAMS, SETTINGS, ManualClock, RecordingVideoZoom, wait_until
from tests.test_app import Rig
from tests.test_uno_hw import PARAMS as UNO_PARAMS
from tests.test_uno_hw import FakeSerial

IDLE = 1_800_000
ABSENT = 60_000
SETTLE = 1_500
SAVE_PITCH = -10.0


@pytest.fixture(autouse=True)
def _never_really_power_off(monkeypatch) -> None:
    """実物の電源断に届く 2 つの道（`PowerHw.poweroff`・`subprocess.run`）を塞ぐ。"""

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("実物の poweroff を呼んではいけない")

    monkeypatch.setattr(PowerHw, "poweroff", boom)
    monkeypatch.setattr(subprocess, "run", boom)


@pytest.fixture
async def rig(loop) -> Any:
    r = Rig(use_fake_lift=True)
    r.params["power_save_pitch_deg"] = SAVE_PITCH
    await r.start()
    try:
        yield r
    finally:
        await r.stop()


async def run_for(rig: Rig, total_ms: float, step_ms: float = 1_000) -> None:
    """制御ループを `step_ms` ずつ進めて `total_ms` 経たせる。"""
    remaining = total_ms
    while remaining > 0:
        step = min(step_ms, remaining)
        await rig.pump(step)
        remaining -= step


async def go_saving(rig: Rig) -> None:
    """最後の操作から 30 分経たせて `SAVING` まで進める（機体の無線は見えている）。"""
    await rig.pump(IDLE)
    await rig.pump(SETTLE)
    assert rig.hw.power_mode == POWER_SAVING


def moving(rig: Rig, monkeypatch, on: bool) -> None:
    """昇降部が動いている・止まっていることにする（`state.lift.dir`）。"""
    monkeypatch.setattr(rig.lift, "latest_state", lambda: {"dir": "up" if on else "stop"})


# --- 30 分で省電力 --------------------------------------------------------------


async def test_idle_30_minutes_settles_then_saves(rig: Rig) -> None:
    state = await rig.pump(100)
    assert state["power"] == {"mode": "active"}
    assert rig.hw.power_mode == POWER_ACTIVE
    assert rig.capture.sent[-1] is True, "ふつうは取り込みを望む"

    state = await rig.pump(IDLE - 100 - 1_000)
    assert state["power"] == {"mode": "active"}
    assert rig.hw.power_mode == POWER_ACTIVE

    state = await rig.pump(1_000)  # 30 分
    assert state["power"] == {"mode": "saving"}
    assert rig.hw.power_mode == POWER_SETTLING
    assert rig.hw.power_pitch_deg == SAVE_PITCH, "倒れてもよい角度へ"
    assert rig.capture.sent[-1] is False, "取り込みを止める"

    state = await rig.pump(SETTLE)
    assert state["power"] == {"mode": "saving"}
    assert rig.hw.power_mode == POWER_SAVING
    assert rig.power.poweroff_count == 0, "機体の無線が見えている間は電源を切らない"


async def test_settling_does_not_overwrite_the_axis_target(rig: Rig) -> None:
    """軸の目標角（`axes.py`・`ControlLoop`）は省電力で書き換えない。"""
    before = rig.hw.pitch_history[:]
    pitch = rig.camera.control.build_state(1)["pitch_deg"]
    await rig.pump(IDLE)
    await rig.pump(SETTLE)
    assert rig.camera.control.build_state(1)["pitch_deg"] == pitch
    assert SAVE_PITCH not in rig.hw.pitch_history[len(before):], "set_pitch で省電力の角度を出さない"


async def test_capture_is_resent_every_check_period(rig: Rig) -> None:
    await rig.pump(100)
    count = len(rig.capture.sent)
    await rig.pump(100)
    assert len(rig.capture.sent) == count, "周期の前は送り直さない"
    await rig.pump(1_000)
    assert len(rig.capture.sent) == count + 1, "power_check_period_ms ごとに送り直す"
    await rig.pump(IDLE)
    await run_for(rig, 3_000)
    assert rig.capture.sent[-1] is False
    assert rig.capture.sent.count(False) >= 3, "止めている間も送り直す（hve_video だけ再起動しても揃う）"


# --- 操作で戻る -----------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        {"t": "hold", "axis": "pitch_up", "speed": 5},
        {"t": "release"},
        {"t": "zoom", "level": 2},
        {"t": "touch"},
    ],
    ids=["hold", "release", "zoom", "touch"],
)
async def test_browser_operations_wake_the_module(rig: Rig, message: dict) -> None:
    ws = await rig.browser()
    await go_saving(rig)
    assert rig.capture.sent[-1] is False
    await ws.send_json(message)
    # 制御ループを回す前に戻る（touch の中で段階を伝え直す）
    await wait_until(lambda: rig.hw.power_mode == POWER_ACTIVE)
    await wait_until(lambda: rig.capture.sent[-1] is True)
    assert rig.hw.power_history[-1] == (POWER_ACTIVE, SAVE_PITCH)
    state = await rig.camera.broadcast_state()
    assert state["power"] == {"mode": "active"}
    # 戻ったあと、また 30 分で省電力に入る（時計を数え直す）
    await rig.pump(IDLE - 2_000)
    assert rig.hw.power_mode == POWER_ACTIVE
    await rig.pump(2_000)
    assert rig.hw.power_mode == POWER_SETTLING
    await ws.close()


async def test_put_settings_wakes_the_module(rig: Rig) -> None:
    await go_saving(rig)
    body = {
        "lift_up": {"min": 10, "max": 20, "init": 15},
        "lift_down": {"min": 10, "max": 60, "init": 30},
        "pitch": {"min": 1, "max": 30, "init": 10},
        "yaw": {"min": 2, "max": 30, "init": 10},
        "motor_max_pct": 50,
    }
    assert rig.client is not None
    response = await rig.client.put("/api/settings", json=body)
    assert response.status == 200, await response.text()
    assert rig.hw.power_mode == POWER_ACTIVE
    assert rig.capture.sent[-1] is True


async def test_opening_or_reading_does_not_wake_the_module(rig: Rig) -> None:
    """画面を開く・映像の接続・状態を読むだけでは戻らない（置き忘れた端末で判定が止まらないように）。"""
    await go_saving(rig)
    ws = await rig.browser()  # 新しく開く（倍率を 1 に戻して state を配る）
    ws2 = await rig.browser()
    assert rig.client is not None
    response = await rig.client.get("/api/settings")
    assert response.status in (200, 503)
    await rig.pump(1_000)
    assert rig.hw.power_mode == POWER_SAVING
    assert rig.capture.sent[-1] is False
    state = await rig.pump(100)
    assert state["power"] == {"mode": "saving"}
    await ws.close()  # 閉じても戻らない
    await ws2.close()
    await rig.pump(100)
    assert rig.hw.power_mode == POWER_SAVING


async def test_unknown_message_does_not_wake_the_module(rig: Rig) -> None:
    ws = await rig.browser()
    await go_saving(rig)
    await ws.send_json({"t": "bogus"})
    await ws.send_str("これは JSON でない")
    await rig.pump(100)
    assert rig.hw.power_mode == POWER_SAVING
    await ws.close()


# --- 昇降部が動いている間 -----------------------------------------------------------


async def test_lift_moving_postpones_saving(rig: Rig, monkeypatch) -> None:
    moving(rig, monkeypatch, True)
    await run_for(rig, 2 * IDLE, step_ms=60_000)
    assert rig.hw.power_mode == POWER_ACTIVE, "昇降部が動いている間は省電力に入らない"
    moving(rig, monkeypatch, False)
    await rig.pump(IDLE - 1_000)
    assert rig.hw.power_mode == POWER_ACTIVE, "止まった時刻から数える"
    await rig.pump(1_000)
    assert rig.hw.power_mode == POWER_SETTLING


async def test_lift_moving_in_saving_blocks_poweroff(rig: Rig, monkeypatch) -> None:
    """省電力中でも昇降部が動いている最中（fk-system が使っている）は、機体の無線が無くても電源を切らない。"""
    await go_saving(rig)
    await rig.client.post("/api/fake", json={"wlan_up": False})  # type: ignore[union-attr]
    moving(rig, monkeypatch, True)
    await run_for(rig, 10 * ABSENT)
    assert rig.power.poweroff_count == 0
    moving(rig, monkeypatch, False)
    await run_for(rig, IDLE - 1_000)
    assert rig.power.poweroff_count == 0, "最後に動いた時刻から 30 分は切らない"
    await run_for(rig, 2_000)
    assert rig.power.poweroff_count == 1


# --- 電源断 ---------------------------------------------------------------------


async def test_no_poweroff_while_the_robot_wlan_is_up(rig: Rig) -> None:
    await run_for(rig, 3 * IDLE, step_ms=10_000)
    assert rig.hw.power_mode == POWER_SAVING
    assert rig.power.wlan_up() is True
    assert rig.power.poweroff_count == 0, "機体の無線が見えている間は何時間でも省電力のまま待つ"


async def test_no_poweroff_before_30_minutes_even_without_wlan(rig: Rig) -> None:
    await rig.client.post("/api/fake", json={"wlan_up": False})  # type: ignore[union-attr]
    await run_for(rig, IDLE - 1_000, step_ms=10_000)
    assert rig.power.poweroff_count == 0, "最後の操作から 30 分経つまでは切らない"
    assert rig.hw.power_mode == POWER_ACTIVE


async def test_poweroff_waits_for_ap_absent_ms(rig: Rig) -> None:
    await go_saving(rig)
    await run_for(rig, 5_000)
    response = await rig.client.post("/api/fake", json={"wlan_up": False})  # type: ignore[union-attr]
    assert response.status == 200
    await rig.pump(1_000)  # 無線が見えなくなったのを見る
    await run_for(rig, ABSENT - 2_000)
    assert rig.power.poweroff_count == 0, "見えない状態が ap_absent_ms 続くまで切らない"
    await run_for(rig, 3_000)
    assert rig.power.poweroff_count == 1
    assert rig.hw.power_mode == POWER_SAVING, "切るときもピッチの力は抜いたまま"


async def test_wlan_blip_restarts_the_count(rig: Rig) -> None:
    await go_saving(rig)
    await rig.pump(1_000)

    async def wlan(up: bool) -> None:
        await rig.client.post("/api/fake", json={"wlan_up": up})  # type: ignore[union-attr]

    await wlan(False)
    await run_for(rig, 40_000)
    await wlan(True)  # 一瞬戻った
    await run_for(rig, 1_000)
    await wlan(False)
    await run_for(rig, 40_000)
    assert rig.power.poweroff_count == 0, "数え直すので、合わせて 80 秒でも切らない"
    await run_for(rig, 30_000)
    assert rig.power.poweroff_count == 1


async def test_poweroff_is_called_once(rig: Rig) -> None:
    await go_saving(rig)
    await rig.client.post("/api/fake", json={"wlan_up": False})  # type: ignore[union-attr]
    await run_for(rig, 5 * ABSENT)
    assert rig.power.poweroff_count == 1
    await run_for(rig, 5 * ABSENT)
    assert rig.power.poweroff_count == 1, "切り始めたあとに何度も呼ばない"


async def test_touch_after_off_does_not_restart(rig: Rig) -> None:
    ws = await rig.browser()
    await go_saving(rig)
    await rig.client.post("/api/fake", json={"wlan_up": False})  # type: ignore[union-attr]
    await run_for(rig, 5 * ABSENT)
    assert rig.power.poweroff_count == 1
    await ws.send_json({"t": "touch"})
    await rig.pump(1_000)
    assert rig.hw.power_mode == POWER_SAVING
    assert rig.power.poweroff_count == 1
    await ws.close()


async def test_fake_wlan_validation(rig: Rig) -> None:
    assert rig.client is not None
    bad = await rig.client.post("/api/fake", json={"wlan_up": "no"})
    assert bad.status == 400
    assert rig.power.wlan_up() is True
    ok = await rig.client.post("/api/fake", json={"wlan_up": False})
    assert ok.status == 200
    assert rig.power.wlan_up() is False


# --- 実物の poweroff が呼ばれない ----------------------------------------------------


async def test_fake_mode_never_accepts_the_real_power_hw(loop) -> None:
    clock = ManualClock()
    from hve_camera.hw.fake_hw import FakeHardware
    from hve_camera.hw.fake_lift import FakeLift

    with pytest.raises(ValueError):
        create_app(
            FakeHardware(clock),
            FakeLift(clock),
            settings=SETTINGS,
            using_defaults=True,
            params=PARAMS,
            clock=clock,
            video_zoom=RecordingVideoZoom(),
            fake=True,
            power_hw=PowerHw(),
        )


async def test_default_power_is_the_fake_one(loop) -> None:
    """電源の口を渡さなければ偽物（実物は `__main__` が実物のモードでだけ渡す）。"""
    clock = ManualClock()
    from hve_camera.hw.fake_hw import FakeHardware
    from hve_camera.hw.fake_lift import FakeLift

    for fake in (True, False):
        app = create_app(
            FakeHardware(clock),
            FakeLift(clock),
            settings=SETTINGS,
            using_defaults=True,
            params=PARAMS,
            clock=clock,
            video_zoom=RecordingVideoZoom(),
            fake=fake,
        )
        assert isinstance(app["camera"]._power_hw, FakePower)  # noqa: SLF001


def test_main_fake_uses_fake_power_and_fake_wlan(monkeypatch) -> None:
    """`--fake` で立ち上げたアプリの電源の口は偽物。無線は既定で見えている（開発機で 30 分後に落ちない）。"""
    from aiohttp import web

    captured: list = []
    monkeypatch.setattr(web, "run_app", lambda app, **kwargs: captured.append(app))
    assert cli.main(["--fake", "--port", "0"]) == 0
    power = captured[0]["camera"]._power_hw  # noqa: SLF001
    assert isinstance(power, FakePower)
    assert power.wlan_up() is True


# --- UNO への行（呼び出し側: UnoHardware の送信ループとアプリの配線） ----------------------


async def test_uno_lines_follow_the_power_state_through_the_app(loop) -> None:
    """アプリ → `UnoHardware` → 直列線。省電力の間 `M` の代わりに `R` が出て、戻れば `M` を目標角で送り直す。"""
    from aiohttp.test_utils import TestClient, TestServer

    from hve_camera.hw.fake_lift import FakeLift

    clock = ManualClock()
    serial = FakeSerial()
    hw = UnoHardware({**UNO_PARAMS, "io_cmd_period_ms": 5}, clock, serial=serial)
    power = FakePower()
    params = dict(PARAMS, power_save_pitch_deg=SAVE_PITCH)
    app = create_app(
        hw,
        FakeLift(clock),
        settings={k: dict(v) for k, v in SETTINGS.items()},
        using_defaults=True,
        params=params,
        clock=clock,
        video_zoom=RecordingVideoZoom(),
        fake=True,
        autostart=False,
        power_hw=power,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    camera = app["camera"]

    def lines() -> list:
        return [w.decode("ascii").strip() for w in serial.written]

    try:
        await hw.set_pitch(12.0)  # 軸の目標角
        await camera.control_step()
        await wait_until(lambda: any(l.startswith("M ") and l.endswith(" 120 0") for l in lines()))

        clock.advance(IDLE)
        await camera.control_step()  # SETTLING
        await wait_until(lambda: any(l.startswith("M ") and l.endswith(" -100 0") for l in lines()))

        clock.advance(SETTLE)
        await camera.control_step()  # SAVING
        mark = len(serial.written)
        await wait_until(lambda: len(serial.written) >= mark + 5)
        later = lines()[mark:]
        assert later[0].startswith("R ")
        assert all(l.startswith("R ") for l in later), "省電力の間は M を送らない"
        assert all(parse_seq(l) is not None for l in later)

        ws = await client.ws_connect("/ws")
        await ws.receive_json(timeout=2)
        await ws.send_json({"t": "touch"})
        await wait_until(lambda: hw._power_mode == POWER_ACTIVE)  # noqa: SLF001
        mark = len(serial.written)
        await wait_until(lambda: len(serial.written) >= mark + 3)
        after = lines()[mark:]
        assert all(l.startswith("M ") and l.endswith(" 120 0") for l in after), "目標角で M を送り直す"
        await ws.close()
    finally:
        await client.close()


def parse_seq(line: str) -> int | None:
    parts = line.split(" ")
    if len(parts) == 2 and parts[0] == "R" and parts[1].isdigit():
        return int(parts[1])
    return None
