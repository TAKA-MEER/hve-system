"""`ceiling_permission()` の試験。上から順に最初に当たったもの」を全て覆う。"""

import pytest

from hve_camera.ceiling import CeilingReading, CeilingStatus, ceiling_permission
from hve_camera.params import load_params

STALE_MS = 600
MIN_RANGE_MM = 150
MARGIN_MM = 500

PARAMS = {
    "ceiling_stale_ms": STALE_MS,
    "srf02_min_range_mm": MIN_RANGE_MM,
    "ceiling_margin_mm": MARGIN_MM,
}


def measured(distance_mm, at_ms=0):
    return CeilingReading(CeilingStatus.MEASURED, distance_mm=distance_mm, at_ms=at_ms)


def no_echo(at_ms=0):
    return CeilingReading(CeilingStatus.NO_ECHO, at_ms=at_ms)


def read_error(at_ms=0):
    return CeilingReading(CeilingStatus.READ_ERROR, at_ms=at_ms)


# --- 1 行目: 読み値が無い・読み取り失敗・古い ------------------------------------------------


def test_reading_is_none_is_stale():
    assert ceiling_permission(None, 0, PARAMS) == (False, "CEILING_STALE")


def test_i2c_read_error_is_stale():
    assert ceiling_permission(read_error(at_ms=0), 0, PARAMS) == (False, "CEILING_STALE")


def test_reading_without_a_timestamp_is_stale():
    reading = CeilingReading(CeilingStatus.MEASURED, distance_mm=2000, at_ms=None)
    assert ceiling_permission(reading, 0, PARAMS) == (False, "CEILING_STALE")


def test_stale_when_the_age_exceeds_ceiling_stale_ms():
    assert ceiling_permission(measured(2000, at_ms=0), STALE_MS + 1, PARAMS) == (
        False,
        "CEILING_STALE",
    )


def test_not_stale_at_exactly_ceiling_stale_ms():
    assert ceiling_permission(measured(2000, at_ms=0), STALE_MS, PARAMS) == (True, "NONE")


def test_ceiling_stale_wins_over_out_of_range():
    """古い反射なしは「反射なし」であって「古い」ではない。"""
    assert ceiling_permission(no_echo(at_ms=0), STALE_MS + 1, PARAMS) == (
        False,
        "CEILING_STALE",
    )


def test_ceiling_stale_wins_over_ceiling_near():
    assert ceiling_permission(measured(10, at_ms=0), STALE_MS + 1, PARAMS) == (
        False,
        "CEILING_STALE",
    )


# --- 2 行目: 反射なしは上昇を許す -------------------------------------------------------------


def test_no_echo_allows_up():
    assert ceiling_permission(no_echo(at_ms=0), 0, PARAMS) == (True, "OUT_OF_RANGE")


def test_no_echo_is_not_confused_with_read_error():
    """変異の芯。`READ_ERROR` を「反射なし（上昇可）」にしたらここで赤になる。"""
    assert ceiling_permission(read_error(at_ms=0), 0, PARAMS)[0] is False
    assert ceiling_permission(no_echo(at_ms=0), 0, PARAMS)[0] is True


# --- 3 行目: 最小測定距離より近い ---------------------------------------------------------------


def test_below_the_minimum_range_is_near():
    assert ceiling_permission(measured(MIN_RANGE_MM - 1, at_ms=0), 0, PARAMS) == (
        False,
        "CEILING_NEAR",
    )


def test_exactly_the_minimum_range_is_not_closer_than_the_minimum():
    """最小測定距離そのもの（150 mm）は「近すぎて測れない」ではない。#4 の判定に進む。"""
    assert ceiling_permission(measured(MIN_RANGE_MM, at_ms=0), 0, PARAMS) == (
        False,
        "CEILING_NEAR",
    )  # 150 ≦ 500 なので #4 が当たる


def test_zero_mm_is_near_not_stale():
    """0 mm は「反射なし」ではない（反射なしは NO_ECHO として別に受ける）。"""
    assert ceiling_permission(measured(0, at_ms=0), 0, PARAMS) == (False, "CEILING_NEAR")


def test_the_minimum_range_is_judged_apart_from_the_margin():
    """#3a（近すぎて測れない）と #3（余裕より近い）は別の規則。

    既定の値（150 / 500）では「150 未満」は必ず「500 以下」でもあるので、
    上の 2 行だけではどちらの規則が効いているのかを見分けられない。
    `srf02_min_range_mm` を `ceiling_margin_mm` より大きくした設定で区別する。
    """
    params = dict(PARAMS, srf02_min_range_mm=900, ceiling_margin_mm=500)
    # 余裕（500）は過ぎるが最小測定距離（900）より近い → #3a で止める
    assert ceiling_permission(measured(700, at_ms=0), 0, params) == (False, "CEILING_NEAR")
    # 最小測定距離を越えれば通る
    assert ceiling_permission(measured(901, at_ms=0), 0, params) == (True, "NONE")


# --- 4 行目: 余裕以下 -------------------------------------------------------------------------


def test_within_the_margin_is_near():
    assert ceiling_permission(measured(MARGIN_MM - 1, at_ms=0), 0, PARAMS) == (
        False,
        "CEILING_NEAR",
    )


def test_exactly_the_margin_is_near():
    """境界。`ceiling_margin_mm` ちょうどでも上昇を許さない（ブリーフ §2 の表の `≦`）。"""
    assert ceiling_permission(measured(MARGIN_MM, at_ms=0), 0, PARAMS) == (
        False,
        "CEILING_NEAR",
    )


# --- 5 行目: それ以外 -------------------------------------------------------------------------


def test_far_enough_allows_up():
    assert ceiling_permission(measured(MARGIN_MM + 1, at_ms=0), 0, PARAMS) == (True, "NONE")


@pytest.mark.parametrize(
    "distance_mm",
    [MARGIN_MM + 1, 1000, 5999, load_params()["srf02_max_range_mm"]],
)
def test_anything_farther_than_the_margin_allows_up(distance_mm):
    assert ceiling_permission(measured(distance_mm, at_ms=0), 0, PARAMS) == (True, "NONE")


# --- 原因と理由 ------------------------------------------------------------------------------


def test_every_reason_is_a_name_from_names_section_3():
    names_section_3 = {
        "NONE",
        "CEILING_NEAR",
        "CEILING_STALE",
        "OUT_OF_RANGE",
    }
    seen = {
        ceiling_permission(None, 0, PARAMS)[1],
        ceiling_permission(read_error(at_ms=0), 0, PARAMS)[1],
        ceiling_permission(measured(2000, at_ms=0), STALE_MS + 1, PARAMS)[1],
        ceiling_permission(no_echo(at_ms=0), 0, PARAMS)[1],
        ceiling_permission(measured(10, at_ms=0), 0, PARAMS)[1],
        ceiling_permission(measured(2000, at_ms=0), 0, PARAMS)[1],
    }
    assert seen <= names_section_3


def test_status_values_are_distinct_strings():
    """`READ_ERROR` と `NO_ECHO` が同じ値に潰れていないことの確認。"""
    values = [status.value for status in CeilingStatus]
    assert values == ["MEASURED", "NO_ECHO", "READ_ERROR"]
    assert len(set(values)) == 3


def test_reading_carries_distance_status_and_time():
    reading = CeilingReading(CeilingStatus.MEASURED, distance_mm=1234, at_ms=5678)
    assert (reading.status, reading.distance_mm, reading.at_ms) == (
        CeilingStatus.MEASURED,
        1234,
        5678,
    )


def test_params_toml_has_the_thresholds_ceiling_permission_reads():
    params = load_params()
    for name in ("ceiling_stale_ms", "srf02_min_range_mm", "ceiling_margin_mm"):
        assert isinstance(params[name], (int, float)), name
