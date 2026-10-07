"""`python3 -m hve_camera` の入口。

[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §4.3 の「偽物のモード」。

    python3 -m hve_camera --fake --port 18000

- `--fake`: 偽のハードウェア（`hw/fake_hw.py`）と偽の昇降部（`hw/fake_lift.py`）で起動する。
  画面に `fake` が付く（偽物だと分かるようにする）
- `--port`: 待ち受けるポート。**既定は 80**（UnitV2 での本番）

`--fake` を付けない本番では `hw/uno_hw.py`（Arduino UNO との UART）と
実の昇降部（`lift_link.py`）を使う。UnitV2 では root で動かす。

時計は `time.monotonic()` の ms。単調増加なので、NTP で時刻が飛んでも古さの判定が壊れない。
"""

from __future__ import annotations

import argparse
import logging
import tempfile
import time
from pathlib import Path

from aiohttp import web

from hve_camera.app import VideoZoom, create_app
from hve_camera.ceiling import CeilingStatus
from hve_camera.lift_link import LIFT_WS_MODULE_PATH, LiftLink
from hve_camera.lift_resolve import resolve_lift_host
from hve_camera.params import load_params
from hve_camera.settings import OWN_AXES, load_settings

#: 画面の静的ファイル（`WP-UI-02` で入る。無くても起動できる）
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

#: **偽物のモードで起動したときの天井の読み値** [mm]。天井の読み値が無いと画面が
#: 「天井 値なし」になり、上昇ボタンが薄いまま動かせない。実測の代わりに「十分遠い」を渡す
FAKE_CEILING_MM = 2000

log = logging.getLogger(__name__)


def monotonic_ms() -> float:
    """単調増加の時計 [ms]。**天井の古さや `HOLD_TIMEOUT` の判定に使う。**"""
    return time.monotonic() * 1000.0


def module_ws_url(params: dict) -> str | None:
    """昇降部の `/ws/module` の URL。名前を引けなければ `None`（繋ぎ直しで待つ）。"""
    host = resolve_lift_host(params.get("lift_host", ""), params.get("lift_mdns_name", ""))
    if not host:
        return None
    return "ws://%s:%d%s" % (host, int(params.get("lift_port", 80)), LIFT_WS_MODULE_PATH)


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
        help="待ち受けるポート（既定 80。UnitV2 での本番）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    params = load_params()
    clock = monotonic_ms
    settings_path = params["settings_path"]

    if args.fake:
        from hve_camera.hw.fake_hw import FakeHardware
        from hve_camera.hw.fake_lift import FakeLift

        hw = FakeHardware(clock)
        # **偽物でも天井は「十分遠い MEASURED」で保つ（古くならない）。**天井の読み値が無く起動すると
        # 画面が「天井 値なし」で上昇ボタンも薄いままになる（画面を見る人が動かせない）
        hw.set_steady_ceiling(CeilingStatus.MEASURED, FAKE_CEILING_MM)
        lift = FakeLift(clock)
        # 既定の保存先（`/home/m5stack/...`）はホストに無く PUT が 500 になる。
        # 偽物のモードは起動ごとの一時ディレクトリに保存する（実機の既定は変えない）
        settings_path = str(Path(tempfile.mkdtemp(prefix="hve_camera_fake_")) / "settings.json")
        log.info("偽物の設定の保存先は %s", settings_path)
        log.warning("偽物のモードで起動する（実機の結果と取り違えないこと）")
        log.info("偽物の天井は %d mm（MEASURED）から始める。POST /api/fake の差し込みは 1 回分、止めるのは freeze", FAKE_CEILING_MM)
    else:
        from hve_camera.hw.uno_hw import UnoHardware

        try:
            hw = UnoHardware(params, clock)
        except Exception:
            # 配線の抜けや権限不足を握りつぶさない（握りつぶすと
            # 「動いているように見えるが、実際には動かない」状態になる）。
            log.exception("Arduino との UART を開けなかった")
            return 2

        lift = LiftLink(
            lambda: module_ws_url(params),
            clock,
            name=str(params.get("module_name", "hve-cam")),
            ceiling_sensor=bool(params.get("module_ceiling_sensor", True)),
        )
        log.info("実物のモードで起動する（UART %s・昇降部 %s）", params.get("io_device"), module_ws_url(params))

    # 自分の 2 軸（ピッチ・ヨー）だけを保存する。昇降の 2 軸は昇降部が持つ。
    # 制御ループの丸めには 4 軸が要るので、起動時はファイルか既定値の 4 軸で種にする
    # （昇降の設定は `GET /api/settings` で取り直す）。
    own, using_defaults = load_settings(settings_path, params, axes=OWN_AXES)
    seed, _ = load_settings(settings_path, params)
    seed.update(own)
    app = create_app(
        hw,
        lift,
        settings=seed,
        using_defaults=using_defaults,
        params=params,
        clock=clock,
        video_zoom=VideoZoom(params["video_port"]),
        fake=args.fake,
        settings_path=settings_path,
        web_dir=WEB_DIR,
    )

    log.info("http://0.0.0.0:%d/ で待ち受ける", args.port)
    web.run_app(app, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
