"""`classify_srf02()` の試験。protocol §5 の表を上から順に全て覆う。"""

from hve_camera.ceiling import CeilingStatus, classify_srf02

PARAMS = {
    "ceiling_read_stale_ms": 600,
    "srf02_min_range_mm": 150,
}


def classify(st=None, cm=None, age_ms=0):
    return classify_srf02(st, cm, age_ms, PARAMS)


# --- 1 行目: st が 1・古い・行が来ていない → READ_ERROR ---------------------------------------


def test_st_1_is_read_error():
    assert classify(st=1, cm=200, age_ms=0) == (CeilingStatus.READ_ERROR, None)


def test_st_1_is_not_confused_with_no_echo():
    """変異 6 の芯。`st=1` を `NO_ECHO` にしたらここで赤になる。"""
    status, _mm = classify(st=1, cm=0, age_ms=0)
    assert status is CeilingStatus.READ_ERROR


def test_stale_age_is_read_error():
    assert classify(st=0, cm=200, age_ms=601) == (CeilingStatus.READ_ERROR, None)


def test_exactly_stale_ms_is_not_read_error():
    assert classify(st=0, cm=200, age_ms=600) == (CeilingStatus.MEASURED, 2000)


def test_missing_line_is_read_error():
    assert classify(st=None, cm=200, age_ms=0) == (CeilingStatus.READ_ERROR, None)
    assert classify(st=0, cm=None, age_ms=0) == (CeilingStatus.READ_ERROR, None)
    assert classify(st=0, cm=200, age_ms=None) == (CeilingStatus.READ_ERROR, None)


def test_stale_wins_over_no_echo_and_too_near():
    assert classify(st=0, cm=0, age_ms=601) == (CeilingStatus.READ_ERROR, None)
    assert classify(st=0, cm=10, age_ms=601) == (CeilingStatus.READ_ERROR, None)


# --- 2 行目: cm が 0 → NO_ECHO ---------------------------------------------------------------


def test_zero_cm_is_no_echo():
    assert classify(st=0, cm=0, age_ms=0) == (CeilingStatus.NO_ECHO, None)


# --- 3 行目: 最小測定距離より近い → TOO_NEAR --------------------------------------------------


def test_below_the_minimum_range_is_too_near():
    assert classify(st=0, cm=14, age_ms=0) == (CeilingStatus.TOO_NEAR, None)


def test_exactly_the_minimum_range_is_measured():
    assert classify(st=0, cm=15, age_ms=0) == (CeilingStatus.MEASURED, 150)


# --- 4 行目: それ以外 → MEASURED --------------------------------------------------------------


def test_measured_returns_cm_times_10():
    assert classify(st=0, cm=200, age_ms=0) == (CeilingStatus.MEASURED, 2000)


def test_status_values_match_the_protocol():
    assert [status.value for status in CeilingStatus] == [
        "MEASURED",
        "TOO_NEAR",
        "NO_ECHO",
        "READ_ERROR",
    ]
