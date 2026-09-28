"""ハードウェアの抽象（[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §4.2）。

ここで扱うのは 3 つだけ。

- **ピッチのサーボ**（SG90）: 角度を出すだけ。目標角への計算は `axes.py` 側の仕事
- **ヨーのステッピング**（28BYJ-48/ULN2003）: 1 秒あたりの半ステップ数と向きで回す・止める
- **天井の距離計**（SRF02・I2C）: 距離を 1 回測って `CeilingReading` で返す

**判定はここに持たせない。**天井の許可は `ceiling.py`、上下端は昇降部側
（spec [Spec-safety.md](../../../docs/plan/spec/Spec-safety.md) §2・`DD-4`）がやる。
中層の `control.py` がこの抽象だけを触れば、偽物と実物で同じ経路を通る（`DD-2`）。

**`read_ceiling()` は「1 回測る」ことしかしない。**新しい測定が無いときは `None` を返す。
中身の `control.py` が最後の読み値を保持し、`ceiling_stale_ms` を超えたら古いまま扱う
（[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §3）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from hve_camera.ceiling import CeilingReading


class HardwareBase(ABC):
    """ピッチ・ヨー・天井の読み値を外へ出す口。"""

    @abstractmethod
    async def read_ceiling(self) -> CeilingReading | None:
        """天井の距離を 1 回測って返す。

        `MEASURED` なら距離を入れる。I2C で読めなかったときは `READ_ERROR`。
        **新しい測定が無いときは `None`**（呼び出し側が最後の読み値を保持する）。
        """

    @abstractmethod
    async def set_pitch(self, angle_deg: float) -> None:
        """ピッチのサーボへ角度を出す。"""

    @abstractmethod
    async def drive_yaw(self, steps_per_s: float, direction: str) -> None:
        """ヨーを 1 秒あたりの半ステップ数（`yaw_step_rate` の戻り値）と向きで回す。"""

    @abstractmethod
    async def stop_yaw(self) -> None:
        """ヨーを止める。**止めている間はコイルの電流を切る**（spec [Spec-safety.md](../../../docs/plan/spec/Spec-safety.md) §2）"""

    async def close(self) -> None:
        """ハードウェアを片付ける。当面は何もしない（実物は `WP-CAM-03`）。"""
