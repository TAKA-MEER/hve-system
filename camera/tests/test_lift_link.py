"""`lift_link.py` v2 と `lift_resolve.py` の試験。**本物の WS 経路**を通す。"""

from __future__ import annotations

import pytest
from collections import deque
from typing import Any, List

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
    """`hold`・`release` の形（protocol §2.1）。速さは `speed` [mm/s] で送り、`duty` は送らない。"""
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
        assert (hold["press"], hold["dir"], hold["speed"]) == (7, "up", 30)
        assert "duty" not in hold, "duty を送ると昇降部が捨てる（変異 a の芯）"
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


async def test_slow_resolver_does_not_block_the_loop() -> None:
    """名前解決が遅い間もイベントループが回る。本番の `_connect_once` の経路で確かめる。

    実機 2026-10-15: `avahi-resolve-host-name` を同期で待って UART の読み出し・WS・
    制御ループまで止まった。**`run_in_executor` を外して同期で呼ぶと赤になる。**
    """
    import asyncio
    import contextlib
    import time

    clock = ManualClock()

    def slow_resolve():
        time.sleep(0.5)
        # すぐ失敗する先。名前解決の後の待ちを混ぜず、解決中の回りだけを測る。
        return "ws://127.0.0.1:1/ws/module"

    lift = LiftLink(slow_resolve, clock)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.05)
            ticks += 1

    try:
        connect = asyncio.create_task(lift._connect_once())
        counting = asyncio.create_task(ticker())
        with contextlib.suppress(Exception):
            await connect
        during = ticks
        await counting
        assert during >= 5, f"名前解決の 0.5 s の間にループが {during} 回しか回らなかった"
    finally:
        await lift.close()


async def test_silent_peer_is_dropped_and_reconnected_with_hello() -> None:
    """相手が TCP を閉じずに `state` だけ止めたら、自分で閉じて繋ぎ直し、`hello` を送り直す。

    実機 2026-10-07: 昇降部の再起動で古い TCP が知らせ無しに死に、永久に `LINK_LOST` だった。
    **受信タイムアウトを外す（永久に待つ）と赤になる。**
    """
    clock = ManualClock()
    esp32 = FakeEsp32(clock)
    url = await esp32.start()
    lift = LiftLink(url, clock, state_timeout_ms=300)
    try:
        await lift.start()
        await wait_until(lambda: len(esp32.hello_history) >= 1)
        await wait_until(lambda: lift.latest_state() is not None)
        esp32.silent = True  # 閉じずに黙る
        await wait_until(lambda: len(esp32.hello_history) >= 2, timeout=5.0)
        # 黙っている間の hold は死んだ口へ出さない（繋ぎ直しの前後どちらでも例外は出ない）
        esp32.silent = False
        before = len(esp32.state_history)
        await wait_until(lambda: len(esp32.state_history) > before + 2)
        n = len(esp32.hold_history)
        await lift.send_hold("up", 30, 1, "MEASURED", 2000, 0)
        await wait_until(lambda: len(esp32.hold_history) > n)
        assert len(esp32.hello_history) == 2
    finally:
        await lift.close()
        await esp32.stop()


def _retained_states(root: Any) -> int:
    """`root` から入れ物（dict・list・tuple・set・deque・`__dict__`）をたどって届く `state` の dict の数。"""
    seen = set()
    stack: List[Any] = [root]
    count = 0
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, dict):
            if obj.get("t") == "state":
                count += 1
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset, deque)):
            stack.extend(obj)
        elif hasattr(obj, "__dict__") and type(obj).__module__.startswith("hve_camera"):
            stack.extend(vars(obj).values())
    return count


async def test_state_is_not_accumulated() -> None:
    """受けた `state` を件数に比例して溜めない（WP-CAM-08。実機で RSS が約 58 MB/時で増えた）。

    **`_accept` で受けた `state` をリスト・deque に足す変異で赤になる。**
    """
    clock = ManualClock()
    esp32 = FakeEsp32(clock, state_period_ms=1)
    url = await esp32.start()
    lift = LiftLink(url, clock)
    try:
        await lift.start()
        await wait_until(lambda: len(esp32.state_history) >= 1500, timeout=10.0)
        await wait_until(lambda: lift.latest_state() is not None)
        assert _retained_states(lift) <= 2
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
