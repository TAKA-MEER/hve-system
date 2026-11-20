"""`lift_goto`（WP-CAM-17）の試験。{網羅: protocol §4.1・packets WP-CAM-17 変異 1〜11}"""

from __future__ import annotations

from typing import Any

from hve_camera.ceiling import CeilingStatus
from hve_camera.control import ControlLoop
from hve_camera.hw.fake_hw import FakeHardware
from hve_camera.hw.fake_lift import FakeLift
from tests.cam_support import PARAMS, SETTINGS, ManualClock, RecordingVideoZoom


async def test_lift_goto_counts_as_touch() -> None:
    """変異 8: `lift_goto` を `touch()` に数える（省電力から戻す）"""
    from hve_camera.app import CameraApp, VideoZoom
    from hve_camera.hw.fake_power import FakePower
    clock = ManualClock()
    hw = FakeHardware(clock)
    hw.set_steady_ceiling(CeilingStatus.MEASURED, 2000)
    lift = FakeLift(clock)
    app = CameraApp(hw, lift, settings=dict(SETTINGS), using_defaults=True, params=PARAMS,
                    clock=clock, video_zoom=VideoZoom(8080), fake=True, autostart=False, power_hw=FakePower())
    calls: list = []
    async def _touch() -> None:
        calls.append(1)
    app._touch = _touch  # noqa: SLF001
    await app._on_message(None, '{"t":"lift_goto","id":"g1","mode":"target","target_mm":800,"speed":30}')  # noqa: SLF001
    assert calls == [1], calls


class Rig:
    def __init__(self) -> None:
        self.clock = ManualClock()
        self.hw = FakeHardware(self.clock)
        self.hw.set_steady_ceiling(CeilingStatus.MEASURED, 2000)
        self.lift = FakeLift(self.clock)
        self.zoom = RecordingVideoZoom()
        self.loop = ControlLoop(self.hw, self.lift, settings=SETTINGS, params=PARAMS,
                                clock=self.clock, video_zoom=self.zoom, fake=True)

    async def prime(self) -> None:
        await self.loop.step()
        self.lift._step(self.clock())  # noqa: SLF001

    async def step(self, ms: float = 100) -> Any:
        self.clock.advance(ms)
        await self.loop.step()
        self.lift._step(self.clock())  # noqa: SLF001
        return self.loop.build_state(1)


async def _arrive(rig: Rig) -> Any:
    """高さ 1200・目標 1230 で着かせる（制御側が着いたのを受け取るまで回す）"""
    rig.lift.set_height_mm(1200)
    rig.loop.lift_goto("g1", "target", 1230, 30)
    for _ in range(12):
        rig.loop.lift_goto("g1", "target", 1230, 30)
        await rig.step()
        if rig.loop.build_state(1)["lift_goto"]["status"] == "ARRIVED":
            return rig.loop.build_state(1)
    return rig.loop.build_state(1)


async def test_goto_carries_ceiling_triple() -> None:
    """変異 1: 昇降部へ送る `goto` に天井を載せる"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(500)
    rig.loop.lift_goto("g1", "target", 800, 30)
    await rig.step()
    assert rig.lift.goto_history, "goto が送られていない"
    goto = rig.lift.goto_history[-1]
    assert goto["ceiling"]["status"] == "MEASURED" and goto["ceiling"]["mm"] == 2000
    assert goto["ceiling"]["age_ms"] >= 0


async def test_press_growth_and_repeated_id() -> None:
    """変異 2: 新しい `id` で `press` を増やす（同じ `id` の再送は増やさない）"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(500)
    rig.loop.lift_goto("g1", "target", 800, 30)
    await rig.step()
    await rig.step()
    presses = [g["press"] for g in rig.lift.goto_history]
    assert presses == [1, 1], presses
    rig.loop.lift_goto("g2", "target", 800, 30)
    await rig.step()
    assert rig.lift.goto_history[-1]["press"] == 2
    rig.loop.lift_goto("g2", "target", 800, 30)
    await rig.step()
    assert rig.lift.goto_history[-1]["press"] == 2


async def test_finished_id_is_not_resent() -> None:
    """変異 3: 着いた `id` が来ても `goto` を送らない"""
    rig = Rig()
    await rig.prime()
    await _arrive(rig)
    state = rig.loop.build_state(1)  # 昇降部の着いた `state` を受けて終わらせる
    assert state["lift_goto"]["status"] == "ARRIVED", state["lift_goto"]
    count = len(rig.lift.goto_history)
    rig.loop.lift_goto("g1", "target", 1230, 30)
    await rig.step()
    assert len(rig.lift.goto_history) == count, "終わった id を送っている"
    assert rig.loop.build_state(1)["lift_goto"]["status"] == "ARRIVED"


async def test_timeout_finishes_goto() -> None:
    """変異 4・10: 途切れたら `NOT_ARRIVED`・`CMD_STOP`。同じ `id` が届いても再開しない"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(500)
    rig.loop.lift_goto("g1", "target", 990, 30)
    await rig.step()
    count = len(rig.lift.goto_history)
    rig.clock.advance(500)  # hold_timeout_ms(400) を超える
    await rig.loop.step()
    rig.lift._step(rig.clock())  # noqa: SLF001
    state = rig.loop.build_state(1)
    assert state["lift_goto"] == {"id": "g1", "mode": "target", "target_mm": 990,
                                  "status": "NOT_ARRIVED", "reason": "CMD_STOP"}, state["lift_goto"]
    rig.loop.lift_goto("g1", "target", 990, 30)  # 同じ id が届く（再開してはいけない）
    await rig.step()
    assert len(rig.lift.goto_history) == count


async def test_bottom_arrives_and_stops() -> None:
    """変異 5: `mode: bottom` が下端に着いたら下降を送るのをやめる"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(400)
    rig.loop.lift_goto("b1", "bottom", None, 30)
    await rig.step()
    rig.loop.lift_goto("b1", "bottom", None, 30)
    rig.lift.set_bottom(True)
    await rig.step()
    state = await rig.step()
    assert state["lift_goto"]["status"] == "ARRIVED", state["lift_goto"]
    count = len(rig.lift.hold_history)
    await rig.step()
    await rig.step()
    assert len(rig.lift.hold_history) == count, "下端に着いたのに下降を送り続ける"


async def test_other_screen_hold_marks_preempted() -> None:
    """変異 6: 別の画面の `hold` に替わったら前の `lift_goto` は `NOT_ARRIVED`・`PREEMPTED`"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(500)
    rig.loop.lift_goto("g1", "target", 990, 30)
    await rig.step()
    rig.loop.hold("lift_down", 30, owner=object())
    state = await rig.step()
    assert state["lift_goto"] == {"id": "g1", "mode": "target", "target_mm": 990,
                                  "status": "NOT_ARRIVED", "reason": "PREEMPTED"}, state["lift_goto"]
    count = len(rig.lift.goto_history)
    await rig.step()
    assert len(rig.lift.goto_history) == count, "替わったあとも goto を送っている"


async def test_link_loss_finishes_and_no_resume_on_reconnect() -> None:
    """変異 9: 接続が切れたら `LINK_LOST` で終わらせ、繋ぎ直しても同じ `id` を送らない"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(500)
    for _ in range(8):
        rig.loop.lift_goto("g1", "target", 990, 30)
        rig.clock.advance(100)
        await rig.loop.step()
    count = len(rig.lift.goto_history)
    state = rig.loop.build_state(1)
    assert state["lift_goto"] == {"id": "g1", "mode": "target", "target_mm": 990,
                                  "status": "NOT_ARRIVED", "reason": "LINK_LOST"}, state["lift_goto"]
    # 繋ぎ直し（新しい接続）を Control に渡し直す。同じ id は送らない
    rig.loop._lift = rig.lift  # noqa: SLF001
    rig.loop._lift_obj_prev = None  # noqa: SLF001
    rig.loop.lift_goto("g1", "target", 990, 30)
    await rig.step()
    assert len(rig.lift.goto_history) == count


async def test_bottom_preempted_when_owner_name_changes() -> None:
    """変異 11: `mode: bottom` で昇降部の `owner_name` が `module_name` でなくなったら `PREEMPTED`"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(400)
    rig.lift.set_height_mm(400)
    for _ in range(12):
        rig.loop.lift_goto("b1", "bottom", None, 30)
        await rig.step()
    rig.lift.set_other_owner("fk")  # 別の上部モジュールが持ち主
    await rig.step()
    state = await rig.step()
    state = rig.loop.build_state(1)
    assert state["lift_goto"] == {"id": "b1", "mode": "bottom", "target_mm": None,
                                  "status": "NOT_ARRIVED", "reason": "PREEMPTED"}, state["lift_goto"]


async def test_target_out_of_range_finishes() -> None:
    """着かなかった（TARGET_RANGE）のときの state.lift_goto"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(500)
    rig.loop.lift_goto("g1", "target", 99999, 30)
    await rig.step()
    state = await rig.step()
    assert state["lift_goto"]["status"] == "NOT_ARRIVED", state["lift_goto"]
    assert state["lift_goto"]["reason"] == "TARGET_RANGE", state["lift_goto"]


async def test_target_arrives_and_state_shape() -> None:
    """着いたら `state.lift_goto` が MOVING → ARRIVED になる"""
    rig = Rig()
    await rig.prime()
    rig.lift.set_height_mm(500)
    rig.loop.lift_goto("g1", "target", 800, 30)
    state = await rig.step()
    assert state["lift_goto"] == {"id": "g1", "mode": "target", "target_mm": 800,
                                  "status": "MOVING", "reason": "NONE"}, state["lift_goto"]
    rig.lift.set_height_mm(805)
    await rig.step()
    state = await rig.step()
    assert state["lift_goto"]["status"] == "ARRIVED", state["lift_goto"]
