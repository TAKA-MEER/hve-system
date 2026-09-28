"""ピッチとヨーの計算。

- **ピッチ**（SG90）: 目標角を deg/s で積分し、`pitch_min_deg`〜`pitch_max_deg` で止める。
  端に着いたら理由 `AXIS_LIMIT`（names §3）。
- **ヨー**（28BYJ-48）: **角度を持たない。**deg/s と向きから、1 秒あたりの半ステップ数
  （`yaw_steps_per_rev` を使う）と回転方向を返すだけ。**範囲の制限はしない**
  （spec [Spec-ui.md](../../docs/plan/spec/Spec-ui.md) §1.4「360° 回し続けられる」）。
"""

from __future__ import annotations

from typing import Any, Mapping

#: ヨーの向き。protocol §2.1 の `axis` の値。
_YAW_DIRECTIONS = {"yaw_left": "left", "yaw_right": "right"}


def pitch_step(
    current_deg: float,
    speed_dps: float,
    dt_ms: float,
    params: Mapping[str, Any],
) -> tuple[float, str]:
    """ピッチの目標角を deg/s × 時間 で進め、可動範囲に収めて `(角度, 理由)` を返す。

    範囲の外に出たときは端の角度に留め、理由を `AXIS_LIMIT` にする。
    範囲の中に収まったときは `NONE`。**端にちょうどいるだけのときは `NONE`**
    （さらに進もうとしたときだけ `AXIS_LIMIT`）。
    """
    target = current_deg + speed_dps * dt_ms / 1000.0

    low = float(params["pitch_min_deg"])
    high = float(params["pitch_max_deg"])
    if target < low:
        return low, "AXIS_LIMIT"
    if target > high:
        return high, "AXIS_LIMIT"
    return target, "NONE"


def yaw_step_rate(
    speed_dps: float,
    direction: str,
    params: Mapping[str, Any],
) -> tuple[float, str]:
    """ヨーの `(1 秒あたりの半ステップ数, 向き)` を返す。**範囲の制限はしない。**

    `direction` は protocol §2.1 の `axis` の値（`yaw_left` / `yaw_right`）で、
    返す `向き` は `left` / `right`。知らない向きは `ValueError`。
    """
    if direction not in _YAW_DIRECTIONS:
        raise ValueError(f"知らないヨーの向き: {direction}")
    steps_per_rev = float(params["yaw_steps_per_rev"])
    return abs(speed_dps) * steps_per_rev / 360.0, _YAW_DIRECTIONS[direction]


def clamp_speed(value: float, bounds: Mapping[str, float]) -> float:
    """速度を設定の `min`〜`max` に丸める。後のパケットの制御ループが使う。"""
    low = float(bounds["min"])
    high = float(bounds["max"])
    return min(max(value, low), high)
