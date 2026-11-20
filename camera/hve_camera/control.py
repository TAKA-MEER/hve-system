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
from hve_camera.hw.base import POWER_ACTIVE, HardwareBase
from hve_camera.lift_link import LiftPort
from hve_camera.pitch_backlash import PitchBacklash

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

        #: ピッチの遊びの片寄せの状態機械。報告する目標角（`pitch_deg`）とサーボへの指令を分ける
        self._pitch = PitchBacklash(params)
        # 起動したときは向きが分からない（`aim` やボタンを待たず、最初の周期で片寄せする）
        self._pitch.force_unknown(clock())
        #: 最後にサーボへ出した指令（変わったときだけ `set_pitch` を呼ぶため）
        self._last_pitch_cmd = 0.0
        #: 前の `step()` で画面のピッチのボタンを押していたか（離したときの片寄せに使う）
        self._pitch_axis_holding = False
        #: 最後に見た UNO の再起動の回数と省電力の段階（変わったら向きが分からない。`force_unknown`）
        self._last_yaw_epoch = hw.yaw_epoch()
        self._last_power_mode = hw.get_power_mode()
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
        #: 動いている `lift_goto`（`id`・`mode`・`target_mm`・`speed`・`owner`・`at`）。無ければ `None`
        self._goto: Optional[dict] = None
        #: 最後の `lift_goto` の状態（`state.lift_goto`）。次の `lift_goto` まで残す
        self._goto_record: Optional[dict] = None
        #: 終わった `id`（着いた・着かなかったあとは送らない。WP-CAM-17 §4.1）
        self._finished_goto_ids: set = set()
        #: 前の `step()` で見た昇降部の接続（繋ぎ直しを途切れとして拾うため）
        self._lift_obj_prev: Any = None
        self._bottom_stop_count = 0
        #: 送っている `hold` の向きと、そのときの画面
        self._press_dir = "stop"
        self._press_owner: Any = None

        #: 今の倍率。全画面で共通（spec [Spec-ui.md](../../docs/plan/spec/Spec-ui.md) §1.5）
        self._zoom = 1.0

        #: 前の `step()` の時刻。ピッチの積分に使う
        self._last_step_at_ms: Optional[float] = None

        #: `aim`（ss-system）が回しているヨーの速さ [deg/s]（正が左。0 は回していない）
        self._aim_yaw_dps = 0.0
        #: ヨー・ピッチの目標が最後に変わった（動かした）時刻。静止画の `X-Still-Ms` の元
        self.motion_at_ms: float = clock()

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
        if self._goto is not None:
            self._finish_goto("NOT_ARRIVED", "PREEMPTED")
        self._axis = axis
        self._speed = float(speed)
        self._hold_at_ms = self._clock()
        self._owner = owner
        return True

    def lift_goto(
        self, id: str, mode: str, target_mm: Any, speed: Any, owner: Any = None
    ) -> bool:
        """画面の「目標の高さへ」「下端へ」を受ける（[-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §4.1）。

        `id` は画面が押し始めごとに作る文字列。新規 `id` ＝新しい押し始め（`press` を 1 増やす）。
        同じ `id` の再送は速さ・目標を更新するだけ。**最後に届いた操作が勝つ**（`hold` と同じ）。
        終わった `id` は受け取らない（送らない）。知らない `mode`・数値でない速さ・目標は無視する。
        """
        if not isinstance(id, str) or not id or len(id) > 32:
            log.warning("lift_goto の id が見当違いなので無視した: %r", id)
            return False
        if id in self._finished_goto_ids:
            return False
        if mode not in ("target", "bottom"):
            log.warning("知らない mode の lift_goto は無視した: %r", mode)
            return False
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            log.warning("lift_goto の speed が数値でないので無視した: %r", speed)
            return False
        now = self._clock()
        if mode == "target":
            if isinstance(target_mm, bool) or not isinstance(target_mm, (int, float)):
                log.warning("lift_goto の target_mm が数値でないので無視した: %r", target_mm)
                return False
            target = int(round(float(target_mm)))
        else:
            target = None
        if self._goto is not None and self._goto["id"] == id and self._goto.get("mode") == mode and self._goto["owner"] is owner:
            self._goto["speed"] = float(speed)
            self._goto["target_mm"] = target
            self._goto["at_ms"] = now
            return True
        if self._goto is not None:
            self._finish_goto("NOT_ARRIVED", "PREEMPTED")
        # 別の操作を蹴る: hold を離す
        self._axis = None
        self._speed = 0.0
        self._hold_at_ms = None
        self._press += 1
        self._press_owner = owner
        self._goto = {"id": id, "mode": mode, "target_mm": target, "speed": float(speed),
                      "owner": owner, "at_ms": now, "started_ms": now, "press": self._press}
        self._goto_record = {"id": id, "mode": mode, "target_mm": target, "status": "MOVING", "reason": "NONE"}
        self._owner = owner
        self._bottom_stop_count = 0
        return True

    def _finish_goto(self, status: str, reason: str) -> None:
        """動いている `lift_goto` を終わらせる（`state.lift_goto` に残し、`id` を終わったものに入れる）。"""
        if self._goto is None:
            return
        self._finished_goto_ids.add(self._goto["id"])
        if self._goto_record is not None:
            self._goto_record = dict(self._goto_record, status=status, reason=reason)
        self._goto = None
        self._bottom_stop_count = 0

    def _active_goto(self, now_ms: float):
        """動いている `lift_goto`（途切れていれば終わらせて `None`）。"""
        if self._goto is None:
            return None
        if (now_ms - self._goto["at_ms"]) > float(self._params["hold_timeout_ms"]):
            self._finish_goto("NOT_ARRIVED", "CMD_STOP")
            return None
        return self._goto

    def release(self) -> None:
        """押していない状態に戻す。**理由も出さない**（画面は `NONE` に戻る）。"""
        if self._goto is not None:
            self._finish_goto("NOT_ARRIVED", "CMD_STOP")
        self._axis = None
        self._speed = 0.0
        self._hold_at_ms = None
        self._owner = None

    # --- `aim`（ss-system）が使う口 ----------------------------------------------------------------

    @property
    def pitch_deg(self) -> float:
        """ピッチの**目標角**（実際の角度ではない。行き過ぎ中の値は出さない）。"""
        return self._pitch.target

    def pitch_arrived(self, now_ms: float) -> bool:
        """ピッチが目標角に着いたとみなしてよいか（戻りの待ちが過ぎたら真。`aim` の `ARRIVED` に使う）。"""
        return self._pitch.arrived(now_ms)

    def mark_motion(self) -> None:
        """ヨー・ピッチが動いた（目標が変わった）時刻を、いまにする（`X-Still-Ms`）。"""
        self.motion_at_ms = self._clock()

    async def set_pitch_target(self, angle_deg: float) -> None:
        """`aim` のピッチ目標角を置く（範囲は呼び出し側が先に確かめる）。

        **置いただけではサーボへ出さない。**片寄せの状態機械が行き過ぎ点・戻りを決め、
        `step()` が指令を出す（`mark_motion()` は指令が変わるたびに `step()` で数え直す）。
        """
        self._pitch.set_target(float(angle_deg), self._clock())
        self._pitch_reason = "NONE"

    async def drive_aim_yaw(self, signed_dps: float) -> None:
        """`aim` のヨーを回す（正が左）。0 なら止める。**画面の操作が無い間だけ効く**（`_step_axes`）。"""
        self._aim_yaw_dps = float(signed_dps)
        await self._apply_aim_yaw()

    async def stop_aim_yaw(self) -> None:
        """`aim` のヨーを止める。"""
        await self.drive_aim_yaw(0.0)

    async def _apply_aim_yaw(self) -> None:
        dps = self._aim_yaw_dps
        if dps == 0.0:
            await self._hw.stop_yaw()
            return
        self.mark_motion()
        direction = "yaw_left" if dps > 0 else "yaw_right"
        steps_per_s, turn = yaw_step_rate(abs(dps), direction, self._params)
        await self._hw.drive_yaw(steps_per_s, turn)

    def active_axis(self, now_ms: float) -> Optional[str]:
        """いま画面が押している軸（途絶えていれば `None`）。"""
        return self._active_axis(now_ms)[0]

    def io_ok(self, now_ms: float) -> bool:
        """Arduino から行が来ているか（`io_lost_ms`。まだ 1 行も来ていなければ偽）。"""
        return not self._io_lost(now_ms)

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
        await self._step_axes(axis, speed, dt_ms, now)

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
        """昇降の `hold`・`release` を送る。`press` は押し始めごとに 1 増やす。
        `lift_goto` が動いていれば `goto`／下降の `hold` を送る（§4.1）。"""
        goto = self._active_goto(now)
        if goto is not None:
            raw = self._lift.latest_state() or {}
            # 繋ぎ直し（接続の切り替わり・`state` の途絶え）→ 終わらせる。再開しない
            if self._lift_obj_prev is not None and self._lift is not self._lift_obj_prev:
                self._finish_goto("NOT_ARRIVED", "LINK_LOST")
            else:
                received = self._lift.state_received_at_ms()
                stale = received is None or (now - received) > float(self._params["lift_state_timeout_ms"])
                if stale and received is not None:
                    self._finish_goto("NOT_ARRIVED", "LINK_LOST")
            if goto is not None and self._goto is not None:
                grace = float(self._params.get("lift_goto_grace_ms", 1000))
                after_grace = (now - self._goto["started_ms"]) >= grace
                owner = raw.get("owner")
                if self._goto["mode"] == "target":
                    lift_goto_state = raw.get("goto") if isinstance(raw.get("goto"), dict) else {}
                    if (lift_goto_state.get("by") == self._params.get("module_name", "hve-cam")
                            and lift_goto_state.get("press") == self._goto["press"]
                            and lift_goto_state.get("status") in ("ARRIVED", "NOT_ARRIVED")):
                        status = lift_goto_state.get("status")
                        reason = lift_goto_state.get("reason") or "NONE"
                        self._finish_goto(status, reason)
                elif self._goto["mode"] == "bottom":
                    if raw.get("bottom") is True:
                        self._finish_goto("ARRIVED", "NONE")
                    elif after_grace and raw.get("dir") == "stop":
                        self._bottom_stop_count += 1
                        if self._bottom_stop_count >= 2:
                            self._finish_goto("NOT_ARRIVED", raw.get("reason") or "NONE")
                    else:
                        self._bottom_stop_count = 0
                if self._goto is not None and after_grace and owner is not None:
                    if owner != "module" or raw.get("owner_name") != self._params.get("module_name", "hve-cam"):
                        self._finish_goto("NOT_ARRIVED", "PREEMPTED")
        self._lift_obj_prev = self._lift
        if self._goto is not None:
            status, mm, age_ms = self._ceiling_triple(now)
            if self._goto["mode"] == "target":
                speed_mm_s = int(round(self._clamped_goto_speed(self._goto["speed"], "target")))
                await self._lift.send_goto(
                    self._goto["press"], int(self._goto["target_mm"]), speed_mm_s, status, mm, age_ms
                )
            else:
                speed_mm_s = int(round(self._clamped_goto_speed(self._goto["speed"], "bottom")))
                await self._lift.send_hold("down", speed_mm_s, self._goto["press"], status, mm, age_ms)
            self._lift_sending = True
            self._press_dir = "down" if self._goto["mode"] == "bottom" else "stop"
            self._press_owner = self._goto["owner"]
            return
        direction = _LIFT_DIRS.get(axis) if holding else None
        if direction is None:
            if self._lift_sending:
                await self._lift.send_release(self._press)
                self._lift_sending = False
                self._press_dir = "stop"
            # **止まっている間も天井の値を送る**（ほかの上部モジュールの上昇の判定に使う。
            # protocol-modules §2.3）。中身と古さの扱いは `hold` に載せるものと同じ。
            status, mm, age_ms = self._ceiling_triple(now)
            await self._lift.send_ceiling(status, mm, age_ms)
            return
        if (
            not self._lift_sending
            or direction != self._press_dir
            or self._owner is not self._press_owner
        ):
            # 押し始め（動かし始め・向きの入れ替え・別の画面の操作）
            self._press += 1
            self._press_owner = self._owner
        speed_mm_s = int(round(speed))
        status, mm, age_ms = self._ceiling_triple(now)
        await self._lift.send_hold(
            direction, speed_mm_s, self._press, status, mm, age_ms
        )
        self._lift_sending = True
        self._press_dir = direction

    def _clamped_goto_speed(self, speed: float, mode: str) -> float:
        """`lift_goto` の速さを昇降の設定に丸める。target は上昇・下降の両枠の和。"""
        if mode == "bottom":
            bounds = self._settings["lift_down"]
            return clamp_speed(speed, bounds)
        low = min(float(self._settings["lift_up"]["min"]), float(self._settings["lift_down"]["min"]))
        high = max(float(self._settings["lift_up"]["max"]), float(self._settings["lift_down"]["max"]))
        return min(max(speed, low), high)

    def _ceiling_triple(self, now_ms: float) -> tuple:
        """`hold` に載せる天井の `(状態, mm, 古さ)`。読み値が無ければ `READ_ERROR`。"""
        reading = self._ceiling
        if reading is None or reading.at_ms is None:
            return "READ_ERROR", None, 0
        age_ms = max(0, int(now_ms - reading.at_ms))
        if reading.status is CeilingStatus.MEASURED:
            return "MEASURED", reading.distance_mm, age_ms
        return reading.status.value, None, age_ms

    async def _step_axes(
        self, axis: Optional[str], speed: float, dt_ms: float, now: float
    ) -> None:
        """ピッチを積分して片寄せの状態機械へ、ヨーは押している間だけ回す。"""
        # 向きが分からない状態になったら（UNO の再起動・省電力から戻ったとき）、逆から来たとみなして片寄せする
        epoch = self._hw.yaw_epoch()
        if epoch != self._last_yaw_epoch:
            self._last_yaw_epoch = epoch
            self._pitch.force_unknown(now)
        mode = self._hw.get_power_mode()
        if mode != self._last_power_mode:
            was_saving = self._last_power_mode != POWER_ACTIVE
            self._last_power_mode = mode
            if was_saving and mode == POWER_ACTIVE:
                # 力を抜いて垂れているので、向きは分からない
                self._pitch.force_unknown(now)

        if axis in _PITCH_AXES:
            signed = speed if axis == "pitch_up" else -speed
            new_target, self._pitch_reason = pitch_step(
                self._pitch.target, signed, dt_ms, self._params
            )
            # 押している間は片寄せしない（目標をそのまま追う）
            self._pitch.ui_follow(new_target, now)
        else:
            self._pitch_reason = "NONE"
            if self._pitch_axis_holding:
                # 離したとき、最後の向きが逆なら 1 回だけ行き過ぎて戻る
                self._pitch.ui_release(now)
        self._pitch_axis_holding = axis in _PITCH_AXES

        # 片寄せの状態機械を回し、指令が変わったときだけサーボへ出す（そのたびに静止の時刻を数え直す）
        cmd = self._pitch.step(now)
        if cmd != self._last_pitch_cmd:
            self.mark_motion()
            await self._hw.set_pitch(cmd)
            self._last_pitch_cmd = cmd

        if axis in _YAW_AXES:
            steps_per_s, direction = yaw_step_rate(speed, axis, self._params)
            self.mark_motion()
            await self._hw.drive_yaw(steps_per_s, direction)
        elif self._aim_yaw_dps != 0.0:
            # `aim` が回している間は止めない（画面が押していない間だけここに来る）
            await self._apply_aim_yaw()
        else:
            # 離している間はコイルの電流を切る（stop_yaw の責務）
            await self._hw.stop_yaw()

    # --- 画面へ配る state ------------------------------------------------------------------------

    def build_state(self, clients: int) -> dict:
        """画面へ配る `state`（protocol §4）を組み立てる。**送信はしない。**"""
        now = self._clock()
        link_ok = self._lift.link_ok(now, float(self._params["lift_state_timeout_ms"]))
        raw = self._lift.latest_state() or {}

        raw_ceiling = raw.get("ceiling") if isinstance(raw.get("ceiling"), dict) else {}
        # 昇降部の判定は、**自分が持ち主**で昇降部が判定を返しているときだけ使う。
        # 持ち主は `owner_name` で見る（`fk`・`ss` が動かしている間の判定と取り違えない。
        # protocol-modules §5）。
        has_verdict = (
            link_ok
            and raw.get("owner") == "module"
            and raw.get("owner_name") == self._params.get("module_name", "hve-cam")
            and "reason" in raw_ceiling
            and "ok" in raw_ceiling
        )
        lift_ceiling = raw_ceiling if has_verdict else {}
        if not link_ok:
            ceiling_ok, ceiling_reason = False, "LINK_LOST"
        elif has_verdict:
            ceiling_ok = bool(raw_ceiling["ok"])
            ceiling_reason = raw_ceiling["reason"]
        else:
            ceiling_ok, ceiling_reason = None, None

        status, mm, age_ms = self._ceiling_triple(now)
        return {
            "t": "state",
            "lift": {
                "link": "ok" if link_ok else "lost",
                "dir": raw.get("dir", "stop"),
                "duty": raw.get("duty", 0),
                "speed": raw.get("speed"),
                "speed_meas": raw.get("speed_meas"),
                "reason": raw.get("reason", "CMD_STOP"),
                "bottom": bool(raw.get("bottom", False)),
                "height_mm": raw.get("height_mm"),
                "height_ok": bool(raw.get("height_ok", False)),
                "top_detect": bool(raw.get("top_detect", False)),
                "owner": raw.get("owner"),
                "owner_name": raw.get("owner_name"),
                "owner_operator": raw.get("owner_operator"),
                "ui_clients": raw.get("ui_clients", 0),
                #: 昇降部の画面・物理ボタンが天井を使うか・使わない理由（`USE`／`SETTING_OFF`／`NO_SOURCE`。
                #: 無いか知らない値なら `None`。protocol-modules §2.5）
                "ceiling_standalone": raw_ceiling.get("standalone"),
                #: 昇降部の IP（画面に昇降部へのリンクを出す。§4.6）
                "lift_ip": getattr(self._lift, "current_host", None),
            },
            #: 繋がっている上部モジュール（持ち主の `busy_label` を画面が引く。§1.6）
            "modules": raw.get("modules", []),
            #: 測定機構。`out` が `true` のとき画面に「腕が出ています」を出す（Spec-ui §1.6）
            "arm": raw.get("arm"),
            "ceiling": {
                "status": status,
                "mm": mm,
                "age_ms": age_ms,
                "ok": ceiling_ok,
                "reason": ceiling_reason,
            },
            "pitch_deg": round(self._pitch.target, 3),
            "zoom": self._zoom,
            # 画面は映像（hve_video）の URL を組み立てるのにこのポートが要る（protocol §2.4）
            "video_port": int(self._params["video_port"]),
            #: 「目標の高さへ」「下端へ」の最後の状態（§4.1）。無いときは `None`
            "lift_goto": dict(self._goto_record) if self._goto_record is not None else None,
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
