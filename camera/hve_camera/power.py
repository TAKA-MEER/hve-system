"""外し忘れの省電力・電源断の判定（`IdlePower`）。

[DetailedDesign-power.md](../../docs/plan/detailed/DetailedDesign-power.md) §1。
**時刻を引数で受ける純関数のクラス。**ハードウェアにも時計にも触らない（ホストで試験する）。

- `touch(now_ms)`: 操作があった。省電力中なら `ACTIVE` に戻る
- `step(now_ms, lift_moving, wlan_up)`: 周期の判定。`lift_moving`（昇降部が動いている）の間は
  最後の操作の時刻を進める（fk-system が昇降機を動かす最中に省電力へ入らない・電源を切らない）

状態は `ACTIVE` → `SETTLING`（取り込みを止め、ピッチを倒れてもよい角度へ）→ `SAVING`
（ピッチの力を抜く）→ `OFF`（電源断）。`OFF` に入ったら戻らない（`touch()` も効かない）。
"""

from __future__ import annotations

import enum
from typing import Optional


class PowerState(enum.Enum):
    """省電力の状態。"""

    ACTIVE = "active"
    SETTLING = "settling"
    SAVING = "saving"
    OFF = "off"


class IdlePower:
    """最後の操作からの時間と機体の無線の有無で、省電力・電源断を決める。"""

    def __init__(
        self,
        idle_save_ms: float,
        ap_absent_ms: float,
        power_save_settle_ms: float,
        now_ms: float,
    ) -> None:
        self._idle_save_ms = float(idle_save_ms)
        self._ap_absent_ms = float(ap_absent_ms)
        self._settle_ms = float(power_save_settle_ms)
        self._state = PowerState.ACTIVE
        #: 時間は起動から数える（`touch()` が無くても起動時刻から 30 分で省電力）
        self._last_op_ms = float(now_ms)
        #: `SETTLING` に入った時刻
        self._settling_at_ms: Optional[float] = None
        #: 機体の無線が見えなくなった時刻（見えている間は `None`）
        self._wlan_down_at_ms: Optional[float] = None

    @property
    def state(self) -> PowerState:
        return self._state

    def touch(self, now_ms: float) -> None:
        """操作があった。**省電力中なら `ACTIVE` に戻る。**`OFF` には効かない。"""
        if self._state is PowerState.OFF:
            return
        self._last_op_ms = float(now_ms)
        if self._state is not PowerState.ACTIVE:
            self._state = PowerState.ACTIVE
            self._settling_at_ms = None

    def step(self, now_ms: float, lift_moving: bool, wlan_up: bool) -> PowerState:
        """周期の判定。新しい状態を返す。"""
        if self._state is PowerState.OFF:
            return self._state
        now = float(now_ms)
        if lift_moving:
            self._last_op_ms = now
        # 一度でも見えたら無い時間を数え直す（一瞬の不調で切らない。spec H-V16）
        if wlan_up:
            self._wlan_down_at_ms = None
        elif self._wlan_down_at_ms is None:
            self._wlan_down_at_ms = now

        idle_ms = now - self._last_op_ms
        if self._state is PowerState.ACTIVE:
            if idle_ms >= self._idle_save_ms:
                self._state = PowerState.SETTLING
                self._settling_at_ms = now
            return self._state

        # SETTLING・SAVING
        if (
            idle_ms >= self._idle_save_ms
            and self._wlan_down_at_ms is not None
            and now - self._wlan_down_at_ms >= self._ap_absent_ms
        ):
            self._state = PowerState.OFF
        elif (
            self._state is PowerState.SETTLING
            and self._settling_at_ms is not None
            and now - self._settling_at_ms >= self._settle_ms
        ):
            self._state = PowerState.SAVING
        return self._state
