"""ss-system の口（`/ws/module`）で使う、ヨーの角度と `hello`・`aim` の読み取り。

[DetailedDesign-protocol-ss.md](../../docs/plan/detailed/DetailedDesign-protocol-ss.md) §1.1・§2.1・§5.1。
**判定の純関数と、原点を覚える `YawTracker` だけ。**持ち主の規則・途切れ・着いたかの判定は
`module_port.py`（呼び出し側）が持つ。

## ヨーの符号

`yaw_deg` は **画面の「左」で増える向き**（上から見て反時計回り。正が左）。
UNO の累計 `yaw_hs` は `M` 行の `yaw_hsps` が正のとき増える（`step_rate.cpp`）。
`hw.drive_yaw(..., "left")` は **正の** `yaw_hsps` を送る（`uno_hw.py`）ので、
**累計が増える向き ＝ 画面の「左」**。よって `YAW_SIGN = +1`。
実機でヨーが逆に回るなら、この定数だけを `-1` にする。
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from hve_camera.hw.base import HardwareBase

#: UNO の累計（半ステップ）→ `yaw_deg` の符号。`+1` は「累計が増える向き ＝ 画面の左」。
YAW_SIGN = 1

#: `hello` の `name` の形（[-protocol-modules.md](../../docs/plan/detailed/DetailedDesign-protocol-modules.md) §1.1）。
NAME_RE = re.compile(r"^[a-z0-9-]{1,16}$")

#: `busy_label` の長さの上限（UTF-8 のバイト数）。
BUSY_LABEL_MAX_BYTES = 48


def wrap_deg(value: float) -> float:
    """角度を -180 以上 180 未満に収める。"""
    return ((float(value) + 180.0) % 360.0) - 180.0


def shortest_delta_deg(current: float, target: float) -> float:
    """今の向きから目標へ、**近い回り方**の差（-180 以上 180 未満。正は左回り）。"""
    return wrap_deg(float(target) - float(current))


def yaw_deg_from_hs(yaw_hs: int, origin_hs: int, steps_per_rev: float) -> float:
    """半ステップの累計から `yaw_deg` を出す（-180 以上 180 未満。正が左）。"""
    deg = (int(yaw_hs) - int(origin_hs)) * 360.0 / float(steps_per_rev) * YAW_SIGN
    return wrap_deg(deg)


def is_number(value: Any) -> bool:
    """真偽値でない数値か。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def decode_hello(body: Any) -> dict:
    """`hello` を読む。**読めないものは「持つ」既定**（昇降部の `decode_hello` と同じ読み方）。

    戻りは `name`（形に合わなければ空）・`role`（`control`／`observe`）・`operator`（`auto`／`human`）・
    `busy_label`（UTF-8 で 1〜48 バイト。超えた・空・文字列でなければ `None`。切り詰めない）・`fw`。
    """
    if not isinstance(body, dict):
        body = {}
    raw_name = body.get("name")
    name = raw_name if isinstance(raw_name, str) and NAME_RE.fullmatch(raw_name) else ""
    raw_role = body.get("role")
    if raw_role is None:
        role = "control"
    else:
        role = raw_role if raw_role in ("control", "observe") else "observe"
    operator = "auto" if body.get("operator") == "auto" else "human"
    raw_label = body.get("busy_label")
    busy_label: Optional[str] = None
    if isinstance(raw_label, str) and 1 <= len(raw_label.encode("utf-8")) <= BUSY_LABEL_MAX_BYTES:
        busy_label = raw_label
    fw = body.get("fw") if isinstance(body.get("fw"), str) else None
    return {"name": name, "role": role, "operator": operator, "busy_label": busy_label, "fw": fw}


def decode_aim(body: Any) -> Optional[dict]:
    """`aim` を読む。読めなければ `None`（捨てる）。

    `press` は整数、`pitch_deg`・`yaw_deg` は数値（**片方だけでもよい**。両方無ければ捨てる）。
    範囲の判定はここではしない（受け付けるときに `TARGET_RANGE`・`YAW_UNKNOWN` にする）。
    """
    if not isinstance(body, dict) or body.get("t") != "aim":
        return None
    press = body.get("press")
    if not isinstance(press, int) or isinstance(press, bool):
        return None
    pitch = body.get("pitch_deg")
    yaw = body.get("yaw_deg")
    if pitch is not None and not is_number(pitch):
        return None
    if yaw is not None and not is_number(yaw):
        return None
    if pitch is None and yaw is None:
        return None
    return {
        "press": press,
        "pitch_deg": None if pitch is None else float(pitch),
        "yaw_deg": None if yaw is None else float(yaw),
    }


class YawTracker:
    """ヨーの向きを数える。**原点を合わせたときの累計を覚え、差を角度にする。**

    - 原点は人だけが合わせる（`zero()`。画面の WS の `yaw_zero`）
    - UNO が再起動したら（`hw.yaw_epoch()` が変わる）原点を捨てる
    - 原点を合わせる前・再起動のあとは `deg()` が `None`
    """

    def __init__(self, hw: HardwareBase, params: Mapping[str, Any]) -> None:
        self._hw = hw
        self._params = params
        self._origin_hs: Optional[int] = None
        self._origin_epoch = 0

    def zero(self) -> bool:
        """いまの向きを 0° にする。累計がまだ来ていなければ何もせず `False`。"""
        hs = self._hw.yaw_hs_total()
        if hs is None:
            return False
        self._origin_hs = hs
        self._origin_epoch = self._hw.yaw_epoch()
        return True

    def zeroed(self) -> bool:
        """原点を合わせてあるか（UNO が再起動していれば `False`。原点を捨てる）。"""
        if self._origin_hs is not None and self._origin_epoch != self._hw.yaw_epoch():
            self._origin_hs = None
        return self._origin_hs is not None

    def deg(self) -> Optional[float]:
        """ヨーの向き（-180 以上 180 未満。正が左）。原点の前・累計が無いときは `None`。"""
        if not self.zeroed():
            return None
        hs = self._hw.yaw_hs_total()
        if hs is None or self._origin_hs is None:
            return None
        return yaw_deg_from_hs(hs, self._origin_hs, float(self._params["yaw_steps_per_rev"]))
