"""`WP-CAM-04` の試験で共有する道具。

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
    "hold_timeout_ms": 400,
    "lift_cmd_period_ms": 100,
    "lift_state_timeout_ms": 600,
    "state_period_ms": 100,
    "axis_speed_abs_max_dps": 60,
    "yaw_steps_per_rev": 4096,
    "srf02_min_range_mm": 150,
    "srf02_max_range_mm": 6000,
    "pitch_min_deg": -45,
    "pitch_max_deg": 45,
    "zoom_max": 4,
    "zoom_step": 0.5,
    "lift_host": "",
    "lift_mdns_name": "hve-lift",
    "lift_port": 80,
    "module_name": "hve-cam",
    "module_ceiling_sensor": True,
    "io_device": "/dev/ttyS1",
    "io_baud": 115200,
    "io_cmd_period_ms": 50,
    "io_lost_ms": 600,
    "ceiling_read_stale_ms": 600,
    "uno_clock_window": 20,
    "video_port": 8080,
    "provisional": ["hold_timeout_ms", "lift_cmd_period_ms"],
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
    """偽の昇降部の WS サーバ。v2 の取り決め（`hello`・`hold`・`release`・`state`）を話す。

    aiohttp で立てる本物の WS なので `LiftLink` は本物の経路を通る。
    設定 API（`/api/settings`）も出し、設定の中継の試験にも使う。
    """

    def __init__(self, clock: Callable[[], float], state_period_ms: int = 50) -> None:
        self._clock = clock
        self._state_period_ms = state_period_ms
        self.hello_history: list[dict[str, Any]] = []
        self.hold_history: list[dict[str, Any]] = []
        self.release_history: list[dict[str, Any]] = []
        self.state_history: list[dict[str, Any]] = []
        #: 昇降の設定（`GET /api/settings` が返す・`PUT /api/settings` が置き換える）
        self.lift_settings: dict[str, dict[str, float]] = {
            "lift_up": {"min": 10, "max": 60, "init": 30},
            "lift_down": {"min": 10, "max": 60, "init": 30},
        }
        #: `True` にすると `PUT /api/settings` を `400` で返す（中継の変異 5 用）
        self.fail_put = False
        #: `True` にすると `state` を出し止める。途絶えを再現する。
        self.silent = False
        self._ws: web.WebSocketResponse | None = None
        self._runner: web.AppRunner | None = None
        self._task: asyncio.Task[None] | None = None
        self._seq = 0

    @property
    def holds(self) -> list[dict[str, Any]]:
        return self.hold_history

    @property
    def last_hold(self) -> dict[str, Any] | None:
        return self.hold_history[-1] if self.hold_history else None

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def start(self) -> str:
        """立ち上げて `/ws/module` の URL を返す。"""
        app = web.Application()
        app.add_routes(
            [
                web.get("/ws/module", self._handler),
                web.get("/api/settings", self._get_settings),
                web.put("/api/settings", self._put_settings),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        assert self._runner is not None
        host, port = self._runner.addresses[0][:2]
        self._task = asyncio.create_task(self._send_states())
        return f"ws://{host}:{port}/ws/module"

    @property
    def http_base(self) -> str:
        """設定 API の起点（`http://127.0.0.1:ポート`）。`start()` のあとに使う。"""
        assert self._runner is not None
        host, port = self._runner.addresses[0][:2]
        return f"http://{host}:{port}"

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
        """`hello`・`hold`・`release` を受けて覚える。読めないものは無視する。"""
        with contextlib.suppress(ValueError):
            data = json.loads(raw)
            if not isinstance(data, dict):
                return
            kind = data.get("t")
            if kind == "hello":
                self.hello_history.append(data)
            elif kind == "hold":
                self.hold_history.append(data)
            elif kind == "release":
                self.release_history.append(data)

    async def _get_settings(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"settings": self.lift_settings, "using_defaults": False}
        )

    async def _put_settings(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"errors": ["JSON が読めない"]}, status=400)
        if self.fail_put:
            return web.json_response({"errors": ["lift_up.min: だめ"]}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"errors": ["オブジェクトでない"]}, status=400)
        for axis in ("lift_up", "lift_down"):
            entry = data.get(axis)
            if not isinstance(entry, dict):
                return web.json_response({"errors": [f"{axis}: 無い"]}, status=400)
            self.lift_settings[axis] = {
                key: entry[key] for key in ("min", "max", "init") if key in entry
            }
        return web.json_response({"settings": self.lift_settings, "using_defaults": False})

    async def _send_states(self) -> None:
        """`state` を v2 の形で出す（持ち主なし・天井なしの止まった状態）。"""
        period = self._state_period_ms / 1000.0
        while True:
            await asyncio.sleep(period)
            ws = self._ws
            if ws is None or ws.closed or self.silent:
                continue
            self._seq += 1
            state = {
                "t": "state",
                "seq": self._seq,
                "dir": "stop",
                "duty": 0,
                "reason": "CMD_STOP",
                "bottom": False,
                "height_mm": 0,
                "height_ok": True,
                "top_detect": False,
                "ceiling": {
                    "used": False,
                    "status": "MISSING",
                    "mm": None,
                    "age_ms": 0,
                    "ok": False,
                    "reason": "CEILING_STALE",
                },
                "owner": None,
                "ui_clients": 0,
                "module": {
                    "connected": True,
                    "ceiling_sensor": True,
                    "ip": "127.0.0.1",
                    "name": "hve-cam",
                },
                "provisional": [],
                "fw": "fake-esp32-0.2.0",
            }
            self.state_history.append(state)
            with contextlib.suppress(Exception):
                await ws.send_str(json.dumps(state))


def make_fake_hw(clock: Callable[[], float], distance_mm: int = 2000) -> FakeHardware:
    """天井が遠いところにある偽のハードウェア。毎回新しい測定値を返す。"""
    hw = FakeHardware(clock)
    hw.set_ceiling(CeilingStatus.MEASURED, distance_mm)
    return hw


def make_fake_lift(clock: Callable[[], float]) -> FakeLift:
    """偽の昇降部（プロセス内）。"""
    return FakeLift(clock)
