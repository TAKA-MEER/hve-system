"""`WP-CAM-02` の試験で共有する道具。

ファイル名が `test_` で始まらないので pytest には収集されず、他のモジュールを
`from tests.cam_support import ...` として読み込んで使う。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Any

import aiohttp
from aiohttp import web

from hve_camera.ceiling import CeilingStatus
from hve_camera.hw.fake_hw import FakeHardware
from hve_camera.hw.fake_lift import FakeLift

#: 制御ループの試験で使う設定。値は DetailedDesign §3 の例に合わせる。
SETTINGS: dict[str, dict[str, float]] = {
    "lift_up": {"min": 10, "max": 60, "init": 30},
    "lift_down": {"min": 10, "max": 60, "init": 30},
    "pitch": {"min": 1, "max": 30, "init": 10},
    "yaw": {"min": 1, "max": 30, "init": 10},
}

#: 試験が使う params.toml の部分。値は DetailedDesign-names.md §5 に合わせる。
PARAMS: dict[str, Any] = {
    "ceiling_margin_mm": 500,
    "ceiling_stale_ms": 600,
    "hold_timeout_ms": 400,
    "lift_cmd_period_ms": 100,
    "lift_state_timeout_ms": 600,
    "state_period_ms": 100,
    "axis_speed_abs_max_dps": 60,
    "yaw_steps_per_rev": 4096,
    "srf02_min_range_mm": 150,
    "srf02_max_range_mm": 6000,
    "srf02_i2c_addr": 0x70,
    "srf02_ranging_wait_ms": 70,
    "pitch_min_deg": -45,
    "pitch_max_deg": 45,
    "zoom_max": 4,
    "zoom_step": 0.5,
    "lift_ws_url": "ws://hve-lift.local/ws",
    "video_port": 8080,
    "provisional": ["ceiling_margin_mm", "ceiling_stale_ms"],
}


class ManualClock:
    """試験で自分で進める時計。`ControlLoop` と同じく ms を返す。"""

    def __init__(self, start_ms: float = 0.0) -> None:
        self.now_ms = float(start_ms)

    def __call__(self) -> float:
        return self.now_ms

    def advance(self, ms: float) -> float:
        self.now_ms += float(ms)
        return self.now_ms


async def wait_until(
    predicate: Callable[[], bool], timeout: float = 2.0, interval: float = 0.01
) -> None:
    """`predicate` が真になるまで待つ。時間切れなら AssertionError。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("待っている条件が 2 秒以内に真にならなかった")


class RecordingVideoZoom:
    """`VideoZoom` の偽物。受け取った倍率を順番どおりに覚える。"""

    def __init__(self, deliver: bool = True) -> None:
        self.sent: list[float] = []
        #: 送れなかった倍率。`VideoZoom` と同じ。
        self.pending: float | None = None
        #: `False` にすると送れない。`hve_video` が居ないときを再現する。
        self.deliver = deliver
        self.closed = False

    async def send_zoom(self, level: float) -> bool:
        self.sent.append(float(level))
        if self.deliver:
            self.pending = None
            return True
        self.pending = float(level)
        return False

    async def close(self) -> None:
        self.closed = True


class FakeEsp32:
    """偽の ESP32 の WS サーバ。`cmd` を受けて覚えられる。

    aiohttp で立てる本物の WS なので `LiftLink` は本物の経路を通る
    （DetailedDesign §4.2「偽 ESP32 サーバ」）。
    """

    def __init__(self, clock: Callable[[], float], state_period_ms: int = 50) -> None:
        self._clock = clock
        self._state_period_ms = state_period_ms
        self.cmd_history: list[dict[str, Any]] = []
        self.state_history: list[dict[str, Any]] = []
        self.height_mm = 0
        self.bottom = False
        self.top_mm: int | None = None
        #: `True` にすると `state` を出し止める。途絶えを再現する。
        self.silent = False
        self._ws: web.WebSocketResponse | None = None
        self._runner: web.AppRunner | None = None
        self._task: asyncio.Task[None] | None = None
        self._cmd_at_ms: list[float] = []
        self._seq = 0

    @property
    def last_cmd(self) -> dict[str, Any] | None:
        return self.cmd_history[-1] if self.cmd_history else None

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def start(self) -> str:
        """立ち上げて `ws://` の URL を返す。"""
        app = web.Application()
        app.add_routes([web.get("/ws", self._handler)])
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        assert self._runner is not None
        host, port = self._runner.addresses[0][:2]
        self._task = asyncio.create_task(self._send_states())
        return f"ws://{host}:{port}/ws"

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._ws = None

    async def _handler(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws = ws
        async for message in ws:
            if message.type is aiohttp.WSMsgType.TEXT:
                self._accept(message.data)
        self._ws = None
        return ws

    def _accept(self, raw: str) -> None:
        """`cmd` を受けて覚える。読めないものは無視する。"""
        with contextlib.suppress(ValueError):
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("t") == "cmd":
                self.cmd_history.append(data)
                self._cmd_at_ms.append(self._clock())

    async def _send_states(self) -> None:
        """`LIFT_STATE_PERIOD_MS` ごとに `state` を出す。"""
        period = self._state_period_ms / 1000.0
        while True:
            await asyncio.sleep(period)
            ws = self._ws
            if ws is None or ws.closed or self.silent:
                continue
            last = self.cmd_history[-1] if self.cmd_history else None
            self._seq += 1
            if last is None:
                reason, direction, duty = "NONE", "stop", 0
                cmd_age_ms = 0
            else:
                direction, duty = last["dir"], last["duty"]
                reason = "CMD_STOP" if direction == "stop" else "NONE"
                cmd_age_ms = int(self._clock() - self._cmd_at_ms[-1])
            state = {
                "t": "state",
                "seq": self._seq,
                "dir": direction,
                "duty": duty,
                "reason": reason,
                "bottom": self.bottom,
                "height_mm": self.height_mm,
                "height_ok": True,
                "top_mm": self.top_mm,
                "cmd_age_ms": cmd_age_ms,
                "fw": "fake-esp32-0.1.0",
            }
            self.state_history.append(state)
            with contextlib.suppress(Exception):
                await ws.send_str(json.dumps(state))


def make_fake_hw(clock: Callable[[], float], distance_mm: int = 2000) -> FakeHardware:
    """天井が遠いところにある偽のハードウェア。毎回新しい測定値を返す。"""
    hw = FakeHardware(clock)
    hw.set_ceiling(CeilingStatus.MEASURED, distance_mm)
    return hw


def make_fake_lift(clock: Callable[[], float], top_mm: int | None = None) -> FakeLift:
    """偽の昇降部。上端の閾値は未設定（DetailedDesign-names.md §5 の `LIFT_TOP_MM`）。"""
    return FakeLift(clock, top_mm=top_mm)
