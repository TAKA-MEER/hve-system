"""`ControlLoop` の試験。**呼び出し側の数行**（`hold`・`release`・`press`・天井の値）を縛る。

[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §3 と
[-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §1・§2・§4。
時計は `ManualClock` で自分で進めるので、待ち時間を作らずに確かめられる。
"""

from __future__ import annotations

from typing import Any

from hve_camera.ceiling import CeilingStatus
from hve_camera.control import ControlLoop, clamp_zoom
from hve_camera.hw.fake_hw import FakeHardware
from hve_camera.hw.fake_lift import FakeLift
from tests.cam_support import PARAMS, SETTINGS, ManualClock, RecordingVideoZoom


class RecordingLift(FakeLift):
    """`FakeLift` の派生。指令の**順番**が分かるように記録する。"""

    def __init__(self, clock: Any, log: list[str]) -> None:
        super().__init__(clock)
        self._log = log

    async def send_hold(self, *args: Any, **kwargs: Any) -> None:
        self._log.append("hold")
        await super().send_hold(*args, **kwargs)

    async def send_release(self, *args: Any, **kwargs: Any) -> None:
        self._log.append("release")
        await super().send_release(*args, **kwargs)


class RecordingHw(FakeHardware):
    """`FakeHardware` の同上。天井を読んだ順番が分かる。"""

    def __init__(self, clock: Any, log: list[str]) -> None:
        super().__init__(clock)
        self._log = log

    async def read_ceiling(self) -> Any:
        self._log.append("read")
        return await super().read_ceiling()


class Loop:
    """試験用に `ControlLoop` と偽物をまとめる。"""

    def __init__(self) -> None:
        self.clock = ManualClock()
        self.log: list[str] = []
        self.hw = RecordingHw(self.clock, self.log)
        self.hw.set_steady_ceiling(CeilingStatus.MEASURED, 2000)
        self.lift = RecordingLift(self.clock, self.log)
        self.zoom = RecordingVideoZoom()
        self.loop = ControlLoop(
            self.hw,
            self.lift,
            settings=SETTINGS,
            params=PARAMS,
            clock=self.clock,
            video_zoom=self.zoom,
            fake=True,
        )

    async def prime(self) -> None:
        """**1 回回してから始める。**昇降部の `state` を手に入れるため。"""
        await self.loop.step()
        self.lift._step(self.clock())  # noqa: SLF001 - 試験用の priming

    async def step(self, ms: float = 100) -> Any:
        """時計を進めて 1 回回し、偽昇降部の `state` も進める。"""
        self.clock.advance(ms)
        await self.loop.step()
        self.lift._step(self.clock())
        return self.loop.build_state(1)

    @property
    def holds(self) -> list[dict[str, Any]]:
        return self.lift.hold_history

    @property
    def releases(self) -> list[dict[str, Any]]:
        return self.lift.release_history


# --- 指令の順番と止めている間の沈黙 ---------------------------------------------------------------


async def test_hold_is_sent_before_ceiling_is_read() -> None:
    """DetailedDesign §4.3「天井の測定は指令を送ったあと」。"""
    rig = Loop()
    rig.loop.hold("lift_up", 30)
    await rig.step()
    assert rig.log[0] == "hold", "まず昇降部への指令を送ってから天井を読む"
    assert rig.log[1] == "read"


async def test_nothing_is_sent_while_idle() -> None:
    """止まっている間は `hold` を送らない（protocol §1）。"""
    rig = Loop()
    for _ in range(3):
        await rig.step()
    assert rig.holds == []
    assert rig.releases == []


async def test_release_is_sent_once_when_released() -> None:
    """**変異 4 の芯。**離したら `release` を送る。送らなければ昇降部は止まらない。

    呼び出し側（`ControlLoop` → `LiftPort`）を通して縛る。
    """
    rig = Loop()
    await rig.prime()
    rig.loop.hold("lift_up", 30)
    await rig.step()
    assert len(rig.holds) == 1

    rig.loop.release()
    await rig.step()
    assert [r["press"] for r in rig.releases] == [1], "その `press` の `release` を送る"
    await rig.step()
    assert len(rig.releases) == 1, "`release` は 1 度だけ"


async def test_hold_carries_the_ceiling_value() -> None:
    """`hold` に載るのはその瞬間の天井の値。前回の使い回しでないこと。"""
    rig = Loop()
    await rig.prime()
    rig.loop.hold("lift_up", 30)
    await rig.step()
    assert rig.holds[-1]["ceiling"]["status"] == "MEASURED"
    assert rig.holds[-1]["ceiling"]["mm"] == 2000

    rig.hw.set_ceiling(CeilingStatus.TOO_NEAR)
    await rig.step()  # この回で近い読み値を取り込む（送るのは前回の値）
    await rig.step()  # 送るのはこのとき
    assert rig.holds[-1]["ceiling"]["status"] == "TOO_NEAR"
    assert rig.holds[-1]["ceiling"]["mm"] is None


# --- press の採番 ----------------------------------------------------------------------------------


async def test_press_starts_at_1_and_stays_while_holding() -> None:
    rig = Loop()
    await rig.prime()
    rig.loop.hold("lift_up", 30)
    await rig.step()
    await rig.step()
    assert [h["press"] for h in rig.holds] == [1, 1]


async def test_press_increases_when_the_direction_flips() -> None:
    """`lift_up` と `lift_down` が入れ替わったら `press` を増やす（取って代わるため）。"""
    rig = Loop()
    await rig.prime()
    rig.loop.hold("lift_up", 30)
    await rig.step()
    rig.loop.hold("lift_down", 30)
    await rig.step()
    assert [h["press"] for h in rig.holds] == [1, 2]
    assert rig.releases == [], "入れ替えに `release` は要らない（新しい `press` が勝つ）"


async def test_press_increases_when_another_screen_takes_over() -> None:
    """別の画面の `hold` に替わったら `press` を増やす。"""
    rig = Loop()
    await rig.prime()
    screen_a, screen_b = object(), object()
    rig.loop.hold("lift_up", 30, owner=screen_a)
    await rig.step()
    rig.loop.hold("lift_up", 30, owner=screen_b)
    await rig.step()
    assert [h["press"] for h in rig.holds] == [1, 2]


async def test_press_increases_after_a_release() -> None:
    """止まったあとは同じ `press` では動けないので、押し直したら増やす。"""
    rig = Loop()
    await rig.prime()
    rig.loop.hold("lift_up", 30)
    await rig.step()
    rig.loop.release()
    await rig.step()
    rig.loop.hold("lift_up", 30)
    await rig.step()
    assert [h["press"] for h in rig.holds] == [1, 2]


async def test_stale_press_does_not_restart_the_fake_lift() -> None:
    """止まったあと、古い `press` の `hold` では動き出さない（§3.3。偽昇降部側の規則）。"""
    rig = Loop()
    await rig.prime()
    rig.loop.hold("lift_up", 30)
    await rig.step()
    rig.loop.release()
    await rig.step()
    assert rig.lift.latest_state()["dir"] == "stop"
    # 遅れて届いた古い `press` の `hold`（UnitV2 が固まって溜まった想定）
    await rig.lift.send_hold("up", 30, 1, "MEASURED", 2000, 0)
    rig.lift._step(rig.clock())  # noqa: SLF001
    assert rig.lift.latest_state()["dir"] == "stop"


# --- 天井・途絶の表示 -------------------------------------------------------------------------------


async def test_close_ceiling_stops_the_fake_lift_up() -> None:
    """天井が近いと偽昇降部が上昇を止める（判定は昇降部側。ここは値を運ぶだけ）。"""
    rig = Loop()
    await rig.prime()
    rig.hw.set_steady_ceiling(CeilingStatus.TOO_NEAR)
    rig.loop.hold("lift_up", 30)
    await rig.step()  # 近い読み値を取り込む
    state = await rig.step()  # 送って止まるのはこのとき
    assert state["lift"]["reason"] == "CEILING_NEAR"
    assert state["reason"] == "CEILING_NEAR", "天井の理由は昇降部の判定を使う"
    assert state["ceiling"]["status"] == "TOO_NEAR"


async def test_idle_ceiling_shows_own_measurement_without_verdict() -> None:
    """止まっている間は、自分で測った値だけを載せ、判定（ok・reason）は null（protocol §4）。

    偽昇降部は止まっていても `ceiling`（`CEILING_STALE`）を返してくるので、
    持ち主でないときに判定を拾わないことを縛る。
    """
    rig = Loop()
    await rig.prime()
    state = await rig.step()
    assert state["lift"]["owner"] is None
    assert rig.lift.latest_state()["ceiling"]["reason"] == "CEILING_STALE", "前提: 昇降部は返す"
    assert state["ceiling"]["status"] == "MEASURED"
    assert state["ceiling"]["mm"] == 2000
    assert state["ceiling"]["ok"] is None
    assert state["ceiling"]["reason"] is None
    assert state["reason"] == "NONE", "止まっている間に CEILING_STALE を停止理由に出さない"


async def test_verdict_only_while_module_is_owner() -> None:
    """持ち主のあいだは昇降部の判定を載せ、離すと null に戻る。"""
    rig = Loop()
    await rig.prime()
    rig.loop.hold("lift_up", 30)
    state = await rig.step()
    assert state["ceiling"]["ok"] is True
    assert state["ceiling"]["reason"] == "NONE"
    rig.loop.release()
    await rig.step()
    state = await rig.step(700)
    assert state["lift"]["owner"] is None
    assert state["ceiling"]["ok"] is None
    assert state["ceiling"]["reason"] is None


async def test_ceiling_link_lost_is_false_link_lost() -> None:
    """昇降部と切れているときは `ok: false`・`LINK_LOST`。"""
    rig = Loop()
    await rig.prime()
    rig.lift.link_ok = lambda now, timeout: False  # type: ignore[method-assign]
    state = rig.loop.build_state(1)
    assert state["ceiling"]["ok"] is False
    assert state["ceiling"]["reason"] == "LINK_LOST"
    assert state["ceiling"]["status"] == "MEASURED", "自分の値は載せ続ける"


async def test_io_lost_when_no_lines_arrive() -> None:
    """Arduino から行が来ないと `IO_LOST`（protocol §4）。"""
    rig = Loop()
    await rig.prime()
    rig.hw.freeze_ceiling()
    state = await rig.step(700)
    assert state["reason"] == "IO_LOST"


async def test_state_shape() -> None:
    """`state` の形（protocol §4）。"""
    rig = Loop()
    await rig.prime()
    rig.loop.hold("lift_up", 30)
    state = await rig.step()
    assert state["lift"]["dir"] == "up"
    assert state["lift"]["top_detect"] is False
    assert state["lift"]["owner"] == "module"
    assert state["lift"]["lift_ip"] is None, "プロセス内に線は無い"
    assert state["ceiling"]["status"] == "MEASURED"
    assert state["ceiling"]["ok"] is True
    assert state["ceiling"]["reason"] == "NONE"


# --- ピッチ・ヨー・倍率（旧版のまま） -----------------------------------------------------------------


async def test_pitch_and_yaw_still_work() -> None:
    rig = Loop()
    rig.loop.hold("pitch_up", 10)
    await rig.step()
    assert rig.hw.pitch_history, "ピッチのサーボへ出す"
    assert rig.holds == [], "昇降以外では `hold` を送らない"

    rig.loop.hold("yaw_left", 10)
    await rig.step()
    assert rig.hw.yaw_running, "ヨーを回す"
    rig.loop.release()
    await rig.step()
    assert not rig.hw.yaw_running


async def test_unknown_axis_and_speed_are_ignored() -> None:
    rig = Loop()
    assert rig.loop.hold("diagonal", 30) is False
    assert rig.loop.hold("lift_up", "fast") is False


async def test_clamp_zoom() -> None:
    assert clamp_zoom(2, PARAMS) == 2.0
    assert clamp_zoom(99, PARAMS) == 4.0
    assert clamp_zoom("fast", PARAMS) == 1.0
