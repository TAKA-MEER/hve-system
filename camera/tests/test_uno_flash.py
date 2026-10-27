"""UNO を UnitV2 経由で書き込む道具（`camera/deploy/uno_flash.py`・`flash_uno.sh`）の試験。WP-CAM-09。

optiboot を真似た偽のシリアルと偽の時計で、同期・書き込み・読み戻しを縛る。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

CAMERA = Path(__file__).resolve().parents[1]
DEPLOY = CAMERA / "deploy"
sys.path.insert(0, str(DEPLOY))

import uno_flash  # noqa: E402

LINE_PERIOD = 0.07
RESET_HOLD = 0.2  # ボタンを押している間は何も受けない・出さない
BOOT_WINDOW = 1.0  # リセットを離してから optiboot が待つ時間
APP_LINE = b"C 12 0 34\n"


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t


class FakeOptiboot:
    """pyserial の `Serial` の読み書きだけを真似た、optiboot つきの UNO。時間は偽の時計だけで進む。"""

    def __init__(self, clock, reset_at=1.0, signature=b"\x1e\x95\x0f", corrupt_page=None):
        self.clock = clock
        self.timeout = 0.0
        self.reset_at = reset_at
        self.signature = signature
        self.corrupt_page = corrupt_page  # 書くとき、このページ番号だけ壊す
        self.mode = "app"  # app → (silence) → boot_first → boot → app
        self.next_line = LINE_PERIOD
        self.boot_started = None
        self.out = []  # (ts, byte)
        self.inbuf = b""
        self.addr = 0
        self.flash = {}  # ページ先頭のバイトアドレス → 128 バイト
        self.app_rx = []  # アプリが動いているときに受けたバイト列（時刻つき）
        self.write_times = []
        self.started_app = False

    # --- 時間を進める ---
    def _advance_to(self, t):
        if self.mode == "app":
            limit = t
            if self.next_line <= self.reset_at and not self.started_app and self.reset_at is not None:
                limit = min(t, self.reset_at)
            while self.next_line <= limit:
                if self.started_app or self.next_line < self.reset_at:
                    self.out.append((self.next_line, APP_LINE))
                self.next_line += LINE_PERIOD
            if not self.started_app and t >= self.reset_at:
                self.mode = "silence"
                self.next_line = None
        if self.mode == "silence" and t >= self.reset_at + RESET_HOLD:
            self.mode = "boot_first"
            self.boot_started = self.reset_at + RESET_HOLD
        if self.mode == "boot_first" and t >= self.boot_started + BOOT_WINDOW and not self.inbuf:
            self._to_app(self.boot_started + BOOT_WINDOW)
            self._advance_to(t)

    def _to_app(self, at):
        self.mode = "app"
        self.started_app = True
        self.reset_at = float("inf")
        self.next_line = at + LINE_PERIOD

    # --- pyserial の面 ---
    def reset_input_buffer(self):
        self.out = [o for o in self.out if o[0] > self.clock.t]

    def read(self, n=1):
        deadline = self.clock.t + self.timeout
        self._advance_to(deadline)
        ready = [i for i, (ts, _) in enumerate(self.out) if ts <= deadline]
        got = b""
        stop = deadline
        used = 0
        for i in ready:
            got += self.out[i][1]
            used = i + 1
            if len(got) >= n:
                stop = max(self.clock.t, self.out[i][0])
                break
        # 行は 1 つずつ。n バイトに満たない読みは、切り分けて残りを返す
        if len(got) > n:
            extra = got[n:]
            got = got[:n]
            ts = self.out[used - 1][0]
            self.out[:used] = [(ts, extra)]
        else:
            del self.out[:used]
        self.clock.t = stop
        return got

    def write(self, data):
        t = self.clock.t
        self.write_times.append(t)
        self._advance_to(t)
        if self.mode == "app":
            self.app_rx.append((t, bytes(data)))
            return len(data)
        if self.mode == "silence":
            return len(data)  # リセット中で受けない
        self.inbuf += bytes(data)
        self._parse(t)
        return len(data)

    # --- optiboot ---
    def _reply(self, t, payload):
        self.out.append((t, payload))

    def _parse(self, t):
        if self.mode == "boot_first":
            if len(self.inbuf) < 2:
                return
            if self.inbuf[:2] != b"\x30\x20":
                self.inbuf = b""
                self._to_app(t)
                return
            self.mode = "boot"
        while self.inbuf:
            b = self.inbuf
            if b[:2] == b"\x30\x20":
                self.inbuf = b[2:]
                self._reply(t, b"\x14\x10")
            elif b[:2] == b"\x75\x20":
                self.inbuf = b[2:]
                self._reply(t, b"\x14" + self.signature + b"\x10")
            elif b[0] == 0x55 and len(b) >= 4:
                self.addr = (b[1] | (b[2] << 8)) * 2
                self.inbuf = b[4:]
                self._reply(t, b"\x14\x10")
            elif b[0] == 0x64 and len(b) >= 133:
                page = bytes(b[4:132])
                if self.corrupt_page is not None and self.addr == self.corrupt_page * uno_flash.PAGE:
                    page = bytes([page[0] ^ 0xFF]) + page[1:]
                self.flash[self.addr] = page
                self.inbuf = b[133:]
                self._reply(t, b"\x14\x10")
            elif b[0] == 0x74 and len(b) >= 5:
                page = self.flash.get(self.addr, b"\xff" * uno_flash.PAGE)
                self.inbuf = b[5:]
                self._reply(t, b"\x14" + page + b"\x10")
            elif b[:2] == b"\x51\x20":
                self.inbuf = b[2:]
                self._reply(t, b"\x14\x10")
                self._to_app(t)
                return
            else:
                return  # 続きを待つ


def make_hex(path, data, start=0):
    """データを 16 バイトずつの Intel HEX にして書く。"""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i : i + 16]
        rec = bytes([len(chunk), ((start + i) >> 8) & 0xFF, (start + i) & 0xFF, 0]) + chunk
        lines.append(":" + (rec + bytes([(-sum(rec)) & 0xFF])).hex().upper())
    lines.append(":00000001FF")
    Path(path).write_text("\n".join(lines) + "\n")


@pytest.fixture
def image():
    # 128 バイトのページ 2 枚と半端（300 バイト）。ページごとに中身が違うようにする
    return bytes((i * 7 + 3) & 0xFF for i in range(300))


def run_flash(image, wait_s=30.0, **kw):
    clock = FakeClock()
    ser = FakeOptiboot(clock, **kw)
    code = uno_flash.flash(ser, image, wait_s, clock.now)
    return code, ser, clock


def test_flashes_after_reset_and_verifies(image):
    code, ser, _ = run_flash(image)
    assert code == uno_flash.EXIT_OK
    assert ser.started_app  # 最後に 0x51 0x20 でアプリを起動した
    for off in range(0, len(image), uno_flash.PAGE):
        assert ser.flash[off] == image[off : off + uno_flash.PAGE].ljust(uno_flash.PAGE, b"\xff")


def test_never_writes_while_app_is_running(image):
    """リセットの前（アプリが行を出しているあいだ）に何も送らない。送ると UNO のアプリに食われる。"""
    code, ser, _ = run_flash(image, reset_at=2.0)
    assert code == uno_flash.EXIT_OK
    assert ser.app_rx == []
    assert min(ser.write_times) >= 2.0  # 最後の行から GAP_S 後、つまりリセットのあと


def test_sync_survives_a_dropped_first_pair(image):
    """最初の 1 組がリセット中（受けない）に当たっても、途切れを待ち直して書ける。"""
    code, ser, _ = run_flash(image, reset_at=1.0)
    assert code == uno_flash.EXIT_OK
    first = ser.write_times[0]
    assert 1.0 <= first < 1.0 + RESET_HOLD  # 最初の 1 組はリセット中で捨てられた
    assert ser.write_times[1] - first >= uno_flash.REPLY_WAIT_S  # 返事を待ってから、途切れを待ち直した


def test_sync_times_out_without_reset(image):
    code, ser, clock = run_flash(image, reset_at=10_000.0, wait_s=3.0)
    assert code == uno_flash.EXIT_SYNC
    assert ser.write_times == []
    assert clock.t < 3.5  # 待つ秒数で諦める（偽の時計なので試験は速い）


def test_wrong_signature_is_reported_and_nothing_written(image):
    code, ser, _ = run_flash(image, signature=b"\x1e\x96\x09")
    assert code == uno_flash.EXIT_SIGNATURE
    assert ser.flash == {}


def test_verify_mismatch_is_reported(image):
    code, _, _ = run_flash(image, corrupt_page=1)
    assert code == uno_flash.EXIT_VERIFY


def test_missing_reply_mid_flash_is_protocol_error(image):
    class Silent(FakeOptiboot):
        def _parse(self, t):
            if len(self.flash) >= 1:  # 最初のページを書いたあとは返事をしない
                self.inbuf = b""
                return
            super()._parse(t)

    clock = FakeClock()
    ser = Silent(clock)
    assert uno_flash.flash(ser, image, 30.0, clock.now) == uno_flash.EXIT_PROTOCOL


def test_exit_codes_are_distinct():
    codes = [
        uno_flash.EXIT_OK,
        uno_flash.EXIT_ARGS,
        uno_flash.EXIT_SYNC,
        uno_flash.EXIT_SIGNATURE,
        uno_flash.EXIT_VERIFY,
        uno_flash.EXIT_PROTOCOL,
    ]
    assert len(set(codes)) == len(codes) and uno_flash.EXIT_OK == 0


def test_parse_hex_fills_gaps_and_handles_extended_address(tmp_path):
    p = tmp_path / "a.hex"
    # 0x0000 に 2 バイト、0x0010 に 2 バイト（間は 0xFF）。拡張リニアアドレス 0 の行も入れる
    def rec(addr, typ, data):
        r = bytes([len(data), addr >> 8, addr & 0xFF, typ]) + bytes(data)
        return ":" + (r + bytes([(-sum(r)) & 0xFF])).hex().upper()

    p.write_text("\n".join([rec(0, 4, [0, 0]), rec(0, 0, [1, 2]), rec(0x10, 0, [3, 4]), rec(0, 1, [])]) + "\n")
    img = uno_flash.parse_hex(str(p))
    assert len(img) == 0x12
    assert img[:2] == b"\x01\x02" and img[2:0x10] == b"\xff" * 14 and img[0x10:] == b"\x03\x04"


def test_parse_hex_rejects_bad_checksum(tmp_path):
    p = tmp_path / "b.hex"
    p.write_text(":0100000001FF\n:00000001FF\n")
    with pytest.raises(ValueError):
        uno_flash.parse_hex(str(p))


def test_main_reports_args_and_bad_hex(tmp_path):
    assert uno_flash.main(["uno_flash.py"]) == uno_flash.EXIT_ARGS
    bad = tmp_path / "bad.hex"
    bad.write_text("nothing\n")
    assert uno_flash.main(["uno_flash.py", "/dev/null", str(bad)]) == uno_flash.EXIT_ARGS


# --- flash_uno.sh ---


@pytest.fixture
def fake_env(tmp_path):
    """PATH の先頭に偽の sshpass を置く。受けたコマンドを記録し、python を走らせるコマンドだけ失敗させられる。"""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    (bindir / "sshpass").write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_LOG"\n'
        "cat > /dev/null\n"
        'case "$*" in *uno_flash.py\\ /dev*) exit "${FAKE_FLASH_RC:-0}";; esac\n'
        "exit 0\n"
    )
    (bindir / "sshpass").chmod(0o755)
    hexfile = tmp_path / "fw.hex"
    make_hex(hexfile, bytes(range(32)))
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]), FAKE_LOG=str(log))
    return env, log, hexfile


def run_script(env, hexfile, rc=0):
    env = dict(env, FAKE_FLASH_RC=str(rc))
    proc = subprocess.run(
        ["sh", str(DEPLOY / "flash_uno.sh"), "192.0.2.1", str(hexfile)], env=env, capture_output=True, text=True
    )
    return proc


def calls(log):
    return log.read_text().splitlines()


def index_of(lines, needle):
    return [i for i, l in enumerate(lines) if needle in l]


def test_flash_script_stops_flashes_and_restarts(fake_env):
    env, log, hexfile = fake_env
    proc = run_script(env, hexfile, rc=0)
    assert proc.returncode == 0, proc.stderr
    lines = calls(log)
    stop, run, start = index_of(lines, "S86hve stop"), index_of(lines, "uno_flash.py /dev/ttyS1"), index_of(lines, "S86hve start")
    assert stop and run and start
    assert stop[0] < run[0] < start[-1]
    assert any("rm -f /tmp/uno_flash.py" in l for l in lines)  # RAM 上の /tmp を片付ける


@pytest.mark.parametrize("rc", [2, 3, 4, 5])
def test_flash_script_restarts_app_when_flash_fails(fake_env, rc):
    """書き込みが失敗しても S86hve start で戻し、終了コードは書き込みのものを返す。"""
    env, log, hexfile = fake_env
    proc = run_script(env, hexfile, rc=rc)
    assert proc.returncode == rc
    lines = calls(log)
    run, start = index_of(lines, "uno_flash.py /dev/ttyS1"), index_of(lines, "S86hve start")
    assert run and start and start[-1] > run[0]
    assert any("rm -f /tmp/uno_flash.py" in l for l in lines)


def test_flash_script_stops_before_touching_app_if_hex_missing(fake_env, tmp_path):
    env, log, _ = fake_env
    proc = run_script(env, tmp_path / "nothing.hex")
    assert proc.returncode != 0
    assert "pio run -d firmware/cam_io -e uno" in proc.stderr
    assert not log.exists()  # UnitV2 には何もしない
