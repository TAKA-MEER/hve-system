"""UNO（optiboot）へ UART だけで書き込む（STK500v1）。UnitV2 の上で動かす（WP-CAM-09）。

    sudo python3 uno_flash.py /dev/ttyS1 firmware.hex [待つ秒数]

UnitV2 から UNO の RESET には線が無いので、リセットボタンは人が押す。起動すると UNO が出す
アプリの行（約 70 ms ごと）を見ながら、押されるのを待つ。

optiboot は**リセット直後の最初のバイトが形に合わないとすぐアプリへ戻る**。同期（`0x30 0x20`）を
送り続けると、リセットの途中のバイトが混ざって失敗する。そこで、**アプリの出力が `GAP_S` 途切れたら
リセットとみて、1 組だけ送り、返事を `REPLY_WAIT_S` 待つ**ことをくり返す。

結果は終了コードで返す（`EXIT_*`）。標準ライブラリと pyserial だけで動く（Python 3.8）。
試験からは `flash()` に偽のシリアル（`read`・`write`・`timeout`・`reset_input_buffer`）と
偽の時計（`now`）を渡す。
"""

import sys
import time

PAGE = 128
SIGNATURE = b"\x1e\x95\x0f"  # ATmega328P
BAUD = 115200

GAP_S = 0.15  # アプリの出力がこれだけ途切れたらリセットとみる
REPLY_WAIT_S = 0.6  # 同期を 1 組送ってから返事を待つ時間
POLL_S = 0.02  # 待っているあいだの 1 回の読みの長さ
CMD_WAIT_S = 0.5
PAGE_WAIT_S = 1.0
DEFAULT_WAIT_S = 30.0

INSYNC = b"\x14"
OK = b"\x10"
SYNC_CMD = b"\x30\x20"
REPLY_OK = INSYNC + OK

EXIT_OK = 0
EXIT_ARGS = 1  # 引数・hex・ポートの不備
EXIT_SYNC = 2  # 待つ秒数のうちに同期できなかった
EXIT_SIGNATURE = 3  # ATmega328P ではない
EXIT_VERIFY = 4  # 読み戻しが書いたものと違う
EXIT_PROTOCOL = 5  # 途中の応答が異常（INSYNC・OK が無い）


class ProtocolError(IOError):
    pass


def parse_hex(path):
    """Intel HEX を読み、先頭（アドレス 0）から最後のデータまでのバイト列にする。穴は 0xFF。"""
    mem = {}
    base = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line.startswith(":"):
                continue
            b = bytes.fromhex(line[1:])
            n, addr, typ = b[0], (b[1] << 8) | b[2], b[3]
            data = b[4 : 4 + n]
            if (sum(b) & 0xFF) != 0:
                raise ValueError("チェックサムが合わない: " + line)
            if typ == 0:
                for i, v in enumerate(data):
                    mem[base + addr + i] = v
            elif typ == 2:
                base = ((data[0] << 8) | data[1]) << 4
            elif typ == 4:
                base = ((data[0] << 8) | data[1]) << 16
            elif typ == 1:
                break
    if not mem:
        raise ValueError("hex にデータが無い")
    img = bytearray(b"\xff" * (max(mem) + 1))
    for a, v in mem.items():
        img[a] = v
    return bytes(img)


def command(ser, payload, nread=0, timeout=CMD_WAIT_S):
    """1 コマンドを送り、`INSYNC` ＋ データ（nread バイト）＋ `OK` を確かめてデータを返す。"""
    ser.write(payload)
    ser.timeout = timeout
    head = ser.read(1)
    if head != INSYNC:
        raise ProtocolError("INSYNC が無い: %r" % head)
    data = ser.read(nread) if nread else b""
    if len(data) != nread:
        raise ProtocolError("データが足りない: %d/%d バイト" % (len(data), nread))
    tail = ser.read(1)
    if tail != OK:
        raise ProtocolError("OK が無い: %r" % tail)
    return data


def sync(ser, wait_s, now=time.time):
    """アプリの出力の途切れ（リセット）を待って同期する。できたら真。"""
    end = now() + wait_s
    last = now()
    ser.timeout = POLL_S
    while now() < end:
        if ser.read(256):
            last = now()
            continue
        if now() - last < GAP_S:
            continue
        ser.write(SYNC_CMD)
        ser.timeout = REPLY_WAIT_S
        got = ser.read(2)
        ser.timeout = POLL_S
        last = now()  # 来なかったときも、途切れを待つところからやり直す
        if got == REPLY_OK:
            command(ser, SYNC_CMD)  # 2 回目の同期で、bootloader に入ったことを確かめる
            return True
    return False


def _set_address(ser, offset):
    w = offset // 2  # 語アドレス
    command(ser, bytes([0x55, w & 0xFF, w >> 8, 0x20]))


def _page_at(img, offset):
    return img[offset : offset + PAGE].ljust(PAGE, b"\xff")


def flash(ser, img, wait_s=DEFAULT_WAIT_S, now=time.time):
    """同期 → シグネチャ → 書き込み → 読み戻し → アプリ起動。終了コードを返す。"""
    try:
        print("同期を待つ（%d 秒）。UNO のリセットを押す" % wait_s, flush=True)
        if not sync(ser, wait_s, now):
            print("同期できなかった", flush=True)
            return EXIT_SYNC
        sig = command(ser, b"\x75\x20", 3)
        print("シグネチャ %s" % sig.hex(), flush=True)
        if sig != SIGNATURE:
            print("ATmega328P ではない", flush=True)
            return EXIT_SIGNATURE
        for off in range(0, len(img), PAGE):
            _set_address(ser, off)
            command(ser, bytes([0x64, 0, PAGE, 0x46]) + _page_at(img, off) + b"\x20", timeout=PAGE_WAIT_S)
        print("書き込んだ。読み戻す", flush=True)
        for off in range(0, len(img), PAGE):
            _set_address(ser, off)
            got = command(ser, bytes([0x74, 0, PAGE, 0x46, 0x20]), PAGE, timeout=PAGE_WAIT_S)
            if got != _page_at(img, off):
                print("読み戻しが違う（0x%04x）" % off, flush=True)
                return EXIT_VERIFY
        command(ser, b"\x51\x20")
    except ProtocolError as exc:
        print("応答が異常: %s" % exc, flush=True)
        return EXIT_PROTOCOL
    print("一致した。アプリを起動した", flush=True)
    return EXIT_OK


def main(argv):
    if len(argv) < 3:
        sys.stderr.write("usage: uno_flash.py <port> <firmware.hex> [待つ秒数]\n")
        return EXIT_ARGS
    port, hex_path = argv[1], argv[2]
    try:
        wait_s = float(argv[3]) if len(argv) > 3 else DEFAULT_WAIT_S
        img = parse_hex(hex_path)
        import serial

        ser = serial.Serial(port, BAUD, timeout=POLL_S)
    except Exception as exc:  # hex・ポート・引数の不備はまとめて扱う
        print("始められない: %s" % exc, flush=True)
        return EXIT_ARGS
    print("イメージ %d バイト" % len(img), flush=True)
    try:
        return flash(ser, img, wait_s)
    finally:
        ser.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
