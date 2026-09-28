"""天井の許可を判定する純関数。

spec [Spec-safety.md](../../docs/plan/spec/Spec-safety.md) §2 の #3・#3a・#3b・#4
と [DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §3 の仕組み。
ここで受け取るのは**状態だけ**。SRF02 の生の値（I2C の返り値）を状態へ変換するのは
後のパケット（`hw/rpi_hw.py`）の仕事で、ここは判断だけする。

**`READ_ERROR`（I2C で読めなかった）と `NO_ECHO`（反射が返らない）は別の状態。**
取り違えると、センサが死んだときに「天井は遠い」とみなし天井へ突っ込む。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Any


class CeilingStatus(str, Enum):
    """天井の読み値の状態。DetailedDesign-names.md §1。"""

    #: 距離を測れた。`distance_mm` がある。
    MEASURED = "MEASURED"
    #: 反射が返らない（測定範囲より遠い）。spec §2 #3b。上昇を許す。
    NO_ECHO = "NO_ECHO"
    #: I2C の読み取り失敗。spec §2 #4。上昇を許さない。
    READ_ERROR = "READ_ERROR"


@dataclass(frozen=True)
class CeilingReading:
    """天井の読み値。時刻は単調増加の時計の ms。"""

    status: CeilingStatus
    distance_mm: int | None = None
    at_ms: int | None = None


def ceiling_permission(
    reading: CeilingReading | None,
    now_ms: float,
    params: Mapping[str, Any],
) -> tuple[bool, str]:
    """天井の許可を `(ok, 理由)` で返す。

    **上から順に、最初に当たったもの**を返す。

    | # | 条件 | 結果 |
    | --- | --- | --- |
    | 1 | 読み値が無い・I2C の読み取り失敗・時刻から `ceiling_stale_ms` 超 | `(False, "CEILING_STALE")` |
    | 2 | 反射なし | `(True, "OUT_OF_RANGE")` |
    | 3 | 距離 < `srf02_min_range_mm` | `(False, "CEILING_NEAR")` |
    | 4 | 距離 ≦ `ceiling_margin_mm` | `(False, "CEILING_NEAR")` |
    | 5 | それ以外 | `(True, "NONE")` |

    #3 の「近すぎて測れない」（spec §2 #3a）と #4 の余裕の 2 つを別々に判定する。
    #5 の `None` は「反射なし」。`distance_mm` が無いので #3・#4 にはかからない。
    """
    if reading is None:
        return False, "CEILING_STALE"
    if reading.status is CeilingStatus.READ_ERROR:
        return False, "CEILING_STALE"
    if reading.at_ms is None:
        return False, "CEILING_STALE"

    if now_ms - reading.at_ms > params["ceiling_stale_ms"]:
        return False, "CEILING_STALE"

    if reading.status is CeilingStatus.NO_ECHO:
        return True, "OUT_OF_RANGE"

    distance = reading.distance_mm
    if distance < params["srf02_min_range_mm"]:
        return False, "CEILING_NEAR"
    if distance <= params["ceiling_margin_mm"]:
        return False, "CEILING_NEAR"

    return True, "NONE"
