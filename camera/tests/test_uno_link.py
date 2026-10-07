"""`uno_link.py` の試験。行の形は `io_codec.{h,cpp}` と同じ。"""

import pytest

from hve_camera.uno_link import UnoClock, encode_io_cmd, parse_io_line


# --- encode_io_cmd ------------------------------------------------------------


def test_encode_io_cmd():
    assert encode_io_cmd(0, 0, 0) == "M 0 0 0"
    assert encode_io_cmd(65535, -450, -680) == "M 65535 -450 -680"


def test_encode_io_cmd_rejects_bad_values():
    with pytest.raises(ValueError):
        encode_io_cmd(65536, 0, 0)
    with pytest.raises(ValueError):
        encode_io_cmd(-1, 0, 0)
    with pytest.raises(ValueError):
        encode_io_cmd(0.0, 0, 0)
    with pytest.raises(ValueError):
        encode_io_cmd(True, 0, 0)


# --- parse_io_line ------------------------------------------------------------


def test_parse_c_line():
    assert parse_io_line("C 12345 0 200") == ("C", 12345, 0, 200)
    assert parse_io_line("C 0 1 0") == ("C", 0, 1, 0)


def test_parse_b_line():
    assert parse_io_line("B 99 hve_cam_io") == ("B", 99, "hve_cam_io")


def test_parse_rejects_broken_lines():
    assert parse_io_line("M 1 0 0") is None, "M 行は UnitV2 → Arduino の向き"
    assert parse_io_line("C 1 0") is None
    assert parse_io_line("C 1 0 200 余分") is None
    assert parse_io_line("C -1 0 200") is None
    assert parse_io_line("C x 0 200") is None
    assert parse_io_line("C 1.5 0 200") is None
    assert parse_io_line("C  1 0 200") is None, "空白は 1 つだけ"
    assert parse_io_line("B 1 ") is None
    assert parse_io_line("B 1 に 空白") is None
    assert parse_io_line("") is None
    assert parse_io_line(None) is None
    assert parse_io_line("C 1 0 200\n") == ("C", 1, 0, 200), "行末の改行は落とす"
    assert parse_io_line("X" * 33) is None, "IO_LINE_MAX を超える行は捨てる"


# --- UnoClock -----------------------------------------------------------------


def test_uno_clock_measures_the_age():
    clock = UnoClock(window=20)
    assert clock.age_ms(1000, 1100.0) is None, "まだ 1 行も見ていない"
    age, restarted = clock.observe(received_ms=1100.0, uno_ms=1000)
    assert (age, restarted) == (0.0, False), "最初の 1 行の古さは分からない"
    assert clock.age_ms(1000, 1100.0) == pytest.approx(0.0)
    assert clock.age_ms(1000, 1200.0) == pytest.approx(100.0)


def test_uno_clock_uses_the_minimum_offset():
    """遅れて読んだ行の d は大きいので、直近の最小が基準になる（§3.5）。"""
    clock = UnoClock(window=20)
    clock.observe(received_ms=1100.0, uno_ms=1000)  # d=100
    age, restarted = clock.observe(received_ms=1500.0, uno_ms=1000)  # d=500（溜まった行を遅れて読んだ）
    assert restarted is False
    assert age == pytest.approx(400.0)


def test_uno_clock_measures_before_restarting_on_rewind():
    """巻き戻り（再起動か遅れた古い行）は、捨てる前の窓で古さを測ってからやり直す。"""
    clock = UnoClock(window=20)
    clock.observe(received_ms=1100.0, uno_ms=1000)
    age, restarted = clock.observe(received_ms=1200.0, uno_ms=10)
    assert restarted is True
    assert age == pytest.approx(1090.0), "古い窓では uno=1000 の 990 ms 前の行＋経過 100 ms"
    age, restarted = clock.observe(received_ms=1250.0, uno_ms=20)
    assert (restarted, age) == (False, pytest.approx(60.0))


def test_uno_clock_reset():
    clock = UnoClock(window=20)
    clock.observe(received_ms=1100.0, uno_ms=1000)
    clock.reset()
    assert clock.age_ms(1000, 1100.0) is None


def test_uno_clock_rejects_a_bad_window():
    with pytest.raises(ValueError):
        UnoClock(window=0)
