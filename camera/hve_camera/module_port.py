"""ss-system の口 `/ws/module` の中身（`hello`・`aim`・`release`・持ち主・`state`）。

[DetailedDesign-protocol-ss.md](../../docs/plan/detailed/DetailedDesign-protocol-ss.md) §1〜§3・§5.1。
**昇降部の口（[-protocol-modules.md](../../docs/plan/detailed/DetailedDesign-protocol-modules.md) §1.1・§2.2）と同じ形**:
`hello` で名乗る・`press` で持ち主を決める・送り続けている間だけ続く・`state` で結果を返す。

## ここで守ること

1. **持ち主は画面（`/ws` の `hold`）と `/ws/module` の `aim` で 1 つ。規則は `press`**（最後の `press` が勝つ）。
   画面が押せば取って代わり、`aim` は `PREEMPTED`（`preempt_by_ui()`）。
2. **着いたら `ARRIVED` にして持ち主を離す。**そのあと同じ `press` では動かない（`press` が同じなら捨てる）。
3. **ピッチは範囲の外を `TARGET_RANGE` で受け付けない**（丸めない。ss は目標角を実際の角度として使う）。
   範囲の中なら、片寄せの**戻りの指令を出してから見積もった待ち時間が過ぎたら**着いたとみなす
   （正しい向きから来たときも見積もりだけ待つ。DetailedDesign §4.3.1）。
4. **ヨーは原点を合わせる前は `YAW_UNKNOWN` で受け付けない。**近い回り方で `yaw_dps` の速さで回し、
   `aim_yaw_tol_deg` 以内で止める。目標に近づくと 1 周期で目標を越えない速さに絞る。
5. **途切れ（`aim_cmd_timeout_ms`）は `CMD_TIMEOUT`・`release` は `CMD_STOP`・接続が閉じたら `OWNER_GONE`・
   Arduino から行が来なければ `IO_LOST`。**どれも持ち主を離し、ヨーを止める（ピッチはその目標角を保つ）。
6. **`role: observe` からの `aim`・`release` は捨てる。**
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Mapping, Optional

from aiohttp import web

from hve_camera.aim import YawTracker, decode_aim, decode_hello, shortest_delta_deg
from hve_camera.control import ControlLoop

log = logging.getLogger(__name__)

#: 時計の型。ms を返す。
Clock = Callable[[], float]


class ModuleConn:
    """`/ws/module` の 1 本の接続。"""

    def __init__(self, ws: web.WebSocketResponse, conn_id: int) -> None:
        self.ws = ws
        self.id = conn_id
        self.name = ""
        self.role = "control"
        self.operator = "human"
        self.busy_label: Optional[str] = None
        self.fw: Optional[str] = None
        self.hello_seen = False
        #: この接続で最後に受け付けた `press`（これより小さいものは捨てる）
        self.last_press: Optional[int] = None

    @property
    def display_name(self) -> str:
        """名前が無いときは `anon-<接続番号>`（同じ名前の置き換えの対象にしない）。"""
        return self.name or "anon-%d" % self.id


class ModulePort:
    """`/ws/module` の接続と、`aim` の持ち主・進み具合を持つ。"""

    def __init__(
        self,
        *,
        control: ControlLoop,
        yaw: YawTracker,
        params: Mapping[str, Any],
        settings: Mapping[str, Any],
        clock: Clock,
        touch: Callable[[], Awaitable[None]],
        release_ui: Callable[[], None],
    ) -> None:
        self._control = control
        self._yaw = yaw
        self._params = params
        self._settings = settings
        self._clock = clock
        self._touch = touch
        self._release_ui = release_ui

        self.conns: dict[web.WebSocketResponse, ModuleConn] = {}
        self._next_id = 1
        #: 持ち主（`aim` を受け付けた接続）と、その `press`・最後に届いた時刻
        self.owner: Optional[ModuleConn] = None
        self._owner_press = 0
        self._owner_at_ms = 0.0
        #: 動かしている最中の `aim`
        self._live = False
        self._t_pitch: Optional[float] = None
        self._t_yaw: Optional[float] = None
        #: 最後の `aim` の記録（`state.aim`）。起動してから無ければ `None`
        self.record: Optional[dict] = None

    # --- 接続 -------------------------------------------------------------------------------------

    def add_conn(self, ws: web.WebSocketResponse) -> Optional[ModuleConn]:
        """接続を足す。**上限（`module_ws_max_conn`）を超えたら `None`**（その接続は閉じる。古い方を閉じない）。"""
        if len(self.conns) >= int(self._params["module_ws_max_conn"]):
            return None
        conn = ModuleConn(ws, self._next_id)
        self._next_id += 1
        self.conns[ws] = conn
        return conn

    async def remove_conn(self, conn: ModuleConn) -> None:
        """接続を外す。**持ち主だったら止めて `OWNER_GONE`。**"""
        self.conns.pop(conn.ws, None)
        if self.owner is conn:
            await self._lose("OWNER_GONE")

    async def on_message(self, conn: ModuleConn, raw: str) -> None:
        """`hello`・`aim`・`release` を受ける。**読めないものは捨てる。**"""
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("読めない JSON なので無視した: %r", raw)
            return
        if not isinstance(data, dict):
            return
        kind = data.get("t")
        if kind == "hello":
            await self._on_hello(conn, data)
        elif kind == "aim":
            await self._on_aim(conn, data)
        elif kind == "release":
            await self._on_release(conn, data)
        else:
            log.warning("知らない種類のメッセージなので無視した: %r", kind)

    async def _on_hello(self, conn: ModuleConn, body: dict) -> None:
        """名乗り。**2 度目以降は無視する。同じ名前の別の接続があれば古い方を閉じる。**"""
        if conn.hello_seen:
            return
        info = decode_hello(body)
        conn.hello_seen = True
        conn.name = info["name"]
        conn.role = info["role"]
        conn.operator = info["operator"]
        conn.busy_label = info["busy_label"]
        conn.fw = info["fw"]
        if not conn.name:
            return
        for other in list(self.conns.values()):
            if other is not conn and other.hello_seen and other.name == conn.name:
                await self.remove_conn(other)
                try:
                    await other.ws.close()
                except Exception:  # noqa: BLE001 - 古い接続は切れていてもよい
                    pass

    # --- aim・release ------------------------------------------------------------------------------

    async def _on_aim(self, conn: ModuleConn, body: dict) -> None:
        if conn.role == "observe":
            return  # 状態を読むだけの接続からは命じられない
        aim = decode_aim(body)
        if aim is None:
            log.warning("読めない aim を捨てた: %r", body)
            return
        # 操作に数える（省電力の間なら戻す。ピッチの力が戻ってから動く）
        await self._touch()
        now = self._clock()
        if not await self._claim(conn, aim["press"], now):
            return

        pitch, yaw = aim["pitch_deg"], aim["yaw_deg"]
        self._t_pitch, self._t_yaw = pitch, yaw
        reason = self._reject_reason(pitch, yaw)
        if reason is not None:
            if self._live:
                await self._end_live(reason)  # 送り続ける途中で受け付けない値に替わった
            else:
                self.record = self._make_record(conn, aim["press"], "NOT_ARRIVED", reason)
            return

        if not self._live:
            self._live = True
        self.record = self._make_record(conn, aim["press"], "MOVING", "NONE")
        if pitch is not None:
            # 同じ目標を繰り返し送っても片寄せはやり直さない（向きが分かったあと）。
            # 起動したときなど向きが分からない間は、同じ角度の `aim` でも片寄せする
            await self._control.set_pitch_target(pitch)
        await self._progress()

    def _reject_reason(self, pitch: Optional[float], yaw: Optional[float]) -> Optional[str]:
        """受け付けない理由。**丸めない**（範囲の外は `TARGET_RANGE`・原点の前の向きは `YAW_UNKNOWN`）。"""
        if pitch is not None and not (
            float(self._params["pitch_min_deg"]) <= pitch <= float(self._params["pitch_max_deg"])
        ):
            return "TARGET_RANGE"
        if yaw is not None and not (-180.0 <= yaw < 180.0):
            return "TARGET_RANGE"
        if yaw is not None and not self._yaw.zeroed():
            return "YAW_UNKNOWN"
        return None

    async def _claim(self, conn: ModuleConn, press: int, now: float) -> bool:
        """持ち主の規則（昇降部の `hold`・`goto` と同じ）。受け付けたら `True`。"""
        last = conn.last_press
        if last is not None and press < last:
            return False
        if last is None or press > last:
            conn.last_press = press
            if self.owner is not None or self._live:
                await self._end_live("PREEMPTED")  # 新しい `press` が取って代わった
            # 画面が押していたら取って代わる（最後に押したものが勝つ）
            self._release_ui()
            self.owner = conn
            self._owner_press = press
            self._owner_at_ms = now
            return True
        if self.owner is conn and self._owner_press == press:
            self._owner_at_ms = now
            return True
        return False  # 着いたあと・止まったあとの同じ `press` は動かさない

    async def _on_release(self, conn: ModuleConn, body: dict) -> None:
        if conn.role == "observe":
            return
        press = body.get("press")
        if not isinstance(press, int) or isinstance(press, bool):
            return
        if self.owner is conn and self._owner_press == press:
            await self._lose("CMD_STOP")

    async def preempt_by_ui(self) -> None:
        """画面が押した・ヨーの原点を合わせた。**持ち主の `aim` は `PREEMPTED`。**"""
        if self.owner is not None:
            await self._lose("PREEMPTED")

    # --- 制御ループ 1 回 ----------------------------------------------------------------------------

    async def step(self) -> None:
        """途切れ・Arduino の行・ヨーの進み具合を見る。**`control.step()` のあとに呼ぶ**（行を読んだあと）。"""
        if self.owner is None:
            return
        now = self._clock()
        if now - self._owner_at_ms > float(self._params["aim_cmd_timeout_ms"]):
            await self._lose("CMD_TIMEOUT")
            return
        if self._live and not self._control.io_ok(now):
            await self._lose("IO_LOST")
            return
        await self._progress()

    async def _progress(self) -> None:
        """ヨーを目標へ向ける。両軸が着いたら `ARRIVED` にして持ち主を離す。

        ピッチは戻りの指令を出してから見積もった待ち時間が過ぎたら着いたとみなす
        （正しい向きから来たときも見積もりだけ待つ）。
        """
        if not self._live:
            return
        done = True
        if self._t_pitch is not None and not self._control.pitch_arrived(self._clock()):
            done = False
        if self._t_yaw is not None:
            current = self._yaw.deg()
            if current is None:
                # 回している最中に原点が無くなった（UNO の再起動）
                await self._lose("YAW_UNKNOWN")
                return
            delta = shortest_delta_deg(current, self._t_yaw)
            if abs(delta) <= float(self._params["aim_yaw_tol_deg"]):
                await self._control.stop_aim_yaw()
            else:
                done = False
                period_s = float(self._params["lift_cmd_period_ms"]) / 1000.0
                rate = min(float(self._settings["yaw"]["init"]), abs(delta) / period_s)
                await self._control.drive_aim_yaw(rate if delta > 0 else -rate)
        if done:
            self._live = False
            if self.record is not None:
                self.record = dict(self.record, status="ARRIVED", reason="NONE")
            self.owner = None  # 着いたら持ち主を離す
            await self._control.stop_aim_yaw()

    async def _lose(self, reason: str) -> None:
        """持ち主を離す。動かしていたなら `NOT_ARRIVED` と理由を残し、ヨーを止める。"""
        await self._end_live(reason)
        self.owner = None

    async def _end_live(self, reason: str) -> None:
        if not self._live:
            return
        self._live = False
        if self.record is not None:
            self.record = dict(self.record, status="NOT_ARRIVED", reason=reason)
        await self._control.stop_aim_yaw()

    def _make_record(self, conn: ModuleConn, press: int, status: str, reason: str) -> dict:
        return {
            "by": conn.display_name,
            "press": press,
            "pitch_deg": self._t_pitch,
            "yaw_deg": self._t_yaw,
            "status": status,
            "reason": reason,
        }

    # --- state ------------------------------------------------------------------------------------

    def busy_label(self) -> Optional[str]:
        """持ち主が `/ws/module` の接続のときの、画面に出す文字（`hello` の `busy_label`。無ければ名前）。"""
        if self.owner is None:
            return None
        return self.owner.busy_label or self.owner.display_name

    async def broadcast(self, state: dict) -> None:
        """`state` を全接続へ。切れた接続は外す。"""
        payload = json.dumps(state, ensure_ascii=False)
        for conn in list(self.conns.values()):
            try:
                await conn.ws.send_str(payload)
            except Exception:  # noqa: BLE001 - 切れた接続は一覧から外す
                await self.remove_conn(conn)
