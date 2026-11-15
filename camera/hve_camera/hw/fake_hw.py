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
from hve_camera.hw.base import POWER_ACTIVE, POWER_MODES, HardwareBase

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
        #: `True` の間は行が来ない（`POST /api/fake` の `io_lost`。`IO_LOST` を再現する）
        self._io_lost = False
        #: 外し忘れの省電力の段階と、そのとき渡されたピッチの角度（履歴つき）
        self.power_mode: str = POWER_ACTIVE
        self.power_pitch_deg: float = 0.0
        self.power_history: list[tuple[str, float]] = []
        self.closed = False
        #: 偽の UNO のヨーの累計（`Y` 行の元）。**速さに合わせて時計で進める**（符号は `left` が正）
        self._yaw_total = 0.0
        self._yaw_hsps = 0.0
        self._yaw_at_ms = clock()
        self._yaw_epoch = 0

    # --- 偽の UNO のヨーの累計 -------------------------------------------------------------------------

    def _integrate_yaw(self) -> None:
        """前回から今までの時計の進みぶん、今の速さで累計を進める（速さは区間の中で一定）。"""
        now = self._clock()
        self._yaw_total += self._yaw_hsps * (now - self._yaw_at_ms) / 1000.0
        self._yaw_at_ms = now

    def yaw_hs_total(self) -> int | None:
        self._integrate_yaw()
        return int(round(self._yaw_total))

    def yaw_epoch(self) -> int:
        return self._yaw_epoch

    def reboot_uno(self) -> None:
        """偽の UNO が再起動した（`B` 行を送った）ことにする。累計は 0 から数え直す。"""
        self._yaw_total = 0.0
        self._yaw_hsps = 0.0
        self._yaw_at_ms = self._clock()
        self._yaw_epoch += 1

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

    def set_io_lost(self, lost: bool) -> None:
        """Arduino から行が来ない状態にする・戻す（`POST /api/fake` の `io_lost`）。"""
        self._io_lost = bool(lost)
        if lost:
            self._pending = None
            self._steady = None

    # --- HardwareBase -------------------------------------------------------------------------

    async def read_ceiling(self) -> CeilingReading | None:
        """差し込まれた測定値を 1 回返す。無いなら `None`（呼び出し側が保持する）。"""
        if self._io_lost:
            return None
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
        self._integrate_yaw()
        self._yaw_hsps = float(steps_per_s) * (1.0 if direction == "left" else -1.0)
        self.yaw_steps_per_s = float(steps_per_s)
        self.yaw_direction = direction
        self.yaw_running = True
        self.yaw_history.append((self.yaw_steps_per_s, direction))

    async def stop_yaw(self) -> None:
        self._integrate_yaw()
        self._yaw_hsps = 0.0
        self.yaw_running = False
        self.yaw_steps_per_s = 0.0
        self.yaw_direction = None
        self.yaw_history.append(None)

    async def set_power_mode(self, mode: str, pitch_deg: float) -> None:
        if mode not in POWER_MODES:
            raise ValueError("知らない省電力の段階: %r" % (mode,))
        self.power_mode = mode
        self.power_pitch_deg = float(pitch_deg)
        self.power_history.append((mode, self.power_pitch_deg))

    async def close(self) -> None:
        self.closed = True
