"""取り込み元（`hve_video.sources`）の試験。"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from hve_video import sources

# 取り込みの大きさ（DetailedDesign-names.md §5 の video_capture_width /
# video_capture_height と同じものと、試験しやすい小さいのを混ぜる）
CAPTURES = [(320, 240), (641, 481), (1920, 1080)]


class _RecordingCapture:
    """`cv2.VideoCapture` の代わり。要求されたプロパティを覚えておく。"""

    def __init__(self, opened: bool = True, frame: np.ndarray | None = None):
        self._opened = opened
        self._frame = frame
        self.properties: list[tuple[int, float]] = []
        self.released = 0

    def isOpened(self) -> bool:
        return self._opened

    def set(self, prop: int, value: float) -> bool:
        self.properties.append((prop, value))
        return True

    def read(self):
        if self._frame is None:
            return False, None
        return True, self._frame

    def release(self) -> None:
        self.released += 1


@pytest.fixture
def capture_factory(monkeypatch):
    """`cv2.VideoCapture` を偽物に差し替え、開いた機器番号を覚えるようにする。"""
    opened: list[tuple[int, _RecordingCapture]] = []

    def factory(device: int) -> _RecordingCapture:
        capture = _RecordingCapture(frame=np.zeros((4, 4, 3), dtype=np.uint8))
        opened.append((device, capture))
        return capture

    monkeypatch.setattr(sources.cv2, "VideoCapture", factory)
    return opened


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


def test_v4l2_source_requests_mjpeg_and_the_capture_size(capture_factory):
    sources.V4L2Source(1920, 1080, device=2)
    assert [device for device, _ in capture_factory] == [2]
    properties = capture_factory[0][1].properties
    # 4CC を先にして、そのあとで取り込みの大きさ（V4L2 の指定のしかたに合わせる）
    assert properties[0][0] == cv2.CAP_PROP_FOURCC
    assert properties[0][1] == cv2.VideoWriter_fourcc(*"MJPG")
    assert (cv2.CAP_PROP_FRAME_WIDTH, 1920) in properties
    assert (cv2.CAP_PROP_FRAME_HEIGHT, 1080) in properties


def test_v4l2_source_read_returns_the_frame(monkeypatch):
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(
        sources.cv2, "VideoCapture", lambda device: _RecordingCapture(frame=frame)
    )
    assert sources.V4L2Source(8, 8).read() is frame


def test_v4l2_source_read_returns_none_when_it_fails(monkeypatch):
    monkeypatch.setattr(sources.cv2, "VideoCapture", lambda device: _RecordingCapture())
    assert sources.V4L2Source(8, 8).read() is None


def test_v4l2_source_raises_when_the_camera_cannot_be_opened(monkeypatch):
    monkeypatch.setattr(
        sources.cv2, "VideoCapture", lambda device: _RecordingCapture(opened=False)
    )
    with pytest.raises(RuntimeError):
        sources.V4L2Source(8, 8)


def test_v4l2_source_releases_the_camera_when_it_cannot_be_opened(monkeypatch):
    captures = []

    def factory(device):
        capture = _RecordingCapture(opened=False)
        captures.append(capture)
        return capture

    monkeypatch.setattr(sources.cv2, "VideoCapture", factory)
    with pytest.raises(RuntimeError):
        sources.V4L2Source(8, 8)
    assert captures[0].released == 1


def test_open_source_picks_the_fake_image_series():
    source = sources.open_source(True, 320, 240)
    assert isinstance(source, sources.FakeSource)
    assert source.capture_size == (320, 240)


def test_open_source_picks_the_real_camera(monkeypatch):
    monkeypatch.setattr(
        sources.cv2, "VideoCapture", lambda device: _RecordingCapture(opened=True)
    )
    assert isinstance(sources.open_source(False, 320, 240), sources.V4L2Source)


# --- カメラの選び方 -----------------------------------------------------------------------


def test_find_camera_device_picks_the_usb_camera_by_name(tmp_path):
    """**`/dev/videoN` の番号ではなく、名前（`*-video-index0`）で選ぶ**（挿し直しで番号が変わる）。"""
    (tmp_path / "usb-Image+_UGREEN_Camera_4K_LL-0000000001-video-index1").touch()
    wanted = tmp_path / "usb-Image+_UGREEN_Camera_4K_LL-0000000001-video-index0"
    wanted.touch()
    assert sources.find_camera_device(tmp_path) == str(wanted)


def test_find_camera_device_falls_back_to_zero(tmp_path):
    """名前で見つからなければ従来どおり 0 番（ディレクトリが無くても落ちない）。"""
    assert sources.find_camera_device(tmp_path) == 0
    assert sources.find_camera_device(tmp_path / "missing") == 0


def test_open_source_opens_the_camera_found_by_name(monkeypatch):
    """**実物を開くときは `find_camera_device` の結果を渡す**（経路を縛る）。"""
    opened = []

    class FakeCapture:
        def __init__(self, device):
            opened.append(device)

        def isOpened(self):
            return True

        def set(self, *_args):
            return True

    monkeypatch.setattr(sources.cv2, "VideoCapture", FakeCapture)
    monkeypatch.setattr(sources, "find_camera_device", lambda: "/dev/v4l/by-id/cam-video-index0")
    sources.open_source(False, 320, 240)
    assert opened == ["/dev/v4l/by-id/cam-video-index0"]
