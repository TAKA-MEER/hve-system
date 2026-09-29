"""`HardwareBase` の偽物（偽物のモード `--fake` と、試験の呼び出し側を縛るため）。

[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §4.2・`DD-2`。
**出た値を全部記録する**ので、試験は「サーボに何を出したか」「ヨーを止めたか」を
確かめられる。**天井の読み値だけは外から差し替えられる**（`set_ceiling`・
`freeze_ceiling`）ので、上昇の許可が振れる経路を機器なしで試せる。

時計は外から渡す（`ControlLoop` と同じもの）。天井の読み値の `at_ms` は
**測ったときの時計**なので、試験は時計を進めるだけで「古い読み値」を作れる。
"""

from __future__ import annotations

from typing import Callable

from hve_camera.ceiling import CeilingReading, CeilingStatus
from hve_camera.hw.base import HardwareBase

#: 時計の型。`Callable[[], float]`。ms を返す。
Clock = Callable[[], float]


class FakeHardware(HardwareBase):
    """記録だけするハードウェア。"""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        #: 出したピッチの角度の履歴
        self.pitch_deg: float = 0.0
        self.pitch_history: list[float] = []
        #: 出したヨーの指令。`None` は `stop_yaw()`
        self.yaw_history: list[tuple[float, str] | None] = []
        #: 今ヨーが回っているか
        self.yaw_running: bool = False
        self.yaw_steps_per_s: float = 0.0
        self.yaw_direction: str | None = None
        #: 天井の「次の測定値」。`None` なら新しく測れない
        self._pending: CeilingReading | None = None
        #: 測った読み値の履歴
        self.ceiling_history: list[CeilingReading] = []
        #: 差し込みが無いとき毎回返す「いつもの読み値」（偽物のモードの起動用。試験は使わない）
        self._steady: tuple[CeilingStatus, int | None] | None = None
        self.closed = False

    # --- 外から天井の読み値を差し替える ----------------------------------------------------------

    def set_ceiling(self, status: CeilingStatus | str, distance_mm: int | None = None) -> None:
        """天井の「次の測定値」を差し込む。`read_ceiling()` を呼ぶと 1 回分だけ返る。"""
        self._pending = CeilingReading(CeilingStatus(status), distance_mm)

    def set_steady_ceiling(self, status: CeilingStatus | str, distance_mm: int | None = None) -> None:
        """**差し込みが無いとき、毎回この読み値を新しく測ったことにして返す。**

        偽物のモードを立ち上げただけで上昇できるようにする（1 回分の差し込みだと
        `ceiling_stale_ms` で古くなって「天井 値なし」に戻る）。`freeze_ceiling()` で止まる。
        """
        self._steady = (CeilingStatus(status), distance_mm)

    def freeze_ceiling(self) -> None:
        """以降新しい測定値を返さない（読み値が止まった・古い状態。spec §2 #4 を再現する）。"""
        self._pending = None
        self._steady = None

    # --- HardwareBase -------------------------------------------------------------------------

    async def read_ceiling(self) -> CeilingReading | None:
        """差し込まれた測定値を 1 回返す。無いなら `None`（呼び出し側が保持する）。"""
        if self._pending is not None:
            status, distance_mm = self._pending.status, self._pending.distance_mm
            self._pending = None
        elif self._steady is not None:
            status, distance_mm = self._steady
        else:
            return None
        reading = CeilingReading(status=status, distance_mm=distance_mm, at_ms=self._clock())
        self.ceiling_history.append(reading)
        return reading

    async def set_pitch(self, angle_deg: float) -> None:
        self.pitch_deg = float(angle_deg)
        self.pitch_history.append(self.pitch_deg)

    async def drive_yaw(self, steps_per_s: float, direction: str) -> None:
        self.yaw_steps_per_s = float(steps_per_s)
        self.yaw_direction = direction
        self.yaw_running = True
        self.yaw_history.append((self.yaw_steps_per_s, direction))

    async def stop_yaw(self) -> None:
        self.yaw_running = False
        self.yaw_steps_per_s = 0.0
        self.yaw_direction = None
        self.yaw_history.append(None)

    async def close(self) -> None:
        self.closed = True
