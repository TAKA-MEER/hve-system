"""アプリをログ付きで起動する薄い入口（UnitV2 の配備用。WP-CAM-05）。

    python3 run_logged.py <ログファイル> <モジュール> [引数...]

UnitV2 の `/tmp` は RAM なので、ログはフラッシュ（ルート）に書く。書き過ぎでフラッシュを
食わないよう、**256 KB × 3 世代**で回す。標準出力・標準エラー（落ちたときのトレースを含む）も
同じファイルへ入れる。aiohttp のアクセスログ（画面の更新のたびに出る）は警告以上だけにする。

`hve_camera`・`hve_video` の `main()` は `logging.basicConfig` を呼ぶが、ここで先に根のロガーへ
ハンドラを付けるので basicConfig は何もしない（同じ書式をここで付ける）。

Python 3.8（UnitV2）で動くように書く（`X | None` などを使わない）。
"""

import logging
import logging.handlers
import runpy
import sys

MAX_BYTES = 256 * 1024
BACKUP_COUNT = 2
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _LogStream:
    """`print` や素の例外出力を、ログのファイルへ流す。"""

    def __init__(self, logger, level):
        self._logger = logger
        self._level = level
        self._buf = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line)
        return len(text)

    def flush(self):
        if self._buf.strip():
            self._logger.log(self._level, self._buf)
        self._buf = ""

    def isatty(self):
        return False


def setup_logging(log_path):
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    sys.stdout = _LogStream(logging.getLogger("stdout"), logging.INFO)
    sys.stderr = _LogStream(logging.getLogger("stderr"), logging.ERROR)


def main(argv):
    if len(argv) < 3:
        sys.stderr.write("usage: run_logged.py <logfile> <module> [args...]\n")
        return 2
    log_path, module = argv[1], argv[2]
    setup_logging(log_path)
    log = logging.getLogger("run_logged")
    log.info("起動する: %s %s", module, " ".join(argv[3:]))
    sys.argv = [module] + argv[3:]
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        log.info("終了した: %s", exc.code)
        raise
    except BaseException:
        log.exception("落ちた")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
