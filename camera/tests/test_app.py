"""`CameraApp` の試験。**本物の経路**を通す。

- 画面は aiohttp の WS クライアント（ブラウザ役）
- 昇降部は [`FakeEsp32`](cam_support.py)（本物の WS サーバ）＋**本物の `LiftLink`**
- 制御ループは `control_step()` を自分で回す（時計は `ManualClock`）

これで「画面が押した操作が ESP32 の `cmd` になる」までを途切れさせずに確かめられる
（[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §4.2）。
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

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
SETTINGS_DIR = "/home/yisahia787/code/MIRS-Local/TM/hve-system/.briefs/tmp/cam02"


class Rig:
    """アプリ・昇降部・画面（ブラウザ役）をまとめたもの。

    `use_fake_lift=False`（既定）だと**本物の `LiftLink` ＋ 偽 ESP32 サーバ**。
    `use_fake_lift=True` にするとプロセス内の `FakeLift`（`POST /api/fake` の試験用）。
    """

    def __init__(self, *, fake: bool = True, autostart: bool = False,
                 deliver_zoom: bool = True, use_fake_lift: bool = False) -> None:
        self.clock = ManualClock()
        self.esp32: FakeEsp32 | None = None
        self.hw = FakeHardware(self.clock)
        self.zoom = RecordingVideoZoom(deliver=deliver_zoom)
        self.settings = copy.deepcopy(SETTINGS)
        self.fake = fake
        self.autostart = autostart
        self.use_fake_lift = use_fake_lift
        self.client: TestClient | None = None
        self.app: Any = None
        #: 制御ループを何回回したか。指令は 1 回につき 1 本なので、指令の到達を待ち合わせに使う
        self.steps = 0

    @property
    def camera(self) -> Any:
        assert self.app is not None, "先に start() する"
        return self.app["camera"]

    @property
    def lift(self) -> Any:
        return self.camera._lift  # noqa: SLF001 - 試験から接続状態を見る

    @property
    def cmds(self) -> list[dict[str, Any]]:
        """受け取った指令の履歴。どちらの昇降部でも同じ形。"""
        if self.use_fake_lift:
            return self.lift.cmd_history
        assert self.esp32 is not None
        return self.esp32.cmd_history

    @property
    def last_cmd(self) -> dict[str, Any] | None:
        return self.cmds[-1] if self.cmds else None

    async def start(self) -> TestClient:
        """偽 ESP32 を立ててから、**本物の `LiftLink`** をその URL に繋いでアプリを作る。"""
        if self.use_fake_lift:
            lift: Any = FakeLift(self.clock)
        else:
            assert FakeEsp32 is not None
            self.esp32 = FakeEsp32(self.clock)
            lift = LiftLink(await self.esp32.start(), self.clock)

        self.app = create_app(
            self.hw,
            lift,
            settings=self.settings,
            using_defaults=True,
            params=PARAMS,
            clock=self.clock,
            video_zoom=self.zoom,
            fake=self.fake,
            autostart=self.autostart,
            settings_path=f"{SETTINGS_DIR}/settings-test.json",
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

    async def pump(
        self, ms: float = 100, distance_mm: int | None = 2000, expect: bool = True
    ) -> Any:
        """1 周期ぶん進める。

        `distance_mm` を渡すと次の測定値を差し替える（天井を一定に保つ）。
        **`None` なら差し替えない。**`POST /api/fake` で入れた値をそのまま使いたいとき用。
        """
        self.clock.advance(ms)
        if distance_mm is not None:
            self.hw.set_ceiling(CeilingStatus.MEASURED, distance_mm)
        self.steps += 1
        await self.camera.control_step()
        state = await self.camera.broadcast_state()
        if expect:
            await self.expect_cmds()
        return state

    async def expect_cmds(self) -> None:
        """回した回数分の指令が昇降部に届くまで待つ。"""
        await wait_until(lambda: len(self.cmds) >= self.steps)

    async def warm(self) -> Any:
        """1 回回して**天井の読み値と昇降部の `state` を手に入れる。**

        指令は測定より先に送るので、最初の 1 回は `ceil_ok` が `false` のままになる。
        試験したい経路に入る前に 1 回回しておく。
        """
        await self.pump()
        await self.wait_state()

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

    def last(self) -> dict[str, Any]:
        assert self.last_cmd is not None, "まだ指令を受けていない"
        return self.last_cmd


@pytest.fixture
async def rig() -> Any:
    r = Rig()
    await r.start()
    await wait_until(lambda: r.lift.connected)  # WS が開いている = 繋がっている
    try:
        yield r
    finally:
        await r.stop()


# --- 画面 → ESP32 の本物の経路 ----------------------------------------------------------------------


async def test_hold_from_the_browser_reaches_the_esp32(rig: Rig) -> None:
    await rig.warm()  # 最初の一本は天井を測る前の `false`
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()

    cmd = rig.last()
    assert cmd["t"] == "cmd"
    assert cmd["dir"] == "up"
    assert cmd["duty"] == 30
    assert cmd["ceil_ok"] is True, "天井が遠いので上昇を許す"
    await ws.close()


async def test_speed_from_the_browser_is_clamped(rig: Rig) -> None:
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 999})
    await rig.wait_axis("lift_up")
    await rig.pump()
    assert rig.last()["duty"] == 60, "設定 `lift_up` の max 60 で止める"
    await ws.close()


async def test_ceil_ok_false_over_the_wire_when_the_ceiling_is_close(rig: Rig) -> None:
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.pump(distance_mm=100)  # この回で近い読み値を取り込む
    await rig.pump()  # 送るのはこのとき
    assert rig.last()["dir"] == "up"
    assert rig.last()["ceil_ok"] is False
    await ws.close()


async def test_release_from_the_browser_sends_stop(rig: Rig) -> None:
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    assert rig.last()["dir"] == "up"

    await ws.send_json({"t": "release"})
    await rig.wait_axis(None)  # release が届くのを待つ
    await rig.pump()
    assert rig.last()["dir"] == "stop"
    assert rig.last()["duty"] == 0
    await ws.close()


async def test_closing_the_browser_releases_its_hold(rig: Rig) -> None:
    """**この試験が変異 4 を殺す。**切断は `release` 扱い。止まらないと上昇し続ける。"""
    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    assert rig.last()["dir"] == "up"

    await ws.close()
    await rig.pump()
    assert rig.last()["dir"] == "stop", "画面が閉じたら押していた操作は止める"


async def test_closing_one_screen_does_not_stop_another_screen_hold(rig: Rig) -> None:
    """spec §1.6「止めたいときに止められない場面を作らない」。"""
    first = await rig.browser()
    second = await rig.browser()

    await second.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump()
    assert rig.last()["dir"] == "up"

    await first.close()
    await rig.pump()
    assert rig.last()["dir"] == "up", "別画面の操作は止めない"

    await second.close()
    await rig.pump()
    assert rig.last()["dir"] == "stop"


async def test_unknown_and_broken_messages_are_ignored(rig: Rig) -> None:
    ws = await rig.browser()
    await ws.send_str("これは JSON ではない")
    await ws.send_json([1, 2, 3])
    await ws.send_json({"t": "explode"})
    await ws.send_json({"t": "hold", "axis": "diagonal", "speed": 10})
    await rig.pump()
    assert rig.last()["dir"] == "stop", "読めないものは無視して押していないまま"
    await ws.close()


# --- 画面へ配る state --------------------------------------------------------------------------------


async def test_state_is_pushed_to_the_browser_with_the_client_count(rig: Rig) -> None:
    first = await rig.browser()
    second = await rig.browser()  # 繋がった直後の配信は 2 人分
    state = await first.receive_json(timeout=2)
    assert state["t"] == "state"
    assert state["clients"] == 2
    assert state["fake"] is True
    assert "provisional" in state
    await first.close()
    await second.close()


async def test_link_lost_when_the_esp32_stops_sending(rig: Rig) -> None:
    """**LINK_LOST の経路。**`state` が `lift_state_timeout_ms` 越えで届かなくなったら出る。"""
    ws = await rig.browser()
    await rig.pump()
    await wait_until(lambda: rig.esp32.state_history)  # state は届いている
    state = await rig.camera.broadcast_state()
    assert state["lift"]["link"] == "ok"

    rig.esp32.silent = True  # state を出し止める
    state = await rig.pump(1000)
    assert state["lift"]["link"] == "lost"
    assert state["reason"] == "LINK_LOST"
    await ws.close()


async def test_cmd_is_discarded_while_the_link_is_lost(rig: Rig) -> None:
    """繋がっていない間は指令を送らない（送っても捨てられる）。"""
    ws = await rig.browser()
    await rig.pump()
    before = len(rig.esp32.cmd_history)
    await rig.lift.close()  # 昇降部を落とす
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    await rig.pump(expect=False)
    assert len(rig.esp32.cmd_history) == before
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


async def test_app_retries_the_zoom_it_could_not_send() -> None:
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


# --- 設定 API ---------------------------------------------------------------------------------------


async def test_get_settings_returns_settings_and_the_defaults_flag(rig: Rig) -> None:
    assert rig.client is not None
    response = await rig.client.get("/api/settings")
    assert response.status == 200
    body = await response.json()
    assert body["using_defaults"] is True
    assert body["settings"] == SETTINGS


async def test_put_settings_rejects_invalid_and_keeps_the_old_ones(rig: Rig) -> None:
    assert rig.client is not None
    bad = {"lift_up": {"min": 90, "max": 10, "init": 50}}  # min > max かつ項目が足りない
    response = await rig.client.put("/api/settings", json=bad)
    assert response.status == 400
    body = await response.json()
    assert body["errors"], "理由の一覧を返す"
    assert rig.settings == SETTINGS, "保存せず、元のまま"


async def test_put_settings_applies_valid_values_to_the_control_loop(rig: Rig) -> None:
    assert rig.client is not None
    good = {
        "lift_up": {"min": 10, "max": 20, "init": 15},
        "lift_down": {"min": 10, "max": 60, "init": 30},
        "pitch": {"min": 1, "max": 30, "init": 10},
        "yaw": {"min": 1, "max": 30, "init": 10},
    }
    response = await rig.client.put("/api/settings", json=good)
    assert response.status == 200
    body = await response.json()
    assert body["using_defaults"] is False

    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 999})
    await rig.wait_axis("lift_up")
    await rig.pump()
    assert rig.last()["duty"] == 20, "保存した設定の上限を使う"
    await ws.close()


# --- 偽物の API -------------------------------------------------------------------------------------


@pytest.fixture
async def fake_rig() -> Any:
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

    await rig.pump(distance_mm=None)  # 入れた読み値をそのまま使う
    await rig.wait_state()
    state = await rig.camera.broadcast_state()
    assert state["ceiling"]["mm"] == 120
    assert state["lift"]["height_mm"] == 800
    assert state["lift"]["bottom"] is True


async def test_fake_api_can_freeze_the_ceiling(fake_rig: Rig) -> None:
    """偽物 API で「読み値が止まった」状態を作れる。"""
    rig = fake_rig
    assert rig.client is not None
    await rig.pump(distance_mm=2000)  # まず新しい読み値を持たせる
    await rig.client.post("/api/fake", json={"ceiling": {"stale": True}})

    ws = await rig.browser()
    await ws.send_json({"t": "hold", "axis": "lift_up", "speed": 30})
    await rig.wait_axis("lift_up")
    state = await rig.pump(distance_mm=None)  # まだ古くない
    assert rig.last()["ceil_ok"] is True

    state = await rig.pump(1000, distance_mm=None)  # 新しい読み値を返さないので古くなる
    assert rig.last()["ceil_ok"] is False
    assert state["ceiling"]["reason"] == "CEILING_STALE"
    await ws.close()


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"ceiling": {"status": "MAYBE", "mm": 1}}, "知らない状態"),
        ({"ceiling": {"status": "MEASURED"}}, "mm"),
        ({"ceiling": {"status": "MEASURED", "mm": -1}}, "0 未満"),
        ({"ceiling": "far"}, "オブジェクトでない"),
        ({"height_mm": "高い"}, "数値でない"),
        ({"bottom": 1}, "真偽値でない"),
    ],
)
async def test_fake_api_rejects_bad_values(fake_rig: Rig, payload: dict, needle: str) -> None:
    rig = fake_rig
    assert rig.client is not None
    response = await rig.client.post("/api/fake", json=payload)
    assert response.status == 400
    body = await response.json()
    assert any(needle in error for error in body["errors"]), body["errors"]


async def test_fake_api_does_not_exist_outside_fake_mode() -> None:
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


async def test_index_is_404_until_wp_ui_01_arrives(rig: Rig) -> None:
    """静的ファイルは `WP-UI-01` の仕事。それまで 404。"""
    assert rig.client is not None
    response = await rig.client.get("/")
    assert response.status == 404


async def test_state_is_json_serialisable(rig: Rig) -> None:
    state = await rig.pump()
    assert json.loads(json.dumps(state, ensure_ascii=False))["t"] == "state"
