"""機体の無線と電源断の実物（UnitV2 で root）。`PowerBase` の実装。

[DetailedDesign-power.md](../../../docs/plan/detailed/DetailedDesign-power.md) §4・§5。

- `wlan_up()`: `/sys/class/net/wlan0/operstate` が `up` なら真。読めない・`up` 以外は偽
  （AP の名前は読まない。AP 名を直書きしない決まり）
- `poweroff()`: `/sbin/poweroff` を呼ぶ

**開発機では絶対に作らない。**`--fake` と試験は `fake_power.FakePower` を使う
（`app.CameraApp` は偽物のモードで実物を渡されたら拒否する）。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from hve_camera.hw.base import PowerBase

log = logging.getLogger(__name__)

#: 機体の無線の状態を読むファイル
WLAN_OPERSTATE_PATH = "/sys/class/net/wlan0/operstate"
#: 電源を切るコマンド
POWEROFF_COMMAND = ("/sbin/poweroff",)


class PowerHw(PowerBase):
    """実物。`runner`・`operstate_path` は試験が差し替える（実物の `poweroff` を試験で呼ばないため）。"""

    def __init__(
        self,
        operstate_path: str = WLAN_OPERSTATE_PATH,
        runner: Callable[[Sequence[str]], Any] = None,  # type: ignore[assignment]
    ) -> None:
        self._operstate_path = operstate_path
        self._runner = runner if runner is not None else _run_command

    def wlan_up(self) -> bool:
        try:
            text = Path(self._operstate_path).read_text(encoding="ascii")
        except (OSError, ValueError):
            return False
        return text.strip() == "up"

    def poweroff(self) -> None:
        log.warning("電源を切る（%s）", " ".join(POWEROFF_COMMAND))
        self._runner(POWEROFF_COMMAND)


def _run_command(command: Sequence[str]) -> Any:
    return subprocess.run(list(command), check=False)
