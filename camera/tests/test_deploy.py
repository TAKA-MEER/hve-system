"""配備（`camera/deploy/`・`camera/vendor/`）の試験。WP-CAM-05。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CAMERA = Path(__file__).resolve().parents[1]
DEPLOY = CAMERA / "deploy"


def test_run_logged_writes_logs_and_traceback(tmp_path):
    """標準出力・標準エラー・落ちたときのトレースがログのファイルに入り、aiohttp のアクセスログは出ない。"""
    mod_dir = tmp_path / "mods"
    mod_dir.mkdir()
    (mod_dir / "demo_mod.py").write_text(
        "import logging, sys\n"
        "logging.basicConfig(level=logging.INFO)\n"  # run_logged が先に付けたので何もしない
        "logging.getLogger('demo').info('hello-log')\n"
        "logging.getLogger('aiohttp.access').info('ACCESS-LINE')\n"
        "print('hello-stdout', sys.argv[1:])\n"
        "raise RuntimeError('boom')\n"
    )
    log = tmp_path / "x.log"
    proc = subprocess.run(
        [sys.executable, str(DEPLOY / "run_logged.py"), str(log), "demo_mod", "--port", "1"],
        env={"PYTHONPATH": str(mod_dir)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert proc.stdout == "" and proc.stderr == ""  # 端末には何も出ない
    text = log.read_text(encoding="utf-8")
    assert "hello-log" in text
    assert "hello-stdout ['--port', '1']" in text
    assert "RuntimeError: boom" in text
    assert "ACCESS-LINE" not in text


def test_vendored_tomli_reads_params():
    """UnitV2 には tomli が無いので `camera/vendor/` のものでパラメータを読めること。"""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import tomli; assert tomli.__file__.startswith(%r)\n"
        "from pathlib import Path\n"
        "d = tomli.loads(Path(%r).read_text(encoding='utf-8'))\n"
        "assert d['video_port'] == 8080\n"
    ) % (str(CAMERA / "vendor"), str(CAMERA / "vendor"), str(CAMERA / "config" / "params.toml"))
    subprocess.run([sys.executable, "-c", code], check=True)


def _sed(script: str, text: str) -> str:
    return subprocess.run(
        ["sed", "-f", str(DEPLOY / "os" / script)], input=text, capture_output=True, text=True, check=True
    ).stdout


def test_os_sed_scripts_make_the_documented_small_diffs():
    """OS の 3 ファイルの sed が、原本の想定の行だけを変える（P-11・P-12・P-13）。"""
    s23 = '\t\tfor kofile in /lib/modules/*.ko\n\t\t    do /sbin/insmod $kofile\n\t\tdone\n'
    out = _sed("S23oadfsko.sed", s23)
    assert 'do [ "$kofile" = /lib/modules/grace.ko ] && continue; /sbin/insmod $kofile' in out
    assert out.count("\n") == s23.count("\n") + 0

    s90 = "  /usr/sbin/wpa_supplicant -B -iwlan0\n  /usr/sbin/hostapd -d /etc/hostapd.conf -B\n"
    out = _sed("S90wifi-conf.sed", s90)
    assert "  # HVE-P11: /usr/sbin/hostapd" in out
    assert "\n  /usr/sbin/wpa_supplicant" in "\n" + out  # wlan0 側には触れない

    out = _sed("avahi-daemon.conf.sed", "use-ipv4=yes\nallow-interfaces=eth0, wlan1\n")
    assert "allow-interfaces=eth0, wlan0, wlan1\n" in out
