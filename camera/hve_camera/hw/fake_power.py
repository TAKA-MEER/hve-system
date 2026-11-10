"""機体の無線と電源断の偽物（偽物のモード `--fake` と試験）。`PowerBase` の実装。

[DetailedDesign-power.md](../../../docs/plan/detailed/DetailedDesign-power.md) §5。
**`poweroff()` はログを出して回数を数えるだけ。何も切らない。**
`wlan_up` は既定で真。`POST /api/fake` の `wlan_up` で切り替える。
"""

from __future__ import annotations

import logging

from hve_camera.hw.base import PowerBase

log = logging.getLogger(__name__)


class FakePower(PowerBase):
    """記録だけする電源の口。"""

    def __init__(self, wlan_up: bool = True) -> None:
        self._wlan_up = bool(wlan_up)
        #: `poweroff()` を呼ばれた回数
        self.poweroff_count = 0

    def set_wlan_up(self, up: bool) -> None:
        self._wlan_up = bool(up)

    def wlan_up(self) -> bool:
        return self._wlan_up

    def poweroff(self) -> None:
        self.poweroff_count += 1
        log.warning("偽物の電源断（実際には切らない。%d 回目）", self.poweroff_count)
