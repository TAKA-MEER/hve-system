"""偽物のモードを外とつなぐ試験（WP-CAM-18）。

`--lift` で外の偽の昇降部へつなぐ組み立て、`POST /api/fake` の `ceiling.steady` と
`yaw_zero` を、呼び出し側（`build_runtime` の戻り・`POST /api/fake` を通したアプリ・
昇降部が受け取った `ceiling`）で縛る。
"""

from __future__ import annotations

import argparse

import pytest

from hve_camera import __main__ as cli
from hve_camera.ceiling import CeilingStatus
from hve_camera.hw.fake_hw import FakeHardware
from hve_camera.hw.fake_lift import FakeLift
from hve_camera.lift_link import LiftLink
from tests.cam_support import PARAMS, ManualClock, wait_until
from tests.test_app import Rig


def fake_args(lift=None) -> argparse.Namespace:
    """`build_runtime` の `args`（`--fake` と `--lift` だけ）。"""
    return argparse.Namespace(fake=True, lift=lift)


def base_params() -> dict:
    return dict(PARAMS)


# --- 変異 1・2: --lift の組み立て -------------------------------------------------------------

def test_lift_uses_the_external_lift_link_not_the_inner_fake() -> None:
    """変異 1 の芯。`--lift` を付けたら中の `FakeLift` でなく、本番と同じ `LiftLink` を使う。"""
    clock = ManualClock()
    runtime = cli.build_runtime(fake_args(("127.0.0.1", 18099)), base_params(), clock)
    assert isinstance(runtime.lift, LiftLink), "外の偽の昇降部へは LiftLink でつなぐ"
    assert not isinstance(runtime.lift, FakeLift), "中の偽物（FakeLift）を使わない"
    assert isinstance(runtime.hw, FakeHardware), "ハードウェアは偽物のまま"


def test_lift_overrides_the_settings_api_destination() -> None:
    """変異 2 の芯。`--lift` が `lift_port`（と `lift_host`）を上書きし、設定 API の取り先もそこになる。"""
    clock = ManualClock()
    runtime = cli.build_runtime(fake_args(("10.9.8.7", 18099)), base_params(), clock)
    assert runtime.params["lift_host"] == "10.9.8.7"
    assert runtime.params["lift_port"] == 18099
    # WS と設定 API はどちらもこの params から行き先を決める
    assert cli.module_ws_url(runtime.params) == "ws://10.9.8.7:18099/ws/module"


def test_without_lift_keeps_the_inner_fake_lift() -> None:
    """逆側。`--lift` が無ければ今までどおり中の偽物（`FakeLift`）。"""
    clock = ManualClock()
    runtime = cli.build_runtime(fake_args(None), base_params(), clock)
    assert isinstance(runtime.lift, FakeLift)


# --- 変異 3: --fake なしの --lift は起動しない --------------------------------------------------

def test_lift_without_fake_does_not_start() -> None:
    """変異 3 の芯。`--fake` なしの `--lift` は argparse の誤りで起動しない。"""
    with pytest.raises(SystemExit) as caught:
        cli.main(["--lift", "127.0.0.1:18099"])
    assert caught.value.code == 2


@pytest.mark.parametrize("bad", ["127.0.0.1", "127.0.0.1:notaport", ":18099", "127.0.0.1:0", "127.0.0.1:70000"])
def test_lift_with_a_malformed_value_does_not_start(bad: str) -> None:
    """`--lift` の形が `HOST:PORT` でなければ argparse の誤りで起動しない。"""
    with pytest.raises(SystemExit) as caught:
        cli.main(["--fake", "--lift", bad])
    assert caught.value.code == 2


# --- 変異 8: 起動時の FAKE_CEILING_MM に期限が付かない ------------------------------------------

async def test_startup_ceiling_has_no_deadline() -> None:
    """変異 8 の芯。`build_runtime --fake` の起動時の天井は期限なしで保ち続ける。"""
    clock = ManualClock()
    runtime = cli.build_runtime(fake_args(None), base_params(), clock)
    clock.advance(10_000_000)
    reading = await runtime.hw.read_ceiling()
    assert reading is not None and reading.distance_mm == cli.FAKE_CEILING_MM
    assert reading.status is CeilingStatus.MEASURED


# --- 変異 4・5: steady の期限 -----------------------------------------------------------------

async def test_steady_ceiling_stops_after_the_deadline() -> None:
    """変異 4 の芯。`set_steady_ceiling(until_ms=)` の期限を過ぎたら読み値を返さない。"""
    clock = ManualClock()
    hw = FakeHardware(clock)
    hw.set_steady_ceiling(CeilingStatus.MEASURED, 900, until_ms=600.0)
    assert (await hw.read_ceiling()) is not None
    clock.advance(601)
    assert await hw.read_ceiling() is None, "期限を過ぎたら返り続けない"


@pytest.fixture
async def rig_fake(loop) -> Rig:
    rig = Rig(use_fake_lift=True)
    await rig.start()
    try:
        yield rig
    finally:
        await rig.stop()


async def test_steady_post_refreshes_the_deadline(rig_fake: Rig) -> None:
    """変異 5 の芯。2 回目の `steady` POST で期限が延びる（最初の期限では切れない）。"""
    rig = rig_fake
    stale = float(rig.params["ceiling_read_stale_ms"])  # 600
    resp = await rig.client.post("/api/fake", json={"ceiling": {"status": "MEASURED", "mm": 1200, "steady": True}})
    assert resp.status == 200, await resp.text()
    rig.clock.advance(stale - 100)
    resp = await rig.client.post("/api/fake", json={"ceiling": {"status": "MEASURED", "mm": 1300, "steady": True}})
    assert resp.status == 200, await resp.text()
    rig.clock.advance(stale - 100)  # 最初の期限は過ぎている
    reading = await rig.hw.read_ceiling()
    assert reading is not None and reading.distance_mm == 1300, "2 回目の POST の期限で返り続ける"
    rig.clock.advance(101)  # 2 回目の期限も過ぎる
    assert await rig.hw.read_ceiling() is None


# --- 変異 6: 期限切れ＋古さで昇降部へ送る天井が READ_ERROR になる --------------------------------

@pytest.fixture
async def rig_wire(loop) -> Rig:
    rig = Rig(use_fake_lift=False)
    await rig.start()
    try:
        yield rig
    finally:
        await rig.stop()


async def test_expired_steady_reaches_the_lift_as_read_error(rig_wire: Rig) -> None:
    """変異 6 の芯。`steady` の期限が切れて古さ `ceiling_read_stale_ms` を超えたら、昇降部が受け取った
    `ceiling` は `READ_ERROR` になる（偽 ESP32 の `/ws/module` で縛る）。"""
    rig = rig_wire
    assert rig.esp32 is not None
    resp = await rig.client.post("/api/fake", json={"ceiling": {"status": "MEASURED", "mm": 1200, "steady": True}})
    assert resp.status == 200, await resp.text()
    await wait_until(lambda: rig.esp32.connected)
    await rig.pump(100)  # 最初の 1 回はまだ読み値が無いので READ_ERROR
    await rig.pump(100)  # 天井を読んだあと
    await wait_until(lambda: len(rig.esp32.ceiling_history) >= 2)
    assert rig.esp32.ceiling_history[-1]["status"] == "MEASURED"

    # 期限（600）を過ぎて、読み値の古さも 600 を超えるまで進める
    rig.clock.advance(2000)
    await rig.camera.control_step()
    await wait_until(lambda: rig.esp32.ceiling_history[-1]["status"] == "READ_ERROR")
    assert "mm" not in rig.esp32.ceiling_history[-1], "READ_ERROR には mm が無い"


# --- 変異 7: steady なしの ceiling は 1 回分 -----------------------------------------------------

async def test_ceiling_without_steady_is_one_shot(rig_fake: Rig) -> None:
    """変異 7 の芯。`steady` なしの `ceiling` は 1 回だけ返る（2 回目はない）。"""
    rig = rig_fake
    rig.hw.freeze_ceiling()  # Rig の起動時の保ち続ける値（2000）を消す
    resp = await rig.client.post("/api/fake", json={"ceiling": {"status": "MEASURED", "mm": 300}})
    assert resp.status == 200, await resp.text()
    first = await rig.hw.read_ceiling()
    assert first is not None and first.distance_mm == 300
    assert await rig.hw.read_ceiling() is None, "steady なしは 1 回分で尽きる"


# --- 変異 9: yaw_zero ----------------------------------------------------------------------------

async def test_yaw_zero_by_post_zeros_the_yaw(rig_fake: Rig) -> None:
    """変異 9 の a。`POST /api/fake` の `yaw_zero: true` でヨーの原点が合う。"""
    rig = rig_fake
    assert rig.camera.yaw.zeroed() is False
    resp = await rig.client.post("/api/fake", json={"yaw_zero": True})
    assert resp.status == 200, await resp.text()
    assert rig.camera.yaw.zeroed() is True


async def test_yaw_zero_by_post_marks_motion(rig_fake: Rig) -> None:
    """変異 9 の c。`yaw_zero` で `mark_motion()` が呼ばれる（`motion_at_ms` が進む）。"""
    rig = rig_fake
    before = rig.camera.control.motion_at_ms
    rig.clock.advance(123)
    resp = await rig.client.post("/api/fake", json={"yaw_zero": True})
    assert resp.status == 200, await resp.text()
    assert rig.camera.control.motion_at_ms == before + 123


async def test_yaw_zero_by_post_preempts_ss(rig_fake: Rig) -> None:
    """変異 9 の b。ss が持ち主のとき `yaw_zero` で `PREEMPTED` になる。"""
    from tests.test_module_port import connect, record, send_aim

    rig = rig_fake
    resp = await rig.client.post("/api/fake", json={"yaw_zero": True})
    assert resp.status == 200, await resp.text()
    ws = await connect(rig)
    try:
        await send_aim(ws, 1, yaw=90)
        await rig.pump()
        assert record(rig)["status"] == "MOVING", "前提: ss が持ち主で動いている"
        resp = await rig.client.post("/api/fake", json={"yaw_zero": True})
        assert resp.status == 200, await resp.text()
        assert record(rig)["reason"] == "PREEMPTED"
        assert rig.camera.port.owner is None
    finally:
        await ws.close()


async def test_yaw_zero_without_the_yaw_total_is_a_400(rig_fake: Rig) -> None:
    """累計がまだ無ければ `yaw_zero` は理由つき 400（画面と過ごして数えない）。"""
    rig = rig_fake
    rig.hw.yaw_hs_total = lambda: None  # type: ignore[method-assign]
    resp = await rig.client.post("/api/fake", json={"yaw_zero": True})
    assert resp.status == 400, await resp.text()
    assert rig.camera.yaw.zeroed() is False


# --- 変異 10: yaw_zero / steady の受け付け --------------------------------------------------------

@pytest.mark.parametrize("value", [False, "true", 1, None])
async def test_yaw_zero_accepts_only_true(rig_fake: Rig, value) -> None:
    """変異 10 の芯。`yaw_zero` は `true` だけ受け、`false` や文字列・数値は 400。"""
    resp = await rig_fake.client.post("/api/fake", json={"yaw_zero": value})
    assert resp.status == 400, (value, await resp.text())
    assert rig_fake.camera.yaw.zeroed() is False


@pytest.mark.parametrize("value", ["yes", 1, "true"])
async def test_steady_accepts_only_booleans(rig_fake: Rig, value) -> None:
    """`ceiling.steady` に真偽値以外を送ったら 400。"""
    resp = await rig_fake.client.post(
        "/api/fake", json={"ceiling": {"status": "MEASURED", "mm": 300, "steady": value}}
    )
    assert resp.status == 400, await resp.text()


async def test_steady_with_a_missing_measurement_is_a_400(rig_fake: Rig) -> None:
    """`steady: true` でも `MEASURED` の `mm` が無ければ 400（我慢する側の検証が先）。"""
    resp = await rig_fake.client.post("/api/fake", json={"ceiling": {"status": "MEASURED", "steady": True}})
    assert resp.status == 400, await resp.text()
