"""制御ループ。画面から受け取った操作を組み立て、昇降部・ピッチ・ヨーを効かせる。

[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §3・§4.3 と
[-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §1・§2・§4。
`lift_cmd_period_ms` ごとに 1 回 `step()` を回す。

## このファイルで守る 3 つ

1. **天井の値は送る瞬間に載せる。前回の結果を使い回さない。**
   使い回すと、天井の読み値が止まったのに古い値が送られ続けて天井へ突っ込む。
   許可の計算は昇降部がする。ここは値と古さを運ぶだけ。
2. **`hold` は昇降を動かしている間だけ送り、離したら `release` を送る。**
   止まっている間は送らない（protocol §1）。`press` は押し始めごとに 1 増やす。
   押し始め＝動かす軸が `lift_*` 以外から `lift_*` に替わったとき、
   `lift_up` と `lift_down` が入れ替わったとき、別の画面の `hold` に替わったとき。
3. **天井の測定は指令を送ったあとにする。**

## 時計は外から渡す

`clock()` が返すのは**単調増加の ms**。試験は時計を自分で進めるから、天井の古さや
`HOLD_TIMEOUT` を待たずに確かめられる。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Optional

from hve_camera.axes import clamp_speed, pitch_step, yaw_step_rate
from hve_camera.ceiling import CeilingReading, CeilingStatus
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
_LIFT_DIRS = {"lift_up": "up", "lift_down": "down"}

#: 昇降部の `reason` のうち「止まる理由」として画面に出さないもの。
#: 指令が `stop` で止まっているだけの `CMD_STOP` と、動いている `NONE`。
_LIFT_SILENT_REASONS = ("CMD_STOP", "NONE")

#: 昇降部の天井の理由のうち「止める理由でない」もの（反射なし。上昇を許す）。
_LIFT_CEILING_OK_REASONS = ("NONE", "OUT_OF_RANGE")


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
        self._axis: Optional[str] = None
        #: その `hold` の速度（軸の `min`〜`max` に丸める前の値）
        self._speed = 0.0
        #: その `hold` を受け取った時刻
        self._hold_at_ms: Optional[float] = None
        #: その `hold` を送った画面（`None` は試験などの直接操作）
        self._owner: Any = None

        #: ピッチの現在の角度
        self._pitch_deg = 0.0
        #: ピッチ的可動範囲の端にいる理由（`AXIS_LIMIT` か `NONE`）
        self._pitch_reason = "NONE"

        #: 最後に分かった天井の読み値。**新しい測定が返るまで持ち続ける**
        self._ceiling: Optional[CeilingReading] = None
        #: Arduino から行が最後に来た時刻（`read_ceiling()` が値を返した時刻）。
        #: 来なければ `IO_LOST`。最初は `None`（まだ 1 行も来ていない）。
        self._last_io_ms: Optional[float] = None

        #: 昇降への `press`。押し始めごとに 1 増やす（protocol §2.1）。
        self._press = 0
        #: 今 `hold` を送っている最中か（離したら `release` を送るため）
        self._lift_sending = False
        #: 送っている `hold` の向きと、そのときの画面
        self._press_dir = "stop"
        self._press_owner: Any = None

        #: 今の倍率。全画面で共通（spec [Spec-ui.md](../../docs/plan/spec/Spec-ui.md) §1.5）
        self._zoom = 1.0

        #: 前の `step()` の時刻。ピッチの積分に使う
        self._last_step_at_ms: Optional[float] = None

    # --- 画面から受け取った操作 ------------------------------------------------------------------

    def hold(self, axis: str, speed: Any, owner: Any = None) -> bool:
        """操作を覚える。**最後に届いた操作が勝つ**（spec §1.6）。

        知らない軸と数値でない速度は**無視する**（前の操作をそのまま残す）。
        受け取ったときだけ時刻を刻むので、`hold_timeout_ms` はここから数える。
        `owner` は押した画面。別の画面に替わったら昇降の `press` を増やす。
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
        self._owner = owner
        return True

    def release(self) -> None:
        """押していない状態に戻す。**理由も出さない**（画面は `NONE` に戻る）。"""
        self._axis = None
        self._speed = 0.0
        self._hold_at_ms = None
        self._owner = None

    async def set_zoom(self, level: Any) -> float:
        """倍率を丸めて持ち、**変わったときだけ** `hve_video` へ渡す。実際の倍率を返す。

        送るのを待ってから戻る。**送れなくても例外は投げない**（`VideoZoom` 側で受け止める）。
        """
        value = clamp_zoom(level, self._params)
        if value != self._zoom:
            self._zoom = value
            await self._video_zoom.send_zoom(value)
        return value

    # --- 制御ループ 1 回 --------------------------------------------------------------------------

    async def step(self) -> None:
        """制御ループ 1 回。**昇降部への指令を先に送り、そのあと天井を読む。**"""
        now = self._clock()
        dt_ms = self._step_dt_ms(now)
        self._last_step_at_ms = now

        axis, holding = self._active_axis(now)
        speed = self._clamped_speed(axis) if holding else 0.0

        # 1: 昇降部への指令。動かしている間だけ `hold`、離したら `release`。
        #    天井の値は**この瞬間の**読み値。許可の計算は昇降部がする。
        await self._step_lift(axis, holding, speed, now)

        # 2: ピッチとヨー
        await self._step_axes(axis, speed, dt_ms)

        # 3: 天井を読む。**指令を送ったあと**に読む。
        reading = await self._hw.read_ceiling()
        if reading is not None:
            self._ceiling = reading
            self._last_io_ms = now

    def _step_dt_ms(self, now_ms: float) -> float:
        """この `step()` で進む時間。1 回目は 1 周期ぶんとして扱う。"""
        if self._last_step_at_ms is None:
            return float(self._params["lift_cmd_period_ms"])
        return max(0.0, now_ms - self._last_step_at_ms)

    def _active_axis(self, now_ms: float) -> tuple:
        """`hold_timeout_ms` を超えていたら「押していない」扱いにする（protocol §1）。"""
        if self._axis is None or self._hold_at_ms is None:
            return None, False
        if (now_ms - self._hold_at_ms) > float(self._params["hold_timeout_ms"]):
            return None, False
        return self._axis, True

    def _clamped_speed(self, axis: str) -> float:
        """速度を設定の下限〜上限に丸める（protocol §2.1）。"""
        return clamp_speed(self._speed, self._settings[_AXIS_SETTING[axis]])

    async def _step_lift(
        self, axis: Optional[str], holding: bool, speed: float, now: float
    ) -> None:
        """昇降の `hold`・`release` を送る。`press` は押し始めごとに 1 増やす。"""
        direction = _LIFT_DIRS.get(axis) if holding else None
        if direction is None:
            if self._lift_sending:
                await self._lift.send_release(self._press)
                self._lift_sending = False
                self._press_dir = "stop"
            return
        if (
            not self._lift_sending
            or direction != self._press_dir
            or self._owner is not self._press_owner
        ):
            # 押し始め（動かし始め・向きの入れ替え・別の画面の操作）
            self._press += 1
            self._press_owner = self._owner
        duty = int(round(speed))
        status, mm, age_ms = self._ceiling_triple(now)
        await self._lift.send_hold(
            direction, duty, self._press, status, mm, age_ms
        )
        self._lift_sending = True
        self._press_dir = direction

    def _ceiling_triple(self, now_ms: float) -> tuple:
        """`hold` に載せる天井の `(状態, mm, 古さ)`。読み値が無ければ `READ_ERROR`。"""
        reading = self._ceiling
        if reading is None or reading.at_ms is None:
            return "READ_ERROR", None, 0
        age_ms = max(0, int(now_ms - reading.at_ms))
        if reading.status is CeilingStatus.MEASURED:
            return "MEASURED", reading.distance_mm, age_ms
        return reading.status.value, None, age_ms

    async def _step_axes(self, axis: Optional[str], speed: float, dt_ms: float) -> None:
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

    def build_state(self, clients: int) -> dict:
        """画面へ配る `state`（protocol §4）を組み立てる。**送信はしない。**"""
        now = self._clock()
        link_ok = self._lift.link_ok(now, float(self._params["lift_state_timeout_ms"]))
        raw = self._lift.latest_state() or {}

        lift_ceiling = raw.get("ceiling") if isinstance(raw.get("ceiling"), dict) else {}
        if link_ok:
            ceiling_ok = bool(lift_ceiling.get("ok", False))
            ceiling_reason = lift_ceiling.get("reason", "CEILING_STALE")
        else:
            ceiling_ok, ceiling_reason = False, "LINK_LOST"

        status, mm, age_ms = self._ceiling_triple(now)
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
                "top_detect": bool(raw.get("top_detect", False)),
                "owner": raw.get("owner"),
                "ui_clients": raw.get("ui_clients", 0),
                #: 昇降部の IP（画面に昇降部へのリンクを出す。§4.6）
                "lift_ip": getattr(self._lift, "current_host", None),
            },
            "ceiling": {
                "status": status,
                "mm": mm,
                "age_ms": age_ms,
                "ok": ceiling_ok,
                "reason": ceiling_reason,
            },
            "pitch_deg": round(self._pitch_deg, 3),
            "zoom": self._zoom,
            # 画面は映像（hve_video）の URL を組み立てるのにこのポートが要る（protocol §2.4）
            "video_port": int(self._params["video_port"]),
            "active_axis": self._active_axis(now)[0],
            "reason": self._screen_reason(
                now, link_ok, raw.get("reason"), lift_ceiling
            ),
            "provisional": list(self._params.get("provisional", [])),
            "fake": self._fake,
            "clients": clients,
        }

    def _io_lost(self, now_ms: float) -> bool:
        """Arduino から行が来ないか（`io_lost_ms`）。まだ 1 行も来ていなければ `True`。"""
        if self._last_io_ms is None:
            return True
        return (now_ms - self._last_io_ms) > float(self._params["io_lost_ms"])

    def _screen_reason(
        self,
        now_ms: float,
        link_ok: bool,
        lift_reason: Any,
        lift_ceiling: Any,
    ) -> str:
        """画面に出す停止理由。**上から順に、最初に当たったもの**（protocol §4）。

        1. `LINK_LOST`（一番上流。動いていなければ全部ゆがむので先に出す）
        2. `IO_LOST`（Arduino から行が来ない）
        3. 押している軸の理由（ピッチの `AXIS_LIMIT`・上昇のときの天井の理由は昇降部の判定）
        4. 途絶えた操作の `HOLD_TIMEOUT`
        5. 昇降部の理由（指令が `stop` で止まっているだけの `CMD_STOP` と `NONE` は除く）
        """
        if not link_ok:
            return "LINK_LOST"
        if self._io_lost(now_ms):
            return "IO_LOST"

        axis, holding = self._active_axis(now_ms)
        if holding:
            if axis in _PITCH_AXES and self._pitch_reason != "NONE":
                return self._pitch_reason
            if axis == "lift_up" and isinstance(lift_ceiling, dict):
                reason = lift_ceiling.get("reason", "NONE")
                if reason not in _LIFT_CEILING_OK_REASONS:
                    return reason

        if self._axis is not None and not holding:
            return "HOLD_TIMEOUT"

        if isinstance(lift_reason, str) and lift_reason not in _LIFT_SILENT_REASONS:
            return lift_reason
        return "NONE"
