"""`PitchBacklash` の試験（`WP-CAM-16`）。

[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §4.3.1。
報告する目標角（`target`）とサーボへ出す指令（`command`）が分かれていること、
行き過ぎ・戻りの待ち時間・向きが分からないときの扱いを確かめる。
**呼び出し側（`control.py`・`module_port.py`）を通した試験は `test_control.py`・`test_module_port.py` にある。**
"""

from __future__ import annotations

from hve_camera.pitch_backlash import PitchBacklash
from tests.cam_support import PARAMS


def make() -> PitchBacklash:
    return PitchBacklash(PARAMS)


def test_approach_sign_and_value_are_read_from_params() -> None:
    assert PARAMS["pitch_approach_sign"] == -1
    assert PARAMS["pitch_backlash_deg"] == 3
    assert PARAMS["pitch_backlash_settle_ms"] == 300
    assert PARAMS["pitch_servo_dps"] == 200


def test_correct_direction_goes_straight_to_the_target() -> None:
    """正しい向き（`pitch_approach_sign` -1＝減る向き）から来たら、そのまま出す。"""
    pitch = make()
    pitch.set_target(-10, 0)  # 起動時は向きが分からないので逆とみなす → 行き過ぎ（-7）へ
    assert pitch.target == -10
    assert pitch.command == -7

    pitch2 = make()
    pitch2.set_target(10, 0)  # 0 から +10 は逆の向き（増える向き）→ 行き過ぎ点は 13
    assert pitch2.command == 13


def test_reverse_direction_overshoots_then_returns() -> None:
    pitch = make()
    pitch.set_target(10, 0)
    assert pitch.command == 13  # 行き過ぎ点（10 − (-1) × 3）
    assert pitch.target == 10  # 報告する目標角は行き過ぎ点でない
    assert not pitch.arrived(0)
    # 行き過ぎ点への待ち: 300 + 13/200×1000 = 365
    assert pitch.step(364) == 13
    assert pitch.step(365) == 10  # 戻りの指令
    assert not pitch.arrived(365)
    # 戻りの待ち: 300 + 3/200×1000 = 315。365 + 315 = 680
    assert not pitch.arrived(679)
    assert pitch.arrived(680)
    assert pitch.step(680) == 10


def test_overshoot_is_clamped_to_the_range() -> None:
    """行き過ぎ点を可動範囲（-45〜45）に丸める。端が目標のときは片寄せできない。"""
    pitch = make()
    pitch.set_target(-44, 0)  # 行き過ぎ点は -41。範囲内
    assert pitch.command == -41

    pitch2 = make()
    pitch2.set_target(44, 0)  # 行き過ぎ点 47 → 45 に丸める（目標 44 ではないので片寄せする）
    assert pitch2.command == 45
    assert pitch2.target == 44

    pitch3 = make()
    pitch3.set_target(-45, 0)  # 行き過ぎ点 -42。範囲内
    assert pitch3.command == -42

    # 範囲の端が目標のとき（行き過ぎ点が目標と同じ）はそのまま出す
    pitch4 = make()
    pitch4.ui_follow(45, 0)  # 指令 45 に置く（向き分かる）
    pitch4.ui_release(0)  # 増える向きで来たが、行き過ぎ点は 45 で目標と同じ
    assert pitch4.command == 45
    assert pitch4.arrived(0)


def test_unknown_direction_assumes_reverse_even_for_the_same_target() -> None:
    """起動したときは向きが分からない。目標が今の指令と同じでも行き過ぎて戻る。"""
    pitch = make()
    pitch.set_target(0, 0)
    assert pitch.command == 3  # 行き過ぎ点（0 − (-1) × 3）
    assert not pitch.arrived(0)

    pitch2 = make()
    pitch2.force_unknown(100)
    assert pitch2.command == 3


def test_same_target_is_not_redone_once_direction_is_known() -> None:
    """同じ目標を送り続けてもやり直さない（`aim` の送り続け）。"""
    pitch = make()
    pitch.set_target(10, 0)
    pitch.step(1000)  # 行き過ぎ点への待ちが過ぎたので戻りの指令を出す（着くのは 1315）
    assert not pitch.arrived(1000)
    assert pitch.arrived(1315)
    pitch.set_target(10, 2000)  # 同じ目標。向き分かったあとなので何もしない
    assert pitch.command == 10
    assert pitch.arrived(2000)


def test_ui_hold_follows_directly_and_release_backlashes_once() -> None:
    """押している間は片寄せしない。離したとき最後の向きが逆なら 1 回だけ。"""
    pitch = make()
    pitch.ui_follow(5, 0)
    assert pitch.command == 5  # 増える向き。でも押している間はそのまま追う
    pitch.ui_follow(10, 100)
    assert pitch.command == 10
    pitch.ui_release(200)
    assert pitch.command == 13  # 1 回だけ行き過ぎて戻る
    assert pitch.step(200 + 300 + 3 / 200 * 1000) == 10

    pitch2 = make()
    pitch2.ui_follow(-5, 0)  # 減る向き（正しい向き）
    pitch2.ui_release(100)
    assert pitch2.command == -5  # 逆でないので何もしない
    assert pitch2.arrived(100)


def test_force_unknown_uses_the_full_span_for_the_overshoot_wait() -> None:
    """向きが分からないときは可動範囲の全幅（90°）を動くとして見積もる（指令からの差 3° でない）。"""
    pitch = make()
    pitch.force_unknown(0)
    assert pitch.command == 3
    # 行き過ぎ点への待ち: 300 + 90/200×1000 = 750
    assert pitch.step(749) == 3
    assert pitch.step(750) == 0  # 戻りの指令
    assert not pitch.arrived(750)
    assert pitch.arrived(750 + 300 + 3 / 200 * 1000)  # 戻りの待ちは今の式


def test_target_change_mid_backlash_redoes_from_the_current_command() -> None:
    pitch = make()
    pitch.set_target(10, 0)
    assert pitch.command == 13
    pitch.set_target(-10, 100)  # 今の指令 13 から -10 は減る向き（正しい）→ そのまま
    assert pitch.target == -10
    assert pitch.command == -10


def test_bad_params_are_rejected() -> None:
    bad = dict(PARAMS)
    bad["pitch_servo_dps"] = 0
    try:
        PitchBacklash(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("pitch_servo_dps 0 を受けてはいけない")
    bad = dict(PARAMS)
    bad["pitch_approach_sign"] = 0
    try:
        PitchBacklash(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("pitch_approach_sign 0 を受けてはいけない")
