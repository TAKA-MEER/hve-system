"""プロセス内の偽の昇降部（偽物のモード `--fake` 用）。

[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §4.2・§4.3 の「偽物のモード」。

**本物の判定は ESP32 側**（`firmware/lift`・`WP-LIFT-01`）で、ここはその Python での真似に
すぎない。**画面と判定の経路を機器なしで確かめる**ためのもので、高さの積分は
「デューティに比例して上下する」程度の当てずっぽうで十分。

- `fake_lift_decide()`: [DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md)
  §4.1 の判定の表を Python で写した純関数。**上端の閾値が `None` のときは `TOP` で止めない**
  （spec [Spec-safety.md](../../../docs/plan/spec/Spec-safety.md) §2 #2a）
- `FakeLift`: `LiftPort` と同じ使い方ができる偽物。指令のデューティに比例して高さを動かし、
  下端・連続駆動の上限・指令の途絶えで止まる。**上端の閾値は未設定**（`top_mm: null`）

使う側（`hve_camera.control`）は `FakeLift` と `LiftLink` の違いを知らない。
同じ 5 つ（`start`・`close`・`send_cmd`・`link_ok`・`latest_state`）だけを実装している。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Callable, Mapping

from hve_camera.lift_link import LiftPort

#: 昇降部 `firmware/lift/src/config.h` と同じ値。**偽物でしか使わない。**本物はファームウェア側にある
LIFT_CMD_TIMEOUT_MS = 600
HEIGHT_STALE_MS = 600
LIFT_MAX_RUN_MS = 10000
LIFT_DUTY_ABS_MAX_PCT = 100
LIFT_STATE_PERIOD_MS = 100

#: 偽の高さの進む速さ [mm/s per 1 % デューティ]。**画面を動かするための当てずっぽう**
_MM_PER_PCT_S = 2.5

Clock = Callable[[], float]


def fake_lift_decide(
    cmd: Mapping[str, Any],
    now_ms: float,
    bottom: bool,
    height_mm: float | None,
    height_at_ms: float | None,
    top_mm: int | None,
    cmd_at_ms: float,
    run_ms: float,
) -> tuple[str, int, str]:
    """指令・下端・高さ・経過時間から `(dir, duty, 理由)` を返す。

    [DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §4.1 の表を
    そのまま写す。上から順に、最初に当たったもので止める。読めない指令は `stop` にする。

    | 引数 | 何か |
    | --- | --- |
    | `cmd` | 受け取った `cmd`（`dir`・`duty`・`ceil_ok`） |
    | `cmd_at_ms` | その `cmd` を受けた時刻 |
    | `run_ms` | 同じ方向へ動き続けた時間 |
    | `top_mm` | 上端の閾値。`None` は未設定（`TOP` で止めない） |
    """
    direction = cmd.get("dir")
    try:
        duty = int(cmd.get("duty"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "stop", 0, "CMD_STOP"
    if direction not in ("up", "down", "stop"):
        return "stop", 0, "CMD_STOP"
    duty = min(max(duty, 0), LIFT_DUTY_ABS_MAX_PCT)

    if now_ms - cmd_at_ms > LIFT_CMD_TIMEOUT_MS:
        return "stop", 0, "CMD_TIMEOUT"
    if direction == "stop" or duty == 0:
        return "stop", 0, "CMD_STOP"
    if direction == "up" and cmd.get("ceil_ok") is not True:
        return "stop", 0, "CEILING"
    if direction == "up" and (
        height_mm is None or height_at_ms is None or now_ms - height_at_ms > HEIGHT_STALE_MS
    ):
        return "stop", 0, "HEIGHT_UNKNOWN"
    if direction == "up" and top_mm is not None and height_mm >= top_mm:
        return "stop", 0, "TOP"
    if direction == "down" and bottom:
        return "stop", 0, "BOTTOM"
    if run_ms > LIFT_MAX_RUN_MS:
        return "stop", 0, "MAX_RUN"

    return direction, duty, "NONE"


class FakeLift(LiftPort):
    """プロセス内で動く偽の昇降部。指令を受けて高さを動かし、`state` を作る。"""

    def __init__(self, clock: Clock, top_mm: int | None = None) -> None:
        self._clock = clock
        self._top_mm = top_mm
        self._cmd: dict[str, Any] = {"dir": "stop", "duty": 0, "ceil_ok": False}
        self._cmd_at_ms = clock()
        self._run_dir: str | None = None
        self._run_since_ms: float | None = None
        self._last_step_ms = clock()
        self._seq = 0
        self._task: asyncio.Task[None] | None = None

        self._height_mm = 0.0
        self._height_at_ms = clock()
        self._bottom = False
        self._state: dict[str, Any] | None = None
        self._state_at_ms: float | None = None
        #: 受けた指令の履歴（試験用）
        self.cmd_history: list[dict[str, Any]] = []

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

    async def send_cmd(self, direction: str, duty: int, ceil_ok: bool) -> None:
        """指令を覚える。**ここで高さは動かさない**（`state` を作る周期に合わせて動かす）。"""
        now = self._clock()
        if direction in ("up", "down") and direction == self._run_dir:
            pass  # 同じ方向のまま動き続けているので続けて数える
        else:
            self._run_dir = direction if direction in ("up", "down") else None
            self._run_since_ms = now
        self._seq += 1
        self._cmd = {"dir": direction, "duty": int(duty), "ceil_ok": bool(ceil_ok)}
        self._cmd_at_ms = now
        self.cmd_history.append({"seq": self._seq, **self._cmd})

    def link_ok(self, now_ms: float, timeout_ms: float) -> bool:
        """プロセス内に線は無い。`state` を作り始めていれば繋がっている扱い。"""
        return self._state is not None

    def latest_state(self) -> dict[str, Any] | None:
        return dict(self._state) if self._state is not None else None

    def state_received_at_ms(self) -> float | None:
        return self._state_at_ms

    # --- シミュレーション -------------------------------------------------------------------------

    async def _run(self) -> None:
        period = LIFT_STATE_PERIOD_MS / 1000.0
        while True:
            await asyncio.sleep(period)
            self._step(self._clock())

    def _step(self, now: float) -> None:
        """1 周期ぶん進める。判定 → 動かす → `state` を作る。"""
        dt_s = max(0.0, (now - self._last_step_ms) / 1000.0)
        self._last_step_ms = now

        run_ms = 0.0
        if self._run_dir is not None and self._run_since_ms is not None:
            run_ms = now - self._run_since_ms
        direction, duty, reason = fake_lift_decide(
            self._cmd,
            now,
            self._bottom,
            self._height_mm,
            self._height_at_ms,
            self._top_mm,
            self._cmd_at_ms,
            run_ms,
        )
        if direction == "up":
            self._height_mm += duty * _MM_PER_PCT_S * dt_s
        elif direction == "down":
            self._height_mm -= duty * _MM_PER_PCT_S * dt_s
        self._height_at_ms = now

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
            "top_mm": self._top_mm,
            "cmd_age_ms": int(now - self._cmd_at_ms),
            "fw": "fake-0.1.0",
        }
        self._state_at_ms = now
