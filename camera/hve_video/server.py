"""MJPEG の配信と倍率の受け付け（DetailedDesign.md §4.4・protocol §1 の経路）。"""

from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import web

from hve_video.pipeline import VideoPipeline

# MJPEG の区切り（multipart/x-mixed-replace の boundary）
STREAM_BOUNDARY = "hveframe"
# /zoom を受け付ける接続元（protocol §1。hve_camera と同じラズパイからだけ）
ZOOM_PEER = "127.0.0.1"

log = logging.getLogger(__name__)


def is_local_peer(remote: str | None) -> bool:
    """接続元が `127.0.0.1` かどうか。`/zoom` はこれ以外から受けない。"""
    return remote == ZOOM_PEER


class _Subscriber:
    """1 本の接続の受け口。最新の 1 枚だけを持つ（古い 1 枚は上書きして溜めない）。"""

    def __init__(self) -> None:
        self.jpeg: bytes | None = None
        self.event = asyncio.Event()

    def offer(self, jpeg: bytes) -> None:
        self.jpeg = jpeg
        self.event.set()

    def wake(self) -> None:
        """待っているのを起こす（アプリの終了で使う。枚は無いまま）。"""
        self.event.set()


class Broadcaster:
    """JPEG の作り直しを 1 か所にまとめて、全接続に同じ 1 枚を配る（DetailedDesign.md §4.4 「配り方」）。

    作り直しは `video_fps` ごとに 1 回だけ。接続が 0 本のあいだは作り直さない
    （最後の接続が抜けると、ループは次の周期の頭で止まり、次の接続が起こし直す）。
    """

    def __init__(self, pipeline: VideoPipeline, video_fps: float) -> None:
        self._pipeline = pipeline
        self._period = 1.0 / video_fps
        self._subscribers: set[_Subscriber] = set()
        self._task: asyncio.Future | None = None
        self.closing = False

    def subscribe(self) -> _Subscriber:
        subscriber = _Subscriber()
        self._subscribers.add(subscriber)
        if self.closing:
            subscriber.wake()
        elif self._task is None:
            self._task = asyncio.ensure_future(self._run())
        return subscriber

    def unsubscribe(self, subscriber: _Subscriber) -> None:
        self._subscribers.discard(subscriber)

    async def close(self) -> None:
        """アプリの終了。ループを止め、待っている接続を起こして抜けさせる。"""
        self.closing = True
        for subscriber in list(self._subscribers):
            subscriber.wake()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        me = asyncio.current_task()
        next_frame_at = loop.time()
        try:
            while self._subscribers:
                try:
                    # JPEG の処理は重いので、ループを止めないよう別スレッドに任せる
                    # （Python 3.8 には asyncio.to_thread が無いので run_in_executor を使う）
                    jpeg = await loop.run_in_executor(None, self._pipeline.jpeg_bytes)
                except Exception:
                    log.exception("JPEG の作り直しに失敗した")
                    jpeg = None
                if jpeg is not None:
                    for subscriber in list(self._subscribers):
                        subscriber.offer(jpeg)
                next_frame_at += self._period
                remaining = next_frame_at - loop.time()
                if remaining > 0.0:
                    await asyncio.sleep(remaining)
                else:
                    # 処理が video_fps より遅いときは追いつかず、今の時間から数え直す
                    next_frame_at = loop.time()
        finally:
            if self._task is me:
                self._task = None


def _part(jpeg: bytes) -> bytes:
    return (
        b"--"
        + STREAM_BOUNDARY.encode()
        + b"\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: "
        + str(len(jpeg)).encode()
        + b"\r\n\r\n"
        + jpeg
        + b"\r\n"
    )


async def _write_within(coro, timeout: float) -> None:
    """`coro`（1 枚の書き込み）が `timeout` 秒で終わらなければ `asyncio.TimeoutError`。

    `asyncio.wait_for` は使わない。Python 3.8 の `wait_for` は、完了と同時にキャンセルが来ると
    そのキャンセルを握りつぶすことがあり、切れた画面のハンドラが抜けずに残る。
    """
    task = asyncio.ensure_future(coro)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
    except BaseException:
        task.cancel()
        raise
    if not done:
        task.cancel()
        raise asyncio.TimeoutError()
    task.result()  # 書き込みの例外（ConnectionResetError 等）はここで出る


async def _stream(request: web.Request) -> web.StreamResponse:
    """作り直し済みの最新の 1 枚を配る。前の 1 枚を送り終えていなければ、その間の枚は飛ばす。"""
    broadcaster: Broadcaster = request.app["broadcaster"]
    send_timeout: float = request.app["video_send_timeout"]
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": f"multipart/x-mixed-replace; boundary={STREAM_BOUNDARY}",
            "Cache-Control": "no-store",
        },
    )
    await response.prepare(request)
    subscriber = broadcaster.subscribe()
    try:
        while not broadcaster.closing:
            await subscriber.event.wait()
            subscriber.event.clear()
            jpeg, subscriber.jpeg = subscriber.jpeg, None
            if jpeg is None:
                continue
            try:
                await _write_within(response.write(_part(jpeg)), send_timeout)
            except asyncio.TimeoutError:
                # 相手が受け取らない。`close()` は送り残しを送り切ってから閉じるので、
                # 止まった相手だと閉じない。`abort()` で捨てる
                log.warning("%s 秒以内に送れない接続を捨てる", send_timeout)
                if request.transport is not None:
                    request.transport.abort()
                break
    except ConnectionResetError:
        # 画面が切れた。配信は続けるので静かに戻る
        pass
    finally:
        broadcaster.unsubscribe(subscriber)
    return response


async def _zoom(request: web.Request) -> web.Response:
    """倍率を受け取る。接続元が `127.0.0.1` でなければ 403。"""
    remote = request.remote
    if not is_local_peer(remote):
        log.warning("接続元 %s からの /zoom を拒否する", remote)
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        body = await request.json()
    except ValueError:  # JSON が読めない・Content-Type が JSON でない
        return web.json_response({"error": "JSON が読めない"}, status=400)
    if not isinstance(body, dict) or "level" not in body:
        return web.json_response({"error": "level が無い"}, status=400)
    level = body["level"]
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        return web.json_response({"error": "level が数値でない"}, status=400)
    pipeline: VideoPipeline = request.app["pipeline"]
    # 丸めは crop_rect が行う（hve_camera 側と同じ規則。spec Spec-ui.md §1.5）
    pipeline.level = float(level)
    return web.json_response({"level": pipeline.level})


async def _snapshot(request: web.Request) -> web.Response:
    """静止画（spec `H-U9`）。最後に受けたカメラの JPEG をそのまま返す。無ければ 503。

    倍率に関わらず、作り直さない・切り出さない。接続元は問わない（外の端末から受ける）。
    """
    pipeline: VideoPipeline = request.app["pipeline"]
    loop = asyncio.get_running_loop()
    jpeg = await loop.run_in_executor(None, pipeline.latest_jpeg)
    if jpeg is None:
        return web.json_response({"error": "まだ 1 枚も受けていない"}, status=503)
    return web.Response(
        body=jpeg, content_type="image/jpeg", headers={"Cache-Control": "no-store"}
    )


async def _close_broadcaster(app: web.Application) -> None:
    await app["broadcaster"].close()


def create_app(
    pipeline: VideoPipeline, video_fps: float, video_send_timeout_ms: float
) -> web.Application:
    """`GET /stream`（MJPEG）・`GET /snapshot`・`POST /zoom` を持つアプリを作る。"""
    app = web.Application()
    app["pipeline"] = pipeline
    app["video_fps"] = float(video_fps)
    app["video_send_timeout"] = float(video_send_timeout_ms) / 1000.0
    app["broadcaster"] = Broadcaster(pipeline, float(video_fps))
    app.on_shutdown.append(_close_broadcaster)
    app.add_routes(
        [
            web.get("/stream", _stream),
            web.get("/snapshot", _snapshot),
            web.post("/zoom", _zoom),
        ]
    )
    return app
