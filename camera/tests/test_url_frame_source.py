"""外の URL から JPEG を 1 枚ずつ取り込む取り込み元（`UrlFrameSource`。WP-VIDEO-06）。

取得と時計は差し替えて試験する（手元に立てた HTTP サーバも使う）。
`fcntl` を使わないので Windows でも回る。
"""

from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pytest

from hve_video import sources


def jpeg_gray(level: int, size=(64, 48)) -> bytes:
    ok, buffer = cv2.imencode(".jpg", np.full((size[1], size[0], 3), level, dtype=np.uint8))
    assert ok
    return buffer.tobytes()


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class Clock:
    """動かせる単調時計（秒）。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Recorder:
    """取得関数の代わり。呼ばれた回数と、同時実行数を数える。"""

    def __init__(self, handler):
        self._handler = handler
        self._lock = threading.Lock()
        self.calls = 0
        self.timeouts = []
        self.concurrent = 0
        self.max_concurrent = 0

    def __call__(self, url, timeout):
        with self._lock:
            self.calls += 1
            self.timeouts.append(timeout)
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            return self._handler(url, timeout, self.calls)
        finally:
            with self._lock:
                self.concurrent -= 1


def make_source(fetch, **kwargs):
    kwargs.setdefault("read_timeout", 0.3)
    return sources.UrlFrameSource("http://drawing.invalid/frame", fetch=fetch, **kwargs)


# --- 間隔・重ねない ---------------------------------------------------------------


def test_does_not_issue_the_next_request_until_the_previous_one_finishes():
    """前の要求が終わる前に次を出さない（同時に 2 本以上は無い）。"""
    gate = threading.Event()
    entered = threading.Event()

    def handler(url, timeout, call):
        entered.set()
        gate.wait(timeout=5.0)
        return 200, jpeg_gray(60 + call)

    fetch = Recorder(handler)
    source = make_source(fetch, capture_fps=100, timeout_ms=5000)
    try:
        assert entered.wait(3.0)
        time.sleep(0.3)  # 間隔（0.01 秒）を過ぎても次は出ない
        assert fetch.calls == 1
        assert fetch.max_concurrent == 1
        gate.set()
        assert wait_until(lambda: fetch.calls >= 2)
        assert fetch.max_concurrent == 1, "同時に 2 本の要求が出た"
    finally:
        gate.set()
        source.release()


def test_next_request_waits_the_interval_from_the_request_time():
    """次の要求は「前の要求を出してから 1/fps 秒」と「前の応答が返った時」の遅い方。"""
    starts = []

    def handler(url, timeout, call):
        starts.append(time.monotonic())
        return 200, jpeg_gray(60 + call)

    fetch = Recorder(handler)
    source = make_source(fetch, capture_fps=5, timeout_ms=5000)  # 間隔 0.2 秒
    try:
        assert wait_until(lambda: len(starts) >= 2)
        assert starts[1] - starts[0] >= 0.19, "間隔を守らずに次を出した"
    finally:
        source.release()


# --- 1 枚の時刻 ------------------------------------------------------------------


def test_frame_time_is_the_request_time_not_the_response_time():
    clock = Clock(1000.0)
    gate = threading.Event()

    def handler(url, timeout, call):
        if call == 1:
            clock.now += 2.0  # 描画に 2 秒かかった（応答は 1002 に返る）
            return 200, jpeg_gray(90)
        gate.wait(timeout=5.0)  # 2 回目以降は止める（時刻が進まないように）
        return 200, jpeg_gray(91)

    fetch = Recorder(handler)
    source = make_source(fetch, capture_fps=10, timeout_ms=5000, clock=clock)
    try:
        stamped = source.wait_latest_jpeg_stamped(3.0)
        assert stamped is not None
        jpeg, age = stamped
        assert jpeg == jpeg_gray(90)
        assert age == 2000, "返ってきた時刻（1002）から数えた経過を返した"
        clock.now += 0.25
        assert source.latest_jpeg_stamped()[1] == 2250
    finally:
        gate.set()
        source.release()


# --- 待つ上限（遅いだけでは失敗にしない・上限超えは失敗） --------------------------


class SlowJpegServer:
    """手元に立てる HTTP サーバ。`delay` 秒待ってから JPEG を 1 枚返す。"""

    def __init__(self, delay, body):
        self.delay = delay
        self.body = body
        self.request_count = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.request_count += 1
                time.sleep(outer.delay)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                try:
                    self.wfile.write(outer.body)
                except (BrokenPipeError, ConnectionAbortedError):
                    pass  # 受け手が上限で切った（試験の上限超え）

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.url = "http://127.0.0.1:%d/frame" % self._server.server_address[1]

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()


def test_slow_but_within_the_timeout_is_not_a_failure():
    """上限より短い遅れ（例 0.3 秒）を失敗にしない。"""
    with SlowJpegServer(0.3, jpeg_gray(120)) as server:
        source = sources.UrlFrameSource(server.url, 10, 5000, read_timeout=0.3)
        try:
            assert wait_until(lambda: source.latest_jpeg() == jpeg_gray(120), timeout=3.0)
        finally:
            source.release()


def test_giving_up_after_the_timeout_keeps_the_previous_frame():
    """上限を超えても待ち続けない。超えた回は 1 枚にせず、前の 1 枚とその時刻を残す。"""
    with SlowJpegServer(0.5, jpeg_gray(200)) as server:
        source = sources.UrlFrameSource(server.url, 10, 200, read_timeout=0.3)
        try:
            time.sleep(1.0)  # 上限（0.2 秒）を 2 回分超える。応答も返っているはず
            assert source.latest_jpeg() is None, "上限を超えた回を 1 枚にした"
            assert server.request_count >= 2, "上限で切らずに待ち続けた"
        finally:
            source.release()


# --- 失敗のとき前の 1 枚を残す ----------------------------------------------------


def test_a_failed_request_keeps_the_previous_frame_and_its_time():
    """失敗した回に時刻だけ新しくしない（古さが伸びる）。"""
    clock = Clock(1000.0)

    def handler(url, timeout, call):
        if call == 1:
            clock.now += 0.1
            return 200, jpeg_gray(70)
        return 503, b""

    fetch = Recorder(handler)
    source = make_source(fetch, capture_fps=10, timeout_ms=5000, clock=clock)
    try:
        assert wait_until(lambda: source.latest_jpeg() == jpeg_gray(70))
        assert wait_until(lambda: fetch.calls >= 3)  # 失敗が続いている
        clock.now = 1001.6
        jpeg, age = source.latest_jpeg_stamped()
        assert jpeg == jpeg_gray(70)
        assert age == 1600, "失敗した回で時刻が新しくなった（古さが伸びない）"
    finally:
        source.release()


def test_non_200_and_undecodable_bodies_are_not_frames(caplog):
    """200 以外・展開できない本体を 1 枚にしない。失敗の記録は続くあいだ 1 回だけ。"""
    def handler(url, timeout, call):
        if call % 2 == 1:
            return 404, jpeg_gray(70)  # 200 以外（絵は JPEG でも 1 枚にしない）
        return 200, b"not a jpeg"

    fetch = Recorder(handler)
    source = make_source(fetch, capture_fps=20, timeout_ms=5000)
    try:
        with caplog.at_level(logging.WARNING, logger="hve_video.sources"):
            time.sleep(0.5)
        assert fetch.calls >= 3, "失敗しても次の要求を出さなかった"
        assert source.latest_jpeg() is None
        warnings = [
            record
            for record in caplog.records
            if record.name == "hve_video.sources" and record.levelno >= logging.WARNING
        ]
        assert len(warnings) == 1, "連続する失敗で記録が何度も出た"
    finally:
        source.release()


def test_a_fetch_exception_keeps_the_previous_frame():
    def handler(url, timeout, call):
        if call == 1:
            return 200, jpeg_gray(70)
        raise TimeoutError("上限を超えた")

    fetch = Recorder(handler)
    source = make_source(fetch, capture_fps=20, timeout_ms=5000)
    try:
        assert wait_until(lambda: source.latest_jpeg() == jpeg_gray(70))
        assert wait_until(lambda: fetch.calls >= 2)
        time.sleep(0.2)
        assert source.latest_jpeg() == jpeg_gray(70)
    finally:
        source.release()


# --- 省電力・解放 -----------------------------------------------------------------


def test_no_requests_while_capturing_is_off():
    """`set_capturing(False)` の間は要求を出さない。止めると最後の 1 枚を捨てる。"""
    fetch = Recorder(lambda url, timeout, call: (200, jpeg_gray(60 + call)))
    source = make_source(fetch, capture_fps=20, timeout_ms=5000)
    try:
        assert wait_until(lambda: source.latest_jpeg() is not None)
        source.set_capturing(False)
        assert not source.capturing
        assert source.latest_jpeg() is None, "古い 1 枚が残っている"
        assert source.read() is None
        calls_when_stopped = fetch.calls
        time.sleep(0.3)
        assert fetch.calls == calls_when_stopped, "取り込みを止めている間も要求を出した"
        source.set_capturing(True)
        assert wait_until(lambda: fetch.calls > calls_when_stopped)
        assert wait_until(lambda: source.latest_jpeg() is not None)
        frame = source.read()
        assert frame is not None and frame.shape == (48, 64, 3)
    finally:
        source.release()


def test_read_returns_the_latest_frame_once_and_release_stops_the_thread():
    fetch = Recorder(lambda url, timeout, call: (200, jpeg_gray(100 + call)))
    source = make_source(fetch, capture_fps=100, timeout_ms=5000)
    try:
        frame = source.read()
        assert frame is not None and frame.shape == (48, 64, 3)
        assert source.latest_jpeg() is not None
    finally:
        source.release()
    assert not source._reader.is_alive(), "解放しても読み取りスレッドが残っている"
    calls_after_release = fetch.calls
    time.sleep(0.2)
    assert fetch.calls == calls_after_release, "解放したあとにも要求を出した"
    assert source.read() is None


# --- open_source ------------------------------------------------------------------


def test_open_source_picks_the_url_source_only_with_fake(monkeypatch):
    made = []

    class Recorded:
        def __init__(self, url, fps, timeout_ms):
            made.append((url, fps, timeout_ms))

        def release(self):
            pass

    monkeypatch.setattr(sources, "UrlFrameSource", Recorded)
    source = sources.open_source(
        True, 1280, 720, 10, "/dev/video0",
        frame_url="http://drawing.invalid/frame", frame_url_timeout_ms=5000,
    )
    assert isinstance(source, Recorded)
    assert made == [("http://drawing.invalid/frame", 10, 5000)]


def test_open_source_rejects_a_frame_url_without_fake():
    """偽物でなければ外の絵を作らない（本番で取り違えない）。"""
    with pytest.raises(ValueError):
        sources.open_source(
            False, 1280, 720, 10, "/dev/video0",
            frame_url="http://drawing.invalid/frame",
        )


def test_open_source_without_a_frame_url_is_unchanged():
    source = sources.open_source(True, 320, 240)
    assert isinstance(source, sources.FakeSource)
    assert source.capture_size == (320, 240)
