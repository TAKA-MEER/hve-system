"""`VideoPipeline` の試験（DetailedDesign.md §4.3・spec Spec-ui.md §1.5）。"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from hve_video.pipeline import VideoPipeline
from hve_video.sources import FakeSource

# 取り込みの大きさ・出力の高さ・上限・刻み（DetailedDesign-names.md §5）
CAPTURE = (640, 480)
OUT_HEIGHT = 240
ZOOM_MAX = 4.0
ZOOM_STEP = 0.5
JPEG_QUALITY = 60
# 倍率 1・2・4（zoom_step の倍数）
ZOOMS = [1.0, 2.0, 4.0]


def make_pipeline(zoom: float, capture: tuple[int, int] = CAPTURE, **kwargs) -> VideoPipeline:
    options = {
        "zoom_max": ZOOM_MAX,
        "zoom_step": ZOOM_STEP,
        "out_height": OUT_HEIGHT,
        "jpeg_quality": JPEG_QUALITY,
    }
    options.update(kwargs)
    return VideoPipeline(FakeSource(*capture), zoom=zoom, **options)


@pytest.mark.parametrize(
    "capture, out_height, expected",
    [
        ((1920, 1080), 480, (853, 480)),
        ((640, 480), 240, (320, 240)),
        ((320, 240), 240, (320, 240)),
        ((641, 481), 240, (320, 240)),
    ],
)
def test_output_size_follows_the_capture_aspect(capture, out_height, expected):
    pipeline = make_pipeline(1.0, capture=capture, out_height=out_height)
    pipeline.next_frame()
    assert pipeline.output_size() == expected


def test_output_size_is_needed_after_the_first_frame():
    with pytest.raises(RuntimeError):
        make_pipeline(1.0).output_size()


@pytest.mark.parametrize("zoom", ZOOMS)
def test_every_zoom_produces_the_same_output_size(zoom):
    # 帯域は倍率によらない（spec Spec-ui.md §1.5）。倍率で配信の大きさが変わるのは誤り
    pipeline = make_pipeline(zoom)
    frame = pipeline.next_frame()
    assert frame is not None
    assert frame.shape == (OUT_HEIGHT, 320, 3)


def test_the_output_size_is_the_same_for_every_zoom():
    sizes = []
    for zoom in ZOOMS:
        pipeline = make_pipeline(zoom)
        frame = pipeline.next_frame()
        assert pipeline.output_size() == (320, OUT_HEIGHT)
        sizes.append(frame.shape)
    assert sizes[0] == sizes[1] == sizes[2]


def test_the_mark_in_the_center_grows_with_the_zoom(mark):
    # 1 倍 → 2 倍 → 4 倍で、目印のの大きさが 1 : 2 : 4 になる
    widths = []
    for zoom in ZOOMS:
        frame = make_pipeline(zoom).next_frame()
        mark_width, mark_height, _, _ = mark(frame)
        assert abs(mark_width - mark_height) <= 2, f"倍率 {zoom} で目印が縦横比を崩す"
        widths.append(mark_width)
    assert widths[0] * 2 == pytest.approx(widths[1], rel=0.1)
    assert widths[1] * 2 == pytest.approx(widths[2], rel=0.1)


@pytest.mark.parametrize("zoom", ZOOMS)
def test_the_mark_stays_in_the_center_of_the_output(zoom, mark):
    frame = make_pipeline(zoom).next_frame()
    _, _, center_x, center_y = mark(frame)
    assert abs(center_x - 320 / 2) <= 1
    assert abs(center_y - OUT_HEIGHT / 2) <= 1


def test_the_zoom_beyond_the_maximum_stops_at_the_maximum(mark):
    # 範囲外の倍率（10）は上限の 4 倍と同じ結果になる
    biggest = mark(make_pipeline(ZOOM_MAX).next_frame())
    too_big = mark(make_pipeline(10.0).next_frame())
    assert too_big == biggest


def test_jpeg_bytes_decodes_to_the_output_size():
    jpeg = make_pipeline(1.0).jpeg_bytes()
    assert jpeg is not None
    assert jpeg[:2] == b"\xff\xd8"  # JPEG の SOI
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert frame is not None
    assert frame.shape == (OUT_HEIGHT, 320, 3)


def test_jpeg_bytes_keeps_the_zoom(mark):
    # JPEG にしても中央の目印は拡大されたまま（JPEG で倍率だけが失われてはいけない）
    widths = []
    for zoom in (1.0, 4.0):
        jpeg = make_pipeline(zoom).jpeg_bytes()
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        mark_width, _, center_x, _ = mark(frame)
        assert abs(center_x - 320 / 2) <= 2
        widths.append(mark_width)
    assert widths[1] == pytest.approx(widths[0] * 4, rel=0.15)


def test_jpeg_quality_is_used():
    # 同じ取り込みでも JPEG の品質でバイト数が変わる（video_jpeg_quality が効いている）
    # 取り込みを縮めない大きさにして、市松模様の細かい模様を残したまま比べる
    low = make_pipeline(1.0, capture=(320, 240), out_height=240, jpeg_quality=20)
    high = make_pipeline(1.0, capture=(320, 240), out_height=240, jpeg_quality=95)
    assert len(low.jpeg_bytes()) * 2 < len(high.jpeg_bytes())


def test_next_frame_returns_none_when_the_source_stops():
    class _EmptySource:
        def read(self):
            return None

        def release(self):
            return None

    pipeline = VideoPipeline(
        _EmptySource(),
        zoom=1.0,
        zoom_max=ZOOM_MAX,
        zoom_step=ZOOM_STEP,
        out_height=OUT_HEIGHT,
        jpeg_quality=JPEG_QUALITY,
    )
    assert pipeline.next_frame() is None
    assert pipeline.jpeg_bytes() is None


def test_release_releases_the_source():
    pipeline = make_pipeline(1.0)
    pipeline.release()
    assert pipeline.next_frame() is None


def test_latest_jpeg_is_the_source_jpeg_not_the_rebuilt_one():
    # 静止画は取り込みの大きさのまま（作り直した出力の大きさではない）
    pipeline = make_pipeline(4.0)
    pipeline.jpeg_bytes()
    frame = cv2.imdecode(np.frombuffer(pipeline.latest_jpeg(), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert (frame.shape[1], frame.shape[0]) == CAPTURE
