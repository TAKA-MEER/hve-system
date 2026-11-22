"""`python3 -m hve_camera` の入口。

[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §4.3 の「偽物のモード」。

    python3 -m hve_camera --fake --port 18000

- `--fake`: 偽のハードウェア（`hw/fake_hw.py`）と偽の昇降部（`hw/fake_lift.py`）で起動する。
  画面に `fake` が付く（偽物だと分かるようにする）
- `--lift HOST:PORT`: `--fake` と一緒にだけ使える。外の偽の昇降部（`tools/fake_lift_server.py` など）に
  本番と同じ `LiftLink` でつなぐ（ハードウェアは偽物のまま）。指定する `HOST:PORT` が `params`
  の `lift_host`・`lift_port` になる（`/ws/module` と設定 API の両方がそこへ行く）
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
from typing import Tuple

from aiohttp import web

from hve_camera.app import VideoCapture, VideoZoom, create_app
from hve_camera.ceiling import CeilingStatus
from hve_camera.lift_link import LIFT_WS_MODULE_PATH, LiftLink
from hve_camera.lift_resolve import resolve_lift_host
from hve_camera.params import load_params
from hve_camera.settings import OWN_AXES, load_settings

#: `--lift HOST:PORT` の値（`argparse` の `type` で HOST:PORT の形を確かめ、`_lift_value` が返す）
LiftHostPort = Tuple[str, int]

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


def _lift_value(value: str) -> LiftHostPort:
    """`--lift HOST:PORT` の形を確かめて `(ホスト, ポート)` を返す（形が違うのは起動しない）。"""
    parts = value.split(":")
    if len(parts) != 2 or not parts[0].strip():
        raise argparse.ArgumentTypeError("--lift は HOST:PORT の形で指定する（%r）" % (value,))
    host, port = parts[0].strip(), parts[1].strip()
    try:
        port_num = int(port)
    except ValueError:
        raise argparse.ArgumentTypeError("--lift のポートは整数で指定する（%r）" % (value,))
    if not 1 <= port_num <= 65535:
        raise argparse.ArgumentTypeError("--lift のポートは 1〜65535（%r）" % (value,))
    return host, port_num


class Runtime:
    """`build_runtime()` の戻り（`main()` と試験が共有する組み立て）。"""

    def __init__(
        self,
        hw: object,
        lift: object,
        params: dict,
        clock: object,
        settings_path: str,
        power_hw: object,
        fake: bool,
    ) -> None:
        self.hw = hw
        self.lift = lift
        self.params = params
        self.clock = clock
        self.settings_path = settings_path
        self.power_hw = power_hw
        self.fake = fake


def build_runtime(args: argparse.Namespace, params: dict, clock: object) -> Runtime:
    """`main()` の組み立てを試験から呼べる関数にしたもの（`web.run_app` は呼ばない）。

    `args` は `--fake` と `--lift`（`None` か `(ホスト, ポート)`）を持つ。
    ハードウェアと昇降部・電源・設定の保存先を作り、`params`（`--lift` のときは
    `lift_host`・`lift_port` を上書きしたもの）を返す。
    """
    lift_addr = getattr(args, "lift", None)
    if lift_addr is not None and not args.fake:
        raise ValueError("--lift は --fake と一緒にだけ使える（本番の昇降部の見つけ方はそのまま）")

    if args.fake:
        from hve_camera.hw.fake_hw import FakeHardware
        from hve_camera.hw.fake_lift import FakeLift
        from hve_camera.hw.fake_power import FakePower

        hw = FakeHardware(clock)
        # **偽物でも天井は「十分遠い MEASURED」で保つ（古くならない）。**天井の読み値が無く起動すると
        # 画面が「天井 値なし」で上昇ボタンも薄いままになる（画面を見る人が動かせない）
        hw.set_steady_ceiling(CeilingStatus.MEASURED, FAKE_CEILING_MM)
        # **偽物のモードでは電源の口も偽物**（実物の `poweroff` で開発機を落とさない）
        power_hw = FakePower()
        # 既定の保存先（`/home/m5stack/...`）はホストに無く PUT が 500 になる。
        # 偽物のモードは起動ごとの一時ディレクトリに保存する（実機の既定は変えない）
        settings_path = str(Path(tempfile.mkdtemp(prefix="hve_camera_fake_")) / "settings.json")
        log.info("偽物の設定の保存先は %s", settings_path)
        log.warning("偽物のモードで起動する（実機の結果と取り違えないこと）")
        log.info("偽物の天井は %d mm（MEASURED）から始める。POST /api/fake の差し込みは 1 回分、止めるのは freeze", FAKE_CEILING_MM)

        if lift_addr is not None:
            # **外の偽の昇降部へ（ハードウェアは偽物のまま。本番と同じ `LiftLink` でつなぐ）**
            host, port = lift_addr
            params = dict(params)
            params["lift_host"] = host
            params["lift_port"] = port
            lift = LiftLink(
                lambda: module_ws_url(params),
                clock,
                name=str(params.get("module_name", "hve-cam")),
                ceiling_sensor=bool(params.get("module_ceiling_sensor", True)),
                state_timeout_ms=float(params["lift_state_timeout_ms"]),
            )
            log.info("偽物のモードで外の偽の昇降部（%s:%d）につなぐ", host, port)
        else:
            lift = FakeLift(clock)
        return Runtime(hw, lift, params, clock, settings_path, power_hw, True)

    from hve_camera.hw.power_hw import PowerHw
    from hve_camera.hw.uno_hw import UnoHardware

    try:
        hw = UnoHardware(params, clock)
    except Exception:
        # 配線の抜けや権限不足を握りつぶさない（握りつぶすと
        # 「動いているように見えるが、実際には動かない」状態になる）。
        log.exception("Arduino との UART を開けなかった")
        raise

    power_hw = PowerHw()
    lift = LiftLink(
        lambda: module_ws_url(params),
        clock,
        name=str(params.get("module_name", "hve-cam")),
        ceiling_sensor=bool(params.get("module_ceiling_sensor", True)),
        state_timeout_ms=float(params["lift_state_timeout_ms"]),
    )
    log.info("実物のモードで起動する（UART %s・昇降部 %s）", params.get("io_device"), module_ws_url(params))
    return Runtime(hw, lift, params, clock, params["settings_path"], power_hw, False)


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
        "--lift",
        type=_lift_value,
        default=None,
        metavar="HOST:PORT",
        help="偽物のモードで外の偽の昇降部につなぐ（--fake と一緒にだけ使える）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=80,
        help="待ち受けるポート（既定 80。UnitV2 での本番）",
    )
    args = parser.parse_args(argv)
    if args.lift is not None and not args.fake:
        parser.error("--lift は --fake と一緒にだけ使える（本番の昇降部の見つけ方はそのまま）")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    params = load_params()
    clock = monotonic_ms

    try:
        runtime = build_runtime(args, params, clock)
    except ValueError as exc:
        parser.error(str(exc))
    except Exception:
        # 本番で Arduino の UART を開けなかった（`build_runtime` が記録済み）
        if args.fake:
            raise
        return 2
    hw = runtime.hw
    lift = runtime.lift
    params = runtime.params
    settings_path = runtime.settings_path
    power_hw = runtime.power_hw

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
        video_capture=VideoCapture(params["video_port"]),
        power_hw=power_hw,
        fake=args.fake,
        settings_path=settings_path,
        web_dir=WEB_DIR,
    )

    log.info("http://0.0.0.0:%d/ で待ち受ける", args.port)
    web.run_app(app, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
