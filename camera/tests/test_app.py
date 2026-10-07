"""`CameraApp` の試験。**本物の経路**を通す。

- 画面は aiohttp の WS クライアント（ブラウザ役）
- 昇降部は [`FakeEsp32`](cam_support.py)（本物の WS サーバ＋設定 API）＋**本物の `LiftLink`**
- 制御ループは `control_step()` を自分で回す（時計は `ManualClock`）

これで「画面が押した操作が昇降部の `hold`・`release` になる」までを途切れさせずに確かめる。
"""

from __future__ import annotations

import copy
import json
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hve_camera.__main__ import WEB_DIR
from hve_camera.app import VideoZoom, create_app
from hve_camera.ceiling import CeilingStatus
from hve_camera.hw.fake_hw import FakeHardware
from hve_camera.hw.fake_lift import FakeLift
from hve_camera.lift_link import LiftLink
from tests.cam_support import (
    PARAMS,
    SETTINGS,
    FakeEsp32,
    ManualClock,
    RecordingVideoZoom,
    wait_until,
)

#: 設定を保存する場所。作業ツリーを汚さないよう `.briefs/tmp` の下へ置く
SETTINGS_DIR = str(Path(__file__).resolve().parents[2] / ".briefs" / "tmp" / "cam04")


class Rig:
    """アプリ・昇降部・画面（ブラウザ役）をまとめたもの。

    `use_fake_lift=False`（既定）だと**本物の `LiftLink` ＋ 偽 ESP32 サーバ**。
    `use_fake_lift=True` にするとプロセス内の `FakeLift`（`POST /api/fake` の試験用）。
    """

    def __init__(self, *, fake: bool = True, autostart: bool = False,
                 deliver_zoom: bool = True, use_fake_lift: bool = False,
                 web_dir: str | None = None) -> None:
        self.clock = ManualClock()
        self.esp32: FakeEsp32 | None = None
        self.hw = FakeHardware(self.clock)
        self.hw.set_steady_ceiling(CeilingStatus.MEASURED, 2000)
        self.zoom = RecordingVideoZoom(deliver=deliver_zoom)
        self.settings = copy.deepcopy(SETTINGS)
        self.fake = fake
        self.autostart = autostart
        self.use_fake_lift = use_fake_lift
        #: 画面を配る置き場（`WP-UI-02` で `camera/web` を入れ直す）
        self.web_dir = web_dir
        self.client: TestClient | None = None
        self.app: Any = None
        self.params: dict[str, Any] = dict(PARAMS)

    @property
    def camera(self) -> Any:
        assert self.app is not None, "先に start() する"
        return self.app["camera"]

    @property
    def lift(self) -> Any:
        return self.camera._lift  # noqa: SLF001 - 試験から接続状態を見る

    @property
    def holds(self) -> list[dict[str, Any]]:
        """昇降部が受け取った `hold` の履歴。どちらの昇降部でも同じ。"""
        if self.use_fake_lift:
            return self.lift.hold_history
        assert self.esp32 is not None
        return self.esp32.hold_history

    @property
    def releases(self) -> list[dict[str, Any]]:
        if self.use_fake_lift:
            return self.lift.release_history
        assert self.esp32 is not None
        return self.esp32.release_history

    async def start(self) -> TestClient:
        """偽 ESP32 を立ててから、**本物の `LiftLink`** をその URL に繋いでアプリを作る。"""
        if self.use_fake_lift:
            lift: Any = FakeLift(self.clock)
        else:
            self.esp32 = FakeEsp32(self.clock)
            url = await self.esp32.start()
            parts = urlsplit(self.esp32.http_base)
            self.params["lift_host"] = parts.hostname or "127.0.0.1"
            self.params["lift_port"] = parts.port or 80
            lift = LiftLink(url, self.clock)

        self.app = create_app(
            self.hw,
            lift,
            settings=self.settings,
            using_defaults=True,
            params=self.params,
            clock=self.clock,
            video_zoom=self.zoom,
            fake=self.fake,
            autostart=self.autostart,
            settings_path=f"{SETTINGS_DIR}/settings-test.json",
            web_dir=self.web_dir,
        )
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        return self.client

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None
        if self.esp32 is not None:
            await self.esp32.stop()
            self.esp32 = None

    async def pump(self, ms: float = 100) -> Any:
        """1 周期ぶん進める。天井は「十分遠い」を保つ（`freeze` したら止まる）。"""
        self.clock.advance(ms)
        await self.camera.control_step()
        if self.use_fake_lift:
            self.lift._step(self.clock())  # noqa: SLF001 - プロセス内の偽物は手で進める
        return await self.camera.broadcast_state()

    async def wait_holds(self, count: int) -> None:
        """昇降部に `hold` が `count` 本届くまで待つ。"""
        await wait_until(lambda: len(self.holds) >= count)

    async def wait_releases(self, count: int) -> None:
        await wait_until(lambda: len(self.releases) >= count)

    async def wait_state(self) -> None:
        """昇降部から `state` が届くまで待つ。"""
        await wait_until(lambda: self.lift.latest_state() is not None)

    async def wait_zoom(self, level: float) -> None:
        """その倍率が `hve_video` へ送られるまで待つ。"""
        await wait_until(lambda: self.zoom.sent and self.zoom.sent[-1] == level)

    async def wait_axis(self, axis: str | None) -> None:
        """制御ループがその軸を操作中になるまで待つ。**画面からの操作が届くまで待つ。**"""
        await wait_until(lambda: self.camera.control.build_state(1)["active_axis"] == axis)

    async def browser(self) -> Any:
        """画面（ブラウザ役）の WS を返す。**繋がったときの `state` は読み終えておく。**"""
        assert self.client is not None
        ws = await self.client.ws_connect("/ws")
        await ws.receive_json(timeout=2)
        return ws

    def last_hold(self) -> dict[str, Any]:
        assert self.holds, "まだ `hold` を受けていない"
        return self.holds[-1]


@pytest.fixture
async def rig(loop) -> Any:
    r = Rig()
    await r.start()
    await wait_until(lambda: r.lift.connected)  # WS が開いている = 繋がっている
    try:
        yield r
    finally:
        await r.stop()


# --- 画面 → 昇降部の本物の経路 ----------------------------------------------------------------------


async def test_hold_from_the_browser_reaches_the_esp32(rig: Rig) -> None:
    await rig.pump()  # 天井の読み値と `state` を手に入れる
    await rig.wait_state()
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    await rig.wait_holds(1)

    hold = rig.last_hold()
    assert hold["t"] == "hold"
    assert hold["dir"] == "up"
    assert hold["duty"] == 30
    assert hold["press"] == 1
    assert hold["ceiling"]["status"] == "MEASURED"
    assert rig.esp32 is not None and rig.esp32.hello_history[0]["ceiling_sensor"] is True
    await ws.close()


async def test_nothing_is_sent_while_idle(rig: Rig) -> None:
    for _ in range(3):
        await rig.pump()
    assert rig.holds == []
    assert rig.releases == []


async def test_press_increases_over_the_wire(rig: Rig) -> None:
    """向きを入れ替えると `press` が増える（本物の WS 経路）。"""
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    await ws.send_json({"t": "hold", "axis": "lift_down", "speed": 30})
    await rig.wait_axis("lift_down")
    await rig.pump()
    await rig.wait_holds(2)
    assert [h["press"] for h in rig.holds] == [1, 2]
    await ws.close()


async def test_release_from_the_browser_sends_release(rig: Rig) -> None:
    """**変異 4 を本物の経路で縛る。**離したら `release` を送る。"""
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    await rig.wait_holds(1)

    await ws.send_json({"t": "release"})
    await rig.wait_axis(None)  # release が届くのを待つ
    await rig.pump()
    await rig.wait_releases(1)
    assert rig.releases[-1]["press"] == 1
    await rig.pump()
    assert len(rig.releases) == 1, "`release` は 1 度だけ"
    await ws.close()


async def test_closing_the_browser_releases_its_hold(rig: Rig) -> None:
    """切断は `release` 扱い。"""
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    await rig.wait_holds(1)

    await ws.close()
    await rig.pump()
    await rig.wait_releases(1)


async def test_closing_one_screen_does_not_stop_another_screen_hold(rig: Rig) -> None:
    """spec §1.6「止めたいときに止められない場面を作らない」。"""
    first = await rig.browser()
    second = await rig.browser()

    await second.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    await rig.wait_holds(1)

    await first.close()
    await rig.pump()
    await rig.pump()
    await rig.wait_holds(2)
    assert len(rig.holds) >= 2, "別画面の操作は止めない"
    assert rig.releases == []

    await second.close()


async def test_unknown_and_broken_messages_are_ignored(rig: Rig) -> None:
    ws = await rig.browser()
    await ws.send_str("これは JSON ではない")
    await ws.send_json([1, 2, 3])
    await ws.send_json({"t": "explode"})
    await ws.send_json({"t": "hold", "axis": "diagonal", "speed": 10})
    await rig.pump()
    assert rig.holds == [], "読めないものは無視して押していないまま"
    await ws.close()


# --- 画面へ配る state --------------------------------------------------------------------------------


async def test_state_is_pushed_to_the_browser_with_the_client_count(rig: Rig) -> None:
    await rig.pump()
    first = await rig.browser()
    second = await rig.browser()  # 繋がった直後の配信は 2 人分
    state = await first.receive_json(timeout=2)
    assert state["t"] == "state"
    assert state["clients"] == 2
    assert state["fake"] is True
    assert "provisional" in state
    assert state["lift"]["lift_ip"] == "127.0.0.1"
    assert state["ceiling"]["status"] == "MEASURED"
    await first.close()
    await second.close()


async def test_link_lost_when_the_esp32_stops_sending(rig: Rig) -> None:
    """**LINK_LOST の経路。**`state` が `lift_state_timeout_ms` 越えで届かなくなったら出る。"""
    ws = await rig.browser()
    await rig.pump()
    await rig.wait_state()  # 昇降部の `state` を受け取るまで待つ
    state = await rig.camera.broadcast_state()
    assert state["lift"]["link"] == "ok"

    assert rig.esp32 is not None
    rig.esp32.silent = True  # state を出し止める
    state = await rig.pump(1000)
    assert state["lift"]["link"] == "lost"
    assert state["reason"] == "LINK_LOST"
    await ws.close()


async def test_hold_is_dropped_while_the_link_is_lost(rig: Rig) -> None:
    """繋がっていない間は指令を送らない（送っても捨てられる）。"""
    ws = await rig.browser()
    await rig.pump()
    before = len(rig.holds)
    await rig.lift.close()  # 昇降部を落とす
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    assert len(rig.holds) == before
    await ws.close()


# --- 倍率 -------------------------------------------------------------------------------------------


async def test_zoom_from_the_browser_reaches_hve_video(rig: Rig) -> None:
    ws = await rig.browser()
    await ws.send_json({"t": "zoom", "level": 2})
    await rig.wait_zoom(2.0)
    assert rig.zoom.sent == [2.0]
    await ws.send_json({"t": "zoom", "level": 2.3})
    await rig.wait_zoom(2.5)
    assert rig.zoom.sent == [2.0, 2.5], "zoom_step=0.5 の倍数にそろえる"
    await ws.send_json({"t": "zoom", "level": 99})
    await rig.wait_zoom(4.0)
    assert rig.zoom.sent[-1] == 4.0, "zoom_max=4 で止める"
    await ws.close()


async def test_a_new_screen_resets_the_zoom_for_everyone(rig: Rig) -> None:
    """spec §1.5。画面が繋がったら倍率を 1 に戻し、全画面に配る。"""
    first = await rig.browser()
    await first.send_json({"t": "zoom", "level": 3})
    await rig.wait_zoom(3.0)

    second = await rig.browser()
    await rig.wait_zoom(1.0)
    state = await first.receive_json(timeout=2)
    assert state["zoom"] == 1.0, "全画面に配る"
    await first.close()
    await second.close()


async def test_zoom_survives_absent_hve_video() -> None:
    """`hve_video` が居なくても例外は投げず、送れなかった倍率を覚えておく。"""
    zoom = VideoZoom(1)  # 1 番ポートには誰も居ない
    try:
        assert await zoom.send_zoom(2.0) is False
        assert zoom.pending == 2.0
    finally:
        await zoom.close()


async def test_zoom_logs_only_when_it_starts_and_stops_failing(caplog) -> None:
    """**同じ失敗を 100 ms ごとに出さない。**送れなくなったときと復帰したときだけ出す。"""
    from aiohttp import web

    def unused_port() -> int:
        """誰も居ないポートを 1 個取る（`hve_video` を出し入れするため）。"""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def video_app() -> web.Application:
        """`hve_video` 役。`POST /zoom` を受け取るだけ。"""
        async def zoom_handler(request: web.Request) -> web.Response:
            await request.json()
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_post("/zoom", zoom_handler)
        return app

    port = unused_port()
    zoom = VideoZoom(port)
    video = TestClient(TestServer(video_app(), port=port))
    await video.start_server()
    try:
        # 1. 最初から送れるとき: 記録しない
        with caplog.at_level("INFO", logger="hve_camera.app"):
            assert await zoom.send_zoom(1.5) is True
        assert caplog.records == [], [r.getMessage() for r in caplog.records]

        # 2. `hve_video` を止める: 送れなくなったときだけ 1 回、続けても 1 回
        await video.close()
        with caplog.at_level("INFO", logger="hve_camera.app"):
            for level in (2.0, 2.5, 3.0):
                assert await zoom.send_zoom(level) is False
        assert len([r for r in caplog.records if "送れなかった" in r.getMessage()]) == 1, (
            [r.getMessage() for r in caplog.records]
        )

        # 3. 復活したら: 復帰の記録を 1 回
        caplog.clear()
        again = TestClient(TestServer(video_app(), port=port))
        await again.start_server()
        with caplog.at_level("INFO", logger="hve_camera.app"):
            assert await zoom.send_zoom(2.0) is True
            assert await zoom.send_zoom(2.5) is True
        assert len([r for r in caplog.records if "送れるように" in r.getMessage()]) == 1, (
            [r.getMessage() for r in caplog.records]
        )
        await again.close()
    finally:
        await zoom.close()


async def test_app_retries_the_zoom_it_could_not_send(loop) -> None:
    """**送れなかった倍率を次の周期に送り直す。**（握り潰す経路を縛る）"""
    r = Rig(autostart=True, deliver_zoom=False)
    await r.start()
    try:
        ws = await r.browser()
        await ws.send_json({"t": "zoom", "level": 2})
        await wait_until(lambda: len(r.zoom.sent) >= 3, timeout=3.0)
        assert set(r.zoom.sent) == {2.0}
        assert r.zoom.pending == 2.0, "送れないので持ち続ける"
        await ws.close()
    finally:
        await r.stop()


# --- 設定 API（昇降は昇降部へ中継。protocol §4） -------------------------------------------------------


async def test_get_settings_merges_lift_settings(rig: Rig) -> None:
    assert rig.client is not None
    response = await rig.client.get("/api/settings")
    assert response.status == 200
    body = await response.json()
    assert body["using_defaults"] is True
    assert body["settings"]["pitch"] == SETTINGS["pitch"]
    assert body["settings"]["lift_up"] == {"min": 10, "max": 60, "init": 30}


async def test_get_settings_is_503_when_the_lift_is_gone(loop) -> None:
    """昇降部に繋がらないときは `503`（protocol §4）。"""
    r = Rig()
    await r.start()
    r.params["lift_host"] = "127.0.0.1"
    r.params["lift_port"] = 1  # 誰も居ない
    try:
        assert r.client is not None
        response = await r.client.get("/api/settings")
        assert response.status == 503
    finally:
        await r.stop()


async def test_put_settings_rejects_invalid_and_keeps_the_old_ones(rig: Rig) -> None:
    assert rig.client is not None
    bad = {"lift_up": {"min": 90, "max": 10, "init": 50}}  # min > max かつ項目が足りない
    response = await rig.client.put("/api/settings", json=bad)
    assert response.status == 400
    body = await response.json()
    assert body["errors"], "理由の一覧を返す"
    assert rig.settings["pitch"] == SETTINGS["pitch"], "保存せず、元のまま"


async def test_put_settings_relays_and_saves(rig: Rig) -> None:
    assert rig.client is not None
    assert rig.esp32 is not None
    good = {
        "lift_up": {"min": 10, "max": 20, "init": 15},
        "lift_down": {"min": 10, "max": 60, "init": 30},
        "pitch": {"min": 1, "max": 30, "init": 10},
        "yaw": {"min": 2, "max": 30, "init": 10},
    }
    response = await rig.client.put("/api/settings", json=good)
    assert response.status == 200
    body = await response.json()
    assert body["using_defaults"] is False
    # 昇降の 2 軸は昇降部へ中継する
    assert rig.esp32.lift_settings["lift_up"] == {"min": 10, "max": 20, "init": 15}
    # 自分の 2 軸だけを保存する
    assert rig.settings["yaw"] == {"min": 2, "max": 30, "init": 10}
    assert rig.settings["lift_up"] == {"min": 10, "max": 20, "init": 15}

    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 999})
    await rig.wait_axis("lift_up")
    await rig.pump()
    await rig.wait_holds(1)
    assert rig.last_hold()["duty"] == 20, "保存した設定の上限を使う"
    await ws.close()


async def test_put_settings_does_not_save_when_the_lift_refuses(rig: Rig) -> None:
    """**変異 5 の芯。**昇降部が `400` を返したのに自分の 2 軸を保存したら赤になる。"""
    assert rig.client is not None
    assert rig.esp32 is not None
    rig.esp32.fail_put = True
    good = {
        "lift_up": {"min": 10, "max": 20, "init": 15},
        "lift_down": {"min": 10, "max": 60, "init": 30},
        "pitch": {"min": 1, "max": 30, "init": 10},
        "yaw": {"min": 2, "max": 30, "init": 10},
    }
    response = await rig.client.put("/api/settings", json=good)
    assert response.status == 400
    body = await response.json()
    assert body["errors"], "昇降部の理由を返す"
    assert rig.settings["yaw"] == SETTINGS["yaw"], "自分も保存しない"


async def test_put_settings_is_503_when_the_lift_is_gone(loop) -> None:
    """昇降部に繋がらなければ `503` で何も保存しない。"""
    r = Rig()
    await r.start()
    r.params["lift_host"] = "127.0.0.1"
    r.params["lift_port"] = 1
    try:
        assert r.client is not None
        good = copy.deepcopy(SETTINGS)
        response = await r.client.put("/api/settings", json=good)
        assert response.status == 503
        assert r.settings == SETTINGS
    finally:
        await r.stop()


# --- 偽物の API -------------------------------------------------------------------------------------


@pytest.fixture
async def fake_rig(loop) -> Any:
    """`POST /api/fake` の試験用。昇降部はプロセス内の `FakeLift`。"""
    r = Rig(use_fake_lift=True)
    await r.start()
    try:
        yield r
    finally:
        await r.stop()


async def test_fake_api_changes_the_ceiling_and_the_lift(fake_rig: Rig) -> None:
    rig = fake_rig
    assert rig.client is not None
    response = await rig.client.post(
        "/api/fake",
        json={"ceiling": {"status": "MEASURED", "mm": 120}, "height_mm": 800, "bottom": True},
    )
    assert response.status == 200
    assert await response.json() == {"ok": True}

    await rig.pump()
    state = await rig.camera.broadcast_state()
    assert state["ceiling"]["mm"] == 120
    assert state["lift"]["height_mm"] == 800
    assert state["lift"]["bottom"] is True


async def test_fake_api_can_freeze_the_ceiling(fake_rig: Rig) -> None:
    """偽物 API で「読み値が止まった」状態を作れる。"""
    rig = fake_rig
    assert rig.client is not None
    await rig.pump()
    await rig.client.post("/api/fake", json={"ceiling": {"stale": True}})

    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    state = await rig.pump()
    assert rig.holds[-1]["ceiling"]["status"] == "MEASURED", "まだ古くない"

    rig.clock.advance(1000)  # ceiling_read_stale_ms=600 を超える
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})  # 押し直す
    await rig.wait_axis("lift_up")
    await rig.camera.control_step()
    rig.lift._step(rig.clock())  # noqa: SLF001 - 偽昇降部の `state` を進める
    state = await rig.camera.broadcast_state()
    assert rig.holds[-1]["ceiling"]["age_ms"] > 600, "前の値を載せ続ける（使い回しでないことの裏返し）"
    assert state["ceiling"]["reason"] == "CEILING_STALE"
    await ws.close()


async def test_fake_api_reports_io_lost(fake_rig: Rig) -> None:
    """`io_lost` で Arduino から行が来ない状態を作れる（protocol §4・names §4）。"""
    rig = fake_rig
    assert rig.client is not None
    await rig.pump()
    response = await rig.client.post("/api/fake", json={"io_lost": True})
    assert response.status == 200
    state = await rig.pump(700)
    assert state["reason"] == "IO_LOST"
    response = await rig.client.post("/api/fake", json={"io_lost": False})
    assert response.status == 200


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"ceiling": {"status": "MAYBE", "mm": 1}}, "知らない状態"),
        ({"ceiling": {"status": "MEASURED"}}, "mm"),
        ({"ceiling": {"status": "MEASURED", "mm": -1}}, "0 未満"),
        ({"ceiling": "far"}, "オブジェクトでない"),
        ({"height_mm": "高い"}, "数値でない"),
        ({"bottom": 1}, "真偽値でない"),
        ({"io_lost": "yes"}, "真偽値でない"),
    ],
)
async def test_fake_api_rejects_bad_values(fake_rig: Rig, payload: dict, needle: str) -> None:
    rig = fake_rig
    assert rig.client is not None
    response = await rig.client.post("/api/fake", json=payload)
    assert response.status == 400
    body = await response.json()
    assert any(needle in error for error in body["errors"]), body["errors"]


async def test_fake_api_does_not_exist_outside_fake_mode(loop) -> None:
    """偽物のモードのときだけ存在する（names §4）。"""
    r = Rig(fake=False)
    await r.start()
    try:
        assert r.client is not None
        response = await r.client.post("/api/fake", json={"bottom": True})
        assert response.status == 404
    finally:
        await r.stop()


# --- 画面そのもの -----------------------------------------------------------------------------------


async def test_index_is_served_after_wp_ui_01(loop) -> None:
    """`camera/web` を配る設定なら操作画面を返す（names §1）。"""
    r = Rig(web_dir=str(WEB_DIR))
    await r.start()
    try:
        assert r.client is not None
        response = await r.client.get("/")
        assert response.status == 200
        body = await response.text()
        assert "hve 昇降・カメラ" in body, "画面の題が出る"
        assert 'src="settings.js"' in body and 'src="app.js"' in body, "読み込みの順番"
        # スタイルも配る
        assert (await r.client.get("/style.css")).status == 200
    finally:
        await r.stop()


async def test_state_is_json_serialisable(rig: Rig) -> None:
    state = await rig.pump()
    assert json.loads(json.dumps(state, ensure_ascii=False))["t"] == "state"
