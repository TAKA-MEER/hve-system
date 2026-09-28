"""`camera/tests` 共有の試験道具。"""

from __future__ import annotations

import numpy as np
import pytest

# 偽の画像列の中央の目印（`hve_video.sources` の白い正方形）を検出するしきい値。
# JPEG の縁の歪みに強いように、白の芯だけを測る。
MARK_THRESHOLD = 250


def _longest_run(line: np.ndarray, threshold: int) -> tuple[int, int]:
    """しきい値以上の画素が連続している最長の区間を `(開始, 長さ)` で返す。"""
    mask = line >= threshold
    best_start, best_len, start = 0, 0, None
    for index, on in enumerate(mask):
        if on:
            if start is None:
                start = index
        elif start is not None:
            if index - start > best_len:
                best_start, best_len = start, index - start
            start = None
    if start is not None and len(mask) - start > best_len:
        best_start, best_len = start, len(mask) - start
    return best_start, best_len


@pytest.fixture
def mark():
    """偽の画像列の中央の目印を測る関数。`(幅, 高さ, 中心 x, 中心 y)` を返す。

    中央の行と列で白が連続している長さを測る（枠の縁のぶれに強く出るので、
    目印そのものの大きさを同じ方法で比べられる）。
    """

    def measure(frame: np.ndarray) -> tuple[int, int, float, float]:
        height, width = frame.shape[:2]
        row_start, row_len = _longest_run(frame[height // 2, :, 0], MARK_THRESHOLD)
        col_start, col_len = _longest_run(frame[:, width // 2, 0], MARK_THRESHOLD)
        assert row_len > 0 and col_len > 0, "中央の目印が見つからない"
        return row_len, col_len, row_start + row_len / 2, col_start + col_len / 2

    return measure
