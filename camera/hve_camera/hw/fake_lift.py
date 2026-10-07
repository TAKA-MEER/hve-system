"""プロセス内の偽の昇降部（偽物のモード `--fake` 用）。

[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §3・§4.3 の「偽物のモード」。
v2 の取り決め（`hello` はプロセス内なので不要・`hold`・`release`・`state`）を話し、
昇降部側の規則（持ち主・天井・途絶え）を同じように真似る。
**本物の判定は ESP32 側**（`firmware/lift`）で、ここは画面と判定の経路を
機器なしで確かめるためのもの。

使う側（`hve_camera.control`）は `FakeLift` と `LiftLink` の違いを知らない。
`LiftPort` の口だけを実装している。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Callable, Dict, List, Optional

from hve_camera.lift_link import LiftPort

#: 昇降部の定数（`lift_core` と同じ値。**偽物でしか使わない。**本物はファームウェア側にある）
LIFT_CMD_TIMEOUT_MS = 600
LIFT_MAX_RUN_MS = 10000
LIFT_DUTY_ABS_MAX_PCT = 100
LIFT_STATE_PERIOD_MS = 100
CEILING_MARGIN_MM = 500
CEILING_STALE_MS = 600

#: 偽の高さの進む速さ [mm/s per 1 % デューティ]。**画面を動かすための当てずっぽう**
_MM_PER_PCT_S = 2.5

Clock = Callable[[], float]


def fake_ceiling_verdict(status: str, mm: Optional[int], age_ms: int) -> tuple:
    """天井の値 → `(ok, 理由)`。昇降部の `ceiling_check` の真似（§3.2）。

    `NO_ECHO`（反射なし）は許し、理由は `OUT_OF_RANGE`。
    """
    if status == "NO_ECHO":
        return True, "OUT_OF_RANGE"
    if status == "MEASURED" and mm is not None and mm > CEILING_MARGIN_MM and age_ms <= CEILING_STALE_MS:
        return True, "NONE"
    if status == "MEASURED" and mm is not None and mm <= CEILING_MARGIN_MM:
        return False, "CEILING_NEAR"
    if status == "TOO_NEAR":
        return False, "CEILING_NEAR"
    return False, "CEILING_STALE"


class FakeLift(LiftPort):
    """プロセス内で動く偽の昇降部。`hold` を受けて高さを動かし、`state` を作る。"""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        #: 持ち主の `press` と、前に見た最大の `press`
        self._owner_press: Optional[int] = None
        self._seen_press = 0
        self._cmd: Dict[str, Any] = {"dir": "stop", "duty": 0}
        self._cmd_at_ms = clock()
        self._ceiling: Dict[str, Any] = {"status": "MISSING", "mm": None, "age_ms": 0}
        self._run_dir: Optional[str] = None
        self._run_since_ms: Optional[float] = None
        self._last_step_ms = clock()
        self._seq = 0
        self._task: Optional[asyncio.Task] = None

        self._height_mm = 0.0
        self._height_at_ms = clock()
        self._bottom = False
        self._state: Optional[Dict[str, Any]] = None
        self._state_at_ms: Optional[float] = None
        #: 受けた `hold`・`release` の履歴（試験用）
        self.hold_history: List[Dict[str, Any]] = []
        self.release_history: List[Dict[str, Any]] = []

    # --- 外から偽の値を変える ---------------------------------------------------------------------

    def set_height_mm(self, height_mm: float) -> None:
        self._height_mm = float(height_mm)

    def set_bottom(self, pressed: bool) -> None:
        self._bottom = bool(pressed)

    @property
    def height_mm(self) -> float:
        return self._height_mm

    @property
    def bottom(self) -> bool:
        return self._bottom

    @property
    def current_host(self) -> None:
        """プロセス内に線は無いので `None`（`state.lift.lift_ip` 用）。"""
        return None

    # --- LiftPort ---------------------------------------------------------------------------------

    async def start(self) -> None:
        """シミュレーションを始める。`LIFT_STATE_PERIOD_MS` ごとに `state` を作る。"""
        self._last_step_ms = self._clock()
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def send_hold(
        self,
        direction: str,
        duty: int,
        press: int,
        ceiling_status: str,
        ceiling_mm: Optional[int],
        ceiling_age_ms: int,
    ) -> None:
        """`hold` を受ける。古い `press`（取って代わられた側の押し続け）は無視する（§3.3）。"""
        now = self._clock()
        press = int(press)
        self.hold_history.append(
            {
                "dir": direction,
                "duty": int(duty),
                "press": press,
                "ceiling": {
                    "status": ceiling_status,
                    "mm": ceiling_mm,
                    "age_ms": int(ceiling_age_ms),
                },
            }
        )
        if self._owner_press is None:
            if press <= self._seen_press:
                return  # 止まったあとの古い `press` では持ち主になれない
        elif press < self._owner_press:
            return  # 取って代わられた側の押し続けは無視する
        self._owner_press = press
        if press > self._seen_press:
            self._seen_press = press
        if direction in ("up", "down") and direction == self._run_dir:
            pass  # 同じ方向のまま動き続けているので続けて数える
        else:
            self._run_dir = direction if direction in ("up", "down") else None
            self._run_since_ms = now
        self._cmd = {"dir": direction, "duty": int(duty)}
        self._cmd_at_ms = now
        self._ceiling = {
            "status": ceiling_status,
            "mm": ceiling_mm,
            "age_ms": int(ceiling_age_ms),
        }

    async def send_release(self, press: int) -> None:
        """`release` を受ける。持ち主からでなければ無視する（§3.3）。"""
        self.release_history.append({"press": int(press)})
        if self._owner_press is not None and int(press) == self._owner_press:
            self._owner_press = None
            self._cmd = {"dir": "stop", "duty": 0}
            self._cmd_at_ms = self._clock()

    def link_ok(self, now_ms: float, timeout_ms: float) -> bool:
        """プロセス内に線は無い。`state` を作り始めていれば繋がっている扱い。"""
        return self._state is not None

    def latest_state(self) -> Optional[Dict[str, Any]]:
        return dict(self._state) if self._state is not None else None

    def state_received_at_ms(self) -> Optional[float]:
        return self._state_at_ms

    # --- シミュレーション -------------------------------------------------------------------------

    async def _run(self) -> None:
        period = LIFT_STATE_PERIOD_MS / 1000.0
        while True:
            await asyncio.sleep(period)
            self._step(self._clock())

    def _decide(self, now: float) -> tuple:
        """持ち主の命令・天井・下端・経過時間から `(dir, duty, 理由)` を返す（§3.2・§4.1）。"""
        direction = self._cmd.get("dir")
        try:
            duty = int(self._cmd.get("duty"))
        except (TypeError, ValueError):
            return "stop", 0, "CMD_STOP"
        if direction not in ("up", "down", "stop"):
            return "stop", 0, "CMD_STOP"
        duty = min(max(duty, 0), LIFT_DUTY_ABS_MAX_PCT)

        if self._owner_press is None:
            return "stop", 0, "CMD_STOP"
        if now - self._cmd_at_ms > LIFT_CMD_TIMEOUT_MS:
            self._owner_press = None
            return "stop", 0, "CMD_TIMEOUT"
        if direction == "stop" or duty == 0:
            return "stop", 0, "CMD_STOP"
        if direction == "up":
            ok, reason = fake_ceiling_verdict(
                self._ceiling.get("status", "MISSING"),
                self._ceiling.get("mm"),
                int(self._ceiling.get("age_ms", 0)),
            )
            if not ok:
                return "stop", 0, reason
        if direction == "down" and self._bottom:
            return "stop", 0, "BOTTOM"
        run_ms = 0.0
        if self._run_dir is not None and self._run_since_ms is not None:
            run_ms = now - self._run_since_ms
        if run_ms > LIFT_MAX_RUN_MS:
            return "stop", 0, "MAX_RUN"
        return direction, duty, "NONE"

    def _step(self, now: float) -> None:
        """1 周期ぶん進める。判定 → 動かす → `state` を作る。"""
        dt_s = max(0.0, (now - self._last_step_ms) / 1000.0)
        self._last_step_ms = now

        direction, duty, reason = self._decide(now)
        if direction == "up":
            self._height_mm += duty * _MM_PER_PCT_S * dt_s
        elif direction == "down":
            self._height_mm -= duty * _MM_PER_PCT_S * dt_s
        self._height_at_ms = now

        ok, ceiling_reason = fake_ceiling_verdict(
            self._ceiling.get("status", "MISSING"),
            self._ceiling.get("mm"),
            int(self._ceiling.get("age_ms", 0)),
        )
        self._seq += 1
        self._state = {
            "t": "state",
            "seq": self._seq,
            "dir": direction,
            "duty": duty,
            "reason": reason,
            "bottom": self._bottom,
            "height_mm": int(self._height_mm),
            "height_ok": True,
            "top_detect": False,
            "ceiling": {
                "used": self._owner_press is not None,
                "status": self._ceiling.get("status", "MISSING"),
                "mm": self._ceiling.get("mm"),
                "age_ms": int(self._ceiling.get("age_ms", 0)),
                "ok": ok,
                "reason": ceiling_reason,
            },
            "owner": "module" if self._owner_press is not None else None,
            "ui_clients": 0,
            "module": {
                "connected": True,
                "ceiling_sensor": True,
                "ip": None,
                "name": "hve-cam",
            },
            "provisional": ["CEILING_MARGIN_MM", "CEILING_STALE_MS", "LIFT_MAX_RUN_MS"],
            "fw": "fake-0.2.0",
        }
        self._state_at_ms = now
