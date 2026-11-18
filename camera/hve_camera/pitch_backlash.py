"""ピッチの遊びの片寄せ（`PitchBacklash`）。

[DetailedDesign.md](../docs/plan/detailed/DetailedDesign.md) §4.3.1・spec
[Spec-ui.md](../docs/plan/spec/Spec-ui.md) §1.3。**報告する目標角と、サーボへ出す指令を分ける。**

- 報告する目標角（`target`）は `state.pitch_deg`・`X-Pitch-Deg` に出す。**行き過ぎ中の値は出さない。**
- サーボへ出す指令（`command`）だけをこの状態機械が決める。`control.py` の周期で `step()` を回し、
  返った指令が変わったら `hw.set_pitch()` へ出す（**`uno_hw` の送信スレッドには入れない**。ホストで試験できないため）。
- 近づく向きは `pitch_approach_sign`（-1 なら値が減る向き）。**正しい向きから来たら目標をそのまま出す。**
  **逆の向きから来たら、行き過ぎ点 `目標 − sign × pitch_backlash_deg` を出し、着くのを待ってから目標を出す。**
  行き過ぎ点は `pitch_min_deg`〜`pitch_max_deg` に丸める（範囲の端が目標のときは行き過ぎ点が目標と同じになり、
  そのまま出す＝片寄せできない）。
- **画面のボタンは押している間は片寄せしない**（目標をそのまま追う）。離したとき、最後に動いた向きが逆なら
  **1 回だけ**行き過ぎて戻る。
- **向きが分からないときは逆から来たとみなして片寄せする**（起動・UNO の再起動・省電力から戻ったとき。
  `force_unknown()` を呼ぶ側が決める）。**このときサーボの実際の位置は分からないので、行き過ぎ点への待ちは
  可動範囲の全幅（`pitch_max_deg − pitch_min_deg`）を動くとして見積もる**（普通の逆向きの片寄せは今の指令からの差）。

**時刻は引数で受ける純関数のクラス。**ハードウェアにも時計にも触らない（ホストで試験する）。

## 待ち時間の見積もり（`arrived()` の根拠）

動く角度から見積もる: `pitch_backlash_settle_ms` ＋ 動く角度 ÷ `pitch_servo_dps`。行き過ぎ点へも戻りも同じ式。
**正しい向きから来たときも同じ式で待つ**（[-protocol-ss.md](../docs/plan/detailed/DetailedDesign-protocol-ss.md) §2.1
「正しい向きから来たときも見積もりだけ待つ」。`settle` の固定分は表の式をそのまま使う）。
`aim` の `ARRIVED` は、戻りの指令を出してからこの待ち時間が過ぎたときに `arrived()` が真になることで決める
（実際の角度は読めない）。
"""

from __future__ import annotations

from typing import Any, Mapping

#: 片寄せの途中の段階。`idle` は行き過ぎていない／終わった。`overshoot` は行き過ぎ点へ向かう待ちの間。
#: `return` は目標角へ戻る待ちの間（正しい向きで直接向かう場合もここ）。
_IDLE = "idle"
_OVERSHOOT = "overshoot"
_RETURN = "return"


class PitchBacklash:
    """目標角とサーボ指令を分けて持つ、ピッチの遊びの片寄せの状態機械。"""

    def __init__(self, params: Mapping[str, Any]) -> None:
        sign = int(params["pitch_approach_sign"])
        if sign not in (-1, 1):
            raise ValueError("pitch_approach_sign は -1 か 1 でない: %r" % (sign,))
        self._sign = sign
        self._backlash_deg = float(params["pitch_backlash_deg"])
        self._settle_ms = float(params["pitch_backlash_settle_ms"])
        self._dps = float(params["pitch_servo_dps"])
        if self._dps <= 0:
            raise ValueError("pitch_servo_dps は正でない: %r" % (self._dps,))
        self._min_deg = float(params["pitch_min_deg"])
        self._max_deg = float(params["pitch_max_deg"])

        #: 報告する目標角（`state.pitch_deg`・`X-Pitch-Deg` に出す）。行き過ぎ中の値は入らない
        self._target = 0.0
        #: サーボへ出す指令。`step()` が返す
        self._command = 0.0
        self._phase = _IDLE
        #: 今の段階の待ちが終わる時刻（`step()` がこの時刻以降で次へ進む）
        self._leg_done_at_ms: float | None = None
        #: 戻りの指令を出してからの待ちが終わる時刻（`arrived()` の判定に使う）
        self._arrive_at_ms: float | None = None
        #: 最後に指令が変わった向き（+1 は値が増える向き、-1 は減る向き）。まだ動いていなければ `None`
        self._last_direction: int | None = None
        #: 向きが分からないか（起動したときは真。1 回動かせば分かる）
        self._unknown = True
        #: 画面のボタンでピッチを押しているか（押している間は片寄せしない）
        self._holding = False

    # --- 見る口 --------------------------------------------------------------------------------------

    @property
    def target(self) -> float:
        """報告する目標角。"""
        return self._target

    @property
    def command(self) -> float:
        """サーボへ出す指令（今の値）。"""
        return self._command

    def arrived(self, now_ms: float) -> bool:
        """目標角に着いたとみなしてよいか。

        行き過ぎ点へ向かう間は偽。戻りの指令を出してから見積もった待ち時間が過ぎたら真。
        動いていない（`idle`）ときは真。
        """
        if self._phase == _IDLE:
            return True
        if self._phase == _OVERSHOOT:
            return False
        return self._arrive_at_ms is not None and float(now_ms) >= self._arrive_at_ms

    # --- 目標を置く口 ----------------------------------------------------------------------------------

    def set_target(self, angle_deg: float, now_ms: float) -> None:
        """`aim` の目標角を置く（**置いただけではサーボは出さない**。`step()` が出す）。

        今の指令からの向きで決める（片寄せの途中で目標が変わったら、今の指令から新しい目標へ同じ規則でやり直す）。
        向きが分からないときは**逆から来たとみなして**片寄せする（目標が今の指令と同じでも行き過ぎて戻る）。
        """
        angle = float(angle_deg)
        if angle == self._target and not self._unknown:
            return  # 同じ目標を置き直してもやり直さない（`aim` の送り続け）
        self._target = angle
        if self._holding:
            # 押している間は片寄せしない（`ui_follow()` がそのまま追う）
            self._phase = _IDLE
            self._arrive_at_ms = None
            return
        self._plan(float(now_ms), assume_reverse=self._unknown)

    def ui_follow(self, angle_deg: float, now_ms: float) -> None:
        """画面のボタンを押している間、目標をそのまま追う（片寄せしない）。"""
        angle = float(angle_deg)
        self._target = angle
        self._holding = True
        self._phase = _IDLE
        self._arrive_at_ms = None
        if angle != self._command:
            self._last_direction = 1 if angle > self._command else -1
            self._unknown = False
            self._command = angle

    def ui_release(self, now_ms: float) -> None:
        """画面のボタンを離した。**最後に動いた向きが逆なら 1 回だけ**行き過ぎて戻る。"""
        self._holding = False
        if self._last_direction is None or self._last_direction == self._sign:
            return  # 正しい向きから来た（または動いていない）ので、そのまま
        self._plan(float(now_ms), assume_reverse=True)

    def force_unknown(self, now_ms: float) -> None:
        """向きが分からない状態に戻し、今の目標へ逆から来たとみなして片寄せする。

        起動・UNO の再起動（`yaw_epoch` の変化）・省電力から戻ったとき（力を抜いて垂れている）に呼ぶ。
        **サーボの実際の位置は分からない**（UNO の再起動では 0° へ跳び、省電力では垂れている）ので、
        行き過ぎ点への待ちは**可動範囲の全幅**を動くとして見積もる。
        """
        self._unknown = True
        if self._holding:
            return  # 押している間は片寄せしない（離したときの規則で扱う）
        self._plan(float(now_ms), assume_reverse=True, full_span=True)

    # --- 周期 ----------------------------------------------------------------------------------------

    def step(self, now_ms: float) -> float:
        """1 周期分の判定。今のサーボ指令を返す（変わっていなければ同じ値を返す）。"""
        now = float(now_ms)
        if self._phase == _OVERSHOOT and self._leg_done_at_ms is not None and now >= self._leg_done_at_ms:
            # 行き過ぎ点に着いた。目標角を出す（待ちは戻りの指令を出した時刻から数える）
            wait = self._wait_ms(self._target, self._command)
            self._set_command(self._target)
            self._phase = _RETURN
            self._leg_done_at_ms = now + wait
            self._arrive_at_ms = self._leg_done_at_ms
        elif self._phase == _RETURN and self._leg_done_at_ms is not None and now >= self._leg_done_at_ms:
            self._phase = _IDLE
            self._leg_done_at_ms = None
            self._arrive_at_ms = None
        return self._command

    # --- 中身 ----------------------------------------------------------------------------------------

    def _plan(self, now_ms: float, assume_reverse: bool, full_span: bool = False) -> None:
        """今の指令から目標へ向かう計画を立てる。指令はすぐ置く（送るのは `step()` の呼び出し側）。

        `full_span` は行き過ぎ点への待ちを可動範囲の全幅を動くとして見積もる（向きが分からないとき）。
        """
        target = self._target
        if not assume_reverse and self._command != target:
            direction = 1 if target > self._command else -1
            if direction == self._sign:
                assume_reverse = False
            else:
                assume_reverse = True
        if assume_reverse:
            overshoot = self._clamp(target - self._sign * self._backlash_deg)
            if overshoot != target:
                if full_span:
                    wait = self._settle_ms + (self._max_deg - self._min_deg) / self._dps * 1000.0
                else:
                    wait = self._wait_ms(overshoot, self._command)
                self._set_command(overshoot)
                self._phase = _OVERSHOOT
                self._leg_done_at_ms = now_ms + wait
                # 着く時刻は戻りの指令を出したときに決まる（`step()`）
                self._arrive_at_ms = None
                self._unknown = False
                return
            # 範囲の端が目標のときは行き過ぎ点が目標と同じ。片寄せできないのでそのまま出す
        if self._command != target:
            wait = self._wait_ms(target, self._command)
            self._set_command(target)
            self._phase = _RETURN
            self._leg_done_at_ms = now_ms + wait
            self._arrive_at_ms = self._leg_done_at_ms
            self._unknown = False
        else:
            self._phase = _IDLE
            self._leg_done_at_ms = None
            self._arrive_at_ms = None

    def _set_command(self, angle: float) -> None:
        """指令を置く。向きを覚える（動いたら向きは分かる）。"""
        if angle != self._command:
            self._last_direction = 1 if angle > self._command else -1
            self._unknown = False
            self._command = angle

    def _wait_ms(self, to_deg: float, from_deg: float) -> float:
        """動く角度から見積もった待ち時間（`settle` ＋ 角度 ÷ 速さ）。"""
        return self._settle_ms + abs(float(to_deg) - float(from_deg)) / self._dps * 1000.0

    def _clamp(self, angle: float) -> float:
        """行き過ぎ点を可動範囲に収める。"""
        return min(max(float(angle), self._min_deg), self._max_deg)
