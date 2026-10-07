"""MJPEG の配信と倍率の受け付け（DetailedDesign.md §4.3・protocol §1 の経路）。"""

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


async def _stream(request: web.Request) -> web.StreamResponse:
    """`video_fps` ごとに 1 フレームを JPEG にして配る。"""
    pipeline: VideoPipeline = request.app["pipeline"]
    video_fps: float = request.app["video_fps"]
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": f"multipart/x-mixed-replace; boundary={STREAM_BOUNDARY}",
            "Cache-Control": "no-store",
        },
    )
    await response.prepare(request)
    period = 1.0 / video_fps
    loop = asyncio.get_running_loop()
    next_frame_at = loop.time()
    try:
        while True:
            # JPEG の処理は重いので、ループを止めないよう別スレッドに任せる
            # （Python 3.8 には asyncio.to_thread が無いので run_in_executor を使う）
            jpeg = await loop.run_in_executor(None, pipeline.jpeg_bytes)
            if jpeg is not None:
                await response.write(
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
            next_frame_at += period
            remaining = next_frame_at - loop.time()
            if remaining > 0.0:
                await asyncio.sleep(remaining)
            else:
                # 処理が video_fps より遅いときは追いつかず、今の時間から数え直す
                next_frame_at = loop.time()
    except ConnectionResetError:
        # 画面が切れた。配信は続けるので静かに戻る
        pass
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


def create_app(pipeline: VideoPipeline, video_fps: float) -> web.Application:
    """`GET /stream`（MJPEG）・`GET /snapshot`・`POST /zoom` を持つアプリを作る。"""
    app = web.Application()
    app["pipeline"] = pipeline
    app["video_fps"] = float(video_fps)
    app.add_routes(
        [
            web.get("/stream", _stream),
            web.get("/snapshot", _snapshot),
            web.post("/zoom", _zoom),
        ]
    )
    return app
