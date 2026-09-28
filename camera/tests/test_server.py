"""`hve_video.server` の試験（DetailedDesign.md §4.3・protocol §1）。"""

from __future__ import annotations

import asyncio
import fcntl
import re
import socket
import struct
from unittest import mock

import aiohttp
import cv2
import numpy as np
import pytest
from aiohttp import streams
from aiohttp.test_utils import TestServer, make_mocked_request

from hve_video import server as video_server
from hve_video.pipeline import VideoPipeline
from hve_video.sources import FakeSource

# 取り込みの大きさ・出力の高さ・配信の周期（DetailedDesign-names.md §5）
CAPTURE = (640, 480)
OUT_HEIGHT = 240
VIDEO_FPS = 20.0
JPEG_QUALITY = 60
ZOOM_MAX = 4.0
ZOOM_STEP = 0.5
OUT_SIZE = (320, OUT_HEIGHT)
# 127.0.0.1 以外の接続元（`/zoom` は 403 になる）
FOREIGN_PEER = "10.0.0.5"


def make_app(zoom: float = 1.0, video_fps: float = VIDEO_FPS) -> object:
    pipeline = VideoPipeline(
        FakeSource(*CAPTURE),
        zoom=zoom,
        zoom_max=ZOOM_MAX,
        zoom_step=ZOOM_STEP,
        out_height=OUT_HEIGHT,
        jpeg_quality=JPEG_QUALITY,
    )
    return video_server.create_app(pipeline, video_fps)


def pipeline_of(app) -> VideoPipeline:
    return app["pipeline"]


async def first_jpeg(response) -> tuple[dict[str, str], bytes]:
    """MJPEG の最初の 1 フレームを `(区切りのヘッダ, JPEG)` で返す。"""
    head = await response.content.readuntil(b"\r\n\r\n")
    fields = {}
    for line in head.decode().splitlines():
        name, _, value = line.partition(":")
        fields[name.strip()] = value.strip()
    body = await response.content.readexactly(int(fields["Content-Length"]))
    return fields, body


def handler_of(app, method: str, path: str):
    """アプリに登録されたハンドラを取り出す（分岐が登録されていることも確かめる）。"""
    for route in app.router.routes():
        if route.method == method and route.resource.canonical == path:
            return route.handler
    raise LookupError(f"{method} {path} が登録されていない")


def _non_loopback_address() -> str | None:
    """127.0.0.1 以外の自分の IPv4 アドレス。無ければ None。

    名前で引くと /etc/hosts に 127.0.1.1 しか書かれていないことがあるので、網の機器を一つずつ見る。
    """
    for _, name in socket.if_nameindex():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                info = fcntl.ioctl(probe.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))
            except OSError:
                continue
            address = socket.inet_ntoa(info[20:24])
            if not address.startswith("127."):
                return address
    return None


def request_from(peer: str, body: bytes = b"") -> object:
    """接続元を偽ったリクエストを作る（実際の socket を作らずに 403 の経路を通す）。"""
    transport = mock.Mock()

    def get_extra_info(key: str):
        if key == "peername":
            return (peer, 40000)
        if key == "sockname":
            return ("0.0.0.0", 8080)
        return None

    transport.get_extra_info.side_effect = get_extra_info
    payload = streams.StreamReader(
        mock.Mock(_reading_paused=False), 2**16, loop=asyncio.get_event_loop()
    )
    payload.feed_data(body)
    payload.feed_eof()
    return make_mocked_request(
        "POST",
        "/zoom",
        headers={"Content-Type": "application/json"},
        transport=transport,
        payload=payload,
    )


# --- 配信 ---------------------------------------------------------------


async def test_stream_is_multipart_x_mixed_replace(aiohttp_client):
    client = await aiohttp_client(make_app())
    async with client.get("/stream") as response:
        assert response.status == 200
        content_type = response.headers["Content-Type"]
        assert content_type.startswith("multipart/x-mixed-replace; boundary=")
        boundary = content_type.split("boundary=")[1]
        head = await response.content.readuntil(b"\r\n\r\n")
    assert head.startswith(f"--{boundary}".encode())


async def test_stream_sends_jpeg_frames(aiohttp_client, mark):
    client = await aiohttp_client(make_app())
    async with client.get("/stream") as response:
        fields, body = await first_jpeg(response)
    assert fields["Content-Type"] == "image/jpeg"
    assert body[:2] == b"\xff\xd8"
    frame = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert frame is not None
    assert (frame.shape[1], frame.shape[0]) == OUT_SIZE


async def test_stream_keeps_the_output_size_for_every_zoom(aiohttp_client):
    # 配信の大きさ（帯域）は倍率によらない（spec Spec-ui.md §1.5）
    for zoom in (1.0, 2.0, 4.0):
        client = await aiohttp_client(make_app(zoom=zoom))
        async with client.get("/stream") as response:
            _, body = await first_jpeg(response)
        frame = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert (frame.shape[1], frame.shape[0]) == OUT_SIZE, zoom


async def test_stream_sends_frames_at_video_fps(aiohttp_client):
    client = await aiohttp_client(make_app(video_fps=5.0))
    async with client.get("/stream") as response:
        started = asyncio.get_running_loop().time()
        await first_jpeg(response)
        await first_jpeg(response)
        elapsed = asyncio.get_running_loop().time() - started
    # 5 fps なら 2 フレーム受け取るまで少なくとも 1 周期（0.2 秒）はかかる
    assert elapsed >= 0.2 * 0.75


# --- 倍率の受け付け ------------------------------------------------------


async def test_zoom_from_localhost_is_accepted(aiohttp_client):
    app = make_app()
    client = await aiohttp_client(app)
    response = await client.post("/zoom", json={"level": 2.5})
    assert response.status == 200
    assert (await response.json()) == {"level": 2.5}
    assert pipeline_of(app).level == 2.5


async def test_zoom_changes_the_stream(aiohttp_client, mark):
    # 「倍率を受け取る」だけでなく、切り出しと縮小まで効いていることを確かめる
    app = make_app(zoom=1.0)
    client = await aiohttp_client(app)
    async with client.get("/stream") as response:
        _, first = await first_jpeg(response)
    assert (await client.post("/zoom", json={"level": 4.0})).status == 200
    async with client.get("/stream") as response:
        _, second = await first_jpeg(response)
    plain = cv2.imdecode(np.frombuffer(first, dtype=np.uint8), cv2.IMREAD_COLOR)
    zoomed = cv2.imdecode(np.frombuffer(second, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert mark(zoomed)[0] == pytest.approx(mark(plain)[0] * 4, rel=0.2)
    assert abs(mark(zoomed)[2] - OUT_SIZE[0] / 2) <= 2


async def test_zoom_from_another_address_is_forbidden():
    app = make_app()
    handler = handler_of(app, "POST", "/zoom")
    response = await handler(request_from(FOREIGN_PEER, b'{"level": 4.0}'))
    assert response.status == 403
    # 倍率は変わらない
    assert pipeline_of(app).level == 1.0


async def test_zoom_checks_the_peer_before_the_body():
    # 接続元を先に調べる（中身の検証より前）。外から壊れた JSON を送っても 403
    app = make_app()
    handler = handler_of(app, "POST", "/zoom")
    response = await handler(request_from(FOREIGN_PEER, b"not json"))
    assert response.status == 403


@pytest.mark.skipif(
    _non_loopback_address() is None, reason="127.0.0.1 以外のアドレスが無い"
)
async def test_zoom_over_a_real_socket_from_another_address_is_forbidden():
    # socket を介した実際の経路でも 403 になる（接続元が 127.0.0.1 でない）
    address = _non_loopback_address()
    app = make_app()
    test_server = TestServer(app, host="0.0.0.0")
    await test_server.start_server()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://{address}:{test_server.port}/zoom", json={"level": 4.0}
            ) as response:
                assert response.status == 403
        assert pipeline_of(app).level == 1.0
    finally:
        await test_server.close()


async def test_zoom_accepts_localhost_on_the_real_path(aiohttp_client):
    app = make_app()
    client = await aiohttp_client(app)
    response = await client.post("/zoom", json={"level": 3.0})
    assert response.status == 200
    assert pipeline_of(app).level == 3.0


async def test_zoom_keeps_the_level_when_the_body_is_broken(aiohttp_client):
    app = make_app()
    client = await aiohttp_client(app)
    response = await client.post(
        "/zoom", data="not json", headers={"Content-Type": "application/json"}
    )
    assert response.status == 400
    assert pipeline_of(app).level == 1.0


async def test_zoom_rejects_a_body_without_a_level(aiohttp_client):
    app = make_app()
    client = await aiohttp_client(app)
    assert (await client.post("/zoom", json={})).status == 400
    assert (await client.post("/zoom", json={"zoom": 2.0})).status == 400
    assert pipeline_of(app).level == 1.0


async def test_zoom_rejects_a_level_that_is_not_a_number(aiohttp_client):
    app = make_app()
    client = await aiohttp_client(app)
    for body in ({"level": "2"}, {"level": True}, {"level": None}, [2.0]):
        response = await client.post("/zoom", json=body)
        assert response.status == 400, body
    assert pipeline_of(app).level == 1.0


async def test_zoom_over_the_maximum_is_accepted_and_clamped_by_the_pipeline(
    aiohttp_client, mark
):
    # 範囲外の倍率（10）でも受け付けられる。切り出しは上限（4 倍）で止まる
    app = make_app(zoom=1.0)
    client = await aiohttp_client(app)
    assert (await client.post("/zoom", json={"level": 10.0})).status == 200
    async with client.get("/stream") as response:
        _, body = await first_jpeg(response)
    frame = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    at_one = mark(make_app(zoom=1.0)["pipeline"].next_frame())[0]
    assert mark(frame)[0] == pytest.approx(at_one * 4, rel=0.2)


# --- 補助 ---------------------------------------------------------------


def test_is_local_peer():
    assert video_server.is_local_peer("127.0.0.1")
    assert not video_server.is_local_peer("10.0.0.5")
    assert not video_server.is_local_peer("192.168.188.125")
    assert not video_server.is_local_peer("::1")
    assert not video_server.is_local_peer(None)


def test_the_boundary_is_a_valid_mjpeg_boundary():
    assert re.fullmatch(r"[0-9A-Za-z'()+_,\-./:=?]{1,70}", video_server.STREAM_BOUNDARY)
