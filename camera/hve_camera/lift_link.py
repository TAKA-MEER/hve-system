"""昇降部（ESP32）への WS クライアント（`/ws/module`）。

[DetailedDesign-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §1・§2。
カメラ部は**クライアント側**（昇降部 ESP32 がサーバ）。
繋いだら `hello`、昇降を動かしている間だけ `hold`、離したら `release` を送る。
**止まっている間は送らない**（protocol §1）。
**上下端の判定はしない**（spec [Spec-safety.md](../../docs/plan/spec/Spec-safety.md) §2・`DD-4`）。

- `LiftPort`: 昇降部への出口の抽象。**`FakeLift`（`hw/fake_lift.py`）も同じものを実装する**ので、
  制御ループは偽物と実物のどちらでも同じ使い方しかしない
- `LiftLink`: 本物。**切れたら繋ぎ直す**。繋がない間は `link_ok()` が `False` になり、
  それが `LINK_LOST` として画面に出る

**時計は外から渡す。**`state` の受信時刻と `LINK_LOST` の判定は、この時計だけを見る。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional, Union

import aiohttp

log = logging.getLogger(__name__)


def _host_of(url: str) -> Optional[str]:
    """`ws://ホスト:ポート/道` からホストだけを抜く。読めなければ `None`。"""
    try:
        rest = url.split("://", 1)[1]
        host = rest.split("/", 1)[0].rsplit(":", 1)[0]
        return host.strip("[]") or None
    except (IndexError, AttributeError):
        return None

#: 上部モジュール用の WS の口。**`params.toml` に置かない**
#: （書き間違えて `/ws/ui` に繋ぐと天井の守りが外れる。DetailedDesign §3.1）。
#: 昇降部の `LIFT_WS_MODULE_PATH` と同じ値。
LIFT_WS_MODULE_PATH = "/ws/module"

#: `hello` の `fw`。表示とログのためだけ（判定に使わない）。
_FW = "0.1.0"

#: 切れてから繋ぎ直しをかけるまでの待ち [s]
_RECONNECT_DELAY_S = 0.5

Clock = Callable[[], float]

#: WS の URL。そのままの文字列か、繋ぐたびに呼び出す関数（引けなければ `None`）。
Url = Union[str, Callable[[], Optional[str]]]


class LiftPort(ABC):
    """昇降部への出口。`LiftLink`（本物の WS）と `FakeLift`（プロセス内の偽物）が実装する。"""

    @abstractmethod
    async def start(self) -> None:
        """昇降部と繋がり始める。**繋がれなくても戻る**（繋ぐのは裏のタスク）。"""

    @abstractmethod
    async def close(self) -> None:
        """繋ぎ直しを止める。"""

    @abstractmethod
    async def send_hold(
        self,
        direction: str,
        duty: int,
        press: int,
        ceiling_status: str,
        ceiling_mm: Optional[int],
        ceiling_age_ms: int,
    ) -> None:
        """昇降部へ `hold` を送る。`ceiling_mm` は `MEASURED` のときだけ値が入る。"""

    @abstractmethod
    async def send_release(self, press: int) -> None:
        """昇降部へ `release` を送る。"""

    @abstractmethod
    def link_ok(self, now_ms: float, timeout_ms: float) -> bool:
        """昇降部から `state` が届いているなら `True`。**`False` の間が `LINK_LOST`。**"""

    @abstractmethod
    def latest_state(self) -> Optional[Dict[str, Any]]:
        """最後に受けた `state`。"""

    @abstractmethod
    def state_received_at_ms(self) -> Optional[float]:
        """最後の `state` を受けた時刻。"""


class LiftLink(LiftPort):
    """昇降部への WS クライアント。切れても再接続し続ける。"""

    def __init__(
        self,
        url: Url,
        clock: Clock,
        *,
        name: str = "hve-cam",
        ceiling_sensor: bool = True,
        state_timeout_ms: float = 600.0,
    ) -> None:
        self._url = url
        #: `state` がこれだけ（実時間）届かなければ、その WS を自分で閉じて繋ぎ直す
        #: （protocol §1。相手の再起動で死んだ TCP は切断の知らせが来ない）。
        self._state_timeout_s = max(float(state_timeout_ms), 1.0) / 1000.0
        self._clock = clock
        #: `hello` の `name`。表示とログのためだけ。
        self._name = name
        #: `hello` の `ceiling_sensor`。**常に `true`**（センサの調子から計算しない。§3.1）。
        self._ceiling_sensor = bool(ceiling_sensor)
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._state: Optional[Dict[str, Any]] = None
        self._state_at_ms: Optional[float] = None
        #: 繋いだ先のホスト（`state.lift.lift_ip` 用。画面に昇降部へのリンクを出す）
        self._remote_host: Optional[str] = None
        #: 受けた `state` の履歴（試験用）
        self.state_history: list = []

    @property
    def current_host(self) -> Optional[str]:
        """今繋いでいる（最後に繋いだ）昇降部のホスト。繋いだことが無ければ `None`。"""
        return self._remote_host

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

    async def send_hold(
        self,
        direction: str,
        duty: int,
        press: int,
        ceiling_status: str,
        ceiling_mm: Optional[int],
        ceiling_age_ms: int,
    ) -> None:
        """`hold` を送る。**繋がっていなければ捨てる**（昇降部は途絶えで止まる）。"""
        ws = self._ws
        if ws is None or ws.closed:
            return
        ceiling: Dict[str, Any] = {
            "status": str(ceiling_status),
            "age_ms": int(ceiling_age_ms),
        }
        if ceiling_status == "MEASURED":
            ceiling["mm"] = int(ceiling_mm) if ceiling_mm is not None else 0
        message = {
            "t": "hold",
            "press": int(press),
            "dir": direction,
            "duty": int(duty),
            "ceiling": ceiling,
        }
        with contextlib.suppress(Exception):
            await ws.send_str(json.dumps(message))

    async def send_release(self, press: int) -> None:
        """`release` を送る。**繋がっていなければ捨てる**（持ち主が空なら止まっている）。"""
        ws = self._ws
        if ws is None or ws.closed:
            return
        with contextlib.suppress(Exception):
            await ws.send_str(json.dumps({"t": "release", "press": int(press)}))

    def link_ok(self, now_ms: float, timeout_ms: float) -> bool:
        if self._state is None or self._state_at_ms is None:
            return False
        return (now_ms - self._state_at_ms) <= timeout_ms

    def latest_state(self) -> Optional[Dict[str, Any]]:
        return dict(self._state) if self._state is not None else None

    def state_received_at_ms(self) -> Optional[float]:
        return self._state_at_ms

    # --- 中身 ---------------------------------------------------------------------------------

    def _resolve_url(self) -> Optional[str]:
        """繋ぐ先の URL。引けなければ `None`（少し待って繋ぎ直す）。"""
        if isinstance(self._url, str):
            return self._url
        try:
            return self._url()
        except Exception as exc:  # noqa: BLE001 - 引けない間も繋ぎ直し続ける
            log.info("昇降部の名前を引けない: %s", exc)
            return None

    def _hello(self) -> str:
        """繋いだら 1 度送る `hello`。`ceiling_sensor` は常に `true`（§3.1）。"""
        return json.dumps(
            {
                "t": "hello",
                "ceiling_sensor": self._ceiling_sensor,
                "name": self._name,
                "fw": _FW,
            }
        )

    async def _run(self) -> None:
        """繋ぎ直すのはず。**接続できない間も例外を投げずに続ける。**"""
        while not self._stopping:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - どんな切断でも再接続を試す
                log.info("昇降部に繋がらない: %s", exc)
            if self._stopping:
                break
            self._ws = None
            await asyncio.sleep(_RECONNECT_DELAY_S)

    async def _connect_once(self) -> None:
        url = self._resolve_url()
        if url is None:
            await asyncio.sleep(_RECONNECT_DELAY_S)
            return
        if self._session is None:
            self._session = aiohttp.ClientSession()
        async with self._session.ws_connect(url) as ws:
            self._ws = ws
            self._remote_host = _host_of(url)
            log.info("昇降部 %s に繋がった", url)
            with contextlib.suppress(Exception):
                await ws.send_str(self._hello())
            loop = asyncio.get_event_loop()
            last_state = loop.time()
            try:
                while True:
                    remaining = last_state + self._state_timeout_s - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    message = await ws.receive(timeout=remaining)
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        if message.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            return
                        continue
                    if self._accept(message.data):
                        last_state = loop.time()
            except asyncio.TimeoutError:
                log.info("昇降部から state が %.0f ms 届かない。繋ぎ直す", self._state_timeout_s * 1000)
                # hold を死んだ口へ送らない。先に外す。
                self._ws = None
                await self._abandon(ws)

    @staticmethod
    async def _abandon(ws: aiohttp.ClientWebSocketResponse) -> None:
        """死んだ口を閉じる。**閉じの握手を待たない**（死んだ相手は答えないので）。"""
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.close(), 1.0)
        # 握手待ちを打ち切ると接続が残ることがあるので、下の層も閉じる
        with contextlib.suppress(Exception):
            ws._response.close()  # type: ignore[attr-defined]

    def _accept(self, raw: str) -> bool:
        """`state` を受けて、受信時刻を**この時計**で記録する。読めないものは捨てる。

        `state` として受けたら `True`。"""
        try:
            data = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(data, dict) or data.get("t") != "state":
            return False
        self._state = data
        self._state_at_ms = self._clock()
        self.state_history.append(dict(data))
        return True
