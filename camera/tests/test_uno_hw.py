"""`hw/uno_hw.py` の試験。**呼び出し側を通して**変異 1・1b・2 を縛る。

`UnoClock` の純粋な振る舞いは `test_uno_link.py` で縛る。ここでは
「`UnoHardware.read_ceiling()` が `UnoClock` を実際に使っていること」を確かめる。
"""

from __future__ import annotations

import asyncio
from typing import Any

from hve_camera.ceiling import CeilingStatus
from hve_camera.hw.uno_hw import UnoHardware

PARAMS: dict[str, Any] = {
    "io_device": "/dev/ttyS1",
    "io_baud": 115200,
    "io_cmd_period_ms": 50,
    "ceiling_read_stale_ms": 600,
    "uno_clock_window": 20,
    "srf02_min_range_mm": 150,
}


class FakeSerial:
    """`pyserial` の偽物。渡した行を読み出し、書いた行を覚える。"""

    def __init__(self, lines: list[bytes] | None = None) -> None:
        self._buffer = b"".join(lines) if lines else b""
        self.written: list[bytes] = []
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def readline(self) -> bytes:
        head, sep, rest = self._buffer.partition(b"\n")
        if not sep:
            return b""
        self._buffer = rest
        return head + sep

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def feed(self, line: str) -> None:
        self._buffer += line.encode("ascii")

    def close(self) -> None:
        self.closed = True


class ManualClock:
    def __init__(self, start_ms: float = 0.0) -> None:
        self.now_ms = float(start_ms)

    def __call__(self) -> float:
        return self.now_ms

    def advance(self, ms: float) -> float:
        self.now_ms += float(ms)
        return self.now_ms


def make_hw(clock: ManualClock, serial: FakeSerial) -> UnoHardware:
    hw = UnoHardware(PARAMS, clock, serial=serial)
    return hw


async def shutdown(hw: UnoHardware) -> None:
    await hw.close()


# --- 読み出し ------------------------------------------------------------------


async def test_newest_c_line_wins() -> None:
    """変異 2 の芯。読むたびの全部の読み出しをやめ 1 行ずつ処理すると、古い行が残る。"""
    clock = ManualClock(start_ms=10000.0)
    serial = FakeSerial([b"C 1000 0 100\n", b"C 1001 0 200\n"])
    hw = make_hw(clock, serial)
    try:
        reading = await hw.read_ceiling()
        assert reading is not None
        assert reading.status is CeilingStatus.MEASURED
        assert reading.distance_mm == 2000, "最も新しい uno_ms の行を使う"
    finally:
        await shutdown(hw)


async def test_buffered_old_lines_look_old() -> None:
    """変異 1 の芯。`UnoClock` を使わず受け取った時刻を読み値の時刻にすると、
    溜まった古い行が新しく見える。呼び出し側を通して縛る。"""
    clock = ManualClock(start_ms=0.0)
    serial = FakeSerial()
    hw = make_hw(clock, serial)
    try:
        # 普段どおり 100 ms ごとに読み出し、新しい行で窓を作る
        for step in range(5):
            serial.feed("C %d 0 200\n" % (10000 + step * 100,))
            assert await hw.read_ceiling() is not None
            clock.advance(100.0)
        # 溜まった古い行だけが遅れて届く（新しい側は溢れて捨てられた想定）
        serial.feed("C 50 0 200\n")
        serial.feed("C 51 0 200\n")
        reading = await hw.read_ceiling()
        assert reading is not None
        assert reading.status is CeilingStatus.READ_ERROR, "古い行は古いまま見る"
    finally:
        await shutdown(hw)


async def test_discarded_batch_after_a_gap() -> None:
    """変異 1b の芯。前回の読み出しから空いたときはその回の行を捨てる。

    受信バッファが溢れて新しい側の行が無い場合でも、窓の全行が遅れた行になると
    赤になること（捨てないと最小差の基準ごと遅れて新しく見える）。
    """
    clock = ManualClock(start_ms=0.0)
    serial = FakeSerial()
    hw = make_hw(clock, serial)
    try:
        assert await hw.read_ceiling() is None
        clock.advance(5000.0)
        # 溢れたあとに残った古い側の行だけ（新しい側は捨てられた想定）
        serial.feed("C 1000 0 200\n")
        serial.feed("C 1001 0 200\n")
        reading = await hw.read_ceiling()
        assert reading is not None
        assert reading.status is CeilingStatus.READ_ERROR
        # 次の回に読めた行が来れば復帰する
        clock.advance(100.0)
        serial.feed("C 5100 0 200\n")
        reading = await hw.read_ceiling()
        assert reading is not None
        assert (reading.status, reading.distance_mm) == (CeilingStatus.MEASURED, 2000)
    finally:
        await shutdown(hw)


async def test_st_1_is_read_error() -> None:
    clock = ManualClock(start_ms=10000.0)
    serial = FakeSerial([b"C 9900 1 0\n"])
    hw = make_hw(clock, serial)
    try:
        reading = await hw.read_ceiling()
        assert reading is not None
        assert reading.status is CeilingStatus.READ_ERROR
    finally:
        await shutdown(hw)


async def test_no_echo_and_too_near() -> None:
    clock = ManualClock(start_ms=10000.0)
    serial = FakeSerial([b"C 9900 0 0\n"])
    hw = make_hw(clock, serial)
    try:
        reading = await hw.read_ceiling()
        assert reading is not None
        assert reading.status is CeilingStatus.NO_ECHO
    finally:
        await shutdown(hw)


async def test_no_lines_returns_none() -> None:
    clock = ManualClock(start_ms=0.0)
    serial = FakeSerial()
    hw = make_hw(clock, serial)
    try:
        assert await hw.read_ceiling() is None
    finally:
        await shutdown(hw)


async def test_b_line_resets_the_clock() -> None:
    """再起動（`B`）のあとの行が新しいものとして読める。"""
    clock = ManualClock(start_ms=10000.0)
    serial = FakeSerial([b"C 9000 0 200\n", b"B 10 hve_cam_io\n", b"C 20 0 200\n"])
    hw = make_hw(clock, serial)
    try:
        reading = await hw.read_ceiling()
        assert reading is not None
        # `B` で記録を捨てたので、`C 20` の d だけが基準になり新しく見える
        assert (reading.status, reading.distance_mm) == (CeilingStatus.MEASURED, 2000)
    finally:
        await shutdown(hw)


async def test_broken_lines_are_dropped() -> None:
    clock = ManualClock(start_ms=10000.0)
    serial = FakeSerial([b"garbage\n", b"C 9900 0 200\n"])
    hw = make_hw(clock, serial)
    try:
        reading = await hw.read_ceiling()
        assert reading is not None
        assert (reading.status, reading.distance_mm) == (CeilingStatus.MEASURED, 2000)
    finally:
        await shutdown(hw)


# --- 送り ----------------------------------------------------------------------


async def test_m_lines_are_sent_periodically() -> None:
    clock = ManualClock(start_ms=0.0)
    serial = FakeSerial()
    hw = make_hw(clock, serial)
    try:
        await hw.set_pitch(12.3)
        await hw.drive_yaw(68.0, "left")
        serial.written.clear()
        deadline = asyncio.get_running_loop().time() + 2.0
        while not serial.written and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert serial.written, "io_cmd_period_ms ごとに M 行を送る"
        head, seq, pitch, yaw = serial.written[-1].decode().split()
        assert (head, pitch, yaw) == ("M", "123", "68")
    finally:
        await shutdown(hw)


async def test_stop_yaw_sends_zero_speed() -> None:
    clock = ManualClock(start_ms=0.0)
    serial = FakeSerial()
    hw = make_hw(clock, serial)
    try:
        await hw.drive_yaw(68.0, "left")
        await hw.stop_yaw()
        serial.written.clear()
        deadline = asyncio.get_running_loop().time() + 2.0
        while not serial.written and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert serial.written, "止まっている間も M 行を送る"
        assert serial.written[-1].endswith(b" 0\n")
    finally:
        await shutdown(hw)


async def test_b_line_with_leading_nul_bytes_resets_the_clock() -> None:
    """実機のリセット後の 1 行目（先頭に NUL が 2 つ）も `B` として読み、記録を捨てる。"""
    clock = ManualClock(start_ms=10000.0)
    serial = FakeSerial([b"C 9000 0 200\n", b"\x00\x00B 0 hve_cam_io-0.1.0\n", b"C 20 0 200\n"])
    hw = make_hw(clock, serial)
    try:
        reading = await hw.read_ceiling()
        assert reading is not None
        assert (reading.status, reading.distance_mm) == (CeilingStatus.MEASURED, 2000)
    finally:
        await shutdown(hw)


# --- 外し忘れの省電力（`WP-CAM-13`。送信ループの切り替え） ------------------------------


async def _next_lines(serial: FakeSerial, count: int = 3) -> list:
    """いまから `count` 行以上送られるのを待ち、その行（文字列）を返す。"""
    mark = len(serial.written)
    deadline = asyncio.get_running_loop().time() + 2.0
    while len(serial.written) < mark + count and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert len(serial.written) >= mark + count, "送信ループが行を送らない"
    return [w.decode("ascii").strip() for w in serial.written[mark:]]


async def test_saving_sends_r_lines_instead_of_m() -> None:
    clock = ManualClock(start_ms=0.0)
    serial = FakeSerial()
    hw = make_hw(clock, serial)
    try:
        await hw.set_pitch(12.3)
        await hw.drive_yaw(68.0, "left")
        await hw.set_power_mode("saving", -10.0)
        await asyncio.sleep(0.2)  # 切り替え前に作りかけた行を流す
        lines = await _next_lines(serial, 5)
        assert all(line.startswith("R ") for line in lines), "saving では M を送らない"
        seqs = [int(line.split()[1]) for line in lines]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "連番は M と同じく進む"
    finally:
        await shutdown(hw)


async def test_settling_sends_m_with_the_save_pitch_and_keeps_the_target() -> None:
    clock = ManualClock(start_ms=0.0)
    serial = FakeSerial()
    hw = make_hw(clock, serial)
    try:
        await hw.set_pitch(12.3)
        await hw.set_power_mode("settling", -10.0)
        await asyncio.sleep(0.2)
        lines = await _next_lines(serial)
        assert all(line.startswith("M ") and line.split()[2] == "-100" for line in lines)
        # 軸の目標角は書き換わっていない: 戻れば目標角で送る
        await hw.set_power_mode("active", -10.0)
        await asyncio.sleep(0.2)
        lines = await _next_lines(serial)
        assert all(line.startswith("M ") and line.split()[2] == "123" for line in lines)
    finally:
        await shutdown(hw)


async def test_returning_to_active_sends_m_again() -> None:
    clock = ManualClock(start_ms=0.0)
    serial = FakeSerial()
    hw = make_hw(clock, serial)
    try:
        await hw.set_pitch(5.0)
        await hw.set_power_mode("saving", -10.0)
        await asyncio.sleep(0.2)
        await hw.set_power_mode("active", -10.0)
        await asyncio.sleep(0.2)
        lines = await _next_lines(serial)
        assert all(line.startswith("M ") and line.split()[2] == "50" for line in lines)
    finally:
        await shutdown(hw)


async def test_unknown_power_mode_is_rejected() -> None:
    import pytest

    clock = ManualClock(start_ms=0.0)
    hw = make_hw(clock, FakeSerial())
    try:
        with pytest.raises(ValueError):
            await hw.set_power_mode("sleep", 0.0)
    finally:
        await shutdown(hw)
