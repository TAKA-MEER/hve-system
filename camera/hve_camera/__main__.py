"""`python3 -m hve_camera` の入口。

[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §4.3 の「偽物のモード」。

    python3 -m hve_camera --fake --port 18000

- `--fake`: 偽のハードウェア（`hw/fake_hw.py`）と偽の昇降部（`hw/fake_lift.py`）で起動する。
  画面に `fake` が付く（偽物だと分かるようにする）
- `--port`: 待ち受けるポート。**既定は 80**（ラズパイでの本番）

`--fake` を付けない本番では `hw/rpi_hw.py`（SG90・28BYJ-48・SRF02）と
実の昇降部（`lift_link.py`）を使う。**ラズパイ以外で実行したら exit 2** で
「ラズパイ 4 + Raspberry Pi OS で動かすこと」と「ラズパイ以外なら `--fake`」
を案内する（ラズパイ以外で本物の GPIO を開いてもエラーになるだけなので）。


時計は `time.monotonic()` の ms。単調増加なので、NTP で時刻が飛んでも古さの判定が壊れない。
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from aiohttp import web

from hve_camera.app import VideoZoom, create_app
from hve_camera.params import load_params
from hve_camera.settings import load_settings

#: 画面の静的ファイル（`WP-UI-01` で入る。無くても起動できる）
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

log = logging.getLogger(__name__)


def monotonic_ms() -> float:
    """単調増加の時計 [ms]。**天井の古さや `HOLD_TIMEOUT` の判定に使う。**"""
    return time.monotonic() * 1000.0


def main(argv: list[str] | None = None) -> int:
    """`--fake` と `--port` を読んで aiohttp のアプリを立ち上げる。"""
    parser = argparse.ArgumentParser(
        prog="hve_camera",
        description="カメラ部の制御アプリ（昇降部・ピッチ・ヨー・天井の判定）",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="偽のハードウェアと偽の昇降部で起動する（機器ゼロで画面と判定を確かめる）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=80,
        help="待ち受けるポート（既定 80。ラズパイでの本番）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    params = load_params()
    clock = monotonic_ms

    if args.fake:
        from hve_camera.hw.fake_hw import FakeHardware
        from hve_camera.hw.fake_lift import FakeLift

        hw = FakeHardware(clock)
        lift = FakeLift(clock)
        log.warning("偽物のモードで起動する（実機の結果と取り違えないこと）")
    else:
        from hve_camera.hw.rpi_hw import RpiHardware, is_raspberry_pi
        from hve_camera.lift_link import LiftLink

        if not is_raspberry_pi():
            # ラズパイ以外で本物の GPIO を開くと EROFS や初期化失敗になる。
            # 例外を握りつぶさず exit 2 にして、
            # 「ラズパイだと取り違えて別の機械で動かす」状態をそのまま出さない。
            log.error(
                "ラズパイ以外で実物のハードウェアを開こうとした。"
                "ラズパイ 4 + Raspberry Pi OS で動かすこと。"
                "ラズパイ以外で画面だけ確かめるときは --fake を付ける"
            )
            return 2

        try:
            hw = RpiHardware(params, clock)
        except Exception:
            # 配線の抜けや i2c の権限不足を握りつぶさない（握りつぶすと
            # 「動いているように見えるが、実際には動かない」状態になる）。
            log.exception("ラズパイのハードウェアを開けなかった")
            return 2

        lift = LiftLink(params["lift_ws_url"], clock)
        log.info("実物のモードで起動する（昇降部 %s）", params["lift_ws_url"])

    settings, using_defaults = load_settings(params["settings_path"], params)
    app = create_app(
        hw,
        lift,
        settings=settings,
        using_defaults=using_defaults,
        params=params,
        clock=clock,
        video_zoom=VideoZoom(params["video_port"]),
        fake=args.fake,
        settings_path=params["settings_path"],
        web_dir=WEB_DIR,
    )

    log.info("http://0.0.0.0:%d/ で待ち受ける（昇降部 %s）", args.port, params["lift_ws_url"])
    web.run_app(app, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
