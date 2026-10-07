"""`python3 -m hve_video [--fake] [--port N]` の入口（DetailedDesign.md §4.3）。"""

from __future__ import annotations

import argparse
import logging

from aiohttp import web

from hve_camera.params import load_params
from hve_video.pipeline import VideoPipeline
from hve_video.server import create_app
from hve_video.sources import open_source

# 待ち受けるポート（protocol DetailedDesign-protocol.md §1。映像は :8080）
DEFAULT_PORT = 8080
# 起動時の倍率（spec Spec-ui.md §1.5「画面を開いたとき 1 倍から始まる」）
INITIAL_ZOOM = 1.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m hve_video", description="映像の配信（WP-VIDEO-01）"
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="偽の画像列を使う（実物のカメラは使わない。偽物と実機の結果を取り違えないこと）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"待ち受けるポート（既定 {DEFAULT_PORT}）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("hve_video")
    if args.fake:
        log.warning("偽物のモード（偽の画像列）で動く。実機の結果と取り違えないこと")

    params = load_params()
    source = open_source(
        args.fake,
        params["video_capture_width"],
        params["video_capture_height"],
        params["video_capture_fps"],
        params["video_device"],
    )
    pipeline = VideoPipeline(
        source,
        zoom=INITIAL_ZOOM,
        zoom_max=params["zoom_max"],
        zoom_step=params["zoom_step"],
        out_height=params["video_out_height"],
        jpeg_quality=params["video_jpeg_quality"],
    )
    log.info(
        "映像を配信する: http://0.0.0.0:%d/stream（%d fps・高さ %d px・JPEG 品質 %d）",
        args.port,
        params["video_fps"],
        params["video_out_height"],
        params["video_jpeg_quality"],
    )
    web.run_app(
        create_app(pipeline, params["video_fps"]),
        port=args.port,
        on_shutdown=[pipeline.release],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
