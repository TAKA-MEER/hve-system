"""制御ループ。画面から受け取った操作を組み立て、昇降部・ピッチ・ヨーを効かせる。

[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §4.2・§3 と
[-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §1・§2。
`lift_cmd_period_ms`（100 ms）ごとに 1 回 `step()` を回す。

## このファイルで守る 2 つ

1. **`ceil_ok` は送る瞬間に計算する。前回の結果を使い回さない**
   （[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §3）。
   使い回すと、天井の読み値が止まったのに `true` が送られ続けて天井へ突っ込む。
2. **天井の測定は指令を送ったあとにする。**100 ms ごとの指令を I2C の待ち時間に
   延ばさないため（名前辞書の `step` の行）。

## 時計は外から渡す

`clock()` が返すのは**単調増加の ms**。試験は時計を自分で進めるから、天井の古さや
`HOLD_TIMEOUT` を待たずに確かめられる。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from hve_camera.axes import clamp_speed, pitch_step, yaw_step_rate
from hve_camera.ceiling import CeilingReading, CeilingStatus, ceiling_permission
from hve_camera.hw.base import HardwareBase
from hve_camera.lift_link import LiftPort

log = logging.getLogger(__name__)

#: 時計の型。ms を返す。
Clock = Callable[[], float]

#: 操作できる軸（protocol §2.1）。**どれか 1 つだけ**が動く。
_AXES = ("lift_up", "lift_down", "pitch_up", "pitch_down", "yaw_left", "yaw_right")

#: 軸 → 速度を丸めるときに見る設定の項目。`pitch_up` と `pitch_down` は同じ枠。
_AXIS_SETTING = {
    "lift_up": "lift_up",
    "lift_down": "lift_down",
    "pitch_up": "pitch",
    "pitch_down": "pitch",
    "yaw_left": "yaw",
    "yaw_right": "yaw",
}

_PITCH_AXES = ("pitch_up", "pitch_down")
_YAW_AXES = ("yaw_left", "yaw_right")

#: 昇降部の `reason` のうち「止まる理由」として画面に出さないもの。
#: 指令が `stop` で止まっているだけの `CMD_STOP` と、動いている `NONE`。
_LIFT_SILENT_REASONS = ("CMD_STOP", "NONE")


def clamp_zoom(level: Any, params: Mapping[str, Any]) -> float:
    """倍率を 1〜`zoom_max` に丸め、`zoom_step` の倍数にそろえる（protocol §2.1）。

    数値でないものは 1 倍にする（画面が開いたときと同じ）。
    """
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        return 1.0
    step = float(params["zoom_step"])
    snapped = round(round(float(level) / step) * step, 6)
    return min(max(snapped, 1.0), float(params["zoom_max"]))


class ControlLoop:
    """操作の鮮度・動かす軸・速度を組み立て、昇降部への指令とピッチ・ヨーを効かせる。"""

    def __init__(
        self,
        hw: HardwareBase,
        lift: LiftPort,
        *,
        settings: Mapping[str, Mapping[str, float]],
        params: Mapping[str, Any],
        clock: Clock,
        video_zoom: Any,
        fake: bool = False,
    ) -> None:
        self._hw = hw
        self._lift = lift
        self._settings = settings
        self._params = params
        self._clock = clock
        self._video_zoom = video_zoom
        self._fake = fake

        #: 最後に届いた `hold` の軸。`None` は「押していない」
        self._axis: str | None = None
        #: その `hold` の速度（軸の `min`〜`max` に丸める前の値）
        self._speed = 0.0
        #: その `hold` を受け取った時刻
        self._hold_at_ms: float | None = None

        #: ピッチの現在の角度
        self._pitch_deg = 0.0
        #: ピッチ的可動範囲の端にいる理由（`AXIS_LIMIT` か `NONE`）
        self._pitch_reason = "NONE"

        #: 最後に分かった天井の読み値。**新しい測定が返るまで持ち続ける**
        self._ceiling: CeilingReading | None = None
        #: 直近の天井の許可。画面に出すためだけに残す（**指令には使わない**）
        self._ceiling_ok = False
        self._ceiling_reason = "CEILING_STALE"

        #: 今の倍率。全画面で共通（spec [Spec-ui.md](../../docs/plan/spec/Spec-ui.md) §1.5）
        self._zoom = 1.0

        #: 前の `step()` の時刻。ピッチの積分と連続駆動の時間に使う
        self._last_step_at_ms: float | None = None

    # --- 画面から受け取った操作 ------------------------------------------------------------------

    def hold(self, axis: str, speed: Any) -> bool:
        """操作を覚える。**最後に届いた操作が勝つ**（spec §1.6）。

        知らない軸と数値でない速度は**無視する**（前の操作をそのまま残す）。
        受け取ったときだけ時刻を刻むので、`hold_timeout_ms` はここから数える。
        """
        if axis not in _AXES:
            log.warning("知らない軸の hold は無視した: %r", axis)
            return False
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            log.warning("hold の speed が数値でないので無視した: %r", speed)
            return False
        self._axis = axis
        self._speed = float(speed)
        self._hold_at_ms = self._clock()
        return True

    def release(self) -> None:
        """押していない状態に戻す。**理由も出さない**（画面は `NONE` に戻る）。"""
        self._axis = None
        self._speed = 0.0
        self._hold_at_ms = None

    def set_zoom(self, level: Any) -> float:
        """倍率を丸めて持ち、**変わったときだけ** `hve_video` へ渡す。実際の倍率を返す。"""
        value = clamp_zoom(level, self._params)
        if value != self._zoom:
            self._zoom = value
            self._video_zoom.send_zoom(value)
        return value

    # --- 制御ループ 1 回 --------------------------------------------------------------------------

    async def step(self) -> None:
        """制御ループ 1 回。**昇降部への指令を先に送り、そのあと天井を測る。**"""
        now = self._clock()
        dt_ms = self._step_dt_ms(now)
        self._last_step_at_ms = now

        axis, holding = self._active_axis(now)
        speed = self._clamped_speed(axis) if holding else 0.0

        # 1・2: 昇降部への指令を毎回送る（止まっている間も stop）。
        #    ceil_ok は**この瞬間の**天井の許可。前回の値を使い回さない。
        ceil_ok, ceil_reason = ceiling_permission(self._ceiling, now, self._params)
        self._ceiling_ok, self._ceiling_reason = ceil_ok, ceil_reason
        direction, duty = self._lift_command(axis, speed)
        await self._lift.send_cmd(direction, duty, ceil_ok)

        # 3: ピッチとヨー
        await self._step_axes(axis, speed, dt_ms)

        # 4: 天井を測る。**指令を送ったあと**に測る（I2C の待ち時間を指令に足さない）。
        reading = await self._hw.read_ceiling()
        if reading is not None:
            self._ceiling = reading

    def _step_dt_ms(self, now_ms: float) -> float:
        """この `step()` で進む時間。1 回目は 1 周期ぶんとして扱う。"""
        if self._last_step_at_ms is None:
            return float(self._params["lift_cmd_period_ms"])
        return max(0.0, now_ms - self._last_step_at_ms)

    def _active_axis(self, now_ms: float) -> tuple[str | None, bool]:
        """`hold_timeout_ms` を超えていたら「押していない」扱いにする（protocol §1）。"""
        if self._axis is None or self._hold_at_ms is None:
            return None, False
        if (now_ms - self._hold_at_ms) > float(self._params["hold_timeout_ms"]):
            return None, False
        return self._axis, True

    def _clamped_speed(self, axis: str) -> float:
        """速度を設定の下限〜上限に丸める（protocol §2.1）。"""
        return clamp_speed(self._speed, self._settings[_AXIS_SETTING[axis]])

    def _lift_command(self, axis: str | None, speed: float) -> tuple[str, int]:
        """昇降部への指令。**昇降以外の軸・押していないときは `stop`。**"""
        if axis == "lift_up":
            return "up", int(round(speed))
        if axis == "lift_down":
            return "down", int(round(speed))
        return "stop", 0

    async def _step_axes(self, axis: str | None, speed: float, dt_ms: float) -> None:
        """ピッチを積分してサーボへ、ヨーは押している間だけ回す。"""
        if axis in _PITCH_AXES:
            signed = speed if axis == "pitch_up" else -speed
            self._pitch_deg, self._pitch_reason = pitch_step(
                self._pitch_deg, signed, dt_ms, self._params
            )
            await self._hw.set_pitch(self._pitch_deg)
        else:
            self._pitch_reason = "NONE"

        if axis in _YAW_AXES:
            steps_per_s, direction = yaw_step_rate(speed, axis, self._params)
            await self._hw.drive_yaw(steps_per_s, direction)
        else:
            # 離している間はコイルの電流を切る（stop_yaw の責務）
            await self._hw.stop_yaw()

    # --- 画面へ配る state ------------------------------------------------------------------------

    def build_state(self, clients: int) -> dict[str, Any]:
        """画面へ配る `state`（protocol §2.4）を組み立てる。**送信はしない。**"""
        now = self._clock()
        # 画面に出すものは**今この瞬間**で計算し直す（`step()` の一巡前の値を出さない）
        ceil_ok, ceil_reason = ceiling_permission(self._ceiling, now, self._params)
        self._ceiling_ok, self._ceiling_reason = ceil_ok, ceil_reason

        link_ok = self._lift.link_ok(now, float(self._params["lift_state_timeout_ms"]))
        raw = self._lift.latest_state() or {}

        return {
            "t": "state",
            "lift": {
                "link": "ok" if link_ok else "lost",
                "dir": raw.get("dir", "stop"),
                "duty": raw.get("duty", 0),
                "reason": raw.get("reason", "CMD_STOP"),
                "bottom": bool(raw.get("bottom", False)),
                "height_mm": raw.get("height_mm"),
                "height_ok": bool(raw.get("height_ok", False)),
                "top_mm": raw.get("top_mm"),
            },
            "ceiling": {
                "mm": self._ceiling_mm(),
                "age_ms": self._ceiling_age_ms(now),
                "ok": ceil_ok,
                "reason": ceil_reason,
            },
            "pitch_deg": round(self._pitch_deg, 3),
            "zoom": self._zoom,
            "active_axis": self._active_axis(now)[0],
            "reason": self._screen_reason(now, link_ok, ceil_ok, ceil_reason, raw.get("reason")),
            "provisional": list(self._params.get("provisional", [])),
            "fake": self._fake,
            "clients": clients,
        }

    def _ceiling_mm(self) -> int | None:
        """`MEASURED` のときの距離。それ以外では `None`（反射なし・読取り失敗）。"""
        if self._ceiling is None or self._ceiling.status is not CeilingStatus.MEASURED:
            return None
        return self._ceiling.distance_mm

    def _ceiling_age_ms(self, now_ms: float) -> int | None:
        """読み値からの経過時間。**読み値が 1 つも無いときは `None`。**"""
        if self._ceiling is None or self._ceiling.at_ms is None:
            return None
        return int(now_ms - self._ceiling.at_ms)

    def _screen_reason(self, now_ms: float, link_ok: bool, ceil_ok: bool,
                       ceil_reason: str, lift_reason: Any) -> str:
        """画面に出す停止理由。**上から順に、最初に当たったもの**（protocol §2.4）。

        1. `LINK_LOST`（一番上流。動いていなければ全部ゆがむので先に出す）
        2. 押している軸の理由（ピッチの `AXIS_LIMIT`・上昇のときの天井の理由）
        3. 途絶えた操作の `HOLD_TIMEOUT`
        4. 昇降部の理由（指令が `stop` で止まっているだけの `CMD_STOP` と `NONE` は除く）
        """
        if not link_ok:
            return "LINK_LOST"

        axis, holding = self._active_axis(now_ms)
        if holding:
            if axis in _PITCH_AXES and self._pitch_reason != "NONE":
                return self._pitch_reason
            if axis == "lift_up" and not ceil_ok:
                return ceil_reason

        if self._axis is not None and not holding:
            return "HOLD_TIMEOUT"

        if isinstance(lift_reason, str) and lift_reason not in _LIFT_SILENT_REASONS:
            return lift_reason
        return "NONE"
