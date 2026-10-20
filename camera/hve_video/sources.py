"""取り込み元（DetailedDesign.md §4.3・§4.4）。偽の画像列と実物の UnitV2 のカメラ。"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

import cv2
import numpy as np

# 偽の画像列の絵（大きさは取り込みの短辺に対する比で決める）
MARK_SIDE_DIVISOR = 8  # 中央の目印（白い正方形）の 1 辺は短辺の 1/8
MARK_VALUE = 255  # 目印の色。白として検出する
BACKGROUND_VALUES = (24, 124)  # 市松模様の背景。目印と取り違えないよう MARK_VALUE より暗い
MOVING_BAR_VALUE = 180  # フレームごとに動く帯
MOVING_BAR_WIDTH = 8
MOVING_BAR_STEP_PX = 8

SOI = b"\xff\xd8"  # JPEG の始まり
EOI = b"\xff\xd9"  # JPEG の終わり
# 区切れないまま溜まったバイトの上限（1 枚 約 160 KB。これを超えたら壊れた流れとみて捨てる）
MAX_PENDING_BYTES = 4 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
# 子プロセスが死んだとき、起動し直すまでの待ち
RESPAWN_WAIT_S = 1.0
# 新しい 1 枚を待つ上限（これを超えたら `read()` は `None`）
READ_TIMEOUT_S = 1.0

log = logging.getLogger(__name__)


def split_mjpeg(buf: bytes) -> tuple[list[bytes], bytes]:
    """MJPEG の流れを `FFD8`〜`FFD9` で 1 枚ずつに区切る。

    `(区切れた JPEG のリスト, 残りのバイト列)` を返す。残りは次の読み取りの頭に足して再び渡す。
    **`FFD8` より前のバイト（`v4l2-ctl` が標準出力の頭に出す文字など）は捨てる。**
    終わり（`FFD9`）の前に次の `FFD8` が来た 1 枚は途中で切れているので捨てる。
    """
    frames: list[bytes] = []
    pos = 0
    while True:
        start = buf.find(SOI, pos)
        if start < 0:
            # 次の読み取りの頭が `D8` なら `FFD8` になるので、末尾の `FF` だけ残す
            return frames, (b"\xff" if buf.endswith(b"\xff") else b"")
        end = buf.find(EOI, start + 2)
        next_start = buf.find(SOI, start + 2)
        if next_start >= 0 and (end < 0 or next_start < end):
            pos = next_start  # 途中で切れた 1 枚。捨てて次の始まりへ
            continue
        if end < 0:
            return frames, buf[start:]  # まだ終わりが来ていない
        frames.append(buf[start : end + 2])
        pos = end + 2


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
        self._last: np.ndarray | None = None
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
        self._last = self._render()
        self._index += 1
        return self._last

    def _render(self) -> np.ndarray:
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
        return frame

    def latest_jpeg(self) -> bytes | None:
        """最後に作った絵をその場で JPEG にして返す（実物の「カメラの JPEG」の代わり）。"""
        frame = self._last if self._last is not None else self._render()
        ok, buffer = cv2.imencode(".jpg", frame)
        return buffer.tobytes() if ok else None

    def release(self) -> None:
        self._released = True


class MjpegPipeSource:
    """実物のカメラ。`v4l2-ctl` の子プロセスの標準出力から MJPEG を 1 枚ずつ区切って受ける。

    **`ffmpeg`・OpenCV の `VideoCapture` は使わない**（OOM・MJPEG が選べない。
    DetailedDesign-hardware.md §2.1.1）。区切りは専用のスレッドで行い、**最新の 1 枚だけ**を持つ。
    作り直し（`read()` を呼ぶ側）は別スレッドで、追いつかない分の古い 1 枚は飛ばす。
    """

    def __init__(
        self,
        capture_width: int,
        capture_height: int,
        capture_fps: int = 10,
        device: str = "/dev/video0",
        *,
        spawn=subprocess.Popen,
        read_timeout: float = READ_TIMEOUT_S,
        respawn_wait: float = RESPAWN_WAIT_S,
    ):
        self._command = [
            "v4l2-ctl",
            "-d",
            str(device),
            f"--set-fmt-video=width={int(capture_width)},height={int(capture_height)},pixelformat=MJPG",
            f"--set-parm={int(capture_fps)}",
            "--stream-mmap=4",
            "--stream-to=-",
        ]
        self._spawn = spawn
        self._read_timeout = float(read_timeout)
        self._respawn_wait = float(respawn_wait)
        self._cond = threading.Condition()
        self._latest: bytes | None = None  # 最後に区切れた 1 枚（カメラの JPEG のまま）
        self._sequence = 0  # 区切れた枚数
        self._taken = 0  # `read()` が取った枚数の印
        self._closed = False
        self._proc = None
        self._reader = threading.Thread(target=self._run, name="mjpeg-split", daemon=True)
        self._reader.start()

    @property
    def command(self) -> list[str]:
        return list(self._command)

    def _run(self) -> None:
        """子プロセスを動かし、標準出力を区切って最新の 1 枚を更新する。死んだら起動し直す。"""
        while not self._closed:
            try:
                self._proc = self._spawn(
                    self._command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                )
            except OSError as error:
                log.error("v4l2-ctl を起動できない: %s", error)
                self._wait_before_respawn()
                continue
            pending = b""
            stdout = self._proc.stdout
            while not self._closed:
                chunk = stdout.read1(READ_CHUNK_BYTES)
                if not chunk:
                    break  # 子プロセスが終わった
                frames, pending = split_mjpeg(pending + chunk)
                if len(pending) > MAX_PENDING_BYTES:
                    pending = b""
                if frames:
                    with self._cond:
                        self._latest = frames[-1]
                        self._sequence += len(frames)
                        self._cond.notify_all()
            self._stop_child()
            if not self._closed:
                log.warning("v4l2-ctl が終わった。起動し直す")
                self._wait_before_respawn()

    def _wait_before_respawn(self) -> None:
        deadline = time.monotonic() + self._respawn_wait
        while not self._closed and time.monotonic() < deadline:
            time.sleep(0.05)

    def _stop_child(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:  # 止まらなければ殺す
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.stdout.close()
        except Exception:
            pass

    def latest_jpeg(self) -> bytes | None:
        """最後に受けたカメラの JPEG をそのまま返す。まだ 1 枚も無ければ `None`。"""
        with self._cond:
            return self._latest

    def read(self) -> np.ndarray | None:
        """**最新の** 1 枚を展開して返す。新しい 1 枚が来るまで待ち、上限を超えたら `None`。

        展開できない 1 枚は捨てて次を待つ。取った印より前の 1 枚は二度と返さない。
        """
        deadline = time.monotonic() + self._read_timeout
        while True:
            with self._cond:
                while self._sequence == self._taken and not self._closed:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        return None
                    self._cond.wait(remaining)
                if self._closed:
                    return None
                jpeg = self._latest
                self._taken = self._sequence
            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                return frame
            log.warning("展開できない 1 枚を捨てた（%d バイト）", len(jpeg))

    def release(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._stop_child()
        self._reader.join(timeout=3.0)


def open_source(
    fake: bool,
    capture_width: int,
    capture_height: int,
    capture_fps: int = 10,
    device: str = "/dev/video0",
) -> FakeSource | MjpegPipeSource:
    """`fake` なら偽の画像列、そうでなければ実物のカメラ（`v4l2-ctl` の子プロセス）を作る。"""
    if fake:
        return FakeSource(capture_width, capture_height)
    return MjpegPipeSource(capture_width, capture_height, capture_fps, device)
