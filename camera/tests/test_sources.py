"""取り込み元（`hve_video.sources`）の試験。"""

from __future__ import annotations

import os
import subprocess
import time

import cv2
import numpy as np
import pytest

from hve_video import sources

# 取り込みの大きさ（DetailedDesign-names.md §5 の video_capture_width /
# video_capture_height と同じものと、試験しやすい小さいのを混ぜる）
CAPTURES = [(320, 240), (641, 481), (1920, 1080)]


@pytest.mark.parametrize("capture", CAPTURES)
def test_fake_source_returns_the_requested_size(capture):
    width, height = capture
    frame = sources.FakeSource(width, height).read()
    assert frame is not None
    assert frame.shape == (height, width, 3)
    assert frame.dtype == np.uint8


@pytest.mark.parametrize("capture", CAPTURES)
def test_fake_source_marks_a_square_in_the_center(capture, mark):
    width, height = capture
    frame = sources.FakeSource(width, height).read()
    mark_width, mark_height, center_x, center_y = mark(frame)
    assert abs(mark_width - mark_height) <= 1, "目印は正方形"
    assert abs(center_x - width / 2) <= 1
    assert abs(center_y - height / 2) <= 1


def test_fake_source_changes_every_frame():
    source = sources.FakeSource(320, 240)
    first = source.read()
    second = source.read()
    assert not np.array_equal(first, second)


def test_fake_source_keeps_the_mark_in_the_same_place(mark):
    # 帯は動くが、目印は取り込みの中央から動かない（拡大は切り出しと縮小だけで起きる）
    source = sources.FakeSource(320, 240)
    measured = [mark(source.read()) for _ in range(3)]
    assert measured[0] == measured[1] == measured[2]


def test_fake_source_stops_after_release():
    source = sources.FakeSource(320, 240)
    assert source.read() is not None
    source.release()
    assert source.read() is None


def test_fake_source_rejects_a_zero_size():
    with pytest.raises(ValueError):
        sources.FakeSource(0, 240)


def test_fake_source_latest_jpeg_is_a_jpeg_of_the_capture_size():
    source = sources.FakeSource(320, 240)
    source.read()
    jpeg = source.latest_jpeg()
    assert jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9"
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert frame.shape == (240, 320, 3)


def test_fake_source_latest_jpeg_is_available_before_the_first_read():
    assert sources.FakeSource(320, 240).latest_jpeg() is not None


def test_open_source_picks_the_fake_image_series():
    source = sources.open_source(True, 320, 240)
    assert isinstance(source, sources.FakeSource)
    assert source.capture_size == (320, 240)


# --- split_mjpeg --------------------------------------------------------------------------


def jpeg_of(level: int, size=(64, 48)) -> bytes:
    """一様な灰色の JPEG。`level` で 1 枚ずつ見分ける。"""
    image = np.full((size[1], size[0], 3), level, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


V4L2_TEXT = b"VIDIOC_S_PARM: ok\nFrame rate set to 10.000 fps\n<<<<"


def test_split_mjpeg_splits_every_frame():
    a, b, c = jpeg_of(10), jpeg_of(100), jpeg_of(200)
    frames, rest = sources.split_mjpeg(a + b + c)
    assert frames == [a, b, c]
    assert rest == b""


def test_split_mjpeg_drops_the_text_before_the_first_frame():
    """**`v4l2-ctl` は標準出力の頭に文字を出す。`FFD8` より前は捨てる。**"""
    a, b = jpeg_of(10), jpeg_of(100)
    frames, rest = sources.split_mjpeg(V4L2_TEXT + a + b)
    assert frames == [a, b]
    assert frames[0][:2] == b"\xff\xd8"
    assert rest == b""


def test_split_mjpeg_drops_bytes_between_frames():
    a, b = jpeg_of(10), jpeg_of(100)
    frames, _ = sources.split_mjpeg(a + b"garbage" + b)
    assert frames == [a, b]


def test_split_mjpeg_returns_only_text_as_nothing_to_keep():
    assert sources.split_mjpeg(V4L2_TEXT) == ([], b"")


def test_split_mjpeg_keeps_an_unfinished_frame_as_the_rest():
    a, b = jpeg_of(10), jpeg_of(100)
    frames, rest = sources.split_mjpeg(a + b[:30])
    assert frames == [a]
    assert rest == b[:30]
    frames, rest = sources.split_mjpeg(rest + b[30:])
    assert frames == [b]
    assert rest == b""


def test_split_mjpeg_keeps_a_marker_split_across_reads():
    a = jpeg_of(10)
    frames, rest = sources.split_mjpeg(V4L2_TEXT + b"\xff")
    assert (frames, rest) == ([], b"\xff")
    frames, rest = sources.split_mjpeg(rest + a[1:])
    assert frames == [a]


def test_split_mjpeg_drops_a_truncated_frame():
    """途中で切れた 1 枚（終わりの前に次の始まりが来る）は捨て、次の 1 枚は返す。"""
    a, b = jpeg_of(10), jpeg_of(100)
    frames, rest = sources.split_mjpeg(a[: len(a) // 2] + b)
    assert frames == [b]
    assert rest == b""


def test_split_mjpeg_returns_decodable_frames():
    frames, _ = sources.split_mjpeg(V4L2_TEXT + b"".join(jpeg_of(v) for v in (0, 90, 250)))
    assert len(frames) == 3
    for frame, level in zip(frames, (0, 90, 250)):
        decoded = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert abs(int(decoded.mean()) - level) <= 3


# --- MjpegPipeSource（`v4l2-ctl` の代わりにパイプを使う） ----------------------------------


class PipeChild:
    """`subprocess.Popen` の代わり。標準出力はパイプで、試験が書き込む。"""

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
            os.close(self.write_fd)  # 標準出力が閉じ、区切りのスレッドが抜ける

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return 0


class Spawner:
    def __init__(self):
        self.children: list[PipeChild] = []
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        assert kwargs["stdout"] == subprocess.PIPE
        self.calls.append(command)
        child = PipeChild()
        self.children.append(child)
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
    spawner = Spawner()
    made: list[sources.MjpegPipeSource] = []

    def make(**kwargs):
        source = sources.MjpegPipeSource(
            1280, 720, 10, "/dev/video0", spawn=spawner, read_timeout=0.3, **kwargs
        )
        made.append(source)
        assert wait_until(lambda: spawner.children)
        return source

    make.spawner = spawner
    yield make
    for source in made:
        source.release()


def test_mjpeg_pipe_source_runs_v4l2_ctl_with_the_capture_options(pipe_source):
    source = pipe_source()
    command = pipe_source.spawner.calls[0]
    assert command == source.command
    assert command[0] == "v4l2-ctl"
    assert command[1:3] == ["-d", "/dev/video0"]
    assert "--set-fmt-video=width=1280,height=720,pixelformat=MJPG" in command
    assert "--set-parm=10" in command
    assert "--stream-mmap=4" in command
    assert "--stream-to=-" in command
    assert not any("ffmpeg" in part for part in command)


def test_mjpeg_pipe_source_has_no_snapshot_before_the_first_frame(pipe_source):
    source = pipe_source()
    assert source.latest_jpeg() is None
    assert source.read() is None  # 待っても来なければ None


def test_mjpeg_pipe_source_skips_the_text_and_decodes(pipe_source):
    source = pipe_source()
    pipe_source.spawner.children[0].feed(V4L2_TEXT + jpeg_of(120))
    frame = source.read()
    assert frame is not None
    assert frame.shape == (48, 64, 3)
    assert abs(int(frame.mean()) - 120) <= 3


def test_mjpeg_pipe_source_latest_jpeg_is_the_camera_jpeg_untouched(pipe_source):
    source = pipe_source()
    first, second = jpeg_of(50, (1280, 720)), jpeg_of(150, (1280, 720))
    pipe_source.spawner.children[0].feed(V4L2_TEXT + first + second)
    assert wait_until(lambda: source.latest_jpeg() == second)


def test_mjpeg_pipe_source_read_uses_only_the_latest_frame(pipe_source):
    """**作り直しは最新の 1 枚だけを使う。** 溜まった古い 1 枚を順に返してはいけない。"""
    source = pipe_source()
    levels = [10, 60, 110, 160, 210]
    pipe_source.spawner.children[0].feed(b"".join(jpeg_of(v) for v in levels))
    assert wait_until(lambda: source.latest_jpeg() == jpeg_of(levels[-1]))
    frame = source.read()
    assert abs(int(frame.mean()) - levels[-1]) <= 3, "古い 1 枚を返した"
    assert source.read() is None, "同じ 1 枚か、溜まっていた古い 1 枚を返した"


def test_mjpeg_pipe_source_does_not_return_the_same_frame_twice(pipe_source):
    source = pipe_source()
    child = pipe_source.spawner.children[0]
    child.feed(jpeg_of(40))
    assert source.read() is not None
    assert source.read() is None
    child.feed(jpeg_of(200))
    frame = source.read()
    assert abs(int(frame.mean()) - 200) <= 3


def test_mjpeg_pipe_source_drops_a_frame_it_cannot_decode(pipe_source):
    source = pipe_source()
    child = pipe_source.spawner.children[0]
    child.feed(b"\xff\xd8not a jpeg\xff\xd9")
    assert source.read() is None
    child.feed(jpeg_of(90))
    frame = source.read()
    assert frame is not None and abs(int(frame.mean()) - 90) <= 3


def test_mjpeg_pipe_source_survives_a_truncated_frame(pipe_source):
    source = pipe_source()
    child = pipe_source.spawner.children[0]
    child.feed(jpeg_of(30)[:40] + jpeg_of(130))
    frame = source.read()
    assert frame is not None and abs(int(frame.mean()) - 130) <= 3


def test_mjpeg_pipe_source_updates_the_latest_frame_without_read(pipe_source):
    """区切りは `read()` と別スレッド。`read()` が呼ばれなくても最新の 1 枚が更新される。"""
    source = pipe_source()
    child = pipe_source.spawner.children[0]
    for level in (20, 80, 140):
        child.feed(jpeg_of(level))
        assert wait_until(lambda: source.latest_jpeg() == jpeg_of(level))


def test_mjpeg_pipe_source_restarts_the_child_when_it_dies(pipe_source):
    source = pipe_source(respawn_wait=0.05)
    pipe_source.spawner.children[0].terminate()
    assert wait_until(lambda: len(pipe_source.spawner.children) == 2)
    pipe_source.spawner.children[1].feed(jpeg_of(77))
    frame = source.read()
    assert frame is not None and abs(int(frame.mean()) - 77) <= 3


def test_mjpeg_pipe_source_release_stops_the_child(pipe_source):
    source = pipe_source()
    source.release()
    assert pipe_source.spawner.children[0].terminated
    assert not source._reader.is_alive()
    assert source.read() is None


def test_open_source_picks_the_real_camera_with_the_params(monkeypatch):
    made = []

    class Recorded:
        def __init__(self, *args):
            made.append(args)

    monkeypatch.setattr(sources, "MjpegPipeSource", Recorded)
    source = sources.open_source(False, 1280, 720, 10, "/dev/video0")
    assert isinstance(source, Recorded)
    assert made == [(1280, 720, 10, "/dev/video0")]
