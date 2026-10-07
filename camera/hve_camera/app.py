"""カメラ部のアプリ。画面（静的ファイル）・設定 API・ブラウザとの WS・制御ループ。

[DetailedDesign-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §1・§2・§3。
起動（`__main__.py`）と試験が同じ物を使うように、aiohttp のアプリを組み立てるのは
**`create_app()` だけ**。試験は `create_app()` で作ったアプリを使う。

## ここで守る 4 つ

1. **その画面の WS が閉じたら、その画面が押していた操作は `release` 扱い**（protocol §1）。
   ただし**押していない他の画面の操作は止めない**（spec
   [Spec-ui.md](../../docs/plan/spec/Spec-ui.md) §1.6「止めたいときに止められない場面を作らない」）。
   そのため「今押しているのはどの画面のものか」を `:attr:`CameraApp.control`` ではなく
   ここで持つ。
2. **画面が新しく繋がったら倍率を 1 に戻し、全画面に配る**（spec §1.5）。
3. **`hve_video` が居なくてもアプリは止まらない。**倍率は `VideoZoom` が送れるまで覚えておく。
4. **`POST /api/fake` は偽物のモードのときだけ存在させる。**（偽物でなければ 404）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Mapping

import aiohttp
from aiohttp import web

from hve_camera.control import ControlLoop
from hve_camera.hw.base import HardwareBase
from hve_camera.lift_link import LiftPort
from hve_camera.lift_resolve import resolve_lift_host
from hve_camera.settings import (
    LIFT_AXES,
    OWN_AXES,
    save_settings,
    validate_settings,
)

log = logging.getLogger(__name__)

#: 時計の型。ms を返す。
Clock = Callable[[], float]


class VideoZoom:
    """倍率を `hve_video` へ送る。**`hve_video` が居っても居なくてもアプリは止まらない。**"""

    def __init__(self, port: int) -> None:
        self._url = f"http://127.0.0.1:{int(port)}/zoom"
        self._session: aiohttp.ClientSession | None = None
        #: 送れなかった倍率。次に `send_zoom()` が呼ばれるとき送り直す
        self.pending: float | None = None
        #: 直前の送信が失敗したか。**同じ失敗を 100 ms ごとに出さない**（送れなくなったときと復帰だけ出す）
        self._failing = False

    async def send_zoom(self, level: float) -> bool:
        """倍率を送る。**送れたら `True`、送れなかったら `False`（例外は投げない）。**"""
        value = float(level)
        self.pending = value
        session = self._ensure_session()
        try:
            async with session.post(self._url, json={"level": value}) as response:
                if response.status == 200:
                    self.pending = None
                    self._note_recovered(value)
                    return True
                self._note_failure("hve_video が倍率を %s で受け付けなかった", response.status)
        except Exception as exc:  # noqa: BLE001 - hve_video が居ないのは想定内
            self._note_failure("hve_video (%s) に倍率を送れなかった: %s", self._url, exc)
        return False

    def _note_failure(self, message: str, *args: Any) -> None:
        """**送れなくなったときだけ**記録する。送れ続けるあいだは繰り返し出さない。"""
        if self._failing:
            return
        self._failing = True
        log.info(message, *args)

    def _note_recovered(self, level: float) -> None:
        """**また送れるようになったときだけ**記録する。"""
        if not self._failing:
            return
        self._failing = False
        log.info("hve_video に倍率を送れるようになった（%s 倍）", level)

    async def close(self) -> None:
        """セッションを閉じる。"""
        if self._session is None:
            return
        with contextlib.suppress(Exception):
            await self._session.close()
        self._session = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session


class CameraApp:
    """画面・設定 API・ブラウザとの WS を持つアプリ。制御ループもここに立ち上げる。"""

    def __init__(
        self,
        hw: HardwareBase,
        lift: LiftPort,
        *,
        settings: dict[str, dict[str, float]],
        using_defaults: bool,
        params: Mapping[str, Any],
        clock: Clock,
        video_zoom: VideoZoom,
        fake: bool = False,
        settings_path: str | Path | None = None,
        web_dir: str | Path | None = None,
        autostart: bool = True,
    ) -> None:
        self._hw = hw
        self._lift = lift
        self._params = params
        self._clock = clock
        self._video_zoom = video_zoom
        self._fake = fake
        self._settings_path = settings_path
        self._web_dir = Path(web_dir) if web_dir is not None else None
        self._autostart = autostart

        #: 設定。`PUT /api/settings` で**中身を入れ替える**（制御ループと同じ物を見るため）
        self._settings = settings
        self._using_defaults = using_defaults

        #: 繋がっている画面
        self._clients: dict[web.WebSocketResponse, None] = {}
        #: 今押している操作の、画面（`None` は「押していない」）
        self._owner: web.WebSocketResponse | None = None

        self.control = ControlLoop(
            hw,
            lift,
            settings=settings,
            params=params,
            clock=clock,
            video_zoom=video_zoom,
            fake=fake,
        )
        self._task: asyncio.Task[None] | None = None

    # --- 制御ループと配信 --------------------------------------------------------------------------

    async def start(self) -> None:
        """昇降部と繋がり始める。**繋がれなくても止まらない。**"""
        await self._lift.start()
        if self._autostart:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """裏のタスクと、昇降部・ハードウェア・`hve_video` への接続を片付ける。"""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._lift.close()
        await self._hw.close()
        await self._video_zoom.close()

    async def control_step(self) -> None:
        """制御ループ 1 回。**立ち上げたタスクも試験もこの関数を使う。**"""
        await self.control.step()

    async def broadcast_state(self) -> dict[str, Any]:
        """`state` を組み立てて全画面へ配る。**繋がっている画面の数つきで**。"""
        state = self.control.build_state(len(self._clients))
        payload = json.dumps(state, ensure_ascii=False)
        for ws in list(self._clients):
            try:
                await ws.send_str(payload)
            except Exception:  # noqa: BLE001 - 切れている画面は一覧から外す
                self._drop_client(ws)
        return state

    async def _run_loop(self) -> None:
        """`lift_cmd_period_ms` ごとに制御ループ、`state_period_ms` ごとに配信。"""
        period = float(self._params["lift_cmd_period_ms"]) / 1000.0
        state_period = float(self._params["state_period_ms"]) / 1000.0
        next_state_at_ms = self._clock()
        while True:
            await self.control_step()
            now_ms = self._clock()
            if now_ms >= next_state_at_ms:
                await self.broadcast_state()
                next_state_at_ms = now_ms + state_period
            # 送れなかった倍率は、変わるのを待たずに送り直す
            if self._video_zoom.pending is not None:
                await self._video_zoom.send_zoom(self._video_zoom.pending)
            await asyncio.sleep(period)

    # --- 画面 -------------------------------------------------------------------------------------

    async def index(self, request: web.Request) -> web.StreamResponse:
        """`GET /` の静的ファイル。**`camera/web` は後のパケット（`WP-UI-01`）なので、無ければ 404。**"""
        if self._web_dir is None or not (self._web_dir / "index.html").is_file():
            return web.Response(status=404, text="画面は WP-UI-01 で入る")
        return web.FileResponse(self._web_dir / "index.html")

    async def ws(self, request: web.Request) -> web.StreamResponse:
        """`WS /ws`。操作を受け、`state` を配る。"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._clients[ws] = None

        # 画面が新しく繋がったら倍率を 1 に戻し、全画面に配る（spec §1.5）
        await self.control.set_zoom(1.0)
        await self.broadcast_state()

        try:
            async for message in ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    continue
                await self._on_message(ws, message.data)
        finally:
            self._drop_client(ws)
        return ws

    async def _on_message(self, ws: web.WebSocketResponse, raw: str) -> None:
        """`hold` / `release` / `zoom` を受ける。**読めないものは無視する。**"""
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("読めない JSON なので無視した: %r", raw)
            return
        if not isinstance(data, dict):
            log.warning("オブジェクトでないので無視した: %r", raw)
            return

        kind = data.get("t")
        if kind == "hold":
            if self.control.hold(data.get("axis"), data.get("speed"), owner=ws):
                self._owner = ws
            return
        if kind == "release":
            # **押していた画面の release だけ**で止める（他の画面の操作は残る）
            if self._owner is ws:
                self._owner = None
                self.control.release()
            return
        if kind == "zoom":
            await self.control.set_zoom(data.get("level"))
            return
        log.warning("知らない種類のメッセージなので無視した: %r", kind)

    def _drop_client(self, ws: web.WebSocketResponse) -> None:
        """画面を一覧から外す。**その画面が押していた操作は `release` 扱いにする。**"""
        self._clients.pop(ws, None)
        if self._owner is ws:
            self._owner = None
            self.control.release()

    # --- 設定 API（昇降は昇降部へ中継。protocol §4） ----------------------------------------------------

    async def get_settings(self, request: web.Request) -> web.Response:
        """`GET /api/settings`。自分のピッチ・ヨーと、昇降部から取った昇降をまとめて返す。

        昇降部に繋がらないときは `503`（形は旧版と同じ 4 軸）。
        """
        lift = await self._fetch_lift_settings()
        if lift is None:
            return web.json_response({"errors": ["昇降部に繋がらない"]}, status=503)
        merged = {axis: self._settings[axis] for axis in OWN_AXES}
        merged.update(lift)
        self._settings.update(lift)
        return web.json_response({"settings": merged, "using_defaults": self._using_defaults})

    async def put_settings(self, request: web.Request) -> web.Response:
        """`PUT /api/settings`。4 軸をまとめて受ける。

        先に 4 軸とも自分で検証 → 昇降部へ `PUT`（昇降の 2 軸）→
        成功したら自分の 2 軸を保存。昇降部が `400` ならその `errors` を返し、
        自分も保存しない。昇降部に繋がらなければ `503` で何も保存しない。
        """
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"errors": ["JSON が読めない"]}, status=400)

        errors = validate_settings(data, self._params)
        if errors:
            return web.json_response({"errors": errors}, status=400)

        lift_body = {axis: data[axis] for axis in LIFT_AXES}
        status, lift_errors = await self._put_lift_settings(lift_body)
        if status is None:
            return web.json_response({"errors": ["昇降部に繋がらない"]}, status=503)
        if status == 400:
            return web.json_response({"errors": lift_errors}, status=400)

        own = {axis: data[axis] for axis in OWN_AXES}
        try:
            save_settings(own, self._settings_path, self._params, axes=OWN_AXES)
        except OSError as exc:
            log.error("設定を保存できなかった: %s", exc)
            return web.json_response({"errors": [f"保存できない: {exc}"]}, status=500)

        # 制御ループと同じ辞書の中身を入れ替える（参照を渡すので中身の入れ替えだけにする）
        self._settings.clear()
        self._settings.update(own)
        self._settings.update(lift_body)
        self._using_defaults = False
        return web.json_response({"settings": dict(self._settings), "using_defaults": False})

    async def _lift_http_base(self) -> str | None:
        """昇降部の設定 API の起点（`http://ホスト:ポート`）。引けなければ `None`。"""
        loop = asyncio.get_running_loop()
        host = await loop.run_in_executor(
            None, resolve_lift_host, self._params.get("lift_host", ""), self._params.get("lift_mdns_name", "")
        )
        if not host:
            return None
        return "http://%s:%d" % (host, int(self._params.get("lift_port", 80)))

    async def _fetch_lift_settings(self) -> dict | None:
        """昇降部から昇降の 2 軸を取る。取れなければ `None`。"""
        base = await self._lift_http_base()
        if base is None:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(base + "/api/settings") as response:
                    if response.status != 200:
                        return None
                    payload = await asyncio.wait_for(response.json(), timeout=5)
        except Exception as exc:  # noqa: BLE001 - 繋がらないのは 503 にするだけ
            log.info("昇降部の設定を取れない: %s", exc)
            return None
        if not isinstance(payload, dict):
            return None
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            return None
        body = {axis: settings[axis] for axis in LIFT_AXES if axis in settings}
        if validate_settings(body, self._params, axes=LIFT_AXES):
            return None
        return body

    async def _put_lift_settings(self, body: dict) -> tuple:
        """昇降部へ昇降の 2 軸を `PUT`。`(状態, 理由の一覧)`。繋がらなければ `(None, None)`。"""
        base = await self._lift_http_base()
        if base is None:
            return None, None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(base + "/api/settings", json=body) as response:
                    payload = await asyncio.wait_for(response.json(), timeout=5)
                    if response.status == 400:
                        errors = payload.get("errors") if isinstance(payload, dict) else None
                        return 400, errors if isinstance(errors, list) else ["昇降部が設定を拒否した"]
                    if response.status != 200:
                        return None, None
                    return 200, []
        except Exception as exc:  # noqa: BLE001 - 繋がらないのは 503 にするだけ
            log.info("昇降部の設定を変えられない: %s", exc)
            return None, None

    # --- 偽物のモード専用 -------------------------------------------------------------------------

    async def post_fake(self, request: web.Request) -> web.Response:
        """`POST /api/fake`。**偽物のモードのときだけ登録する。**"""
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"errors": ["JSON が読めない"]}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"errors": ["オブジェクトでない"]}, status=400)

        errors = self._apply_fake(data)
        if errors:
            return web.json_response({"errors": errors}, status=400)
        return web.json_response({"ok": True})

    def _apply_fake(self, data: Mapping[str, Any]) -> list[str]:
        """偽の値を `HardwareBase` と昇降部に入れる。**理由の一覧**を返す（空なら全部入った）。"""
        errors: list[str] = []

        if "ceiling" in data:
            errors += self._apply_fake_ceiling(data["ceiling"])
        if "io_lost" in data:
            value = data["io_lost"]
            if not isinstance(value, bool):
                errors.append("io_lost: 真偽値でない")
            elif hasattr(self._hw, "set_io_lost"):
                self._hw.set_io_lost(value)
            else:
                errors.append("io_lost: 偽物の機構でない")
        if "height_mm" in data or "bottom" in data:
            # 昇降部の偽物（`hw/fake_lift.py`）だけが这些を受け取る。実物には無いので、
            # うっかり付けた時に 500 にしないよう理由として返す。
            lift = self._fake_lift()
            if lift is None:
                if "height_mm" in data:
                    errors.append("height_mm: 昇降部が偽物でない")
                if "bottom" in data:
                    errors.append("bottom: 昇降部が偽物でない")
            else:
                if "height_mm" in data:
                    value = data["height_mm"]
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        errors.append("height_mm: 数値でない")
                    else:
                        lift.set_height_mm(float(value))
                if "bottom" in data:
                    value = data["bottom"]
                    if not isinstance(value, bool):
                        errors.append("bottom: 真偽値でない")
                    else:
                        lift.set_bottom(value)

        return errors

    def _fake_lift(self) -> Any:
        """偽の昇降部ならそれを返す。**偽物でなければ `None`。**"""
        lift = self._lift
        if hasattr(lift, "set_height_mm") and hasattr(lift, "set_bottom"):
            return lift
        return None

    def _apply_fake_ceiling(self, value: Any) -> list[str]:
        """天井の偽の値。**指定しなかったものは動かさない。**"""
        if not isinstance(value, dict):
            return ["ceiling: オブジェクトでない"]

        if value.get("stale") is True:
            self._hw.freeze_ceiling()
            return []

        status = value.get("status")
        if status is None:
            return ["ceiling: status が無い"]
        if not isinstance(status, str) or status not in ("MEASURED", "NO_ECHO", "READ_ERROR"):
            return [f"ceiling.status: 知らない状態 {status!r}"]

        distance = value.get("mm")
        if status == "MEASURED":
            if isinstance(distance, bool) or not isinstance(distance, (int, float)):
                return ["ceiling.mm: MEASURED には mm が要る"]
            if distance < 0:
                return ["ceiling.mm: 0 未満はだめ"]
            self._hw.set_ceiling(status, int(distance))
            return []

        self._hw.set_ceiling(status)
        return []


def create_app(
    hw: HardwareBase,
    lift: LiftPort,
    *,
    settings: dict[str, dict[str, float]],
    using_defaults: bool,
    params: Mapping[str, Any],
    clock: Clock,
    video_zoom: VideoZoom,
    fake: bool = False,
    settings_path: str | Path | None = None,
    web_dir: str | Path | None = None,
    autostart: bool = True,
) -> web.Application:
    """`CameraApp` を作って aiohttp のアプリを返す。**起動と試験が同じ物を使う。**"""
    camera = CameraApp(
        hw,
        lift,
        settings=settings,
        using_defaults=using_defaults,
        params=params,
        clock=clock,
        video_zoom=video_zoom,
        fake=fake,
        settings_path=settings_path,
        web_dir=web_dir,
        autostart=autostart,
    )

    app = web.Application()
    # 試験と起動コードが `CameraApp` を取り出せるようにする
    app["camera"] = camera
    app.add_routes(
        [
            web.get("/", camera.index),
            web.get("/api/settings", camera.get_settings),
            web.put("/api/settings", camera.put_settings),
            web.get("/ws", camera.ws),
        ]
    )
    if fake:
        # 偽物のモードのときだけ存在する（names §4）
        app.add_routes([web.post("/api/fake", camera.post_fake)])
    if web_dir is not None and Path(web_dir).is_dir():
        # 他のパスを取り終えてから足す（競合したパスは上のルートが勝つ）
        app.router.add_static("/", Path(web_dir), name="web")

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


async def _startup(app: web.Application) -> None:
    await app["camera"].start()


async def _cleanup(app: web.Application) -> None:
    await app["camera"].stop()
