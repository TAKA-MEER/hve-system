"""`crop_rect` の試験（DetailedDesign.md §4.3）。"""

import pytest

from hve_video.crop import crop_rect

# 取り込みの大きさ・上限・刻み（DetailedDesign-names.md §5 の zoom_max / zoom_step）
ZOOM_MAX = 4.0
ZOOM_STEP = 0.5
# 取り込みの大きさ（names §5 の video_capture_width / video_capture_height と、
# 奇数や 4:3 のものも混ぜて境界を見る）
CAPTURES = [(1920, 1080), (1280, 720), (640, 480), (641, 481), (854, 480), (320, 240)]
ZOOMS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]


def _level(width: int, capture_width: int) -> float:
    """切り出した幅から実際の倍率を戻す（幅は倍率の逆数に比例する）。"""
    return capture_width / width


@pytest.mark.parametrize("capture", CAPTURES)
@pytest.mark.parametrize("zoom", ZOOMS)
def test_crop_is_centered(capture, zoom):
    width, height = capture
    x, y, w, h = crop_rect(width, height, zoom, ZOOM_MAX, ZOOM_STEP)
    # 切り出す枠の中心が取り込みの中心と一致する（整数の丸めで 0.5 画素ぶんずれるのは許す）
    assert abs((x + w / 2) - width / 2) <= 0.5
    assert abs((y + h / 2) - height / 2) <= 0.5


@pytest.mark.parametrize("capture", CAPTURES)
@pytest.mark.parametrize("zoom", ZOOMS)
def test_crop_stays_inside_the_capture(capture, zoom):
    width, height = capture
    x, y, w, h = crop_rect(width, height, zoom, ZOOM_MAX, ZOOM_STEP)
    assert 0 <= x and x + w <= width
    assert 0 <= y and y + h <= height
    assert w >= 1 and h >= 1


@pytest.mark.parametrize("capture", CAPTURES)
@pytest.mark.parametrize("zoom", ZOOMS)
def test_crop_keeps_the_capture_aspect(capture, zoom):
    width, height = capture
    _, _, w, h = crop_rect(width, height, zoom, ZOOM_MAX, ZOOM_STEP)
    assert abs(w / h - width / height) / (width / height) < 0.01


@pytest.mark.parametrize("capture", CAPTURES)
def test_width_and_height_are_the_capture_divided_by_the_zoom(capture):
    width, height = capture
    for zoom in ZOOMS:
        _, _, w, h = crop_rect(width, height, zoom, ZOOM_MAX, ZOOM_STEP)
        # 整数に丸めるので 1 画素ぶんのずれは許す
        assert abs(w - width / zoom) <= 1
        assert abs(h - height / zoom) <= 1


def test_width_and_height_are_exact_on_the_nominal_capture():
    # names §5 の 1920x1080 ではちょうど割り切れる
    for zoom in ZOOMS:
        _, _, w, h = crop_rect(1920, 1080, zoom, ZOOM_MAX, ZOOM_STEP)
        assert w == round(1920 / zoom)
        assert h == round(1080 / zoom)


def test_zoom_below_one_is_clamped_to_one():
    # 範囲外の倍率（0.5）。切り出さない
    assert crop_rect(1920, 1080, 0.5, ZOOM_MAX, ZOOM_STEP) == (0, 0, 1920, 1080)
    assert crop_rect(1920, 1080, 0.0, ZOOM_MAX, ZOOM_STEP) == (0, 0, 1920, 1080)
    assert crop_rect(1920, 1080, -3.0, ZOOM_MAX, ZOOM_STEP) == (0, 0, 1920, 1080)


def test_zoom_over_zoom_max_is_clamped_to_zoom_max():
    # 範囲外の倍率（10）。上限の 4 倍まで
    assert crop_rect(1920, 1080, 10, ZOOM_MAX, ZOOM_STEP) == (720, 405, 480, 270)
    assert crop_rect(1920, 1080, 4.0, ZOOM_MAX, ZOOM_STEP) == (720, 405, 480, 270)


def test_zoom_is_rounded_to_a_multiple_of_zoom_step():
    capture_width = 1920
    for asked, expected in [
        (1.0, 1.0),
        (1.2, 1.0),
        (1.3, 1.5),
        (2.3, 2.5),
        (2.7, 2.5),
        (2.8, 3.0),
        (3.9, 4.0),
    ]:
        _, _, w, _ = crop_rect(capture_width, 1080, asked, ZOOM_MAX, ZOOM_STEP)
        assert _level(w, capture_width) == pytest.approx(expected, abs=0.01), asked


def test_zoom_step_of_one_keeps_whole_numbers():
    # zoom_step が 1 なら整数倍率だけを使う
    _, _, w, h = crop_rect(1920, 1080, 2.4, ZOOM_MAX, 1.0)
    assert (w, h) == (960, 540)


def test_zoom_max_that_is_not_a_multiple_of_zoom_step():
    # 上限が刻みの倍数でないとき、刻みにそろえた結果が上限を超えても上限で止める
    # （3.4 を 2.0 の倍数にそろえると 4.0 になる。4 倍にはしない）
    _, _, w, h = crop_rect(1920, 1080, 3.4, 3.4, 2.0)
    assert (w, h) == (round(1920 / 3.4), round(1920 / 3.4 * 1080 / 1920))


def test_zoom_one_returns_the_whole_capture():
    assert crop_rect(1920, 1080, 1.0, ZOOM_MAX, ZOOM_STEP) == (0, 0, 1920, 1080)


@pytest.mark.parametrize(
    "args",
    [
        (0, 1080, 1.0, ZOOM_MAX, ZOOM_STEP),
        (1920, 0, 1.0, ZOOM_MAX, ZOOM_STEP),
        (1920, 1080, 1.0, 0.5, ZOOM_STEP),
        (1920, 1080, 1.0, ZOOM_MAX, 0.0),
    ],
)
def test_bad_arguments_are_rejected(args):
    with pytest.raises(ValueError):
        crop_rect(*args)
