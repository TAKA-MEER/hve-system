# 名前辞書

[DetailedDesign.md](DetailedDesign.md) の詳細。**この文書に無い名前を実装で作ってはいけない。**

> **`DD-1`**: 名前を発明する余地をゼロにする。必要な名前が無いと分かったら、
> **実装する前にこのファイルへ行を足す。**

---

## 0. 命名規則

| 対象 | 規則 | 例 |
| --- | --- | --- |
| Python | モジュール・関数は小文字スネーク | `ceiling_permission` |
| C++ | 型は `UpperCamel`、関数は `lower_snake`、定数は `UPPER_SNAKE` | `LiftController` / `lift_decide` / `LIFT_CMD_TIMEOUT_MS` |
| パラメータ | 小文字スネーク＋単位の接尾辞（`_ms` / `_mm` / `_deg` / `_dps` / `_pct`） | `ceiling_margin_mm` |
| 停止理由 | 大文字スネーク | `CEILING` |
| メッセージのフィールド | 小文字スネーク | `ceil_ok` |

## 1. ファイル構成

| パス | 中身 |
| --- | --- |
| `firmware/lift/platformio.ini` | `env:esp32dev`（実機）・`env:native`（ホスト試験） |
| `firmware/lift/lib/lift_core/` | `hal.h`・`lift_decide.{h,cpp}`・`lift_controller.{h,cpp}`・`cmd_codec.{h,cpp}`。**`Arduino.h` を include しない** |
| `firmware/lift/src/` | `main.cpp`・`config.h`・`hal_esp32.{h,cpp}` |
| `firmware/lift/include/secrets.h.example` | SSID・パスワードの雛形（`secrets.h` は gitignore 済み） |
| `firmware/lift/test/test_lift_core/` | Unity の試験（`env:native`） |
| `camera/hve_camera/` | `__main__.py`・`app.py`・`control.py`・`lift_link.py`・`ceiling.py`・`settings.py`・`axes.py`・`params.py`・`hw/{base,pigpio_hw,fake_hw,fake_lift}.py` |
| `camera/web/` | `index.html`・`settings.html`・`app.js`・`settings.js`・`style.css` |
| `camera/config/params.toml` | パラメータ（§5 のカメラ部の行） |
| `camera/tests/` | pytest |
| `camera/systemd/` | `hve-camera.service`・`hve-ustreamer.service` |
| `tools/` | 実機の確認用スクリプト（`lift_probe.py` 等） |

## 2. 機器・ホスト名

| 名前 | 何か |
| --- | --- |
| `hve-lift` | 昇降部 ESP32 の mDNS 名（`hve-lift.local`） |
| `hve-cam` | カメラ部ラズパイのホスト名・mDNS 名（`hve-cam.local`） |

アドレスの決め方は未確定（[-open.md](DetailedDesign-open.md) `D-1`）。**AP の SSID はコードに直書きせず** `secrets.h` ／ OS の無線設定に置く。

## 3. 停止理由

| 名前 | 出す所 | 意味 |
| --- | --- | --- |
| `NONE` | 両方 | 止めていない |
| `CMD_STOP` | 昇降部 | 指令が `stop` |
| `CMD_TIMEOUT` | 昇降部 | 指令が途絶えた |
| `CEILING` | 昇降部 | `ceil_ok` が `true` でない |
| `HEIGHT_UNKNOWN` | 昇降部 | 高さが読めない・古い |
| `TOP_UNSET` | 昇降部 | 上端の閾値が未設定（提案 `P-2`） |
| `TOP` | 昇降部 | 上端に達した |
| `BOTTOM` | 昇降部 | 下端スイッチが押されている |
| `MAX_RUN` | 昇降部 | 連続駆動の上限（提案 `P-1`） |
| `CEILING_NEAR` | カメラ部 | 天井が近い |
| `CEILING_STALE` | カメラ部 | 天井の値が読めない・古い |
| `HOLD_TIMEOUT` | カメラ部 | 画面からの操作が途絶えた |
| `LINK_LOST` | カメラ部 | 昇降部と繋がっていない |
| `AXIS_LIMIT` | カメラ部 | ピッチ・ヨーが可動範囲の端 |

## 4. メッセージ

種類 `t` は `hold` / `release` / `cmd` / `state`。フィールドは [-protocol.md](DetailedDesign-protocol.md) §2 が正。

## 5. パラメータと仮値

**`仮` は根拠の無い仮置き。**実測（`WP-MEAS-*`）で置き換えたら `仮` を外し、出どころを書く。

| 名前 | 置き場 | 値 | 出どころ |
| --- | --- | --- | --- |
| `LIFT_CMD_TIMEOUT_MS` | 昇降部 `config.h` | 600 | 先行試作・th-system のウォッチドッグの実績 |
| `HEIGHT_STALE_MS` | 昇降部 | 600 | **仮**（spec [Spec-safety.md](../spec/Spec-safety.md) §2） |
| `LIFT_TOP_MM` | 昇降部 | 未設定（`-1`） | `WP-MEAS-01` で決める（提案 `P-2`） |
| `LIFT_MAX_RUN_MS` | 昇降部 | 10000 | **仮**・先行試作の値（提案 `P-1`） |
| `LIFT_DUTY_ABS_MAX_PCT` | 昇降部 | 100 | MD10C の上限 |
| `LIFT_STATE_PERIOD_MS` | 昇降部 | 100 | **仮** |
| `SONAR_PERIOD_MS` | 両方 | 100 | **仮** |
| `PWM_FREQ_HZ` / `PWM_RESOLUTION` | 昇降部 | 5000 / 8 | 先行試作 |
| `ceiling_margin_mm` | カメラ部 `params.toml` | 500 | **仮**（`H-V8`） |
| `ceiling_stale_ms` | カメラ部 | 600 | **仮**（spec [Spec-safety.md](../spec/Spec-safety.md) §2） |
| `hold_timeout_ms` | カメラ部 | 400 | **仮** |
| `lift_cmd_period_ms` | カメラ部 | 100 | **仮** |
| `lift_state_timeout_ms` | カメラ部 | 600 | **仮** |
| `state_period_ms` | カメラ部 | 100 | **仮** |
| `ui_hold_period_ms` / `ui_state_timeout_ms` | 画面 | 100 / 1000 | **仮** |
| `axis_speed_abs_max_dps` | カメラ部 | 60 | **仮**（部品未確定） |
| `pitch_min_deg` / `pitch_max_deg` | カメラ部 | -45 / 45 | **仮**（`H-V5`・`H-X5`） |
| `yaw_limit_deg` | カメラ部 | 170 | **仮**（提案 `P-4`） |
| `settings_path` | カメラ部 | `~/hve_data/settings.json` | — |
| 設定の既定値 | カメラ部 | [-protocol.md](DetailedDesign-protocol.md) §3 の例の値 | **仮** |
| 映像 | `hve-ustreamer.service` | 640×480・10 fps・JPEG 品質 60 | **仮**（`H-A8`） |

## 6. th-system 側の名前（参照のみ）

**th-system とは通信しない**ので、実装でこれらを使うことは無い。th-system の文書を読むときの手がかりとしてだけ残す。

| 名前 | 何か | 出典 |
| --- | --- | --- |
| `AT_PANEL` | th-system の盤前のモード。状態は `IDLE_P` ／ `WORKING`（作業中ボタン ON）／ `PAUSE`（ジョグ中） | th-system `docs/plan/detailed/DetailedDesign-names.md` §2・§3 |
| `th-rpi-ap` | th-system のラズパイが出す AP（192.168.5.1）。PC は 192.168.5.50 固定 | th-system `docs/network.md` |
