#!/usr/bin/env python3
"""`web/` を gzip して `src/web_assets.h` を作る（DetailedDesign-names.md §1）。

`platformio.ini` の `extra_scripts`（`pre:`）からも、単体（`python3
scripts/embed_web.py`）でも動く。`src/web_assets.h` は生成物（`.gitignore`）。
中身が変わらなければ書き直さない（毎回ビルドが汚れないように）。

ファイルを足したら、ここの `FILES` と `src/main.cpp` の配信の表の両方に足す。
"""

from __future__ import annotations

import gzip
import io
import pathlib

#: (web/ のファイル名, ヘッダの配列名, Content-Type)
FILES: tuple[tuple[str, str, str], ...] = (
    ("index.html", "WEB_INDEX_HTML_GZ", "text/html"),
    ("app.js", "WEB_APP_JS_GZ", "application/javascript"),
    ("style.css", "WEB_STYLE_CSS_GZ", "text/css"),
)


def compress_file(path: pathlib.Path) -> bytes:
    """gzip 1 枚分。`mtime=0`・ファイル名なしで決定的にする。"""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(path.read_bytes())
    return buf.getvalue()


def render_header(blobs: list[tuple[str, bytes]]) -> str:
    lines = [
        "// 生成物。直に編集しない（scripts/embed_web.py が web/ から作る）。",
        "#pragma once",
        "",
        "#include <cstddef>",
        "#include <cstdint>",
        "",
    ]
    for symbol, blob in blobs:
        lines.append(f"static const uint8_t {symbol}[] PROGMEM = {{");
        for i in range(0, len(blob), 12):
            chunk = ", ".join(str(b) for b in blob[i : i + 12])
            lines.append(f"  {chunk},")
        lines.append("};")
        lines.append(f"static const size_t {symbol}_LEN = {len(blob)};")
        lines.append("")
    return "\n".join(lines)


def generate(web_dir: pathlib.Path, out_path: pathlib.Path) -> bool:
    """ヘッダを作る。中身が同じなら書かず `False` を返す。"""
    blobs = [(symbol, compress_file(web_dir / name)) for name, symbol, _ in FILES]
    text = render_header(blobs)
    if out_path.is_file() and out_path.read_text(encoding="utf-8") == text:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return True


def default_paths() -> tuple[pathlib.Path, pathlib.Path]:
    base = pathlib.Path(__file__).resolve().parent
    return base.parent / "web", base.parent / "src" / "web_assets.h"


try:
    Import("env")  # noqa: F821 - SCons（extra_scripts）のときだけある
except NameError:
    if __name__ == "__main__":
        changed = generate(*default_paths())
        print("web_assets.h " + ("updated" if changed else "up-to-date"))
else:
    # SCons は exec で読むので __file__ が無い。プロジェクトの場所で決める。
    _project = pathlib.Path(env["PROJECT_DIR"])
    generate(_project / "web", _project / "src" / "web_assets.h")
