"""Arduino（UNO）との UART の実物。`HardwareBase` の実装。

[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §4.3・§3.4・§3.5 と
[-protocol.md](../../../docs/plan/detailed/DetailedDesign-protocol.md) §5。
旧版の `hw/rpi_hw.py`（ラズパイの GPIO 直結）を置き換える。

- 送り: `io_cmd_period_ms` ごとに `M` 行を送る（**止まっている間も送る**。生存確認を兼ねる）。
  送る値は `set_pitch`・`drive_yaw`・`stop_yaw` で置き換えるだけ。専用スレッドで回すので、
  制御ループの周期を待たせない。
- 受け: `read_ceiling()` が**読むたびに受信バッファを全部読み出し**、`C` 行のうち
  最も新しい `uno_ms` の行だけを天井に使う。`UnoClock` で Arduino の時計を直す。
- **前回読んでから `ceiling_read_stale_ms` より長く空いたときは、その回に読み出した行を
  すべて捨て、次の回に読めた行が来るまで `READ_ERROR` にする。**
  受信バッファが溢れて新しい側の行が捨てられると、残った中で一番新しい行も実は古く、
  窓の全行が遅れた行になると古さを 0 近くと見誤る。昇降部は送られた `age_ms` を信じる
  しかないので、ここで止める（[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §3.5）。

`pyserial` はこのモジュールの中だけで import する（ホストの試験は `serial` を差す）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Mapping, Optional

from hve_camera.ceiling import CeilingReading, CeilingStatus, classify_srf02
from hve_camera.hw.base import HardwareBase
from hve_camera.uno_link import UnoClock, encode_io_cmd, parse_io_line

log = logging.getLogger(__name__)

#: 単調増加の時計。ms を返す。
Clock = Callable[[], float]

#: 1 回の読み出しで受信バッファから取り出す行数の上限。溢れたゴミで無限に回らないため。
_DRAIN_MAX_LINES = 256


class UnoHardware(HardwareBase):
    """Arduino との UART（`/dev/ttyS1`）。`HardwareBase` の実装。"""

    def __init__(
        self,
        params: Mapping[str, Any],
        clock: Clock,
        serial: Any = None,
    ) -> None:
        self._params = params
        self._clock = clock
        self._serial = serial if serial is not None else _open_serial(params)

        self._clock_ms = UnoClock(int(params["uno_clock_window"]))

        #: 送る値（`set_pitch`・`drive_yaw`・`stop_yaw` が置き換えるだけ）。
        self._lock = threading.Lock()
        self._pitch_deg = 0.0
        self._yaw_hsps = 0
        self._seq = 0

        #: 前回 `read_ceiling()` で読み出した時刻（窓の空きの判定用）。
        self._last_drain_ms: Optional[float] = None

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._send_loop, name="hve-uno", daemon=True)
        self._thread.start()

    # --- HardwareBase ---------------------------------------------------------------------

    async def read_ceiling(self) -> Optional[CeilingReading]:
        """受信バッファを全部読み出し、最も新しい `C` 行だけを天井にして返す。

        新しい `C` 行が無いときは `None`（呼び出し側が最後の読み値を保持する）。
        行は来たが使えないとき（窓が空いた・`C` が無い）は `READ_ERROR` を返す。
        """
        now = self._clock()
        received = self._drain(now)

        gap_ms = float(self._params["ceiling_read_stale_ms"])
        idle_ms = None if self._last_drain_ms is None else now - self._last_drain_ms
        self._last_drain_ms = now
        if idle_ms is not None and idle_ms > gap_ms:
            # 窓が空いた。この回に読めた行は古い側だけの可能性があるので全部捨てる。
            if received:
                log.warning("UART の読み出しが %d ms 空いたので %d 行を捨てる", int(idle_ms), len(received))
            return CeilingReading(CeilingStatus.READ_ERROR, at_ms=now)

        if not received:
            return None

        newest = None
        restarted = False
        for kind in received:
            if kind[0] == "B":
                _, uno_ms, _fw = kind
                # 再起動の印。記録を捨てて、この行からやり直す（§3.5）。
                # `B` の後の `C` は新しい世代の行なので、そのまま使える。
                self._clock_ms.reset()
                self._clock_ms.observe(now, uno_ms)
            elif kind[0] == "C":
                _, uno_ms, _st, _cm = kind
                age, rewound = self._clock_ms.observe(now, uno_ms)
                if rewound:
                    # `B` が無い巻き戻り。再起動か、遅れた古い行か区別できないので、
                    # この回の排出は疑わしいものとして `READ_ERROR` にする（§3.5）。
                    restarted = True
                if newest is None or uno_ms >= newest[1]:
                    newest = (kind, age)
        if newest is None:
            # `B` だけだった。天井の測定は無い。
            return CeilingReading(CeilingStatus.READ_ERROR, at_ms=now)
        if restarted:
            return CeilingReading(CeilingStatus.READ_ERROR, at_ms=now)

        (_tag, uno_ms, st, cm), age = newest
        age = max(0.0, age)
        status, mm = classify_srf02(st, cm, age, self._params)
        return CeilingReading(status, distance_mm=mm, at_ms=now - age)

    async def set_pitch(self, angle_deg: float) -> None:
        """ピッチの目標角を覚えるだけ（送るのは `io_cmd_period_ms` ごとのスレッド）。"""
        with self._lock:
            self._pitch_deg = float(angle_deg)

    async def drive_yaw(self, steps_per_s: float, direction: str) -> None:
        """ヨーの速さと向きを覚えるだけ。知らない向き・0 以下は止める側に倒す。"""
        if direction == "left":
            sign = 1
        elif direction == "right":
            sign = -1
        else:
            log.warning("知らない向き %s なのでヨーを回さない", direction)
            await self.stop_yaw()
            return
        rate = abs(float(steps_per_s))
        if rate <= 0:
            await self.stop_yaw()
            return
        with self._lock:
            self._yaw_hsps = int(round(rate)) * sign

    async def stop_yaw(self) -> None:
        """ヨーを止める（`M` 行の速さを 0 にする。コイルを切るのは Arduino の役）。"""
        with self._lock:
            self._yaw_hsps = 0

    async def close(self) -> None:
        """送るスレッドを止めてシリアルを閉じる。"""
        self._stop.set()
        thread, self._thread = self._thread, None  # type: ignore[assignment]
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        try:
            self._serial.close()
        except Exception:  # noqa: BLE001 - 片付けでは握りつぶす
            pass

    # --- 中身 ---------------------------------------------------------------------------------

    def _drain(self, now: float) -> list:
        """受信バッファを全部読み出し、読めた行だけを順番どおりに返す（時刻は読んだ時刻）。"""
        rows = []
        serial = self._serial
        for _ in range(_DRAIN_MAX_LINES):
            try:
                waiting = serial.in_waiting
            except Exception:  # noqa: BLE001 - 読めなければここまで
                break
            if not waiting:
                break
            try:
                raw = serial.readline()
            except Exception:  # noqa: BLE001 - 読めなければここまで
                break
            if not raw:
                break
            try:
                text = raw.decode("ascii", errors="strict")
            except (UnicodeDecodeError, ValueError):
                continue
            parsed = parse_io_line(text)
            if parsed is None:
                continue
            rows.append(parsed)
        return rows

    def _send_loop(self) -> None:
        """`io_cmd_period_ms` ごとに `M` 行を送る（止まっている間も。生存確認を兼ねる）。"""
        period_s = float(self._params["io_cmd_period_ms"]) / 1000.0
        while not self._stop.is_set():
            started = time.monotonic()
            with self._lock:
                pitch_ddeg = int(round(self._pitch_deg * 10))
                yaw_hsps = self._yaw_hsps
                seq = self._seq
                self._seq = (self._seq + 1) % 65536
            try:
                line = encode_io_cmd(seq, pitch_ddeg, yaw_hsps)
            except ValueError as exc:
                log.error("M 行を作れない: %s", exc)
                line = ""
            if line:
                try:
                    self._serial.write((line + "\n").encode("ascii"))
                except Exception as exc:  # noqa: BLE001 - 送れなくてもスレッドは止めない
                    log.warning("M 行を送れない: %s", exc)
            remaining = period_s - (time.monotonic() - started)
            if remaining > 0:
                self._stop.wait(remaining)


def _open_serial(params: Mapping[str, Any]) -> Any:
    """シリアルを開く。**`pyserial` はこの中だけで import する。**"""
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("pyserial が無い（UnitV2 の OS には同梱）") from exc
    return serial.Serial(
        str(params["io_device"]),
        int(params["io_baud"]),
        timeout=0,
        write_timeout=1,
    )
