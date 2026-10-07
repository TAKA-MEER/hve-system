"""`scripts/embed_web.py` の試験。標準ライブラリだけ。

`python3 -m pytest -p no:anyio firmware/lift/scripts/test_embed_web.py`
（`-p no:anyio` はホストの pytest 6.2.5 が `anyio` プラグインで落ちる回避策）。
"""

from __future__ import annotations

import gzip
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import embed_web

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"


def parse_arrays(text: str) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}
    for match in re.finditer(r"static const uint8_t (\w+)\[\] PROGMEM = \{(.*?)\};", text, re.S):
        blobs[match.group(1)] = bytes(int(n) for n in re.findall(r"\d+", match.group(2)))
    return blobs


def test_roundtrip_back_to_web_files(tmp_path):
    out = tmp_path / "web_assets.h"
    assert embed_web.generate(WEB_DIR, out) is True
    blobs = parse_arrays(out.read_text(encoding="utf-8"))
    assert set(blobs) == {symbol for _, symbol, _ in embed_web.FILES}
    for name, symbol, _ in embed_web.FILES:
        assert gzip.decompress(blobs[symbol]) == (WEB_DIR / name).read_bytes()


def test_gzip_magic_and_declared_length(tmp_path):
    out = tmp_path / "web_assets.h"
    embed_web.generate(WEB_DIR, out)
    text = out.read_text(encoding="utf-8")
    blobs = parse_arrays(text)
    for _, symbol, _ in embed_web.FILES:
        assert blobs[symbol][:2] == b"\x1f\x8b"
        assert f"static const size_t {symbol}_LEN = {len(blobs[symbol])};" in text


def test_second_run_writes_nothing(tmp_path):
    out = tmp_path / "web_assets.h"
    assert embed_web.generate(WEB_DIR, out) is True
    mtime = out.stat().st_mtime_ns
    assert embed_web.generate(WEB_DIR, out) is False
    assert out.stat().st_mtime_ns == mtime
