"""天井の読み値を汎用の 4 状態に直す。

[DetailedDesign-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §5。
**許可の計算はしない**（昇降部の `ceiling_check` が決める。
[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §4.3）。
ここは SRF02 の生の値（`C` 行の `st`・`cm`）と読み値の古さを 4 状態に直すだけ。

**`READ_ERROR`（読めない）と `NO_ECHO`（反射が返らない）は別の状態。**
取り違えると、センサが死んだときに「天井は遠い」とみなし天井へ突っ込む。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple


class CeilingStatus(str, Enum):
    """天井の読み値の状態。[-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §2.1。"""

    #: 測れた（`mm` 付き）。
    MEASURED = "MEASURED"
    #: 近すぎて測れない（`SRF02` の最小測定距離より近い）。
    TOO_NEAR = "TOO_NEAR"
    #: 反射が返らない（遠い）。上昇を許す。
    NO_ECHO = "NO_ECHO"
    #: 距離計が読めない。上昇を許さない。
    READ_ERROR = "READ_ERROR"


@dataclass(frozen=True)
class CeilingReading:
    """天井の読み値。`at_ms` は `UnoClock` で UnitV2 の時計に直した測定時刻。"""

    status: CeilingStatus
    distance_mm: Optional[int] = None
    at_ms: Optional[float] = None


#: `SRF02` が反射なしを返す生の値（`C` 行の `cm`。protocol §5）。
SRF02_NO_ECHO_RAW = 0


def classify_srf02(
    st: Optional[int],
    cm: Optional[int],
    age_ms: Optional[float],
    params: Mapping[str, Any],
) -> Tuple[CeilingStatus, Optional[int]]:
    """SRF02 の生の値と読み値の古さを汎用の 4 状態と `mm` に直す。

    **上から順に、最初に当たったもの**を返す（protocol §5）。

    | 入力 | 状態 |
    | --- | --- |
    | `st` が `1`、または古さが `ceiling_read_stale_ms` を超えた、または行が来ていない | `READ_ERROR` |
    | `cm` が `SRF02_NO_ECHO_RAW`（0） | `NO_ECHO` |
    | `cm × 10` が `srf02_min_range_mm` 未満 | `TOO_NEAR` |
    | それ以外 | `MEASURED`（`mm` ＝ `cm × 10`） |

    行が来ていないときは `st`・`cm`・`age_ms` のどれかを `None` で渡す。
    `mm` は `MEASURED` のときだけ値が入り、それ以外は `None`。
    """
    if st is None or cm is None or age_ms is None:
        return CeilingStatus.READ_ERROR, None
    if st == 1:
        return CeilingStatus.READ_ERROR, None
    if age_ms > float(params["ceiling_read_stale_ms"]):
        return CeilingStatus.READ_ERROR, None
    if cm == SRF02_NO_ECHO_RAW:
        return CeilingStatus.NO_ECHO, None
    distance_mm = int(cm) * 10
    if distance_mm < int(params["srf02_min_range_mm"]):
        return CeilingStatus.TOO_NEAR, None
    return CeilingStatus.MEASURED, distance_mm
