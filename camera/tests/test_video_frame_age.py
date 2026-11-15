"""`/snapshot` の `X-Frame-Age-Ms`（WP-VIDEO-05。DetailedDesign-protocol-ss.md §4・§5.1）。

最後の JPEG を受けてからの経過 ms（単調時計・整数）を、返す時点で付ける。
時計は差し替えて試験する。`fcntl` を使わないので Windows でも回る。
"""

from __future__ import annotations

import os
import subprocess
import time

import cv2
import numpy as np
import pytest

from hve_video import server as video_server
from hve_video import sources
from hve_video.pipeline import VideoPipeline

HEADER = "X-Frame-Age-Ms"


class Clock:
    """動かせる単調時計（秒）。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def jpeg_gray(level: int) -> bytes:
    ok, buffer = cv2.imencode(".jpg", np.full((48, 64, 3), level, dtype=np.uint8))
    assert ok
    return buffer.tobytes()


class PipeChild:
    def __init__(self):
        read_fd, self.write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb")
        self.terminated = False

    def feed(self, data: bytes) -> None:
        os.write(self.write_fd, data)

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        if not self.terminated:
            self.terminated = True
            os.close(self.write_fd)

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return 0


class Spawner:
    """起動のたびに子プロセスを作り、`frame_on_spawn` があれば直後に 1 枚流す。"""

    def __init__(self, frame_on_spawn: bytes | None = None):
        self.frame_on_spawn = frame_on_spawn
        self.children: list[PipeChild] = []

    def __call__(self, command, **kwargs):
        assert kwargs["stdout"] == subprocess.PIPE
        child = PipeChild()
        self.children.append(child)
        if self.frame_on_spawn is not None:
            child.feed(self.frame_on_spawn)
        return child


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def pipe_source():
    made = []

    def make(spawner, clock):
        source = sources.MjpegPipeSource(
            640, 480, 10, "/dev/video0",
            spawn=spawner, read_timeout=0.3, respawn_wait=0.05, clock=clock,
        )
        made.append(source)
        return source

    yield make
    for source in made:
        source.release()


# --- 取り込み元 -------------------------------------------------------------


def test_fake_source_stamps_the_frame_with_the_clock():
    clock = Clock()
    source = sources.FakeSource(64, 48, clock=clock)
    assert source.read() is not None
    clock.now += 0.25
    jpeg, age = source.latest_jpeg_stamped()
    assert jpeg[:2] == b"\xff\xd8"
    assert age == 250
    assert isinstance(age, int)
    source.read()  # 新しく作ると経過は 0 に戻る
    assert source.latest_jpeg_stamped()[1] == 0


def test_fake_source_has_no_stamp_while_stopped():
    clock = Clock()
    source = sources.FakeSource(64, 48, clock=clock)
    source.read()
    source.set_capturing(False)
    assert source.latest_jpeg_stamped() is None
    assert source.wait_latest_jpeg_stamped(0.1) is None
    clock.now += 10.0
    source.set_capturing(True)
    jpeg, age = source.wait_latest_jpeg_stamped(0.1)  # 起こした直後の 1 枚は今作った 1 枚
    assert age == 0


def test_mjpeg_pipe_source_age_is_the_time_since_the_last_jpeg(pipe_source):
    clock = Clock()
    spawner = Spawner()
    source = pipe_source(spawner, clock)
    assert wait_until(lambda: spawner.children)
    assert source.latest_jpeg_stamped() is None  # 1 枚も無い
    spawner.children[0].feed(jpeg_gray(60))
    assert wait_until(lambda: source.latest_jpeg() is not None)
    clock.now += 0.4
    jpeg, age = source.latest_jpeg_stamped()
    assert jpeg == jpeg_gray(60)
    assert age == 400, "受けた時刻そのものでなく、経過を返す"
    clock.now += 0.1
    assert source.latest_jpeg_stamped()[1] == 500, "返す時点の経過（受けた時点で固めない）"
    spawner.children[0].feed(jpeg_gray(120))  # 新しい 1 枚で経過は戻る
    assert wait_until(lambda: source.latest_jpeg() == jpeg_gray(120))
    assert source.latest_jpeg_stamped()[1] == 0


def test_mjpeg_pipe_source_age_after_wake_is_the_new_frames(pipe_source):
    """止めて起こした直後は、止める前の古い時刻でなく、起こして来た 1 枚の経過を返す。"""
    clock = Clock(1000.0)
    spawner = Spawner(jpeg_gray(60))
    source = pipe_source(spawner, clock)
    assert wait_until(lambda: source.latest_jpeg() == jpeg_gray(60))
    source.set_capturing(False)
    assert source.latest_jpeg_stamped() is None
    clock.now += 60.0  # 止めている間に 1 分たつ
    spawner.frame_on_spawn = jpeg_gray(180)
    source.set_capturing(True)
    stamped = source.wait_latest_jpeg_stamped(3.0)
    assert stamped is not None
    jpeg, age = stamped
    assert jpeg == jpeg_gray(180)
    assert age == 0, "止める前の古い時刻の経過（60000）を返した"
    clock.now += 0.03
    assert source.latest_jpeg_stamped()[1] == 30


def test_mjpeg_pipe_source_wait_stamped_times_out_without_a_frame(pipe_source):
    source = pipe_source(Spawner(), Clock())
    assert source.wait_latest_jpeg_stamped(0.1) is None


# --- パイプライン・サーバ ----------------------------------------------------


def make_app(source, wake_wait_ms: float = 3000):
    pipeline = VideoPipeline(
        source, zoom=1.0, zoom_max=4.0, zoom_step=0.5, out_height=240, jpeg_quality=60
    )
    app = video_server.create_app(pipeline, 20.0, 5000, wake_wait_ms)

    async def release(_app):
        source.release()

    app.on_shutdown.append(release)
    return app


async def test_snapshot_has_the_frame_age_header(aiohttp_client, pipe_source):
    clock = Clock()
    spawner = Spawner()
    source = pipe_source(spawner, clock)
    client = await aiohttp_client(make_app(source))
    assert wait_until(lambda: spawner.children)
    spawner.children[0].feed(jpeg_gray(60))
    assert wait_until(lambda: source.latest_jpeg() is not None)
    clock.now += 0.25
    response = await client.get("/snapshot")
    assert response.status == 200
    assert response.headers[HEADER] == "250"
    assert await response.read() == jpeg_gray(60)
    clock.now += 0.5
    response = await client.get("/snapshot")  # 返す時点の経過（750）
    assert response.headers[HEADER] == "750"


async def test_snapshot_503_has_no_frame_age(aiohttp_client, pipe_source):
    source = pipe_source(Spawner(), Clock())
    client = await aiohttp_client(make_app(source))
    response = await client.get("/snapshot")
    assert response.status == 503
    assert HEADER not in response.headers


async def test_snapshot_woken_from_stop_returns_the_new_frames_age(aiohttp_client, pipe_source):
    clock = Clock(1000.0)
    spawner = Spawner(jpeg_gray(60))
    source = pipe_source(spawner, clock)
    client = await aiohttp_client(make_app(source))
    assert wait_until(lambda: source.latest_jpeg() == jpeg_gray(60))
    assert (await client.post("/capture", json={"on": False})).status == 200
    clock.now += 60.0
    spawner.frame_on_spawn = jpeg_gray(180)
    response = await client.get("/snapshot")
    assert response.status == 200
    assert await response.read() == jpeg_gray(180)
    assert response.headers[HEADER] == "0", "止める前の古い時刻の経過を返した"


async def test_snapshot_of_the_fake_source_has_an_integer_age(aiohttp_client):
    clock = Clock()
    source = sources.FakeSource(64, 48, clock=clock)
    client = await aiohttp_client(make_app(source))
    source.read()
    clock.now += 0.12
    response = await client.get("/snapshot")
    assert response.status == 200
    assert response.headers[HEADER] == "120"
