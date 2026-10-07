"""Arduino（UNO）との UART の行の組み立て・読み取りと時計の直し。

[DetailedDesign-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §5。
行の形は `firmware/cam_io/lib/io_core/io_codec.{h,cpp}`（`M`・`C`・`B`）と同じ。
**読めない行は捨てる**（`None` を返し、呼び出し側は生存確認を延ばさない）。

`UnoClock` は [DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §3.5 の
「Arduino の時計を UnitV2 の時計へ直す」仕組み。UnitV2 が一時的に固まると
天井の行が受信バッファに溜まり、再開後に古い行が新しく見える。行ごとに
`d = 受け取った時刻 − uno_ms` を記録し、直近 `uno_clock_window` 行の最小を
`offset` として読み値の古さを直す。
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Tuple, Union

#: 1 行の長さの上限（`io_codec.h` の `IO_LINE_MAX` と同じ値）。
_IO_LINE_MAX = 32

#: `parse_io_line()` が返す `C` 行。`(種別, uno_ms, st, cm)`。
CLine = Tuple[str, int, int, int]
#: `parse_io_line()` が返す `B` 行。`(種別, uno_ms, fw)`。
BLine = Tuple[str, int, str]

#: `parse_io_line()` の戻り値。読めない行は `None`。
IoLine = Union[CLine, BLine, None]


def encode_io_cmd(seq: int, pitch_ddeg: int, yaw_hsps: int) -> str:
    """`M` 行を作る。`seq` は 0〜65535（`B` 行と別の一周する連番。ログ用）。"""
    for name, value in (("seq", seq), ("pitch_ddeg", pitch_ddeg), ("yaw_hsps", yaw_hsps)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("%s が整数でない: %r" % (name, value))
    if not 0 <= seq <= 65535:
        raise ValueError("seq が 0〜65535 の外: %r" % (seq,))
    line = "M %d %d %d" % (seq, pitch_ddeg, yaw_hsps)
    if len(line) > _IO_LINE_MAX:
        raise ValueError("M 行が長すぎる: %r" % (line,))
    return line


def parse_io_line(line: object) -> Union[CLine, BLine, None]:
    """`C`・`B` 行を読む。読めたらタプル、読めなければ `None`。

    `C <uno_ms> <st> <cm>` → `("C", uno_ms, st, cm)`、
    `B <uno_ms> <fw>` → `("B", uno_ms, fw)`。
    `M` 行（UnitV2 から Arduino への向き）や形の違う行は `None`。
    """
    if not isinstance(line, str):
        return None
    text = line.rstrip("\r\n")
    if len(text) > _IO_LINE_MAX or len(text) == 0:
        return None
    parts = text.split(" ")
    if parts[0] == "C" and len(parts) == 4:
        numbers = _take_ints(parts[1:3 + 1])
        if numbers is None:
            return None
        uno_ms, st, cm = numbers
        if not 0 <= uno_ms <= 4294967295:
            return None
        return ("C", uno_ms, st, cm)
    if parts[0] == "B" and len(parts) == 3:
        numbers = _take_ints(parts[1:2])
        if numbers is None:
            return None
        (uno_ms,) = numbers
        if not 0 <= uno_ms <= 4294967295:
            return None
        fw = parts[2]
        if not fw or any(char.isspace() for char in fw):
            return None
        return ("B", uno_ms, fw)
    return None


def _take_ints(parts: list) -> Optional[list]:
    """10 進の整数だけを読む。1 つでも読めなければ `None`（真偽値は受けない）。"""
    numbers = []
    for part in parts:
        if not isinstance(part, str) or not part or part in ("+", "-"):
            return None
        text = part
        if text[0] in ("+", "-"):
            text = text[1:]
        if not text or not all("0" <= char <= "9" for char in text):
            return None
        try:
            numbers.append(int(part))
        except ValueError:
            return None
    return numbers


class UnoClock:
    """Arduino の時計（`millis`）を UnitV2 の時計へ直す（protocol §5）。

    行を受け取るたびに `observe()` で `d = 受け取った時刻 − uno_ms` を記録し、
    直近 `window` 行の最小を `offset` とする。読み値の古さは
    `今 − (uno_ms ＋ offset)`。
    `uno_ms` が前の行より小さくなったら（再起動か、遅れた古い行）、
    **その行の古さは捨てる前の窓で測ってから**記録を捨ててやり直す。
    測ってから捨てるので、遅れた古い行は古いまま見える（§3.5）。
    """

    def __init__(self, window: int) -> None:
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            raise ValueError("window は 1 以上の整数: %r" % (window,))
        self._window = window
        self._ds: Deque[float] = deque(maxlen=window)
        self._max_uno_ms: Optional[int] = None

    def reset(self) -> None:
        """記録を捨てる。`B` を受けたら呼び出す。"""
        self._ds.clear()
        self._max_uno_ms = None

    def observe(self, received_ms: float, uno_ms: int) -> Tuple[float, bool]:
        """1 行を受け取ったことを記録し、`(その行の古さ, 巻き戻りでやり直したか)` を返す。

        古さは**更新前の窓**で測る。窓が空（最初の 1 行）のときは `0.0`。
        巻き戻りのときは測ってから捨てるので、遅れた古い行は古いまま見える。
        """
        now = float(received_ms)
        if not self._ds:
            self._ds.append(now - float(uno_ms))
            self._max_uno_ms = uno_ms
            return 0.0, False
        if uno_ms < self._max_uno_ms:
            age = now - (float(uno_ms) + min(self._ds))
            self.reset()
            self._ds.append(now - float(uno_ms))
            self._max_uno_ms = uno_ms
            return age, True
        age = now - (float(uno_ms) + min(self._ds))
        self._ds.append(now - float(uno_ms))
        if uno_ms > self._max_uno_ms:
            self._max_uno_ms = uno_ms
        return age, False

    def age_ms(self, uno_ms: int, now_ms: float) -> Optional[float]:
        """`uno_ms` に測った読み値の古さ（今の窓で測る）。まだ 1 行も見ていなければ `None`。"""
        if not self._ds:
            return None
        return float(now_ms) - (float(uno_ms) + min(self._ds))
