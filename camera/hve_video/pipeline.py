"""取り込み → 切り出し → 出力の大きさへ縮小 → JPEG（DetailedDesign.md §4.3）。"""

from __future__ import annotations

import cv2
import numpy as np

from hve_video.crop import crop_rect
from hve_video.sources import FakeSource, V4L2Source


class VideoPipeline:
    """倍率に応じて取り込みの中央を切り出し、配信の大きさへ縮めて JPEG にする。

    **出力の大きさは倍率によらず一定**（spec Spec-ui.md §1.5「拡大しても送る映像の大きさは
    変わらない」）。帯域を倍率に依存させないため。変わるのは切り出す範囲だけ。
    """

    def __init__(
        self,
        source: FakeSource | V4L2Source,
        *,
        zoom: float,
        zoom_max: float,
        zoom_step: float,
        out_height: int,
        jpeg_quality: int,
    ):
        self._source = source
        self._zoom_max = float(zoom_max)
        self._zoom_step = float(zoom_step)
        self._out_height = int(out_height)
        self._jpeg_quality = int(jpeg_quality)
        self._capture_size: tuple[int, int] | None = None
        # 今の倍率。丸めは crop_rect が行う（hve_camera 側と同じ規則）
        self.level = float(zoom)

    @property
    def capture_size(self) -> tuple[int, int] | None:
        """取り込んだ 1 フレームの大きさ `(幅, 高さ)`。まだ取り込んでいなければ `None`。"""
        return self._capture_size

    def output_size(self) -> tuple[int, int]:
        """出力の大きさ `(幅, 高さ)`。**取り込みの縦横比から決めるので倍率によらない**。"""
        if self._capture_size is None:
            raise RuntimeError("まだ 1 フレームも取り込んでいない")
        width, height = self._capture_size
        return max(1, round(self._out_height * width / height)), self._out_height

    def next_frame(self) -> np.ndarray | None:
        """次の 1 フレームを切り出して縮め、BGR の ndarray で返す。読めなければ `None`。"""
        frame = self._source.read()
        if frame is None:
            return None
        self._capture_size = int(frame.shape[1]), int(frame.shape[0])
        x, y, width, height = crop_rect(
            *self._capture_size, self.level, self._zoom_max, self._zoom_step
        )
        cropped = frame[y : y + height, x : x + width]
        out_width, out_height = self.output_size()
        if (width, height) == (out_width, out_height):
            return cropped
        # 縮めるときは領域平均で、拡大するときは線形補間で、抜けのない画にする
        shrinking = width * height > out_width * out_height
        return cv2.resize(
            cropped,
            (out_width, out_height),
            interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR,
        )

    def jpeg_bytes(self) -> bytes | None:
        """次の 1 フレームを JPEG にして返す。取り込めなかったときは `None`。"""
        frame = self.next_frame()
        if frame is None:
            return None
        ok, buffer = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
        )
        if not ok:
            return None
        return buffer.tobytes()

    def release(self) -> None:
        """取り込み元を解放する。"""
        self._source.release()
