"""昇降部（ESP32）への WS クライアント。

[DetailedDesign-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §1・§2.2・§2.3。
カメラ部は**クライアント側**（昇降部 ESP32 がサーバ）。`cmd` を送り、`state` を受けるだけ。
**上下端の判定はしない**（spec [Spec-safety.md](../../docs/plan/spec/Spec-safety.md) §2・`DD-4`）。

- `LiftPort`: 昇降部への出口の抽象。**`FakeLift`（`hw/fake_lift.py`）も同じものを実装する**ので、
  制御ループは偽物と実物のどちらでも同じ 5 つしか使わない
- `LiftLink`: 本物。**切れたら繋ぎ直す**。繋がない間は `link_ok()` が `False` になり、
  それが `LINK_LOST` として画面に出る（protocol §1「昇降部: 最後の `cmd` から
  `LIFT_CMD_TIMEOUT_MS` 超で停止」）

**時計は外から渡す。**`state` の受信時刻と `LINK_LOST` の判定は、この時計だけを見る。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable

import aiohttp

log = logging.getLogger(__name__)

#: 切れてから繋ぎ直しをかけるまでの待ち [s]
_RECONNECT_DELAY_S = 0.5

Clock = Callable[[], float]


class LiftPort(ABC):
    """昇降部への出口。`LiftLink`（本物の WS）と `FakeLift`（プロセス内の偽物）が実装する。"""

    @abstractmethod
    async def start(self) -> None:
        """昇降部と繋がり始める。**繋がれなくても戻る**（繋ぐのは裏のタスク）。"""

    @abstractmethod
    async def close(self) -> None:
        """繋ぎ直しを止める。"""

    @abstractmethod
    async def send_cmd(self, direction: str, duty: int, ceil_ok: bool) -> None:
        """昇降部へ `cmd` を送る。`seq` は実装側で数える。"""

    @abstractmethod
    def link_ok(self, now_ms: float, timeout_ms: float) -> bool:
        """昇降部から `state` が届いているなら `True`。**`False` の間が `LINK_LOST`。**"""

    @abstractmethod
    def latest_state(self) -> dict[str, Any] | None:
        """最後に受けた `state`。"""

    @abstractmethod
    def state_received_at_ms(self) -> float | None:
        """最後の `state` を受けた時刻。"""


class LiftLink(LiftPort):
    """昇降部への WS クライアント。切れても再接続し続ける。"""

    def __init__(self, url: str, clock: Clock) -> None:
        self._url = url
        self._clock = clock
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._seq = 0
        self._state: dict[str, Any] | None = None
        self._state_at_ms: float | None = None
        #: 受けた `state` の履歴（試験用）
        self.state_history: list[dict[str, Any]] = []

    @property
    def connected(self) -> bool:
        """今 WS が繋がっているか。**`link_ok` とは別**（`state` が新しいかも見る）"""
        return self._ws is not None and not self._ws.closed

    async def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._stopping = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.close()
            self._session = None

    async def send_cmd(self, direction: str, duty: int, ceil_ok: bool) -> None:
        """`cmd` を送る。**繋がっていなければ捨てる**（昇降部は `LIFT_CMD_TIMEOUT_MS` で止まる）。"""
        ws = self._ws
        if ws is None or ws.closed:
            return
        self._seq += 1
        message = {
            "t": "cmd",
            "seq": self._seq,
            "dir": direction,
            "duty": int(duty),
            "ceil_ok": bool(ceil_ok),
        }
        with contextlib.suppress(Exception):
            await ws.send_str(json.dumps(message))

    def link_ok(self, now_ms: float, timeout_ms: float) -> bool:
        if self._state is None or self._state_at_ms is None:
            return False
        return (now_ms - self._state_at_ms) <= timeout_ms

    def latest_state(self) -> dict[str, Any] | None:
        return dict(self._state) if self._state is not None else None

    def state_received_at_ms(self) -> float | None:
        return self._state_at_ms

    # --- 中身 ---------------------------------------------------------------------------------

    async def _run(self) -> None:
        """繋ぎ直すのはず。**接続できない間も例外を投げずに続ける。**"""
        while not self._stopping:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - どんな切断でも再接続を試す
                log.info("昇降部 %s に繋がらない: %s", self._url, exc)
            if self._stopping:
                break
            self._ws = None
            await asyncio.sleep(_RECONNECT_DELAY_S)

    async def _connect_once(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        async with self._session.ws_connect(self._url) as ws:
            self._ws = ws
            log.info("昇降部 %s に繋がった", self._url)
            async for message in ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    continue
                self._accept(message.data)

    def _accept(self, raw: str) -> None:
        """`state` を受けて、受信時刻を**この時計**で記録する。読めないものは捨てる。"""
        try:
            data = json.loads(raw)
        except ValueError:
            return
        if not isinstance(data, dict) or data.get("t") != "state":
            return
        self._state = data
        self._state_at_ms = self._clock()
        self.state_history.append(dict(data))
