"""`lift_link.py` v2 と `lift_resolve.py` の試験。**本物の WS 経路**を通す。"""

from __future__ import annotations

import pytest

from hve_camera.lift_link import LiftLink
from hve_camera.lift_resolve import resolve_lift_host
from tests.cam_support import FakeEsp32, ManualClock, wait_until


async def test_hello_is_sent_on_connect() -> None:
    """繋いだら `hello` を 1 度送る。`ceiling_sensor` は常に `true`（§3.1）。"""
    clock = ManualClock()
    esp32 = FakeEsp32(clock)
    url = await esp32.start()
    lift = LiftLink(url, clock)
    try:
        await lift.start()
        await wait_until(lambda: len(esp32.hello_history) >= 1)
        hello = esp32.hello_history[0]
        assert hello["t"] == "hello"
        assert hello["ceiling_sensor"] is True
        assert hello["name"] == "hve-cam"
    finally:
        await lift.close()
        await esp32.stop()


async def test_hello_ignores_the_ceiling_state() -> None:
    """変異 3 の芯。天井の読み値の調子から `ceiling_sensor` を計算したら赤になる。

    ここでは「天井が読めない状態でも `true` を送る」ことを、経路を通して縛る。
    （`LiftLink` は天井をそもそも見ないので、調子が悪くても `true` になる。）
    """
    clock = ManualClock()
    esp32 = FakeEsp32(clock)
    url = await esp32.start()
    # 調子が悪くても true のまま（LiftLink は天井を見ない）
    lift = LiftLink(url, clock, ceiling_sensor=True)
    try:
        await lift.start()
        await wait_until(lambda: len(esp32.hello_history) >= 1)
        assert esp32.hello_history[0]["ceiling_sensor"] is True
    finally:
        await lift.close()
        await esp32.stop()


async def test_hold_and_release_shapes() -> None:
    """`hold`・`release` の形（protocol §2.1）。"""
    clock = ManualClock()
    esp32 = FakeEsp32(clock)
    url = await esp32.start()
    lift = LiftLink(url, clock)
    try:
        await lift.start()
        await wait_until(lambda: esp32.connected)
        await lift.send_hold("up", 30, 7, "MEASURED", 2000, 80)
        await lift.send_release(7)
        await wait_until(lambda: len(esp32.hold_history) >= 1)
        await wait_until(lambda: len(esp32.release_history) >= 1)
        hold = esp32.hold_history[-1]
        assert hold["t"] == "hold"
        assert (hold["press"], hold["dir"], hold["duty"]) == (7, "up", 30)
        assert hold["ceiling"] == {"status": "MEASURED", "mm": 2000, "age_ms": 80}
        assert esp32.release_history[-1] == {"t": "release", "press": 7}
    finally:
        await lift.close()
        await esp32.stop()


async def test_state_is_accepted_and_link_ok() -> None:
    clock = ManualClock()
    esp32 = FakeEsp32(clock)
    url = await esp32.start()
    lift = LiftLink(url, clock)
    try:
        await lift.start()
        await wait_until(lambda: lift.latest_state() is not None)
        assert lift.link_ok(clock(), 600) is True
        clock.advance(601)
        assert lift.link_ok(clock(), 600) is False
    finally:
        await lift.close()
        await esp32.stop()


async def test_commands_are_dropped_while_disconnected() -> None:
    """繋がっていなければ捨てる（例外を投げない）。"""
    clock = ManualClock()
    lift = LiftLink("ws://127.0.0.1:1/ws/module", clock)
    await lift.send_hold("up", 30, 1, "MEASURED", 2000, 0)
    await lift.send_release(1)
    await lift.close()


async def test_resolver_is_used_for_reconnect() -> None:
    """URL を関数で渡すと、繋ぐたびに呼び出す。引けない間も例外を投げない。"""
    clock = ManualClock()
    esp32 = FakeEsp32(clock)
    url = await esp32.start()
    calls: list[str] = []
    missing = True

    def resolve():
        calls.append("called")
        return None if missing else url

    lift = LiftLink(resolve, clock)
    try:
        await lift.start()
        await wait_until(lambda: len(calls) >= 1)
        missing = False
        await wait_until(lambda: esp32.connected)
        # hello は接続の少し後に届く。届くのを待ってから確かめる（待たないと競合する）
        await wait_until(lambda: bool(esp32.hello_history))
        assert esp32.hello_history, "引けるようになったら繋いで hello を送る"
    finally:
        await lift.close()
        await esp32.stop()


# --- lift_resolve --------------------------------------------------------------


def test_resolve_prefers_the_configured_host() -> None:
    assert resolve_lift_host("192.168.5.23", "hve-lift") == "192.168.5.23"
    assert resolve_lift_host("  192.168.5.23  ", "hve-lift") == "192.168.5.23"


def test_resolve_returns_none_without_avahi(monkeypatch) -> None:
    """avahi が無い・引けないときは `None`（例外を投げない）。"""
    import subprocess

    def fail(*args: object, **kwargs: object):
        raise FileNotFoundError("avahi-resolve-host-name が無い")

    monkeypatch.setattr(subprocess, "run", fail)
    assert resolve_lift_host("", "hve-lift") is None


def test_resolve_parses_the_avahi_output(monkeypatch) -> None:
    import subprocess

    class Completed:
        returncode = 0
        stdout = "hve-lift.local\t192.168.5.23\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed())
    assert resolve_lift_host("", "hve-lift") == "192.168.5.23"


def test_resolve_rejects_a_non_ipv4_answer(monkeypatch) -> None:
    import subprocess

    class Completed:
        returncode = 0
        stdout = "hve-lift.local\t Cui bono \n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Completed())
    assert resolve_lift_host("", "hve-lift") is None
