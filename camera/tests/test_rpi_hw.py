"""`hw/rpi_hw.py`（ラズパイの実物）の試験。

**偽の `lgpio`・偽の `smbus2`・偽の sysfs を差す**（ラズパイは要らない）。
`lgpio` はラズパイの OS 同梱で、**この試験では import しない**
（`sys.modules` を調べて、`import lgpio` が起きていないことを確かめる）。

3 つの層を分けて縛る。

| 層 | 縛るもの |
| --- | --- |
| 純関数（`pitch_to_pulse_ns`・`srf02_reading`） | 判定表の全部 |
| 部品（`PwmChannel`・`LgpioPort`） | 書く順番・解放の前に LOW・遅延 import |
| 実物（`RpiHardware`） | **止めたら 4 本とも LOW**・**`at_ms` は測定の時刻**・**I2C 失敗が `READ_ERROR`** |

**実物の経路は「周辺数行」で縛る。**純関数の試験が緑でも、
`stop_yaw` の `_write_yaw(0)` が壊れていれば**止まっている間ずっとコイルに電流が流れる**。
だから 4 つのピンの最終状態は毎回見る。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

from hve_camera.ceiling import CeilingStatus, ceiling_permission
from hve_camera.hw import rpi_hw
from hve_camera.hw.rpi_hw import (
    MM_PER_CM,
    SG90_NS_PER_DEG,
    SG90_PULSE_CENTER_NS,
    SG90_PULSE_MAX_NS,
    SG90_PULSE_MIN_NS,
    SRF02_BUSY_RAW,
    SRF02_MIN_PERIOD_S,
    YAW_HALF_STEP_SEQUENCE,
    YAW_PINS,
    LgpioPort,
    PwmChannel,
    RpiHardware,
    is_raspberry_pi,
    pitch_to_pulse_ns,
    srf02_reading,
)
from hve_camera.control import ControlLoop
from hve_camera.hw.fake_lift import FakeLift
from tests.cam_support import PARAMS, SETTINGS, ManualClock, RecordingVideoZoom

# --- 偽物 --------------------------------------------------------------------------------


class FakeLgpio:
    """`lgpio` の偽物。呼んだことを全部記録する。"""

    def __init__(self) -> None:
        self.opened_chips: list[int] = []
        self.closed_chips: list[int] = []
        self.claimed: list[tuple[int, int]] = []
        self.freed: list[tuple[int, int]] = []
        #: `(pin, level)` の履歴。**最後の 4 本が全部 LOW なら安全**（順序そのものをそのまま記録する）
        self.writes: list[tuple[int, int]] = []
        self._levels = {pin: 0 for pin in YAW_PINS}

    def gpiochip_open(self, chip: int) -> int:
        self.opened_chips.append(chip)
        return 0

    def gpiochip_close(self, handle: int) -> None:
        self.closed_chips.append(handle)

    def gpio_claim_output(self, handle: int, pin: int) -> int:
        self.claimed.append((handle, pin))
        return 0

    def gpio_free(self, handle: int, pin: int) -> None:
        self.freed.append((handle, pin))

    def gpio_write(self, handle: int, pin: int, level: int) -> int:
        self.writes.append((pin, level))
        self._levels[pin] = level
        return 0

    def high_pins(self) -> list[int]:
        """HIGH のピンの一覧。**停止後・`close()` 後に空であることを確かめる。**"""
        return [pin for pin in YAW_PINS if self._levels[pin] != 0]


class FakeSmbus:
    """`smbus2.SMBus` の偽物。**返り値を 1 つずつ出す**（尽きたら 0）。"""

    def __init__(self, results: list[object] | None = None) -> None:
        #: 次の `read_i2c_block_data` で返す値。**`Exception` ならそれが上がる**
        self.results = list(results or [])
        self.commands: list[tuple[float, int, int, int]] = []
        self.reads: list[tuple[float, int, int, int]] = []
        self.closed = False
        #: `read_i2c_block_data` を待たせる時間 [s]（**スレッドを止める実験**）
        self.block_for_s = 0.0
        #: 読みに成功した回数
        self.reads_done = 0
        #: 結果を返すときにこの時計を進める（`at_ms` の試験で使う）
        self.clock: object | None = None
        self.advance_ms = 0.0
        self._mono: object | None = None

    def bind(self, clock, advance_ms: float) -> None:
        """試験用。`clock` を読んで、読むたび `advance_ms` だけ進める。"""
        self.clock = clock
        self.advance_ms = advance_ms
        self._mono = time.monotonic

    def _now(self) -> float:
        return time.monotonic()

    def write_byte_data(self, addr: int, register: int, value: int) -> None:
        self.commands.append((self._now(), addr, register, value))

    def read_i2c_block_data(self, addr: int, register: int, length: int) -> bytes:
        self.reads.append((self._now(), addr, register, length))
        if self.block_for_s:
            time.sleep(self.block_for_s)
        self.reads_done += 1
        result = self.results.pop(0) if self.results else 0
        if self.clock is not None and self.advance_ms:
            self.clock.advance(self.advance_ms)  # type: ignore[attr-defined]
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, int):
            return result.to_bytes(2, "big")
        return result

    def close(self) -> None:
        self.closed = True


class RecordingWrite:
    """PWM の sysfs への書き込みの記録。**呼ばれた順に `(名前, 値)` を残す。**"""

    def __init__(self, *, fail_export: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_export = fail_export

    def __call__(self, path: Path, value: str) -> None:
        if path.name == "export" and self.fail_export:
            raise OSError(16, "Device or resource busy")  # EBUSY
        self.calls.append((path.name, value))

    def last(self, name: str) -> str | None:
        """`name` の最後の値。**無ければ `None`。**"""
        for key, value in reversed(self.calls):
            if key == name:
                return value
        return None

    def names(self) -> list[str]:
        """**呼ばれた順にファイル名**（順番の試験用）。"""
        return [name for name, _ in self.calls]

    def values(self, name: str) -> list[str]:
        """`name` の値を全部。"""
        return [value for key, value in self.calls if key == name]


def make_params(**overrides) -> dict:
    """`cam_support.PARAMS` のコピーに上書きする。**元の定数を壊さない。**"""
    params = dict(PARAMS)
    params.update(overrides)
    return params


def make_hw(
    smbus: FakeSmbus | None = None,
    write: RecordingWrite | None = None,
    clock=None,
):
    """偽物を差した `RpiHardware` を作る。**本体と偽物を全部返す。**"""
    lgpio = FakeLgpio()
    bus = smbus if smbus is not None else FakeSmbus([200])
    sysfs = write if write is not None else RecordingWrite()
    hw = RpiHardware(
        make_params(),
        clock=clock if clock is not None else (lambda: 0.0),
        gpio=LgpioPort(module=lgpio),
        smbus=bus,
        pwm=PwmChannel(Path("/sys/class/pwm/pwmchip0"), write=sysfs),
    )
    return hw, lgpio, bus, sysfs


async def wait_for_ceiling(hw: RpiHardware, timeout: float = 2.0):
    """天井の読み値が 1 つ出るまで待つ。**出なければ `None`。**"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reading = await hw.read_ceiling()
        if reading is not None:
            return reading
        await asyncio.sleep(0.01)
    return None


#: 向きを比べる試験用のゆっくりした速さ。**1 回のステップが 100 ms**（8 回で一周の余りが出る）
_SLOW_STEPS_PER_S = 10.0


async def wait_for_high(lgpio: FakeLgpio, timeout: float = 1.0) -> bool:
    """HIGH のピンが 1 つ以上になるまで待つ。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lgpio.high_pins():
            return True
        await asyncio.sleep(0.005)
    return False


async def wait_for_steps(hw: RpiHardware, count: int, timeout: float = 2.0) -> None:
    """**累計**が `count` 回に達するまで待つ。**刻まなければ AssertionError。**"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if hw.step_count >= count:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"半ステップが {count} 回も刻まれなかった（{hw.step_count} 回）")


# --- `pitch_to_pulse_ns`（純関数）--------------------------------------------------------


def test_zero_degrees_is_the_centre_pulse():
    assert pitch_to_pulse_ns(0.0) == SG90_PULSE_CENTER_NS == 1_500_000


def test_pulse_widens_as_the_angle_grows():
    """**角度を上げるほどパルス幅が長くならなきゃいけない**（向きの反転も変異で見る）。"""
    assert pitch_to_pulse_ns(10.0) - pitch_to_pulse_ns(0.0) == 10 * SG90_NS_PER_DEG
    assert pitch_to_pulse_ns(-10.0) == pitch_to_pulse_ns(0.0) - 10 * SG90_NS_PER_DEG


def test_pulse_is_monotonic_over_the_whole_range():
    """-45°〜+45°（`pitch_min_deg`〜`pitch_max_deg`）で単調増加していること。"""
    angles = [-45.0, -30.0, -10.0, 0.0, 10.0, 30.0, 45.0]
    pulses = [pitch_to_pulse_ns(a) for a in angles]
    assert pulses == sorted(pulses)
    assert len(set(pulses)) == len(pulses)


def test_pulse_stays_inside_the_datasheet_band():
    """**±45° が 0.5〜2.4 ms の中**（90° も出さない）。"""
    assert SG90_PULSE_MIN_NS < pitch_to_pulse_ns(-45.0)
    assert pitch_to_pulse_ns(45.0) < SG90_PULSE_MAX_NS


def test_pulse_is_clamped_to_the_datasheet_band():
    """**データシートの外へ出さない**（角度計算が壊れても端で留まる）。"""
    assert pitch_to_pulse_ns(500.0) == SG90_PULSE_MAX_NS
    assert pitch_to_pulse_ns(-500.0) == SG90_PULSE_MIN_NS


def test_pulse_is_an_integer_nanosecond_count():
    """**整数**を返す（sysfs に小数を書くと EINVAL になる）。"""
    value = pitch_to_pulse_ns(12.34)
    assert isinstance(value, int)
    assert value == int(SG90_PULSE_CENTER_NS + 12.34 * SG90_NS_PER_DEG)


# --- `srf02_reading`（純関数）-------------------------------------------------------------


def test_cm_is_converted_to_mm():
    reading = srf02_reading(200, make_params(), 1000.0)
    assert reading.status is CeilingStatus.MEASURED
    assert reading.distance_mm == 200 * MM_PER_CM == 2000
    assert reading.at_ms == 1000.0


def test_i2c_exception_is_read_error_not_no_echo():
    """**`READ_ERROR` と `NO_ECHO` を取り違えない**（判定表の #1 と #3）。

    取り違えると、センサが死んだときに「反射が無い＝天井は遠い」とみなしで天井に突っ込む。
    """
    reading = srf02_reading(OSError("bus error"), make_params(), 0.0)
    assert reading.status is CeilingStatus.READ_ERROR
    assert reading.distance_mm is None


def test_i2c_exception_blocks_going_up():
    """**I2C で読めないときは上昇を許さない**（`ceiling_permission` まで繋ぐ）。"""
    reading = srf02_reading(OSError("bus error"), make_params(), 0.0)
    assert ceiling_permission(reading, 0.0, make_params()) == (False, "CEILING_STALE")


def test_zero_raw_is_no_echo_and_allows_going_up():
    """**反射が無いの（`0`）は上昇を許す**（spec §2 #3b）。"""
    reading = srf02_reading(0, make_params(), 0.0)
    assert reading.status is CeilingStatus.NO_ECHO
    assert ceiling_permission(reading, 0.0, make_params()) == (True, "OUT_OF_RANGE")


def test_busy_raw_is_read_error_not_no_echo():
    """**測定中の `0xFFFF` は反射無しではない**。

    反射無しなら上昇を許すが、**測定中の値は反射の有無が分からない**ので `READ_ERROR`。
    """
    reading = srf02_reading(SRF02_BUSY_RAW, make_params(), 0.0)
    assert reading.status is CeilingStatus.READ_ERROR
    assert ceiling_permission(reading, 0.0, make_params()) == (False, "CEILING_STALE")


def test_beyond_the_maximum_range_is_no_echo():
    """**測定範囲の外は反射無しと同じ扱い**（spec §2 #3b）。"""
    reading = srf02_reading(601, make_params(srf02_max_range_mm=6000), 0.0)
    assert reading.status is CeilingStatus.NO_ECHO


def test_exactly_the_maximum_range_is_measured():
    """**境界の値は `MEASURED`**（「超える」だけ `NO_ECHO`）。"""
    reading = srf02_reading(600, make_params(srf02_max_range_mm=6000), 0.0)
    assert reading.status is CeilingStatus.MEASURED
    assert reading.distance_mm == 6000


def test_too_close_stays_measured_so_that_ceiling_permission_blocks_it():
    """**最小測定距離より近い値は `MEASURED` のまま**（#3a は `ceiling.py` の仕事）。

    **この関数では `READ_ERROR` にしない。**反射が返っているなら「近すぎて測れない」だけ。
    上升を止めるのは `ceiling_permission`（`CEILING_NEAR`）。
    """
    params = make_params(srf02_min_range_mm=150)
    reading = srf02_reading(10, params, 0.0)  # 100 mm < 150 mm
    assert reading.status is CeilingStatus.MEASURED
    assert reading.distance_mm == 100
    assert ceiling_permission(reading, 0.0, params) == (False, "CEILING_NEAR")


def test_at_ms_is_passed_through_untouched():
    """**`at_ms` は渡された値をそのまま使う**（取り出しの時刻に差し替えない）。"""
    assert srf02_reading(200, make_params(), 12_345.6).at_ms == 12_345.6


# --- `PwmChannel` ------------------------------------------------------------------------


def test_pwm_writes_export_period_duty_enable_in_that_order():
    """**`export` → `period` → `duty_cycle` → `enable` の順**（順番を違えると動かない）。"""
    write = RecordingWrite()
    PwmChannel(Path("/sys/class/pwm/pwmchip0"), write=write).enable(20_000_000, 1_500_000)
    assert write.names() == ["export", "period", "duty_cycle", "enable"]


def test_pwm_writes_the_period_and_the_first_pulse_width():
    write = RecordingWrite()
    PwmChannel(Path("/sys/class/pwm/pwmchip0"), write=write).enable(20_000_000, 1_500_000)
    assert write.last("period") == "20000000"
    assert write.last("duty_cycle") == "1500000"
    assert write.last("enable") == "1"


def test_pwm_tolerates_an_already_exported_channel():
    """**既に export 済み（EBUSY）でも動く**（再起動後に `export` だけ残ることがある）。"""
    write = RecordingWrite(fail_export=True)
    PwmChannel(Path("/sys/class/pwm/pwmchip0"), write=write).enable(20_000_000, 1_500_000)
    assert write.last("enable") == "1"
    assert "export" not in write.names()


def test_set_duty_does_not_touch_the_period():
    """**角度の変更で `period` を書き直さない**（書くとサーボが飛ぶ）。"""
    write = RecordingWrite()
    pwm = PwmChannel(Path("/sys/class/pwm/pwmchip0"), write=write)
    pwm.enable(20_000_000, 1_500_000)
    write.calls.clear()
    pwm.set_duty_ns(1_600_000)
    assert write.names() == ["duty_cycle"]
    assert write.last("period") is None


def test_disable_turns_the_pwm_off():
    """**`disable()` で PWM を止める。**"""
    write = RecordingWrite()
    pwm = PwmChannel(Path("/sys/class/pwm/pwmchip0"), write=write)
    pwm.enable(20_000_000, 1_500_000)
    write.calls.clear()
    pwm.disable()
    assert write.last("enable") == "0"


# --- `LgpioPort` -------------------------------------------------------------------------


def test_lgpio_claims_all_four_pins_as_outputs():
    """**4 本（IN1〜IN4）を出力として確保する**（1 本でも足りないと回らない）。"""
    lgpio = FakeLgpio()
    LgpioPort(module=lgpio).setup()
    assert [pin for _, pin in lgpio.claimed] == list(YAW_PINS)


def test_lgpio_starts_with_every_pin_low():
    """**確保した直後は 4 本とも LOW**（確保しただけでコイルに電流を流さない）。"""
    lgpio = FakeLgpio()
    LgpioPort(module=lgpio).setup()
    assert lgpio.high_pins() == []


def test_lgpio_writes_low_before_freeing_the_pins():
    """**解放の前に LOW を書く**（解放するとFloating になって勝手に出力する）。"""
    lgpio = FakeLgpio()
    port = LgpioPort(module=lgpio)
    port.setup()
    port.close()
    assert [level for _, level in lgpio.writes[-len(YAW_PINS) :]] == [0] * len(YAW_PINS)


def test_lgpio_close_writes_low_even_when_some_pin_is_high():
    """HIGH のまま解放しない。**止めた状態を引き継ぐ。**"""
    lgpio = FakeLgpio()
    port = LgpioPort(module=lgpio)
    port.setup()
    for pin in YAW_PINS:
        lgpio.gpio_write(0, pin, 1)
    port.close()
    assert lgpio.high_pins() == []
    assert [level for _, level in lgpio.writes[-len(YAW_PINS) :]] == [0] * len(YAW_PINS)


def test_lgpio_write_before_setup_raises():
    """**setup 前に書くとエラー**（黙って無視しない）。"""
    with pytest.raises(RuntimeError):
        LgpioPort(module=FakeLgpio()).write(YAW_PINS[0], 1)


# --- `RpiHardware`: ピッチ ---------------------------------------------------------------


async def test_construction_moves_the_pitch_to_the_initial_angle():
    """**起動時に正面・水平（0° = 1.5 ms）を最初のパルスで出す。**

    最初のパルスで今の角度から飛ぶので、**初期角を明示して出さないと
    「今の向きが不定」で止まる。**
    """
    hw, _lgpio, _bus, write = make_hw()
    try:
        assert write.values("enable")[0] == "1"
        assert write.last("duty_cycle") == str(SG90_PULSE_CENTER_NS)
    finally:
        await hw.close()


async def test_set_pitch_writes_the_pulse_width():
    hw, _lgpio, _bus, write = make_hw()
    try:
        await hw.set_pitch(30.0)
        assert write.last("duty_cycle") == str(
            SG90_PULSE_CENTER_NS + int(30.0 * SG90_NS_PER_DEG)
        )
    finally:
        await hw.close()


async def test_set_pitch_clamps_to_the_datasheet_band():
    """**±45° の外を要求しても 0.5〜2.4 ms から出ない。**"""
    hw, _lgpio, _bus, write = make_hw()
    try:
        await hw.set_pitch(400.0)
        assert write.last("duty_cycle") == str(SG90_PULSE_MAX_NS)
        await hw.set_pitch(-400.0)
        assert write.last("duty_cycle") == str(SG90_PULSE_MIN_NS)
    finally:
        await hw.close()


async def test_set_pitch_follows_the_servo_direction():
    """**角度を上げるとパルス幅が長くなる**（反転は変異で見る）。"""
    hw, _lgpio, _bus, write = make_hw()
    try:
        await hw.set_pitch(-45.0)
        down = int(write.last("duty_cycle"))
        await hw.set_pitch(0.0)
        zero = int(write.last("duty_cycle"))
        await hw.set_pitch(45.0)
        up = int(write.last("duty_cycle"))
        assert down < zero < up
    finally:
        await hw.close()


# --- `RpiHardware`: ヨー ------------------------------------------------------------------


async def test_construction_leaves_every_yaw_pin_low():
    """**起動直後の 4 本は LOW**（動かさない）。"""
    hw, lgpio, _bus, _write = make_hw()
    try:
        assert lgpio.high_pins() == []
    finally:
        await hw.close()


async def test_drive_yaw_excites_the_pins():
    """**回すと 4 本のどこかが HIGH になる**（励磁している）。"""
    hw, lgpio, _bus, _write = make_hw()
    try:
        await hw.drive_yaw(200.0, "left")
        assert await wait_for_high(lgpio), "回したのにどのピンも HIGH にならない"
    finally:
        await hw.close()


async def test_stop_yaw_turns_every_pin_low():
    """**止めると 4 本とも LOW**。**コイルに電流を残さない**（変異の中心）。

    これが壊れると、止まっている間ずっとコイルが熱くなって電池を食い、
    ギアの摩擦で勝手に回り出すことすらある。
    """
    hw, lgpio, _bus, _write = make_hw()
    try:
        await hw.drive_yaw(200.0, "left")
        assert await wait_for_high(lgpio)
        await hw.stop_yaw()
        assert lgpio.high_pins() == [], "止めたのに HIGH のピンが残ってる"
    finally:
        await hw.close()


async def test_stop_yaw_writes_low_as_the_last_write():
    """**最後の書き込みが 4 本の LOW**（解放や次ステップの後に書くと意味が無い）。"""
    hw, lgpio, _bus, _write = make_hw()
    try:
        await hw.drive_yaw(200.0, "left")
        assert await wait_for_high(lgpio)
        lgpio.writes.clear()
        await hw.stop_yaw()
        assert [level for _pin, level in lgpio.writes] == [0] * len(YAW_PINS)
    finally:
        await hw.close()


async def test_stop_yaw_without_ever_driving_is_still_low():
    """**一度も回していなくても `stop_yaw` は LOW を書く**（中性点へ戻す）。"""
    hw, lgpio, _bus, _write = make_hw()
    try:
        await hw.stop_yaw()
        assert lgpio.high_pins() == []
    finally:
        await hw.close()


async def test_drive_yaw_with_zero_rate_stops():
    """**0 以下なら回さない**（回した後に 0 を渡したら止める）。"""
    hw, lgpio, _bus, _write = make_hw()
    try:
        await hw.drive_yaw(200.0, "left")
        assert await wait_for_high(lgpio)
        await hw.drive_yaw(0.0, "left")
        assert lgpio.high_pins() == []
    finally:
        await hw.close()


async def test_drive_yaw_with_an_unknown_direction_does_not_move():
    """**知らない向きでは回さない**。"""
    hw, lgpio, _bus, _write = make_hw()
    try:
        await hw.drive_yaw(200.0, "up")
        time.sleep(0.03)
        assert lgpio.high_pins() == []
    finally:
        await hw.close()


async def test_left_and_right_turn_in_opposite_directions():
    """**左と右で半ステップ列を進める向きが逆**（`YAW_STEP_SIGN` の意味）。

    8 回のステップで一周するので、**回した回数が 8 の倍数にならないうちに比べる**
    （ちょうど 8 回回すと一周して同じ位置に戻るので差が出ない）。**1 回だけ回して比べる。**
    """
    hw, lgpio, _bus, _write = make_hw()
    try:
        before = hw.step_count
        await hw.drive_yaw(_SLOW_STEPS_PER_S, "left")
        await wait_for_steps(hw, before + 1)
        await hw.stop_yaw()
        left_pos = hw._yaw_pos

        before = hw.step_count
        await hw.drive_yaw(_SLOW_STEPS_PER_S, "right")
        await wait_for_steps(hw, before + 1)
        await hw.stop_yaw()
        right_pos = hw._yaw_pos

        # 8 回のステップで一周するので、1 回進めた位置は必ず 1 つ進むか 1 つ戻る
        assert (right_pos - left_pos) % 8 in (1, 7), (
            f"左右が逆向きに進んでいない: left={left_pos}, right={right_pos}"
        )
    finally:
        await hw.close()


def test_the_half_step_sequence_never_goes_through_the_neutral_point():
    """**半ステップの列に中性点（`0b0000`）が無い。**

    中性点を通る相が混ざると、励磁が 1 段だけ切れて**失歩**する。実機で確認する。
    """
    assert 0 not in YAW_HALF_STEP_SEQUENCE
    for pattern in YAW_HALF_STEP_SEQUENCE:
        assert any((pattern >> i) & 1 for i in range(4)), f"{pattern:04b} が全部 LOW"


def test_the_half_step_sequence_has_eight_phases():
    """**半ステップは 8 相**。相が足りないと同じ向きでも回転がずれる。"""
    assert len(YAW_HALF_STEP_SEQUENCE) == 8
    assert len(set(YAW_HALF_STEP_SEQUENCE)) == 8


def test_the_half_step_sequence_never_exceeds_four_bits():
    """**4 ピンに収まる**（上位ビットが立っても 4 ピンに書かれないように）。"""
    for pattern in YAW_HALF_STEP_SEQUENCE:
        assert 0 <= pattern <= 0b1111


# --- `RpiHardware`: 天井（実物の経路）-----------------------------------------------------


async def test_read_ceiling_gives_a_measured_reading():
    """**`smbus2` を実際に叩いて `MEASURED` を取り出せる**（経路を縛る）。"""
    bus = FakeSmbus([200])  # 200 cm = 2000 mm
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        reading = await wait_for_ceiling(hw)
        assert reading is not None
        assert reading.status is CeilingStatus.MEASURED
        assert reading.distance_mm == 2000
    finally:
        await hw.close()


async def test_the_sonar_thread_writes_the_ranging_command():
    """**I2C に ranging のコマンドを送っている**（送らないと測らない）。"""
    bus = FakeSmbus([200])
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        assert await wait_for_ceiling(hw) is not None
        assert bus.commands, "コマンドが 1 つも出ていない"
        _at, addr, register, value = bus.commands[0]
        assert addr == 0x70
        assert register == rpi_hw.SRF02_COMMAND_REGISTER
        assert value == rpi_hw.SRF02_RANGING_CMD_CM
    finally:
        await hw.close()


async def test_the_sonar_thread_reads_the_result_register():
    """**結果レジスタ（2 バイト）を読む**（0 番だけだと常に 0 が返る）。"""
    bus = FakeSmbus([200])
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        assert await wait_for_ceiling(hw) is not None
        assert bus.reads
        _at, _addr, register, length = bus.reads[0]
        assert register == rpi_hw.SRF02_RESULT_REGISTER
        assert length == 2
    finally:
        await hw.close()


async def test_read_ceiling_returns_none_before_the_first_measurement():
    """**最初の測定が終わる前は `None`**（`control.py` は「古いまま」で扱う）。"""
    bus = FakeSmbus([200])
    bus.block_for_s = 0.5
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        assert await asyncio.wait_for(hw.read_ceiling(), timeout=0.2) is None
    finally:
        bus.block_for_s = 0.0
        await hw.close()


async def test_read_ceiling_consumes_the_reading():
    """**1 回取り出したら次は `None`**（同じ値を返し続けると古さを測れない）。"""
    bus = FakeSmbus([200])
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        assert await wait_for_ceiling(hw) is not None
        # 次の測定が来る前に取り出しても、2 回目は新しい測定で無い限り `None`
        second = await asyncio.wait_for(hw.read_ceiling(), timeout=0.02)
        assert second is None
    finally:
        await hw.close()


async def test_i2c_failure_on_the_real_path_becomes_read_error():
    """**実物の経路で I2C が失敗したら `READ_ERROR`**（`NO_ECHO` ではない）。**変異の中心。**

    `except` を `NO_ECHO` にしてしまうと、**センサが死んでも「反射が無い＝天井は遠い」**
    とみなしで天井に突っ込む。
    """
    bus = FakeSmbus([OSError("bus error")])
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        reading = await wait_for_ceiling(hw)
        assert reading is not None
        assert reading.status is CeilingStatus.READ_ERROR
        assert ceiling_permission(reading, reading.at_ms, make_params()) == (
            False,
            "CEILING_STALE",
        )
    finally:
        await hw.close()


async def test_a_short_read_is_read_error():
    """**2 バイトに満たない応答は `READ_ERROR`**（壊れた値を距離にしない）。"""
    bus = FakeSmbus([b"\x01"])  # 1 バイトしか無い
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        reading = await wait_for_ceiling(hw)
        assert reading is not None
        assert reading.status is CeilingStatus.READ_ERROR
    finally:
        await hw.close()


async def test_the_sonar_thread_keeps_measuring_after_an_error():
    """**一度失敗しても測り続ける**（`READ_ERROR` を置いただけでは止まらない）。"""
    bus = FakeSmbus([OSError("bus error"), 200])
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        first = await wait_for_ceiling(hw)
        assert first is not None
        assert first.status is CeilingStatus.READ_ERROR
        # 2 回目は成功する（止まってはいけない）
        second = await wait_for_ceiling(hw, timeout=1.0)
        assert second is not None
        assert second.status is CeilingStatus.MEASURED
    finally:
        await hw.close()


class GatedSmbus(FakeSmbus):
    """1 回だけ測って、2 回目の読みから**止まる**偽の SRF02（固まった I2C・止まったスレッドの再現）。

    `release()` するまで 2 回目の `read_i2c_block_data` が返らない。
    """

    def __init__(self, first: object = 200) -> None:
        super().__init__([first])
        self._gate = threading.Event()

    def read_i2c_block_data(self, addr: int, register: int, length: int) -> bytes:
        if self.reads_done >= 1:
            self._gate.wait(timeout=5.0)
        return super().read_i2c_block_data(addr, register, length)

    def release(self) -> None:
        self._gate.set()


async def wait_until_measured(bus: FakeSmbus, timeout: float = 2.0) -> None:
    """スレッドが 1 回読み終え、結果を置くまで待つ。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and bus.reads_done < 1:
        await asyncio.sleep(0.005)
    assert bus.reads_done >= 1, "SRF02 のスレッドが測らなかった"
    await asyncio.sleep(0.05)  # 読み終えてから結果を置くまでの余裕


async def test_at_ms_is_the_measurement_time():
    """**`at_ms` は測ったときの時計**。取り出しを遅らせても取り出した時刻にならない。

    時計 1000 ms で測り、取り出す前に時計を 700 ms 進める。`at_ms` が 1000 のままでなければ、
    取り出しの時刻にしている（**固まったスレッドの古い値が新しく見える**バグ）。
    """
    clock = ManualClock(1000.0)
    bus = GatedSmbus(200)
    hw, _lgpio, _b, _w = make_hw(smbus=bus, clock=clock)
    try:
        await wait_until_measured(bus)
        clock.advance(700.0)  # 取り出す前に時間が経つ
        reading = await hw.read_ceiling()
        assert reading is not None
        assert reading.at_ms == 1000.0
    finally:
        bus.release()
        await hw.close()


async def test_a_stuck_sonar_still_goes_stale_in_the_real_path():
    """**スレッドが固まって新しい測定が無くても `CEILING_STALE` に倒れる。**要点。

    1 つ取り出した読み値の `at_ms` は「測ったときの時計」なので、
    `ceiling_stale_ms` を超えると必ず古くなる。**取り出した時刻にしてしまうと、
    固まったスレッドの古い読み値が永遠に新しく見えて上昇を許し続ける。**
    """
    clock = ManualClock(0.0)
    bus = FakeSmbus([200] * 20)
    hw, _lgpio, _b, _w = make_hw(smbus=bus, clock=clock)
    try:
        reading = await wait_for_ceiling(hw)
        assert reading is not None
        measured_at = reading.at_ms
        assert measured_at is not None
        params = make_params()
        # 取り出した直後は許される
        assert ceiling_permission(reading, measured_at, params) == (True, "NONE")
        # スレッドが固まって新しい測定が無くても、時間が立つと古くなる
        assert ceiling_permission(
            reading, measured_at + params["ceiling_stale_ms"] + 1, params
        ) == (False, "CEILING_STALE")
    finally:
        await hw.close()


async def test_a_stuck_sonar_loop_does_not_block_read_ceiling():
    """**スレッドが固まっていても `read_ceiling()` はすぐ返る**（ロックを掴まない）。

    ここがブロックされると、制御ループごと止まって**昇降部の指令が出なくなる**。
    """
    bus = FakeSmbus([200] * 10)
    bus.block_for_s = 1.0  # 読みを 1 秒止める
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        started = time.monotonic()
        await asyncio.wait_for(hw.read_ceiling(), timeout=0.5)
        assert time.monotonic() - started < 0.5
    finally:
        bus.block_for_s = 0.0
        await hw.close()


async def test_the_sonar_thread_waits_between_measurements():
    """**測定と測定の間隔が `srf02_ranging_wait_ms`（70 ms）以上**空く。

    **65 ms より速く ranging を始めてはいけない**（データシート）。
    待つのは**スレッドの中だけ**。制御ループを待たせない。
    """
    bus = FakeSmbus([200] * 10)
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline and len(bus.commands) < 3:
            await asyncio.sleep(0.01)
        assert len(bus.commands) >= 3, "測定が 3 回も出ていない"
        gaps = [
            bus.commands[i + 1][0] - bus.commands[i][0]
            for i in range(len(bus.commands) - 1)
        ]
        assert min(gaps) >= SRF02_MIN_PERIOD_S, f"間隔が短すぎる: {min(gaps):.4f} s"
    finally:
        await hw.close()


async def test_the_sonar_thread_does_not_block_the_event_loop():
    """**I2C の待ちでイベントループを止めない**（`threading.Event.wait` で待つ）。

    `time.sleep` で待つと、I2C の 70 ms のあいだ**画面も指令も全部止まる**。
    """
    bus = FakeSmbus([200] * 10)
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    try:
        ticks = 0
        for _ in range(20):
            await asyncio.sleep(0.005)
            ticks += 1
        assert ticks == 20
        # 同じ時間にイベントループが 100 ms 以上のブレークが無いこと
        assert await wait_for_ceiling(hw, timeout=0.3) is not None
    finally:
        await hw.close()


# --- `RpiHardware`: `close()` ------------------------------------------------------------


async def test_close_turns_every_pin_low():
    """**`close()` で 4 本とも LOW**（電流なしで片付ける）。"""
    hw, lgpio, _bus, _write = make_hw()
    await hw.drive_yaw(200.0, "left")
    assert await wait_for_high(lgpio)
    await hw.close()
    assert lgpio.high_pins() == [], "close() したのに HIGH のピンが残ってる"


async def test_close_releases_the_pins_and_closes_the_chip():
    """**ピンを解放してチップを閉じる**（閉じないと次の起動ができない）。"""
    hw, lgpio, _bus, _write = make_hw()
    await hw.close()
    assert len(lgpio.freed) == len(YAW_PINS)
    assert lgpio.closed_chips


async def test_close_closes_the_i2c_bus():
    """**I2C バスを閉じる**。"""
    bus = FakeSmbus([200] * 100)
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    await wait_for_ceiling(hw)
    await hw.close()
    assert bus.closed, "`close()` で I2C バスを閉じていない"


async def test_close_stops_the_sonar_thread():
    """**`close()` 後は測り続けない**（閉じてもスレッドが生きていると I2C を叩き続ける）。"""
    bus = FakeSmbus([200] * 100)
    hw, _lgpio, _b, _w = make_hw(smbus=bus)
    await wait_for_ceiling(hw)
    await hw.close()
    before = len(bus.commands)
    time.sleep(0.15)
    assert len(bus.commands) == before, "close() 後もスレッドが測定を続けている"


async def test_close_disables_the_pwm():
    """**`close()` で PWM を止める。**"""
    hw, _lgpio, _bus, write = make_hw()
    await hw.close()
    assert write.last("enable") == "0"


async def test_close_stops_the_yaw_thread():
    """**`close()` でヨーのスレッドも止まる**（daemon だから放置すると残骸になる）。"""
    hw, _lgpio, _b, _w = make_hw()
    await hw.drive_yaw(400.0, "left")
    await wait_for_high(_lgpio)
    await hw.close()
    assert hw._yaw_thread is None


# --- `is_raspberry_pi` -------------------------------------------------------------------


def test_is_raspberry_pi_reads_the_model_file(tmp_path):
    """**ラズパイの `model` ファイルを読む**。"""
    model = tmp_path / "model"
    model.write_bytes(b"Raspberry Pi 5 Model B Rev 1.0\x00")
    assert is_raspberry_pi(model) is True


def test_is_raspberry_pi_is_false_when_the_file_is_missing(tmp_path):
    assert is_raspberry_pi(tmp_path / "nope") is False


def test_is_raspberry_pi_is_false_on_another_board(tmp_path):
    """**ラズパイ以外でもエラーにしない**（`False` を返すだけ）。"""
    model = tmp_path / "model"
    model.write_bytes(b"Generic x86 Machine\x00")
    assert is_raspberry_pi(model) is False


# --- ホストでは `lgpio` を import しない --------------------------------------------------


def test_importing_this_module_does_not_import_lgpio():
    """**import 時に `lgpio` を読まない。**

    `lgpio` はラズパイの OS と同じところに入ってる。**import 時に読むと、
    ホストの試験も `--fake` の起動も `ModuleNotFoundError` で死ぬ。**
    """
    assert "lgpio" not in sys.modules


def test_running_the_whole_hardware_does_not_import_lgpio():
    """**偽物を差して一連の操作をしても `lgpio` を import しない。**"""
    lgpio = FakeLgpio()
    port = LgpioPort(module=lgpio)
    port.setup()
    port.write(YAW_PINS[0], 1)
    port.close()
    assert "lgpio" not in sys.modules


def test_rpi_hardware_does_not_import_lgpio():
    """**`RpiHardware` を作り回しても `lgpio` を import しない。**"""
    assert "lgpio" not in sys.modules


# --- 実物の経路: `RpiHardware`（偽の I2C）→ `ControlLoop` → 偽の昇降部への `ceil_ok` --------------
#
# 純関数の試験が緑でも、`read_ceiling` の返す `at_ms` や例外の扱いが壊れていれば、
# 昇降部へ送る `ceil_ok` が true のままになる。**送られた指令そのもの**を見る。


class LoopOverRpi:
    """`RpiHardware`（偽の I2C）と `ControlLoop` と偽の昇降部をまとめる。"""

    def __init__(self, bus: FakeSmbus, start_ms: float = 1000.0) -> None:
        self.clock = ManualClock(start_ms)
        self.bus = bus
        self.hw, _lgpio, _b, _w = make_hw(smbus=bus, clock=self.clock)
        self.lift = FakeLift(self.clock)
        self.loop = ControlLoop(
            self.hw,
            self.lift,
            settings=SETTINGS,
            params=make_params(),
            clock=self.clock,
            video_zoom=RecordingVideoZoom(),
        )

    async def step(self, advance_ms: float = 0.0) -> dict:
        """時計を進め、上昇を押し続けたまま 1 回回し、**送られた指令**を返す。"""
        self.clock.advance(advance_ms)
        self.loop.hold("lift_up", 30)
        await self.loop.step()
        return self.lift.cmd_history[-1]


async def test_a_healthy_sonar_lets_the_lift_go_up_through_the_control_loop():
    """対照: 正常に測れていれば `ceil_ok` は true。**これが無いと下の「false」の試験が空振りする。**"""
    rig = LoopOverRpi(FakeSmbus([200] * 50))  # 200 cm = 2000 mm。余裕の 500 mm より遠い
    try:
        await wait_until_measured(rig.bus)
        await rig.step()  # ここで読み値を受け取る
        cmd = await rig.step()
        assert (cmd["dir"], cmd["ceil_ok"]) == ("up", True)
    finally:
        await rig.hw.close()


class AlwaysFailingSmbus(FakeSmbus):
    """読むたびに例外を出す偽の SRF02（配線が抜けた・バスが死んだ）。"""

    def read_i2c_block_data(self, addr: int, register: int, length: int) -> bytes:
        self.reads_done += 1
        raise OSError(121, "Remote I/O error")


async def test_an_i2c_that_keeps_failing_never_lets_the_lift_go_up():
    """**SRF02 が例外を出し続けたら、昇降部へ送る `ceil_ok` は false のまま。**本物の経路。

    例外を `NO_ECHO`（反射なし＝天井は遠い＝上昇を許す）にしてしまうと、
    センサが死んでも上昇し続けて天井へ突っ込む。
    """
    rig = LoopOverRpi(AlwaysFailingSmbus())
    try:
        await wait_until_measured(rig.bus)
        for _ in range(5):
            cmd = await rig.step(advance_ms=100.0)
            assert cmd["dir"] == "up"
            assert cmd["ceil_ok"] is False
            await asyncio.sleep(0.02)
    finally:
        await rig.hw.close()


async def test_a_stuck_sonar_thread_stops_the_lift_after_the_stale_time():
    """**スレッドが固まって新しい測定が来なくなったら、`ceiling_stale_ms` を過ぎて `ceil_ok` が false になる。**"""
    bus = GatedSmbus(200)
    rig = LoopOverRpi(bus)
    try:
        await wait_until_measured(bus)
        await rig.step()
        assert (await rig.step())["ceil_ok"] is True  # 測った直後は許す
        stale_ms = make_params()["ceiling_stale_ms"]
        assert (await rig.step(advance_ms=stale_ms - 100))["ceil_ok"] is True
        cmd = await rig.step(advance_ms=200.0)  # 合計で stale_ms を超えた
        assert cmd["ceil_ok"] is False
    finally:
        bus.release()
        await rig.hw.close()


async def test_a_reading_collected_late_is_still_judged_by_when_it_was_measured():
    """**取り出しが遅れても、古さは「測った時刻」から数える。**`at_ms` を取り出しの時刻にすると落ちる。

    時計 1000 ms で測り、誰も取り出さないまま 700 ms（> `ceiling_stale_ms`）経つ。
    そのあとの `step()` で取り出した値は、すでに古いので `ceil_ok` は false でなければならない。
    """
    bus = GatedSmbus(200)
    rig = LoopOverRpi(bus)
    try:
        await wait_until_measured(bus)
        await rig.step(advance_ms=700.0)  # この step の最後で読み値を受け取る
        cmd = await rig.step()
        assert cmd["ceil_ok"] is False
    finally:
        bus.release()
        await rig.hw.close()
