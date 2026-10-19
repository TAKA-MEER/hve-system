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
SEND_TIMEOUT_MS = 5000
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
    return video_server.create_app(pipeline, video_fps, SEND_TIMEOUT_MS)


def pipeline_of(app) -> VideoPipeline:
    return app["pipeline"]


async def _read_until(content, sep: bytes) -> bytes:
    """`sep` まで読み、`sep` を含めて返す。余分に読んだ分は戻す。

    aiohttp 3.6 の `StreamReader` には `readuntil` が無いための代替。
    """
    buf = b""
    while sep not in buf:
        chunk = await content.readany()
        if not chunk:
            raise AssertionError("区切りが来る前に配信が切れた")
        buf += chunk
    head, _, rest = buf.partition(sep)
    if rest:
        content.unread_data(rest)
    return head + sep


async def first_jpeg(response) -> tuple[dict[str, str], bytes]:
    """MJPEG の最初の 1 フレームを `(区切りのヘッダ, JPEG)` で返す。"""
    head = await _read_until(response.content, b"\r\n\r\n")
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
    # aiohttp 3.6 の StreamReader は (protocol, *, limit, ...) なので limit は名前で渡す
    payload = streams.StreamReader(
        mock.Mock(_reading_paused=False), limit=2**16, loop=asyncio.get_event_loop()
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
        head = await _read_until(response.content, b"\r\n\r\n")
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


# --- 静止画（spec H-U9） -------------------------------------------------


class CameraJpegSource:
    """取り込み元の代わり。「カメラの JPEG」を固定で持ち、作り直し用の絵は小さい別物にする。"""

    def __init__(self, camera_jpeg):
        self.camera_jpeg = camera_jpeg
        self._fake = FakeSource(*CAPTURE)

    def read(self):
        return self._fake.read()

    def latest_jpeg(self):
        return self.camera_jpeg

    def release(self):
        pass


def camera_jpeg_720p() -> bytes:
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[:, ::7, 1] = 200  # 縞（作り直すと変わる）
    image[300:420, 560:720] = 255
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def make_snapshot_app(camera_jpeg, zoom: float = 1.0):
    pipeline = VideoPipeline(
        CameraJpegSource(camera_jpeg),
        zoom=zoom,
        zoom_max=ZOOM_MAX,
        zoom_step=ZOOM_STEP,
        out_height=OUT_HEIGHT,
        jpeg_quality=JPEG_QUALITY,
    )
    return video_server.create_app(pipeline, VIDEO_FPS, SEND_TIMEOUT_MS)


async def test_snapshot_returns_the_camera_jpeg_unmodified(aiohttp_client):
    """**カメラの JPEG をバイト単位でそのまま返す**（作り直した 480p ではない）。"""
    camera = camera_jpeg_720p()
    client = await aiohttp_client(make_snapshot_app(camera))
    response = await client.get("/snapshot")
    assert response.status == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.headers["Cache-Control"] == "no-store"
    body = await response.read()
    assert body == camera
    frame = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert (frame.shape[1], frame.shape[0]) == (1280, 720)


async def test_snapshot_is_not_cropped_by_the_zoom(aiohttp_client):
    """**倍率を上げても切り出さない。** 配信（/stream）は切り出すが、静止画は 1280×720 のまま。"""
    camera = camera_jpeg_720p()
    app = make_snapshot_app(camera)
    client = await aiohttp_client(app)
    assert (await client.post("/zoom", json={"level": 4.0})).status == 200
    async with client.get("/stream") as response:
        _, streamed = await first_jpeg(response)
    assert streamed != camera
    response = await client.get("/snapshot")
    assert await response.read() == camera


async def test_snapshot_is_503_before_the_first_frame(aiohttp_client):
    client = await aiohttp_client(make_snapshot_app(None))
    response = await client.get("/snapshot")
    assert response.status == 503


async def test_snapshot_is_served_to_other_addresses():
    """`/zoom` と違い、外の端末から受ける（接続元を見ない）。"""
    camera = camera_jpeg_720p()
    app = make_snapshot_app(camera)
    handler = handler_of(app, "GET", "/snapshot")
    request = make_mocked_request("GET", "/snapshot", app=app)
    response = await handler(request)
    assert response.status == 200
    assert response.body == camera


async def test_snapshot_of_the_fake_source_is_the_capture_size_not_the_stream_size(
    aiohttp_client,
):
    """偽物のモードも静止画を返す。取り込みの大きさで、配信の大きさではない。"""
    client = await aiohttp_client(make_app(zoom=4.0))
    response = await client.get("/snapshot")
    assert response.status == 200
    frame = cv2.imdecode(np.frombuffer(await response.read(), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert (frame.shape[1], frame.shape[0]) == CAPTURE


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


# --- 配り方と送れない接続の始末（WP-VIDEO-03） ---------------------------------

FAST_FPS = 50.0
PERIOD = 1.0 / FAST_FPS


class CountingPipeline:
    """`jpeg_bytes` の呼ばれた回数を数え、呼ぶたびに違う小さい JPEG を返す偽の pipeline。"""

    def __init__(self):
        self.calls = 0
        self.level = 1.0

    def jpeg_bytes(self):
        self.calls += 1
        return b"\xff\xd8" + str(self.calls).encode() + b"\xff\xd9"

    def latest_jpeg(self):
        return None

    def release(self):
        pass


class StealablePipeline(CountingPipeline):
    """取り込み元の「どの 1 枚まで取ったか」（全接続で共有）を真似る。

    新しい 1 枚は周期ごとに 1 枚届く。取った 1 枚は他の呼び出しには渡らない（`None`）。
    """

    def __init__(self):
        super().__init__()
        self.produced = 0
        self.taken = 0

    def jpeg_bytes(self):
        self.calls += 1
        if self.produced == self.taken:
            return None
        self.taken = self.produced
        return b"\xff\xd8" + str(self.taken).encode() + b"\xff\xd9"


class FakeResponse:
    """`web.StreamResponse` の代わり。`X-Block` 付きの接続は `write` が返らない。"""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self.writes: list[bytes] = []
        self.blocked = False
        self.write_started = 0
        FakeResponse.instances.append(self)

    async def prepare(self, request):
        self.blocked = "X-Block" in request.headers

    async def write(self, data):
        self.write_started += 1
        if self.blocked:
            await asyncio.Event().wait()  # 返らない
        self.writes.append(data)

    def jpegs(self) -> list[bytes]:
        return [w.split(b"\r\n\r\n", 1)[1][:-2] for w in self.writes]


def fake_app(pipeline, send_timeout_ms: float = 5000.0):
    app = video_server.create_app(pipeline, FAST_FPS, send_timeout_ms)
    app.freeze()  # `app.shutdown()` に要る
    return app


def fake_request(app, blocked: bool = False):
    transport = mock.Mock()
    headers = {"X-Block": "1"} if blocked else {}
    request = make_mocked_request("GET", "/stream", app=app, transport=transport, headers=headers)
    return request, transport


@pytest.fixture
def fake_response(monkeypatch):
    FakeResponse.instances = []
    monkeypatch.setattr(video_server.web, "StreamResponse", FakeResponse)
    return FakeResponse


async def open_connection(app, blocked: bool = False):
    """`_stream` を 1 本動かす。`(task, transport, response)` を返す。"""
    request, transport = fake_request(app, blocked)
    before = len(FakeResponse.instances)
    task = asyncio.ensure_future(video_server._stream(request))
    while len(FakeResponse.instances) == before:
        await asyncio.sleep(0)
    await asyncio.sleep(0)  # prepare を通って購読するまで進める
    return task, transport, FakeResponse.instances[-1]


async def drop(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_two_connections_share_one_rebuild_per_period(fake_response):
    """**2 本つないでも、作り直しは 1 周期に 1 回**。2 本とも同じ JPEG を受け取る。"""
    pipeline = CountingPipeline()
    app = fake_app(pipeline)
    a, _, ra = await open_connection(app)
    b, _, rb = await open_connection(app)
    await asyncio.sleep(PERIOD * 15)
    await drop(a)
    await drop(b)
    await app.shutdown()
    received = max(len(ra.writes), len(rb.writes))
    assert received >= 8
    assert pipeline.calls <= received + 2  # 接続ごとに作ると 2 倍になる
    common = set(ra.jpegs()) & set(rb.jpegs())
    assert len(common) >= 5  # 同じ 1 枚を配っている


async def test_nothing_is_rebuilt_without_a_connection(fake_response):
    """**接続が 0 本のあいだは作り直さない**（待機中の CPU を 0 に保つ）。"""
    pipeline = CountingPipeline()
    app = fake_app(pipeline)
    await asyncio.sleep(PERIOD * 5)
    assert pipeline.calls == 0  # 1 本もつながないうちは動かない
    conn, _, _ = await open_connection(app)
    await asyncio.sleep(PERIOD * 5)
    assert pipeline.calls > 0
    await drop(conn)
    await asyncio.sleep(PERIOD * 4)  # 最後の 1 回が終わるのを待つ
    settled = pipeline.calls
    await asyncio.sleep(PERIOD * 10)
    assert pipeline.calls == settled
    # 次の接続でまた動き出す
    conn, _, response = await open_connection(app)
    await asyncio.sleep(PERIOD * 5)
    await drop(conn)
    await app.shutdown()
    assert pipeline.calls > settled
    assert response.writes


async def test_a_connection_that_cannot_send_is_dropped_by_abort(fake_response):
    """**書き込みが返らない接続は、タイムアウトで抜け、`abort()` で捨てられる。**"""
    pipeline = CountingPipeline()
    app = fake_app(pipeline, send_timeout_ms=100)
    task, transport, _ = await open_connection(app, blocked=True)
    done, _ = await asyncio.wait([task], timeout=1.0)
    assert task in done  # ハンドラが抜ける（タイムアウトを外すと永久に待つ）
    transport.abort.assert_called_once()
    transport.close.assert_not_called()  # 送り残しを待つ close() では閉じない
    await app.shutdown()
    if task not in done:
        await drop(task)


async def test_the_timeout_applies_to_the_write_not_the_wait_for_a_frame(fake_response):
    """次の 1 枚が来ない待ちではタイムアウトで捨てない（`jpeg_bytes` が `None` のとき）。"""
    pipeline = StealablePipeline()  # 1 枚も届かない
    app = fake_app(pipeline, send_timeout_ms=50)
    task, transport, _ = await open_connection(app)
    await asyncio.sleep(0.3)
    assert not task.done()
    transport.abort.assert_not_called()
    await drop(task)
    await app.shutdown()


async def test_a_stuck_connection_does_not_stop_the_others(fake_response):
    """1 本の書き込みが返らなくても、他の接続には次々に 1 枚が届く。"""
    pipeline = CountingPipeline()
    app = fake_app(pipeline, send_timeout_ms=60000)
    stuck, _, stuck_response = await open_connection(app, blocked=True)
    good, _, good_response = await open_connection(app)
    await asyncio.sleep(PERIOD * 15)
    assert not stuck.done()  # まだ待っている
    assert stuck_response.write_started == 1  # 詰まったまま、次を送ろうとしていない
    assert len(good_response.writes) >= 8
    await drop(stuck)
    await drop(good)
    await app.shutdown()


async def test_a_slow_connection_skips_frames_instead_of_queueing_them(fake_response):
    """送っている途中に来た枚は飛ばし、送り終えたら最新の 1 枚を送る（溜めない）。"""
    pipeline = CountingPipeline()
    app = fake_app(pipeline, send_timeout_ms=60000)

    class SlowResponse(FakeResponse):
        async def write(self, data):
            self.write_started += 1
            await asyncio.sleep(PERIOD * 5)
            self.writes.append(data)

    with mock.patch.object(video_server.web, "StreamResponse", SlowResponse):
        task, _, response = await open_connection(app)
        await asyncio.sleep(PERIOD * 30)
    await drop(task)
    await app.shutdown()
    sent = [int(j[2:-2]) for j in response.jpegs()]
    assert len(sent) >= 3
    assert len(sent) < pipeline.calls / 2  # 作った枚の多くは飛ばされる
    assert sent == sorted(sent)
    # 送った枚は飛び飛び（古い枚を順に溜めて送ってはいない）
    assert sent[-1] - sent[0] >= 2 * (len(sent) - 1)


async def test_connect_and_leave_probes_do_not_take_frames_from_a_viewer(fake_response):
    """つないですぐ切る接続（画面の状態確認）があっても、別の接続の枚数は減らない。"""
    pipeline = StealablePipeline()
    app = fake_app(pipeline)
    viewer, _, response = await open_connection(app)
    produced_total = 0
    for _ in range(25):
        pipeline.produced += 1
        produced_total += 1
        probe, _, _ = await open_connection(app)
        await asyncio.sleep(PERIOD * 0.4)
        await drop(probe)
        await asyncio.sleep(PERIOD * 0.6)
    await asyncio.sleep(PERIOD * 3)
    await drop(viewer)
    await app.shutdown()
    assert len(response.writes) >= produced_total * 0.8
