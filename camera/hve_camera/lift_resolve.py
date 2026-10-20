"""昇降部の見つけ方（[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §4.6）。

`params.toml` の `lift_host` に IP が書いてあればそれを使う。
空なら mDNS の `lift_mdns_name` を `avahi-resolve-host-name -4` で引く。
UnitV2 の Python（glibc）からは `.local` を引けないが、avahi は入っている
（[DetailedDesign-hardware.md](../../docs/plan/detailed/DetailedDesign-hardware.md) §2.1.1）。
"""

from __future__ import annotations

import logging
import subprocess
from typing import Callable, Optional

log = logging.getLogger(__name__)


def resolve_lift_host(lift_host: object, lift_mdns_name: object) -> Optional[str]:
    """昇降部の IP を返す。引けなければ `None`。

    `lift_host` が空でなければ（前後の空白を除いて）そのまま使う。
    空なら `<lift_mdns_name>.local` を `avahi-resolve-host-name -4` で引く。
    """
    configured = lift_host.strip() if isinstance(lift_host, str) else ""
    if configured:
        return configured
    name = lift_mdns_name.strip() if isinstance(lift_mdns_name, str) else ""
    if not name:
        return None
    return _resolve_mdns(name + ".local")


def _resolve_mdns(host: str) -> Optional[str]:
    """`avahi-resolve-host-name -4` で 1 件引く。何かあれば `None`。"""
    try:
        completed = subprocess.run(
            ["avahi-resolve-host-name", "-4", host],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            universal_newlines=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("昇降部 %s を引けない: %s", host, exc)
        return None
    if completed.returncode != 0:
        return None
    # 例: "hve-lift.local\t192.168.5.23\n"
    parts = completed.stdout.split()
    if len(parts) >= 2 and _looks_like_ipv4(parts[-1]):
        return parts[-1]
    return None


def _looks_like_ipv4(text: str) -> bool:
    """IPv4 に見えるか（`xxx.xxx.xxx.xxx`・各 0〜255）。名前解決の取り違え防止。"""
    quad = text.split(".")
    if len(quad) != 4:
        return False
    for piece in quad:
        if not piece.isdigit() or not 0 <= int(piece) <= 255:
            return False
    return True


#: 遅延して引くための型（`LiftLink` が再接続のたびに呼び出す）。
Resolver = Callable[[], Optional[str]]
