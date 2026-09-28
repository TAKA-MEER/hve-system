"""取り込み元（DetailedDesign.md §4.3）。偽の画像列と実物の V4L2 カメラ。"""

from __future__ import annotations

import cv2
import numpy as np

# 偽の画像列の絵（大きさは取り込みの短辺に対する比で決める）
MARK_SIDE_DIVISOR = 8  # 中央の目印（白い正方形）の 1 辺は短辺の 1/8
MARK_VALUE = 255  # 目印の色。白として検出する
BACKGROUND_VALUES = (24, 124)  # 市松模様の背景。目印と取り違えないよう MARK_VALUE より暗い
MOVING_BAR_VALUE = 180  # フレームごとに動く帯
MOVING_BAR_WIDTH = 8
MOVING_BAR_STEP_PX = 8


class FakeSource:
    """偽の画像列（numpy で作る）。中央に白い正方形の目印があり、フレームごとに変わる。

    機器なしで取り込みから配信までを通すために使う。目印は取り込みの中央に固定なので、
    倍率を上げると目印がそのまま大きくなる（spec Spec-ui.md §1.5）。
    """

    def __init__(self, capture_width: int, capture_height: int):
        self._width = int(capture_width)
        self._height = int(capture_height)
        self._index = 0
        self._released = False
        if self._width < 1 or self._height < 1:
            raise ValueError(f"取り込みの大きさが 0 以下: {capture_width}x{capture_height}")
        rows = np.arange(self._height, dtype=np.int32).reshape(-1, 1)
        cols = np.arange(self._width, dtype=np.int32).reshape(1, -1)
        checker = (cols + rows) % 2
        self._background = BACKGROUND_VALUES[0] + (
            BACKGROUND_VALUES[1] - BACKGROUND_VALUES[0]
        ) * checker
        side = max(2, (min(self._width, self._height) // MARK_SIDE_DIVISOR) // 2 * 2)
        self._mark = (
            (self._height - side) // 2,
            (self._width - side) // 2,
            side,
        )

    @property
    def capture_size(self) -> tuple[int, int]:
        """取り込みの大きさ `(幅, 高さ)`。"""
        return self._width, self._height

    def read(self) -> np.ndarray | None:
        """次の 1 フレームを返す。`release()` したあとは `None`。"""
        if self._released:
            return None
        frame = np.empty((self._height, self._width, 3), dtype=np.uint8)
        frame[:, :, 0] = self._background
        frame[:, :, 1] = self._background // 2
        frame[:, :, 2] = self._background
        # フレームごとに位置が変わる帯（続けて読むとフレームが変わることが分かる）
        bar_x = (self._index * MOVING_BAR_STEP_PX) % max(1, self._width - MOVING_BAR_WIDTH)
        frame[:, bar_x : bar_x + MOVING_BAR_WIDTH, :] = MOVING_BAR_VALUE
        # 中央の目印
        y, x, side = self._mark
        frame[y : y + side, x : x + side, :] = MARK_VALUE
        self._index += 1
        return frame

    def release(self) -> None:
        self._released = True


class V4L2Source:
    """実物の V4L2 カメラ。OpenCV の `VideoCapture` で MJPEG と取り込みの大きさを要求する。"""

    def __init__(self, capture_width: int, capture_height: int, device: int = 0):
        self._capture = cv2.VideoCapture(device)
        if not self._capture.isOpened():
            self._capture.release()
            raise RuntimeError(f"カメラを開けません（device={device}）")
        # 4CC を先にして、そのあとで取り込みの大きさを求める
        self._capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(capture_width))
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(capture_height))

    def read(self) -> np.ndarray | None:
        """次の 1 フレームを返す。読めなかったときは `None`。"""
        ok, frame = self._capture.read()
        if not ok or frame is None:
            return None
        return frame

    def release(self) -> None:
        self._capture.release()


def open_source(fake: bool, capture_width: int, capture_height: int) -> FakeSource | V4L2Source:
    """`fake` なら偽の画像列、そうでなければ実物の V4L2 カメラを作る。"""
    if fake:
        return FakeSource(capture_width, capture_height)
    return V4L2Source(capture_width, capture_height)
