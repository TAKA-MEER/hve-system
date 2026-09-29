"""カメラ部のラズパイ（実物）のハードウェア。`HardwareBase` の実物。

[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §4.2・§4.4 と
[DetailedDesign-hardware.md](../../../docs/plan/detailed/DetailedDesign-hardware.md) §2・§2.3。

## 3 つとも専用スレッドで回す理由

中層の `control.py` は 100 ms ごとに昇降部へ指令を出し、そのあとで天井を読む
（[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §3）。
**SRF02 の待ち時間（`srf02_ranging_wait_ms` = 70 ms）と 28BYJ-48 の半ステップを
制御ループの中で待たせると、指令の周期がその分だけ伸びる。**そこで 2 つとも専用スレッドで回す。

- **28BYJ-48**: `drive_yaw()` は「1 秒あたりの半ステップ数と向き」を置き換えるだけ。**角度は持たない**（spec [Spec-ui.md](../../../docs/plan/spec/Spec-ui.md) §1.4）
- **SRF02**: 測り終わったら `CeilingReading` を 1 つ置いておく。`read_ceiling()` はそれを 1 回だけ取り出す（新しい測定が無ければ `None`）

**`CeilingReading.at_ms` は「スレッド内で測ったときの時計」**。`read_ceiling()` で取り出した時刻ではない。
取り出した時刻にしてしまうと、スレッドが止まったときに古さを測れず `CEILING_STALE` に倒れない
（[Spec-safety.md](../../../docs/plan/spec/Spec-safety.md) §2）。

## 差し替え（ホストの試験）

`lgpio`・`smbus2`・PWM の sysfs は**コンストラクタの引数で差し替える**。
**`lgpio` は `LgpioPort` の中からしか import しない**（ラズパイの OS 同梱で、
pip に Python 3.13 向けが無い。ホストには無い）。`smbus2` も `RpiHardware._open_smbus` の中だけ。

## 部品の決まりごと

| 部品 | 駆動 | 決まりごと |
| --- | --- | --- |
| SG90（ピッチ） | カーネルの PWM（`/sys/class/pwm/pwmchipN/pwm0`） | **起動時に初期角（正面・水平 = 0°）へ一気に動く**（hardware §2.3）。最初のパルスで今の角度から飛ぶ |
| 28BYJ-48 / ULN2003（ヨー） | `lgpio` で IN1〜IN4 を半ステップ順に | **止まっている間は 4 本とも LOW**。コイルの電流を切る（励磁されたままでは減速ギアの摩擦で留まるので） |
| Devantech SRF02（天井） | `smbus2`（I2C） | コマンドを送って `srf02_ranging_wait_ms` 待ち、読む。**`READ_ERROR`（I2C で読めない）と `NO_ECHO`（反射が無い）を混同しない**（spec [Spec-safety.md](../../../docs/plan/spec/Spec-safety.md) §2 #3b・#4） |
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from hve_camera.ceiling import CeilingReading, CeilingStatus
from hve_camera.hw.base import HardwareBase

log = logging.getLogger(__name__)

#: 単調増加の時計。ms を返す。**天井の読み値の `at_ms` はこれ**（`__main__.monotonic_ms` と同じもの）
Clock = Callable[[], float]

#: sysfs へ 1 回書く。試験は記録だけの偽物を差す
WriteText = Callable[[Path, str], None]

# --- SG90（ピッチ） -------------------------------------------------------------------------
# 値は DetailedDesign-names.md §5 のとおり。**データシートに依る**（勾配は ±10 % 程度ある）

#: PWM の周期 [ns]。20 ms（50 Hz）
SG90_PERIOD_NS = 20_000_000
#: 0°（正面・水平）のパルス幅 [ns]。1.5 ms。`control.py` のピッチの初期角 0° と揃える
SG90_PULSE_CENTER_NS = 1_500_000
#: 1 度あたりのパルス幅 [ns]。10 µs。0.5〜2.4 ms が約 -100°〜+90° に対応する
SG90_NS_PER_DEG = 10_000
#: パルス幅の下限 [ns]。**この外側へ出さない**（データシートの 0.5 ms）
SG90_PULSE_MIN_NS = 500_000
#: パルス幅の上限 [ns]。**この外側へ出さない**（データシートの 2.4 ms）
SG90_PULSE_MAX_NS = 2_400_000
#: 起動時に一気に送る角度 [deg]。正面・水平（動いても害の無い向き）
SG90_INITIAL_PITCH_DEG = 0.0

# --- 28BYJ-48 / ULN2003（ヨー） --------------------------------------------------------------
# ピンは DetailedDesign-names.md §5 と DetailedDesign-hardware.md §2.2

#: IN1〜IN4 のピン（GPIO 番号）
YAW_IN1_PIN = 23
YAW_IN2_PIN = 24
YAW_IN3_PIN = 25
YAW_IN4_PIN = 16

#: 半ステップの相。下位ビットから IN1・IN2・IN3・IN4。
#: **隣り合うコイルを順に励磁する**（IN1 → IN1+IN2 → IN2 → …。1 本と 2 本が交互）。**中性点（0b0000）は使わない**。
#: 隣り合わないコイル（IN1+IN3 など）を混ぜると唸るだけでほとんど回らない（2026-09-29 実機で確認）
YAW_HALF_STEP_SEQUENCE: tuple[int, ...] = (
    0b0001,  # IN1
    0b0011,  # IN1, IN2
    0b0010,  # IN2
    0b0110,  # IN2, IN3
    0b0100,  # IN3
    0b1100,  # IN3, IN4
    0b1000,  # IN4
    0b1001,  # IN4, IN1
)

#: 向き → 半ステップ列を進める符号。**`仮`**（どちらが左かは配線で決まる。実機で確認する）
YAW_STEP_SIGN: dict[str, int] = {"left": 1, "right": -1}

#: 半ステップの間隔の下限 [s]。これより短く刻まない（自分の駒をそれより速く刻ませない）
YAW_MIN_STEP_INTERVAL_S = 0.0002

#: 停止までの待ち時間 [s]。スレッドを止めてピンを LOW にするまで待つ時間（越えたら諦める）
YAW_STOP_TIMEOUT_S = 1.0

#: 待つ大きさを刻む大きさ [s]。**長い待ちも数 ms ごとに分割する**
#: （丸ごと 1 回で待つと、`close()` や `stop_yaw()` の反応が遅くなる）
_WAIT_SLICE_S = 0.005

# --- Devantech SRF02（天井） -----------------------------------------------------------------
# 出典は Devantech「SRF02 Ultrasonic Ranger - I2C Mode」。

#: コマンドのレジスタ。**書けるのはこのロケーションだけ**
SRF02_COMMAND_REGISTER = 0x00
#: 結果のレジスタ。ロケーション 2・3 の 16 bit（上位バイトが先）
SRF02_RESULT_REGISTER = 0x02
#: ranging を始めて結果を cm で返すコマンド。**mm を返すコマンドは無い**
SRF02_RANGING_CMD_CM = 0x51
#: 反射が無いときの返り値。データシート「0 = no objects detected」
SRF02_NO_ECHO_RAW = 0
#: ranging 中に読んだときの返り値（データシート「result = 255 = not ready」）。**反射が無いのとは別**
SRF02_BUSY_RAW = 0xFFFF
#: cm → mm。**SRF02 は inches・cm・µs の 3 種類しか返さない**ので cm を受ける
MM_PER_CM = 10
#: ranging の最小間隔 [s]。データシート「70 ms 後には必ず応答できる」「65 ms より早くは始めない」
SRF02_MIN_PERIOD_S = 0.065

#: I2C のバス番号（`dtparam=i2c_arm=on` の 1 番）
SRF02_I2C_BUS = 1
#: 停止までの待ち時間 [s]
SRF02_STOP_TIMEOUT_S = 1.0

# --- 場所 -----------------------------------------------------------------------------------

#: カーネルの PWM。**書けないときは udev の規則が要る**（DetailedDesign.md §4.4）
PWM_BASE = "/sys/class/pwm"
#: `export` の直後は udev が権限を付け終えるまで書けない。`PermissionError` の間、これだけ待つ [s]
PWM_EXPORT_SETTLE_S = 2.0
#: 上の待ちで、書き直すまでの間隔 [s]
PWM_EXPORT_RETRY_S = 0.02
#: ラズパイかどうかを確かめるファイル（`dtparam` の `model`）
RPI_MODEL_PATH = "/proc/device-tree/model"
#: `lgpio` のチップ番号
LGPIO_CHIP = 0

#: ヨーの 4 ピン（IN1〜IN4）。`YAW_HALF_STEP_SEQUENCE` の下位ビットと順番を揃える
YAW_PINS: tuple[int, int, int, int] = (YAW_IN1_PIN, YAW_IN2_PIN, YAW_IN3_PIN, YAW_IN4_PIN)


# --- 純関数 ---------------------------------------------------------------------------------


def pitch_to_pulse_ns(angle_deg: float) -> int:
    """ピッチの角度を SG90 のパルス幅 [ns] にする。純関数。

    0°（正面・水平）が `SG90_PULSE_CENTER_NS`、1 度につき `SG90_NS_PER_DEG` を足す。
    **範囲外は端に留める**（データシートの 0.5〜2.4 ms の外へ出すと、サーボが端に押し付けられて壊れうる）。
    可動範囲は `axes.py` の `pitch_step` が `pitch_min_deg`〜`pitch_max_deg` で決めるので、
    この留めは「その数行が壊れたとき」の保険。
    """
    pulse_ns = SG90_PULSE_CENTER_NS + float(angle_deg) * SG90_NS_PER_DEG
    return int(min(max(pulse_ns, SG90_PULSE_MIN_NS), SG90_PULSE_MAX_NS))


def srf02_reading(
    raw: int | BaseException,
    params: Mapping[str, Any],
    at_ms: float,
) -> CeilingReading:
    """SRF02 の返り値（cm）と I2C の例外を `CeilingReading` にする。純関数。

    **上から順に、最初に当たったもの**を返す。

    | # | 受け取ったもの | 状態 | 意味 |
    | --- | --- | --- | --- |
    | 1 | I2C の例外 | `READ_ERROR` | バスから読めなかった・応答が無い。**上昇を許さない**（spec §2 #4） |
    | 2 | `SRF02_BUSY_RAW`（`0xFFFF`） | `READ_ERROR` | ranging 中に読んだ。**反射が無いのとは別**（まだ結果が出ていないだけ） |
    | 3 | `SRF02_NO_ECHO_RAW`（`0`） | `NO_ECHO` | 反射が無い。**上昇を許す**（spec §2 #3b） |
    | 4 | `srf02_max_range_mm` を超える | `NO_ECHO` | 測定範囲の外なので反射が無いのと同じ扱い（spec §2 #3b） |
    | 5 | それ以外 | `MEASURED` | 距離 [mm]。近すぎれば `ceiling.py` が `CEILING_NEAR` にする（#3a・#4） |

    **#1 と #3 を取り違えると、センサが死んだときに「反射が無い＝天井は遠い」とみなし
    上げ続けてしまう**（[DetailedDesign.md](../../../docs/plan/detailed/DetailedDesign.md) §3）。
    距離の変換（cm → mm）もこの関数の中でやる。呼び出し側は mm しか扱わない。
    """
    if isinstance(raw, BaseException):
        return CeilingReading(CeilingStatus.READ_ERROR, at_ms=at_ms)

    if raw == SRF02_BUSY_RAW:
        # 2: まだ測っている。反射無い（#3）と混ぜない
        return CeilingReading(CeilingStatus.READ_ERROR, at_ms=at_ms)

    if raw <= SRF02_NO_ECHO_RAW:
        # 3: 反射が無い（データシート「0 = no objects detected」。実機で確認する）
        return CeilingReading(CeilingStatus.NO_ECHO, at_ms=at_ms)

    distance_mm = raw * MM_PER_CM
    if distance_mm > int(params["srf02_max_range_mm"]):
        # 4: 測定範囲の外。反射が無いのと同じ扱い
        return CeilingReading(CeilingStatus.NO_ECHO, at_ms=at_ms)

    return CeilingReading(CeilingStatus.MEASURED, distance_mm=distance_mm, at_ms=at_ms)


def _wait_slices(stop: threading.Event, seconds: float) -> bool:
    """`seconds` 待つ。**途中で `stop` が立ったら即座に True を返す**。

    1 回で丸ごと待たない（丸ごとだと 70 ms ずつ反応が遅れる）。**Mutex ではなく
    `Event` を使う**ので、この待ちのあいだも他スレッドは進める。
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return stop.is_set()
        if stop.wait(min(remaining, _WAIT_SLICE_S)):
            return True


# --- 差し替え可能な部品 -----------------------------------------------------------------------


def _write_text(path: Path, value: str) -> None:
    """`path` に `value` を書く。PWM の sysfs への書き込み。"""
    path.write_text(value)


def find_pwm_chip(base: str | Path = PWM_BASE) -> Path:
    """PWM チップのディレクトリを 1 つ返す。**番号が小さい順。**"""
    for path in sorted(Path(base).glob("pwmchip*")):
        if path.is_dir():
            log.info("PWM チップは %s を使う", path)
            return path
    raise RuntimeError(f"PWM チップが見つからない（{base} を確認）")


def is_raspberry_pi(model_path: str | Path = RPI_MODEL_PATH) -> bool:
    """ラズパイかどうか。**ラズパイ以外で偽物無しでは起動させない**（`__main__` が使う）。"""
    try:
        model = Path(model_path).read_bytes()
    except OSError:
        return False
    return b"raspberry pi" in model.lower()


def _import_lgpio() -> Any:
    """`lgpio` を読み込む。**ラズパイ以外では絶対に呼ばない。**"""
    try:
        import lgpio
    except ImportError as exc:  # pragma: no cover - 実機でしか起きない
        raise RuntimeError(
            "lgpio が無い。Raspberry Pi OS には同梱されているが、"
            "**Trixie には無い**（pip に Python 3.13 向けが無い）"
        ) from exc
    return lgpio


class PwmChannel:
    """カーネルの PWM 1 チャネル（`/sys/class/pwm/pwmchipN/pwm0`）。

    `export` → `period` → `duty_cycle` → `enable` の順に書く。
    `period` は**動かさない**（書き換えるとサーボが飛ぶ）。角度は `duty_cycle` だけで変える。
    """

    def __init__(
        self,
        chip: str | Path,
        channel: int = 0,
        *,
        write: WriteText = _write_text,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._chip = Path(chip)
        self._channel = int(channel)
        #: sysfs への書き込み。**試験は記録だけの偽物を差す**
        self._write_text = write
        #: 権限待ちの間隔。**試験は待たない偽物を差す**
        self._sleep = sleep

    @property
    def path(self) -> Path:
        """チャネル（`pwm0`）のディレクトリ。"""
        return self._chip / f"pwm{self._channel}"

    def enable(self, period_ns: int, duty_ns: int) -> None:
        """チャネルを export して周期と最初のパルス幅を書き、PWM を出す。

        `export` は既に済んでいるときに `EBUSY` になる。**既に動いていたら成功扱いにし、
        周期とパルス幅は上書きする**（再起動で `export` だけ残ることがある）。
        """
        self._export()
        self._write_after_export(self.path / "period", int(period_ns))
        self._write(self.path / "duty_cycle", int(duty_ns))
        self._write(self.path / "enable", 1)

    def set_duty_ns(self, duty_ns: int) -> None:
        """パルス幅だけを変える。**周期は変えない。**"""
        self._write(self.path / "duty_cycle", int(duty_ns))

    def disable(self) -> None:
        """PWM を止める。**チャネルは閉じない**（`unexport` は呼ばない。呼ぶとパルスが途切れる）。"""
        self._write(self.path / "enable", 0)

    def _export(self) -> None:
        try:
            self._write_text(self._chip / "export", str(self._channel))
        except OSError:
            # EBUSY = 既に export 済み。**そのままで使える**ので黙って続ける
            log.info("PWM %s の export は既に済んでいた", self.path)

    def _write_after_export(self, path: Path, value: int) -> None:
        """`export` 直後の最初の書き込み。**権限が付くまで `PermissionError` を待つ。**

        `export` で `pwm0` ができてから udev が `gpio` グループへ権限を付けるまでに
        少し間があり、その間は書けない（再起動後の初回だけ起きる）。
        待っても書けなければ、最後の `PermissionError` をそのまま投げる。
        """
        waited = 0.0
        while True:
            try:
                self._write(path, value)
                return
            except PermissionError:
                if waited >= PWM_EXPORT_SETTLE_S:
                    raise
                self._sleep(PWM_EXPORT_RETRY_S)
                waited += PWM_EXPORT_RETRY_S

    def _write(self, path: Path, value: int) -> None:
        self._write_text(path, str(value))


class LgpioPort:
    """`lgpio` で 4 本のピン（ヨーの IN1〜IN4）を出力にして回す。

    **ホストの試験では偽物を差す**（`lgpio` の fake を `module` に渡す）。
    本物の `lgpio` は**このクラスの中からしか import しない**（`__init__` では読まない）。
    """

    def __init__(
        self,
        pins: Sequence[int] = YAW_PINS,
        *,
        module: Any = None,
        chip: int = LGPIO_CHIP,
    ) -> None:
        self._pins = tuple(int(pin) for pin in pins)
        #: **差し替えた `lgpio`**。`None` のときだけ `_import_lgpio()` で読む
        self._module = module
        self._chip = int(chip)
        self._handle: int | None = None

    @property
    def pins(self) -> tuple[int, ...]:
        """管理するピン（IN1〜IN4）。"""
        return self._pins

    def setup(self) -> None:
        """4 本を出力として確保して**全て LOW** にする（コイルに電流を流さない）。"""
        module, handle = self._open()
        for pin in self._pins:
            if module.gpio_claim_output(handle, pin) < 0:
                raise RuntimeError(
                    f"GPIO{pin} を出力にできない（他モジュールが使用中か、"
                    f"`/boot/firmware/config.txt` の `dtoverlay` が必要）"
                )
            module.gpio_write(handle, pin, 0)

    def write(self, pin: int, level: int) -> None:
        """1 本のピンを書き換える。`level` は 0（LOW）か 1（HIGH）。"""
        if self._handle is None or self._module is None:
            raise RuntimeError("LgpioPort が setup されていない")
        self._module.gpio_write(self._handle, int(pin), int(level))

    def close(self) -> None:
        """4 本を LOW にしてから解放し、チップを閉じる。**LOW を先に書く。**"""
        if self._handle is None or self._module is None:
            return
        with contextlib.suppress(Exception):
            for pin in self._pins:
                self._module.gpio_write(self._handle, pin, 0)
        with contextlib.suppress(Exception):
            for pin in self._pins:
                self._module.gpio_free(self._handle, pin)
        with contextlib.suppress(Exception):
            self._module.gpiochip_close(self._handle)
        self._handle = None
        self._module = None

    def _open(self) -> tuple[Any, int]:
        """チップを開く。**差し替えが無ければ `lgpio` を import する。**"""
        if self._module is None:
            self._module = _import_lgpio()
        if self._handle is None:
            self._handle = self._module.gpiochip_open(self._chip)
            if self._handle < 0:
                raise RuntimeError(
                    f"gpiochip{self._chip} が開けない"
                    "（`gpio` グループに入れているか、`/dev/gpiochip*` の権限を見る）"
                )
        return self._module, self._handle


# --- 本物のハードウェア -----------------------------------------------------------------------


class RpiHardware(HardwareBase):
    """ラズパイの実物（SG90・28BYJ-48・SRF02）。`HardwareBase` の実装。

    **3 つとも専用スレッドで回す**（`control.py` の 100 ms を待たせないため）。
    `lgpio`・`smbus2` は**このモジュールの import 時とホストでは読まない**。

    試験からは `gpio`・`smbus`・`pwm` で差し替える（**実物の I2C と GPIO を待たずに
    角度の計算と停止経路を確かめるため**）。差し替えてもスレッドの構造は同じ。
    """

    def __init__(
        self,
        params: Mapping[str, Any],
        clock: Clock,
        *,
        gpio: LgpioPort | None = None,
        smbus: Any = None,
        pwm: PwmChannel | None = None,
    ) -> None:
        self._params = params
        self._clock = clock
        self._gpio = gpio if gpio is not None else LgpioPort()
        self._smbus = smbus if smbus is not None else self._open_smbus()
        self._pwm = pwm if pwm is not None else self._open_pwm()

        # ピッチの初期角（正面・水平）。**最初のパルスで今の角度から飛べるようにする**
        self._pitch_ns = pitch_to_pulse_ns(SG90_INITIAL_PITCH_DEG)
        self._pwm.enable(SG90_PERIOD_NS, self._pitch_ns)
        self._pitch_deg = SG90_INITIAL_PITCH_DEG

        # ヨー。**停止状態で始める**（`_yaw_active` が False だと 1 ピンも動かさない）
        self._yaw_pos = 0
        self._yaw_dir_sign = YAW_STEP_SIGN["left"]
        self._yaw_step_s = 0.0
        self._yaw_stop = threading.Event()
        self._yaw_idle = threading.Event()
        self._yaw_idle.set()
        self._yaw_thread: threading.Thread | None = None
        self._yaw_lock = threading.Lock()
        self._yaw_active = False
        #: 刻んだ半ステップの累計回数。**`close()` しても 0 に戻さない**（回した量を数えるため）
        self.step_count = 0

        # 天井。**最新の一個だけ**覚えておく（それより古いのは捨てる）
        self._ceiling: CeilingReading | None = None
        self._ceiling_lock = threading.Lock()
        self._sonar_stop = threading.Event()
        self._sonar_thread: threading.Thread | None = None

        self._gpio.setup()
        self._start_yaw_thread()
        self._start_sonar_thread()

    # --- HardwareBase ---------------------------------------------------------------------

    async def read_ceiling(self) -> CeilingReading | None:
        """天井の読み値を 1 回取り出す。**新しい測定が無いときは `None`。**

        `at_ms` はスレッド内で測ったときの時計。**取り出した時刻ではない。**
        """
        with self._ceiling_lock:
            reading, self._ceiling = self._ceiling, None
        return reading

    async def set_pitch(self, angle_deg: float) -> None:
        """ピッチのサーボへ角度を出す。**角度の計算は `axes.py` の仕事**（受け取った値をそのまま出す）。"""
        pulse_ns = pitch_to_pulse_ns(angle_deg)
        if pulse_ns != self._pitch_ns:
            self._pitch_ns = pulse_ns
            self._pitch_deg = angle_deg
            self._pwm.set_duty_ns(pulse_ns)

    async def drive_yaw(self, steps_per_s: float, direction: str) -> None:
        """ヨーを回す（半ステップ/秒・向きだけ。**角度は持たない**）。

        `steps_per_s` が 0 以下、または知らない向きのときは**回さない**（回さない方が安全）。
        """
        if direction not in YAW_STEP_SIGN:
            log.warning("知らない向き %s なのでヨーを回さない", direction)
            return
        rate = abs(float(steps_per_s))
        if rate <= 0:
            await self.stop_yaw()
            return
        self._start_yaw_thread()
        with self._yaw_lock:
            self._yaw_dir_sign = YAW_STEP_SIGN[direction]
            self._yaw_step_s = max(1.0 / rate, YAW_MIN_STEP_INTERVAL_S)
            self._yaw_active = True
        self._yaw_idle.clear()

    async def stop_yaw(self) -> None:
        """ヨーを止める。**止まっている間は 4 本とも LOW にする**（コイルの電流を切る）。

        刻みを止めてから**中性点（全部 LOW）へ戻す**。**スレッドは生きさせたまま**にする
        （次の `drive_yaw()` ですぐまた要るから。スレッドを止めるのは `close()` だけ）。
        半ステップの位相は保持するので、次の回転は前回の続きから。
        """
        with self._yaw_lock:
            self._yaw_active = False
        # 刻み終わるのを待ってから電流を切る（**待たずに切ると 1 段ずれる**）
        if not self._yaw_idle.wait(YAW_STOP_TIMEOUT_S):
            log.error("ヨーのスレッドが %f 秒で止まらない", YAW_STOP_TIMEOUT_S)
        self._write_yaw(0)
        self._yaw_idle.set()

    async def close(self) -> None:
        """スレッドを全部止めて、ピンを LOW にして、PWM とバスを閉じる。"""
        await self.stop_yaw()
        self._yaw_stop.set()
        yaw_thread = self._yaw_thread
        if yaw_thread is not None and yaw_thread is not threading.current_thread():
            yaw_thread.join(timeout=YAW_STOP_TIMEOUT_S)
            if yaw_thread.is_alive():
                log.error(
                    "ヨーのスレッドが %f 秒で止まらない", YAW_STOP_TIMEOUT_S
                )
        self._yaw_thread = None
        self._sonar_stop.set()
        thread = self._sonar_thread
        if thread is not None:
            thread.join(timeout=SRF02_STOP_TIMEOUT_S)
            if thread.is_alive():
                log.error("SRF02 のスレッドが %f 秒で止まらない", SRF02_STOP_TIMEOUT_S)
            self._sonar_thread = None
        with contextlib.suppress(Exception):
            self._pwm.disable()
        with contextlib.suppress(Exception):
            self._gpio.close()
        with contextlib.suppress(Exception):
            self._smbus.close()

    # --- スレッド --------------------------------------------------------------------------

    def _start_yaw_thread(self) -> None:
        """ヨーのスレッドを起こす。**既に alive なら何もしない**（2 回作らない）。"""
        if self._yaw_thread is not None and self._yaw_thread.is_alive():
            return
        self._yaw_stop = threading.Event()
        self._yaw_thread = threading.Thread(
            target=self._yaw_loop, name="hve-yaw", daemon=True
        )
        self._yaw_thread.start()

    def _start_sonar_thread(self) -> None:
        """SRF02 のスレッドを起こす。"""
        if self._sonar_thread is not None and self._sonar_thread.is_alive():
            return
        self._sonar_stop = threading.Event()
        self._sonar_thread = threading.Thread(
            target=self._sonar_loop, name="hve-srf02", daemon=True
        )
        self._sonar_thread.start()

    def _yaw_loop(self) -> None:
        """ヨーのスレッド。**半ステップを刻む。**`close()` で終わる。

        待機している間は 1 ピンも動かさない。**`stop_yaw` は `_yaw_idle` を見てから
        中性点（全部 LOW）へ戻す**ので、ここが合図しないと電流を切れない。

        **ロックを握って待たない**（待つのはロックの外）。
        握ったまま待つと `stop_yaw` も `drive_yaw` もロックを取れず遅れる。
        """
        while not self._yaw_stop.is_set():
            with self._yaw_lock:
                active = self._yaw_active
                if active:
                    self._yaw_idle.clear()
                    self._yaw_pos = (self._yaw_pos + self._yaw_dir_sign) % len(
                        YAW_HALF_STEP_SEQUENCE
                    )
                    self.step_count += 1
                    step_s = self._yaw_step_s
                    pattern = YAW_HALF_STEP_SEQUENCE[self._yaw_pos]
                else:
                    # 待つ合図を出す。**この後すぐロックを離す**
                    self._yaw_idle.set()
            if not active:
                # 待つ。`drive_yaw` が起きたら進む（角度は持たない）
                if _wait_slices(self._yaw_stop, _WAIT_SLICE_S):
                    return
                continue
            self._write_yaw(pattern)
            if _wait_slices(self._yaw_stop, step_s):
                return
        self._yaw_idle.set()

    def _sonar_loop(self) -> None:
        """SRF02 のスレッド。**順に測り続けて 1 個ずつ置いていく。**"""
        addr = int(self._params["srf02_i2c_addr"])
        wait_s = float(self._params["srf02_ranging_wait_ms"]) / 1000.0
        # 最低でも 65 ms 間隔。データシート「70 ms 後には必ず応答できる」を守る
        period_s = max(wait_s, SRF02_MIN_PERIOD_S)
        last_error_at = 0.0

        while not self._sonar_stop.is_set():
            started = time.monotonic()
            try:
                self._smbus.write_byte_data(
                    addr, SRF02_COMMAND_REGISTER, SRF02_RANGING_CMD_CM
                )
                # 待つ。**スレッドの中で待つだけ**（制御ループもイベントループも待たない）
                if _wait_slices(self._sonar_stop, wait_s):
                    return
                raw_cm = self._smbus.read_i2c_block_data(
                    addr, SRF02_RESULT_REGISTER, 2
                )
                cm = (raw_cm[0] << 8) | raw_cm[1]
                reading = srf02_reading(cm, self._params, self._clock())
            except Exception as exc:  # noqa: BLE001 - I2C は何でも「読めない」に倒す
                reading = srf02_reading(exc, self._params, self._clock())
                # 100 ms ごとに出すと画面が埋まる。**1 秒に 1 回だけ** 出す
                if self._clock() - last_error_at > 1000.0:
                    last_error_at = self._clock()
                    log.warning("SRF02 が読めない（READ_ERROR）: %s", exc)

            with self._ceiling_lock:
                self._ceiling = reading

            # 次の測定まで `period_s` を空ける（`stop` されていれば抜ける）
            if _wait_slices(self._sonar_stop, period_s - (time.monotonic() - started)):
                return

    # --- 小さい関数 ------------------------------------------------------------------------

    def _write_yaw(self, pattern: int) -> None:
        """半ステップの相を 4 本に書く。**0（全 LOW）なら電流を切る。**"""
        for index, pin in enumerate(self._gpio.pins):
            self._gpio.write(pin, (pattern >> index) & 1)

    def _open_smbus(self) -> Any:
        """I2C バスを開く。**`smbus2` はこの中だけで import する。**"""
        try:
            from smbus2 import SMBus
        except ImportError as exc:  # pragma: no cover - 実機でしか起きない
            raise RuntimeError("smbus2 が無い（`pip install smbus2`）") from exc
        return SMBus(SRF02_I2C_BUS)

    def _open_pwm(self) -> PwmChannel:
        """PWM のチップを探してチャネルを開く。**チップ番号は環境依存。**"""
        return PwmChannel(find_pwm_chip())
