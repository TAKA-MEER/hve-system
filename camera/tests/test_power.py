"""`IdlePower`・`PowerHw`・`FakePower`・`VideoCapture`・`R` 行の試験（`WP-CAM-13`）。

純関数の試験。**呼び出し側（`app.py`・`uno_hw.py`）を縛る試験は `test_power_app.py`。**
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hve_camera.app import VideoCapture
from hve_camera.hw.fake_power import FakePower
from hve_camera.hw.power_hw import POWEROFF_COMMAND, PowerHw
from hve_camera.power import IdlePower, PowerState
from hve_camera.uno_link import encode_relax_cmd, parse_io_line

IDLE = 1_800_000
ABSENT = 60_000
SETTLE = 1_500


def make(now: float = 0.0) -> IdlePower:
    return IdlePower(IDLE, ABSENT, SETTLE, now)


# --- IdlePower ------------------------------------------------------------------


def test_starts_active_and_stays_until_idle_save() -> None:
    power = make()
    assert power.state is PowerState.ACTIVE
    assert power.step(IDLE - 1, False, True) is PowerState.ACTIVE
    assert power.step(IDLE, False, True) is PowerState.SETTLING


def test_time_counts_from_start_not_from_zero() -> None:
    power = make(now=5_000)
    assert power.step(5_000 + IDLE - 1, False, True) is PowerState.ACTIVE
    assert power.step(5_000 + IDLE, False, True) is PowerState.SETTLING


def test_settling_becomes_saving_after_settle_ms() -> None:
    power = make()
    power.step(IDLE, False, True)
    assert power.step(IDLE + SETTLE - 1, False, True) is PowerState.SETTLING
    assert power.step(IDLE + SETTLE, False, True) is PowerState.SAVING


def test_touch_returns_from_settling_and_saving_and_restarts_the_clock() -> None:
    for extra in (0, SETTLE):
        power = make()
        power.step(IDLE, False, True)
        power.step(IDLE + extra, False, True)
        power.touch(IDLE + extra + 10)
        assert power.state is PowerState.ACTIVE
        assert power.step(IDLE + extra + 10 + IDLE - 1, False, True) is PowerState.ACTIVE
        assert power.step(IDLE + extra + 10 + IDLE, False, True) is PowerState.SETTLING


def test_touch_while_active_postpones_save() -> None:
    power = make()
    power.touch(IDLE - 1)
    assert power.step(IDLE, False, True) is PowerState.ACTIVE
    assert power.step(IDLE - 1 + IDLE, False, True) is PowerState.SETTLING


def test_lift_moving_counts_as_operation_but_does_not_wake() -> None:
    power = make()
    # 動いている間は省電力に入らない
    for t in range(0, 2 * IDLE, 60_000):
        assert power.step(t, True, True) is PowerState.ACTIVE
    last = 2 * IDLE - 60_000
    assert power.step(last + IDLE - 1, False, True) is PowerState.ACTIVE
    assert power.step(last + IDLE, False, True) is PowerState.SETTLING
    # 省電力中に昇降部が動いても自分では戻らない（戻すのは touch）。電源は切らない
    assert power.step(last + IDLE + 10, True, False) is PowerState.SETTLING


def test_off_needs_wlan_absent_for_ap_absent_ms() -> None:
    power = make()
    power.step(0, False, False)  # 無線が無い状態の始まり
    power.step(IDLE, False, False)  # SETTLING
    assert power.state is PowerState.SETTLING
    assert power.step(IDLE + 1, False, False) is PowerState.OFF, "とっくに ap_absent_ms を超えている"
    # 無線が無くても、最後の操作から idle_save_ms 経つまでは切らない
    early = make()
    early.step(0, False, False)
    assert early.step(IDLE - 1, False, False) is PowerState.ACTIVE


def test_off_not_while_wlan_is_up() -> None:
    power = make()
    power.step(IDLE, False, True)
    for t in range(IDLE, IDLE + 10 * IDLE, 60_000):
        assert power.step(t, False, True) is not PowerState.OFF


def test_off_waits_for_ap_absent_ms_after_saving() -> None:
    power = make()
    power.step(IDLE, False, True)  # SETTLING（機体の無線は見えている）
    power.step(IDLE + 10, False, False)  # ここから見えない
    assert power.step(IDLE + 10 + ABSENT - 1, False, False) is not PowerState.OFF
    assert power.step(IDLE + 10 + ABSENT, False, False) is PowerState.OFF


def test_wlan_blip_restarts_the_absent_count() -> None:
    power = make()
    power.step(IDLE, False, True)
    t = IDLE + 1_000
    power.step(t, False, False)  # ここから見えない
    power.step(t + ABSENT - 1_000, False, False)
    power.step(t + ABSENT - 500, False, True)  # 一瞬戻った
    down_again = t + ABSENT + 1_000
    assert power.step(down_again, False, False) is not PowerState.OFF
    # 数え直した時刻（down_again）から ABSENT 経つまでは切らない（数え直さなければ、とうに超えている）
    assert power.step(down_again + ABSENT - 1, False, False) is not PowerState.OFF
    assert power.step(down_again + ABSENT, False, False) is PowerState.OFF


def test_off_not_when_lift_moved_recently_even_if_wlan_absent() -> None:
    power = make()
    power.step(IDLE, False, False)
    assert power.state is PowerState.SETTLING
    # 昇降部が動いた → idle が縮む。無線が無くても切らない
    assert power.step(IDLE + 1_000, True, False) is PowerState.SETTLING
    assert power.step(IDLE + 2_000 + ABSENT, False, False) is not PowerState.OFF
    assert power.step(IDLE + 1_000 + IDLE - 1, False, False) is not PowerState.OFF
    assert power.step(IDLE + 1_000 + IDLE, False, False) is PowerState.OFF


def test_off_is_final() -> None:
    power = make()
    power.step(IDLE, False, False)
    power.step(IDLE + ABSENT, False, False)
    assert power.state is PowerState.OFF
    power.touch(IDLE + ABSENT + 1)
    assert power.state is PowerState.OFF
    assert power.step(IDLE + ABSENT + 2, False, True) is PowerState.OFF


# --- R 行 -----------------------------------------------------------------------


def test_encode_relax_cmd() -> None:
    assert encode_relax_cmd(0) == "R 0"
    assert encode_relax_cmd(65535) == "R 65535"
    assert parse_io_line("R 1") is None, "R は UnitV2 → Arduino の向き。読み取りでは捨てる"


@pytest.mark.parametrize("bad", [-1, 65536, 1.0, True, "1", None])
def test_encode_relax_cmd_rejects_bad_seq(bad) -> None:
    with pytest.raises(ValueError):
        encode_relax_cmd(bad)


# --- 電源の口 -------------------------------------------------------------------


def test_fake_power_never_powers_off() -> None:
    fake = FakePower()
    assert fake.wlan_up() is True, "既定は真"
    fake.set_wlan_up(False)
    assert fake.wlan_up() is False
    fake.poweroff()
    fake.poweroff()
    assert fake.poweroff_count == 2


def test_power_hw_reads_operstate(tmp_path) -> None:
    path = tmp_path / "operstate"
    hw = PowerHw(str(path), runner=lambda cmd: pytest.fail("poweroff を呼んではいけない"))
    assert hw.wlan_up() is False, "ファイルが無い"
    path.write_text("up\n")
    assert hw.wlan_up() is True
    for text in ("down\n", "dormant\n", "unknown\n", ""):
        path.write_text(text)
        assert hw.wlan_up() is False, text


def test_power_hw_poweroff_uses_the_runner_only() -> None:
    """実物の `/sbin/poweroff` は試験で呼ばない。差し替えた実行役にコマンドを渡すことだけ確かめる。"""
    calls: list = []
    hw = PowerHw("/nonexistent", runner=lambda cmd: calls.append(tuple(cmd)))
    hw.poweroff()
    assert calls == [POWEROFF_COMMAND] == [("/sbin/poweroff",)]


# --- VideoCapture ---------------------------------------------------------------


async def test_video_capture_posts_on_and_tolerates_missing_or_old_hve_video() -> None:
    bodies: list = []
    status = {"code": 200}

    async def handler(request: web.Request) -> web.Response:
        bodies.append(await request.json())
        return web.Response(status=status["code"])

    app = web.Application()
    app.add_routes([web.post("/capture", handler)])
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    port = server.port
    capture = VideoCapture(port)
    try:
        assert await capture.send_capture(False) is True
        assert await capture.send_capture(True) is True
        assert bodies == [{"on": False}, {"on": True}]
        status["code"] = 404  # 古い hve_video
        assert await capture.send_capture(False) is False
    finally:
        await capture.close()
        await server.close()
    # 誰も居ない
    gone = VideoCapture(port)
    try:
        assert await gone.send_capture(True) is False
    finally:
        await gone.close()
