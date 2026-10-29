"""偽の昇降部（`FakeLift`）の連続駆動の上限（spec #4b・`MAX_RUN`）。

上限は「全行程 1800 mm の 150 %」を**目標の速さ**で進んだとみなした距離で数える
（30 mm/s なら 90 秒）。固定の時間ではない。
"""

from __future__ import annotations

from hve_camera.hw.fake_lift import FakeLift
from tests.cam_support import ManualClock


async def _drive(lift: FakeLift, clock: ManualClock, until_ms: int, speed: int, direction: str = "up",
                 press: int = 1) -> str:
    """100 ms ごとに押し直して `until_ms` まで進める。最後の理由を返す。"""
    reason = "NONE"
    while clock.now_ms < until_ms:
        await lift.send_hold(direction=direction, speed=speed, press=press, ceiling_status="MEASURED",
                             ceiling_mm=3000, ceiling_age_ms=0)
        clock.advance(100)
        lift._step(clock.now_ms)
        reason = lift.latest_state()["reason"]
    return reason


async def test_max_run_at_30_mm_s_stops_just_after_90_s() -> None:
    clock = ManualClock(1000.0)
    lift = FakeLift(clock)
    assert await _drive(lift, clock, 1000 + 60000, 30) == "NONE"
    # 偽物の最初の step は hold の 100 ms 後で、その前の周期は回していない扱い。
    # 距離がちょうど 2 700 000 µm になる 90 100 ms までは動かせる（上限は「超えたら」）
    assert await _drive(lift, clock, 1000 + 90100, 30) == "NONE"
    assert await _drive(lift, clock, 1000 + 90200, 30) == "MAX_RUN"


async def test_max_run_sums_distance_when_target_speed_changes() -> None:
    """10 mm/s で 60 秒のあと 60 mm/s に上げても、その場では止まらない。"""
    clock = ManualClock(1000.0)
    lift = FakeLift(clock)
    assert await _drive(lift, clock, 1000 + 60000, 10) == "NONE"
    assert await _drive(lift, clock, 1000 + 60100, 60) == "NONE"
    # 600 000 + 6000 × (n - 601) が 2 700 000 を超える最初の周期は 95 200 ms
    assert await _drive(lift, clock, 1000 + 95100, 60) == "NONE"
    assert await _drive(lift, clock, 1000 + 95200, 60) == "MAX_RUN"


async def test_max_run_allows_the_opposite_direction() -> None:
    clock = ManualClock(1000.0)
    lift = FakeLift(clock)
    assert await _drive(lift, clock, 1000 + 90200, 30) == "MAX_RUN"
    assert await _drive(lift, clock, 1000 + 90300, 30, direction="down", press=2) == "NONE"
    assert lift.latest_state()["dir"] == "down"


async def test_provisional_names_max_run_pct() -> None:
    clock = ManualClock(1000.0)
    lift = FakeLift(clock)
    await _drive(lift, clock, 1200, 30)
    provisional = lift.latest_state()["provisional"]
    assert "LIFT_MAX_RUN_PCT" in provisional
    assert [n for n in provisional if "MAX_RUN" in n] == ["LIFT_MAX_RUN_PCT"]
