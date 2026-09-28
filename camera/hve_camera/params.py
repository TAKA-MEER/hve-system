"""パラメータの読み込み。`camera/config/params.toml` を読む。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # Python 3.11 以降
    import tomllib
except ModuleNotFoundError:  # ホストの Python 3.10 には無い
    import tomli as tomllib


def load_params(path: str | Path | None = None) -> dict[str, Any]:
    """`camera/config/params.toml` を読んで dict で返す。`path` を渡したらそのファイルを読む。"""
    target = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parent.parent / "config" / "params.toml"
    )
    with target.open("rb") as fp:
        return tomllib.load(fp)
