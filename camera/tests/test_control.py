"""`ControlLoop` の試験。**呼び出し側の数行**（指令の順序・停止・鮮度・丸め）を縛る。

[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §3 と
[-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §1・§2。
時計は `ManualClock` で自分で進めるので、待ち時間を作らずに確かめられる。
"""

from __future__ import annotations

from typing import Any

import pytest

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

    async def send_cmd(self, direction: str, duty: int, ceil_ok: bool) -> None:
        self._log.append("cmd")
        await super().send_cmd(direction, duty, ceil_ok)


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

    def __init__(self, distance_mm: int = 2000, top_mm: int | None = None) -> None:
        self.clock = ManualClock()
        self.log: list[str] = []
        self.hw = RecordingHw(self.clock, self.log)
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

    def arm(self, status: str = "MEASURED", distance_mm: int | None = 2000) -> None:
        """次の 1 回の測定値を差し替える。"""
        self.hw.set_ceiling(CeilingStatus(status), distance_mm)

    async def prime(self) -> None:
        """**1 回回してから始める。**読めない値を持たせないため。

        最初に `step()` を回すと測った読み値が入り、偽昇降部も `state` を作り始める。
        それが無いと `link` が `lost`、`ceil_ok` も `false` なので、試験したい経路に入れない。
        """
        self.arm("MEASURED", 2000)
        await self.loop.step()
        self.lift._step(self.clock())  # noqa: SLF001 - 試験用の priming

    async def step(self, ms: float = 100) -> Any:
        """`lift_cmd_period_ms` だけ時計を進めてから 1 回回す。"""
        self.clock.advance(ms)
        await self.loop.step()
        return self.loop.build_state(1)

    def last(self) -> dict[str, Any]:
        assert self.lift.cmd_history, "まだ指令を送っていない"
        return self.lift.cmd_history[-1]


# --- 指令の順番と停止 ------------------------------------------------------------------------------


async def test_cmd_is_sent_before_ceiling_is_read() -> None:
    """DetailedDesign §3「天井の測定は指令を送ったあと」。順序が入れ替わると I2C の待ち時間が指令に混ざる。"""
    rig = Loop()
    rig.arm()
    await rig.step()
    assert rig.log[0] == "cmd", "まず昇降部への指令を送ってから天井を測る"
    assert rig.log[1] == "read"


async def test_stop_is_sent_every_step_while_idle() -> None:
    """止まっている間も `lift_cmd_period_ms` ごとに `stop` を送る。止まらないと ESP32 側が `CMD_TIMEOUT` になる。"""
    rig = Loop()
    for _ in range(3):
        await rig.step()
    assert [c["dir"] for c in rig.lift.cmd_history] == ["stop", "stop", "stop"]
    assert [c["duty"] for c in rig.lift.cmd_history] == [0, 0, 0]


async def test_release_sends_stop_on_the_next_step() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    await rig.step()
    assert rig.last()["dir"] == "up"
    rig.loop.release()
    await rig.step()
    last = rig.last()
    assert last["dir"] == "stop"
    assert last["duty"] == 0


# --- ceil_ok は送る瞬間に計算する --------------------------------------------------------------------


async def test_lift_up_sends_ceil_ok_true_when_ceiling_is_far() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    await rig.step()
    assert rig.last()["dir"] == "up"
    assert rig.last()["ceil_ok"] is True


async def test_lift_up_sends_ceil_ok_false_when_ceiling_is_close() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 100)  # margin 500 mm より近い
    await rig.step()  # この回で近い読み値を取り込む
    rig.loop.hold("lift_up", 30)
    state = await rig.step()  # 送るのはこのとき。持ってる読み値で判定する
    assert rig.last()["ceil_ok"] is False
    assert state["ceiling"]["reason"] == "CEILING_NEAR"


async def test_ceil_ok_is_recomputed_even_while_still_holding() -> None:
    """**この試験が変異 1 を殺す。**前の `true` を使い回すと天井に突っ込む。

    最初の測定は遠く、2 回目以降は新しい読み値を返さない（`freeze_ceiling`）。
    時計を進めれば同じ `hold` のまま古くなり、`ceil_ok` が `false` に落ちる。
    """
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    state = await rig.step()
    assert rig.last()["ceil_ok"] is True, "最初は遠いので許可される"
    assert state["ceiling"]["ok"] is True

    rig.hw.freeze_ceiling()  # ここから新しい読み値を返さない
    state = await rig.step(200)  # まだ stale ではない
    assert rig.last()["ceil_ok"] is True

    state = await rig.step(600)  # ceiling_stale_ms=600 を超えた
    assert rig.last()["ceil_ok"] is False, "古くなった読み値を前の許可のまま使い回さない"
    assert state["ceiling"]["ok"] is False
    assert state["ceiling"]["reason"] == "CEILING_STALE"


async def test_ceil_ok_false_when_ceiling_is_never_measured() -> None:
    """1 つも測れていないときは上昇させてはいけない。"""
    rig = Loop()
    rig.loop.hold("lift_up", 30)
    state = await rig.step()
    assert rig.last()["ceil_ok"] is False
    assert state["ceiling"]["mm"] is None
    assert state["ceiling"]["age_ms"] is None


async def test_no_echo_allows_up_but_read_error_does_not() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("NO_ECHO", None)
    await rig.step()  # NO_ECHO を取り込む
    rig.loop.hold("lift_up", 30)
    state = await rig.step()
    assert rig.last()["ceil_ok"] is True, "反射なしは上昇を許す"
    assert state["ceiling"]["reason"] == "OUT_OF_RANGE"

    rig.arm("READ_ERROR", None)
    await rig.step()  # READ_ERROR を取り込む
    state = await rig.step()
    assert rig.last()["ceil_ok"] is False, "読めなかったときは上昇させない"
    assert state["ceiling"]["reason"] == "CEILING_STALE"


# --- 速度の丸め ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [(999, 60), (-5, 10), (0, 10), (30, 30)],
)
async def test_speed_is_clamped_to_settings(given: float, expected: int) -> None:
    """**この試験が変異 5 を殺す。**設定の上限を超える速度をそのまま送らない。"""
    rig = Loop()
    await rig.prime()
    rig.arm()
    rig.loop.hold("lift_up", given)
    await rig.step()
    assert rig.last()["duty"] == expected


async def test_each_axis_uses_its_own_setting() -> None:
    settings = {
        "lift_up": {"min": 5, "max": 50, "init": 20},
        "lift_down": {"min": 6, "max": 40, "init": 20},
        "pitch": {"min": 1, "max": 25, "init": 10},
        "yaw": {"min": 1, "max": 15, "init": 8},
    }
    rig = Loop()
    rig.loop._settings = settings  # noqa: SLF001 - 試験用の差し替え
    await rig.prime()
    rig.arm()
    rig.loop.hold("lift_down", 999)
    await rig.step()
    assert rig.last()["duty"] == 40


# --- hold_timeout ----------------------------------------------------------------------------------


async def test_hold_timeout_stops_the_lift() -> None:
    """**この試験が変異 3 を殺す。**`hold_timeout_ms` を越えたら押していない扱いにして止める。"""
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    await rig.step(100)  # hold_timeout_ms=400 の内側
    state = await rig.step(200)  # 300 ms 経過。まだ押しっぱなし
    assert state["active_axis"] == "lift_up"
    assert rig.last()["dir"] == "up"

    state = await rig.step(300)  # 合計 500 ms > 400 ms
    assert rig.last()["dir"] == "stop", "途絶えた操作は止まる"
    assert state["active_axis"] is None
    assert state["reason"] == "HOLD_TIMEOUT"


async def test_fresh_hold_pushes_the_timeout_forward() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    await rig.step(100)
    await rig.step(250)  # 350 ms
    rig.loop.hold("lift_up", 30)  # 押し直した
    state = await rig.step(300)  # 押し直してから 300 ms
    assert state["active_axis"] == "lift_up"
    assert state["reason"] != "HOLD_TIMEOUT"


# --- ピッチとヨー ---------------------------------------------------------------------------------


async def test_pitch_integrates_while_held_and_is_held_at_the_limit() -> None:
    rig = Loop()
    await rig.prime()
    rig.loop.hold("pitch_up", 10)  # 10 deg/s
    for _ in range(3):
        await rig.step(100)  # 0.1 s ごとに +1 度 → +3 度
    assert rig.hw.pitch_deg == pytest.approx(3.0)
    state = rig.loop.build_state(1)
    assert state["pitch_deg"] == pytest.approx(3.0)
    assert state["reason"] == "NONE"

    # 60 deg/s は設定 `pitch` の max=30 に丸められ、0.3 s ごとに +9 度。
    # 3 → 12 → 21 → 30 → 39 → 48 は 45 度で留まる。
    for expected in (12.0, 21.0, 30.0, 39.0, 45.0):
        rig.loop.hold("pitch_up", 60)  # 設定の上限より大きい値
        state = await rig.step(300)
        assert rig.hw.pitch_deg == pytest.approx(expected)
    assert state["reason"] == "AXIS_LIMIT", "端に留まったら理由を出す"


async def test_pitch_down_moves_back() -> None:
    rig = Loop()
    await rig.prime()
    for _ in range(3):
        rig.loop.hold("pitch_up", 30)
        await rig.step(300)  # 0.3 s × 30 deg/s = +9 度
    assert rig.hw.pitch_deg == pytest.approx(27.0)
    for _ in range(3):
        rig.loop.hold("pitch_down", 30)
        await rig.step(300)  # -9 度
    assert rig.hw.pitch_deg == pytest.approx(0.0)


async def test_yaw_runs_only_while_held_and_stops_otherwise() -> None:
    rig = Loop()
    await rig.prime()
    rig.loop.hold("yaw_left", 10)
    await rig.step()
    assert rig.hw.yaw_running is True
    assert rig.hw.yaw_direction == "left"

    rig.loop.release()
    await rig.step()
    assert rig.hw.yaw_running is False, "離したらコイルの電流を切る"


async def test_lift_axes_stop_the_yaw() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    await rig.step()
    assert rig.hw.yaw_running is False


# --- 画面へ出す state ------------------------------------------------------------------------------


async def test_link_lost_wins_over_every_other_reason() -> None:
    """`LINK_LOST` は一番上。動いていなければ全部ゆがむ。"""
    rig = Loop()
    rig.arm("MEASURED", 100)  # 天井は近い（理由あり）
    rig.loop.hold("lift_up", 30)
    state = await rig.step()
    assert state["lift"]["link"] == "lost"  # 偽昇降部は state を作る前は lost
    assert state["reason"] == "LINK_LOST"


async def test_held_axis_reason_wins_over_hold_timeout_and_lift_reason() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 100)
    rig.loop.hold("lift_up", 30)
    state = await rig.step()
    assert state["lift"]["link"] == "ok"
    assert state["reason"] == "CEILING_NEAR", "押している軸の理由が先"


async def test_none_when_nothing_is_wrong() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    state = await rig.step()
    assert state["reason"] == "NONE"
    assert state["lift"]["link"] == "ok"
    assert state["fake"] is True
    assert state["clients"] == 1


async def test_state_reports_ceiling_age_from_the_reading_time() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    await rig.step()
    state = await rig.step(300)
    assert state["ceiling"]["age_ms"] == 300
    assert state["ceiling"]["mm"] == 2000


# --- hold / zoom の入力 ----------------------------------------------------------------------------


async def test_unknown_axis_is_ignored_and_keeps_the_previous_hold() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    assert rig.loop.hold("diagonal", 30) is False
    await rig.step()
    assert rig.last()["dir"] == "up", "知らない軸は無視して前の操作を残す"


async def test_non_numeric_speed_is_ignored() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    assert rig.loop.hold("lift_up", "fast") is False
    await rig.step()
    assert rig.last()["dir"] == "up"
    assert rig.last()["duty"] == 30


async def test_last_hold_wins() -> None:
    rig = Loop()
    await rig.prime()
    rig.arm("MEASURED", 2000)
    rig.loop.hold("lift_up", 30)
    rig.loop.hold("lift_down", 20)
    await rig.step()
    assert rig.last()["dir"] == "down"
    assert rig.last()["duty"] == 20


def test_clamp_zoom_snaps_and_limits() -> None:
    assert clamp_zoom(1, PARAMS) == 1.0
    assert clamp_zoom(2.3, PARAMS) == 2.5  # zoom_step=0.5 の倍数にそろえる
    assert clamp_zoom(99, PARAMS) == 4.0  # zoom_max
    assert clamp_zoom(0.1, PARAMS) == 1.0
    assert clamp_zoom("2", PARAMS) == 1.0
    assert clamp_zoom(None, PARAMS) == 1.0


async def test_zoom_is_sent_only_when_it_changes() -> None:
    rig = Loop()
    assert await rig.loop.set_zoom(2) == 2.0
    assert await rig.loop.set_zoom(2) == 2.0
    assert rig.zoom.sent == [2.0]
    assert await rig.loop.set_zoom(99) == 4.0
    assert rig.zoom.sent == [2.0, 4.0]
    assert rig.loop.build_state(1)["zoom"] == 4.0


async def test_state_carries_the_video_port() -> None:
    """画面は映像（hve_video）の URL を組み立てるのに `video_port` が要る（protocol §2.4）。"""
    rig = Loop()
    await rig.prime()
    assert rig.loop.build_state(1)["video_port"] == PARAMS["video_port"]
