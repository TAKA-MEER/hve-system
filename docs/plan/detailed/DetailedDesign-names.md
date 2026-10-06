# 名前辞書（v2）

[DetailedDesign.md](DetailedDesign.md) の詳細。**この文書に無い名前を実装で作ってはいけない**（`DD-1`）。
必要な名前が無いと分かったら、**実装する前にこのファイルへ行を足す。**

**旧版の名前辞書（[archive/v1/detailed/DetailedDesign-names.md](../archive/v1/detailed/DetailedDesign-names.md)）にあって、ここで変えていない名前はそのまま使ってよい**
（例: `elapsed_ms`・`height_is_fresh`・`hal_core` の定数・`crop_rect`・`pitch_step`・`yaw_step_rate`・`clamp_speed`・`validate_settings`）。
**旧版の名前を別の意味で使い直さない。**廃止した名前は §6。

---

## 0. 命名規則

旧版と同じ（Python は小文字スネーク、C++ は型 `UpperCamel`・関数 `lower_snake`・定数 `UPPER_SNAKE`、パラメータは単位の接尾辞、停止理由は大文字スネーク）。
単位の接尾辞に `_ddeg`（0.1°）・`_hsps`（1 秒あたりの半ステップ）・`_us` を足す。

## 1. ファイル構成（v2 で足す・変えるもの）

| パス | 中身 |
| --- | --- |
| `firmware/lift/lib/lift_core/` | 旧版の `hal.h`・`lift_decide`・`lift_controller`・`cmd_codec` に加え、**`ceiling_check.{h,cpp}`・`lift_arbiter.{h,cpp}`・`lift_settings.{h,cpp}`**。`Arduino.h` を include しない |
| `firmware/lift/web/` | 昇降部の画面: `index.html`・`app.js`・`style.css`・`tests/`（node の試験・スクロールの検査） |
| `firmware/lift/scripts/embed_web.py` | PlatformIO の `extra_scripts`（`pre:`）。`web/` を gzip して `src/web_assets.h` を作る |
| `firmware/lift/src/web_assets.h` | **生成物**（`.gitignore` に入れる） |
| `firmware/lift/test/test_ceiling_check/`・`test_lift_arbiter/`・`test_lift_settings/` | Unity の試験（`env:native`） |
| **`firmware/cam_io/`** | **Arduino UNO のファーム**（`hve_cam_io`）。`platformio.ini` に `env:uno`（実機）・`env:native`（ホスト試験） |
| `firmware/cam_io/lib/io_core/` | `io_hal.h`・`io_codec.{h,cpp}`・`io_controller.{h,cpp}`・`step_rate.{h,cpp}`。`Arduino.h` を include しない |
| `firmware/cam_io/src/` | `main.cpp`・`config.h`・`hal_uno.{h,cpp}` |
| `firmware/cam_io/test/test_io_codec/`・`test_io_controller/`・`test_step_rate/` | Unity の試験 |
| `camera/hve_camera/uno_link.py` | `encode_io_cmd`・`parse_io_line`・`UnoClock`（[-protocol.md](DetailedDesign-protocol.md) §5） |
| `camera/hve_camera/hw/uno_hw.py` | 実物の Arduino との UART（`pyserial`）。**旧版の `hw/rpi_hw.py` を置き換える**（`rpi_hw.py` と `test_rpi_hw.py` は消す。v1 の中身は git の履歴に残る） |
| `camera/hve_camera/hw/fake_lift.py` | 偽の昇降部（v2 の取り決めを話す。プロセス内） |
| `camera/hve_camera/lift_resolve.py` | 昇降部の名前を引く（`lift_host` が空なら `zeroconf` で `hve-lift.local`） |
| `camera/requirements.txt` ／ `requirements-dev.txt` | UnitV2 で入れるもの（`aiohttp`・`pyserial`・`zeroconf`。**Python 3.8 で入る版に固定**） ／ ホストの試験用 |
| `camera/deploy/` | UnitV2 の自動起動の設定（中身は `WP-MEAS-06` の結果で決める） |
| `tools/fake_lift_server.py` | 偽の昇降部（単体のプロセス）。昇降部の画面・`/ws/ui`・`/ws/module`・設定 API を出す（[DetailedDesign.md](DetailedDesign.md) §4.2） |
| `tools/lift_probe.py` | 旧版の道具を v2 の取り決め（`/ws/module`・`hello`・`hold`/`release`）に直す |
| `.venv38/` | Python 3.8 の試験環境（gitignore。[DetailedDesign.md](DetailedDesign.md) §4.7） |

関数・型（v2 で足すもの）:

| 名前 | 置き場 | 何か |
| --- | --- | --- |
| `CeilingStatus` | `lift_core/ceiling_check.h` | `MEASURED` / `TOO_NEAR` / `NO_ECHO` / `READ_ERROR` / `MISSING`（欠けている・読めない） |
| `CeilingReport` | 同 | `hold` に載る天井の値（状態・mm・`age_ms`）と、受け取った時刻 |
| `ceiling_check` | 同 | `ceiling_check(report, now_ms, connection_has_sensor)` → `CeilingVerdict{ok, reason}`。**距離計を持たない接続なら常に `ok`**。古さは `age_ms ＋ elapsed_ms(now, received_at)` |
| `ConnId` | `lift_core/lift_arbiter.h` | 接続の識別子（整数）と種類（`UI` / `MODULE`） |
| `LiftArbiter` | 同 | 持ち主の規則（[DetailedDesign.md](DetailedDesign.md) §3.3）。`on_hold`・`on_release`・`on_close`・`tick(now)`・`owner()` |
| `LiftSettings` ／ `validate_lift_settings` ／ `lift_settings_from_json` ／ `lift_settings_to_json` | `lift_core/lift_settings.h` | 昇降の速度の設定（[-protocol.md](DetailedDesign-protocol.md) §3） |
| `IoCmd` ／ `parse_m_line` ／ `format_c_line` ／ `format_b_line` | `cam_io/lib/io_core/io_codec.h` | UART の行（[-protocol.md](DetailedDesign-protocol.md) §5） |
| `IoController` | `cam_io/lib/io_core/io_controller.h` | 指令の受付・ウォッチドッグ・丸め。`io_hal.h` の抽象へ出す |
| `StepRate` | `cam_io/lib/io_core/step_rate.h` | 割り込み 1 回ごとに「刻むか・どちら向きか」を返す位相の足し算 |
| `classify_srf02` | `camera/hve_camera/ceiling.py` | SRF02 の生の値 → 汎用の 4 状態（[-protocol.md](DetailedDesign-protocol.md) §5） |
| `encode_io_cmd` ／ `parse_io_line` | `camera/hve_camera/uno_link.py` | `M` 行を作る ／ `C`・`B` 行を読む（読めなければ `None`） |
| `UnoClock` | 同 | Arduino の時計を UnitV2 の時計へ直す（[-protocol.md](DetailedDesign-protocol.md) §5） |
| `resolve_lift_host` | `camera/hve_camera/lift_resolve.py` | 昇降部の IP を返す（引けなければ `None`） |

## 2. 機器・ホスト名

| 名前 | 何か |
| --- | --- |
| `hve-lift` | 昇降部 ESP32 の mDNS 名（旧版と同じ） |
| `hve-cam` | カメラモジュール（UnitV2）の名前。`hello` の `name` に使う。mDNS で名乗れるかは `WP-MEAS-06` |
| `hve_cam_io` | Arduino のファームの名前（`B` 行の `fw` と一緒に出す） |

**AP の SSID はコードに直書きしない**（ESP32 は `secrets.h`、UnitV2 は OS の無線設定）。

## 3. 停止理由

| 名前 | 出す所 | 意味 |
| --- | --- | --- |
| `NONE` | 全部 | 止めていない |
| `CMD_STOP` | 昇降部 | 持ち主が離した・`stop` |
| `CMD_TIMEOUT` | 昇降部 | 持ち主の `hold` が途絶えた |
| **`OWNER_GONE`** | 昇降部 | 持ち主の接続が閉じた |
| **`CEILING_NEAR`** | 昇降部 | 天井が近い（`MEASURED` で閾値以下・`TOO_NEAR`）。**v1 ではカメラ部の理由だった** |
| **`CEILING_STALE`** | 昇降部 | 天井の値が無い・読めない・古い。**v1 ではカメラ部の理由だった** |
| `HEIGHT_UNKNOWN` | 昇降部 | 高さが読めない・古い。**`W-1` の間は出ない** |
| `TOP` | 昇降部 | 上端に達した。**`W-1` の間は出ない** |
| `BOTTOM` | 昇降部 | 下端スイッチが押されている |
| `MAX_RUN` | 昇降部 | 連続駆動の上限 |
| `OUT_OF_RANGE` | 昇降部（天井の理由のみ。**停止理由ではない**） | 天井の距離計に反射が返らない。上昇は許す |
| `HOLD_TIMEOUT` | カメラモジュール | 画面からの操作が途絶えた |
| `LINK_LOST` | カメラモジュール | 昇降部と繋がっていない |
| **`IO_LOST`** | カメラモジュール | Arduino から行が来ない（ヨー・ピッチが動かない・天井が読めない） |
| `AXIS_LIMIT` | カメラモジュール | ピッチが可動範囲の端 |

## 4. メッセージ

種類 `t` は昇降部の WS で `hello` / `hold` / `release` / `state`、カメラモジュールの WS で旧版と同じ `hold` / `release` / `zoom` / `state`。
UART の行は `M` / `C` / `B`。フィールドは [-protocol.md](DetailedDesign-protocol.md) が正。

`POST /api/fake`（カメラモジュールの偽物のモードだけ）は旧版の形に、`io_lost`（真偽値。偽の Arduino が行を止める）を足す。
偽の昇降部（`tools/fake_lift_server.py`・`hw/fake_lift.py`）は `POST /api/fake` で `height_mm`・`bottom` を変えられる。

## 5. パラメータと仮値

**`仮` は根拠の無い仮置き。**実測で置き換えたら `仮` を外し、出どころを書く。

### 5.1 昇降部（`lift_core/lift_decide.h` ほか）

| 名前 | 置き場 | 値 | 出どころ |
| --- | --- | --- | --- |
| `LIFT_CMD_TIMEOUT_MS` | `lift_decide.h` | 600 | 旧版（先行試作・th-system の実績） |
| `HEIGHT_STALE_MS` | `lift_decide.h` | 600 | **仮**（旧版） |
| `LIFT_TOP_MM` | `lift_decide.h` | -1（未設定） | 旧版 |
| **`LIFT_TOP_DETECT_ENABLED`** | `lift_decide.h` | `false` | **`W-1`**（spec [Spec-safety.md](../spec/Spec-safety.md) §1.1）。`true` に戻すのは台帳を閉じるとき |
| `LIFT_MAX_RUN_MS` | `lift_decide.h` | 10000 | **仮**（旧版。spec #4b） |
| `LIFT_DUTY_ABS_MAX_PCT` | `lift_decide.h` | 100 | MD10C |
| **`CEILING_MARGIN_MM`** | `ceiling_check.h` | 500 | **仮**（`H-V8`。v1 の `ceiling_margin_mm` を昇降部へ移した。spec `H-M4`） |
| **`CEILING_STALE_MS`** | `ceiling_check.h` | 600 | **仮**（`H-V8`。v1 の `ceiling_stale_ms` を昇降部へ移した） |
| 昇降の設定の既定値 | `lift_settings.h` | `lift_up` / `lift_down` とも `{min:10, max:60, init:30}` | **仮**（旧版の例の値） |
| `LIFT_STATE_PERIOD_MS` | `config.h` | 100 | **仮** |
| **`LIFT_WS_UI_PATH`** / **`LIFT_WS_MODULE_PATH`** | `config.h` | `/ws/ui` / `/ws/module` | [-protocol.md](DetailedDesign-protocol.md) §1（v1 の `LIFT_WS_PATH` は廃止） |
| **`LIFT_NVS_NAMESPACE`** | `config.h` | `hve_lift` | [-protocol.md](DetailedDesign-protocol.md) §3 |
| **`LIFT_UI_CLIENTS_MAX`** | `config.h` | 4 | **仮**（ESP32 のメモリ。超えた接続は閉じる） |
| `LIFT_STATE_TEXT_MAX` | `config.h` | 640 | **仮**（v2 の `state` は 500 バイト前後） |
| `LIFT_MDNS_NAME` / `LIFT_HTTP_PORT` | `config.h` | `hve-lift` / 80 | 旧版 |
| ピン・PWM・HC-SR04 の定数 | `config.h`・`hal_core.h` | 旧版のまま | 旧版 §5 |

### 5.2 昇降部の画面（`firmware/lift/web/app.js`）

| 名前 | 値 | 出どころ |
| --- | --- | --- |
| `ui_hold_period_ms` / `ui_state_timeout_ms` / `ui_tick_ms` | 100 / 1000 / 500 | **仮**（旧版と同じ） |

### 5.3 カメラモジュール（`camera/config/params.toml`）

旧版の行のうち、**`ceiling_margin_mm`・`ceiling_stale_ms`・`srf02_i2c_addr`・`srf02_ranging_wait_ms`・`lift_ws_url` は廃止**（§6）。ほかは旧版の値のまま。

| 名前 | 値 | 出どころ |
| --- | --- | --- |
| **`lift_host`** | `""`（空なら mDNS で `lift_mdns_name` を引く） | [DetailedDesign.md](DetailedDesign.md) §4.6 |
| **`lift_mdns_name`** / **`lift_port`** | `hve-lift` / 80 | 同・[-protocol.md](DetailedDesign-protocol.md) §1 |
| **`LIFT_WS_MODULE_PATH`** | `camera/hve_camera/lift_link.py` の定数 | `/ws/module`。**`params.toml` に置かない**（[DetailedDesign.md](DetailedDesign.md) §3.1。書き間違えて `/ws/ui` に繋ぐと天井の守りが外れる） |
| **`module_name`** / **`module_ceiling_sensor`** | `hve-cam` / `true` | [DetailedDesign.md](DetailedDesign.md) §3.1（**常に `true`。センサの調子から計算しない**） |
| **`io_device`** / **`io_baud`** | `/dev/ttyS1` / 115200 | M5 文書（[-hardware.md](DetailedDesign-hardware.md) §2.1）。`io_baud` は Arduino の `IO_BAUD` と揃える |
| **`io_cmd_period_ms`** | 50 | **仮** |
| **`io_lost_ms`** | 600 | **仮**（Arduino から行が来ないと `IO_LOST` にするまで） |
| **`ceiling_read_stale_ms`** | 600 | **仮**（これより古い天井の読み値は `READ_ERROR` として送る。昇降部の `CEILING_STALE_MS` と揃える） |
| **`uno_clock_window`** | 20 | **仮**（`C` 行が約 70 ms ごとなので約 1.4 秒分） |
| `srf02_min_range_mm` / `srf02_max_range_mm` | 150 / 6000 | 旧版（SRF02 のデータシート） |
| `pitch_min_deg` / `pitch_max_deg` | -45 / 45 | **仮**（`H-V5`・`H-X5`。MG996R で見直す） |
| `axis_speed_abs_max_dps` | 60 | 旧版（28BYJ-48 の実用の上限の下側） |
| `yaw_steps_per_rev` | 4096 | 旧版（半ステップ） |
| `video_capture_width` / `video_capture_height` | 1920 / 1080 | spec `H-V9`（**UnitV2 で取れるかは `WP-MEAS-06`**） |
| `video_out_height` | 480 | spec `H-V9` |
| `video_fps` / `video_jpeg_quality` | 10 / 60 | **仮**（旧版。`WP-MEAS-06` で UnitV2 の負荷を見て決める） |
| `settings_path` | `~/hve_data/settings.json` | 旧版（**ピッチ・ヨーの 2 軸だけを保存する**。書ける場所は `WP-MEAS-06`） |

### 5.4 Arduino（`firmware/cam_io/`）

| 名前 | 置き場 | 値 | 出どころ |
| --- | --- | --- | --- |
| `IO_BAUD` | `io_core/io_codec.h` | 115200 | M5 文書の例。**16 MHz の UNO では誤差 約 2 %。化けるなら 57600 に下げ、`io_baud` と揃える**（`WP-MEAS-06`） |
| `IO_LINE_MAX` | `io_codec.h` | 32 | `M 65535 -1800 -680` が 20 文字 |
| `IO_CMD_TIMEOUT_MS` | `io_controller.h` | 300 | **仮**（`io_cmd_period_ms` の 6 倍） |
| `YAW_HSPS_ABS_MAX` | `io_controller.h` | 680 | 60 deg/s × 4096 ÷ 360 ≒ 683（`axis_speed_abs_max_dps` と揃える） |
| `PITCH_MIN_DEG` / `PITCH_MAX_DEG` | `io_controller.h` | -60 / 60 | **仮**。**UnitV2 側（±45）より広い 2 つ目の守り**。機構に当たらない範囲を実機で決める（`H-X5`） |
| `PITCH_INITIAL_DEG` | `io_controller.h` | 0 | 旧版の考え方（正面・水平。起動時・再起動時に跳ぶ先） |
| `SERVO_PULSE_CENTER_US` / `SERVO_US_PER_DEG` | `config.h` | 1500 / 10 | **仮**（MG996R のデータシートで確かめる） |
| `SERVO_PULSE_MIN_US` / `SERVO_PULSE_MAX_US` | `config.h` | 900 / 2100 | **仮**（±60° に当たる範囲。この外側へパルスを出さない） |
| `IO_STEP_TICK_US` | `config.h` | 100 | **仮**（680 半ステップ毎秒なら約 1.47 ms ごとに刻む。その 1/14 の細かさ） |
| `YAW_PINS` / `YAW_HALF_STEP_SEQUENCE` | `config.h` | D4〜D7 ／ 旧版と同じ並び | [-hardware.md](DetailedDesign-hardware.md) §2.2・旧版の実機確認 |
| `SERVO_PIN` | `config.h` | 9 | **仮** |
| `SRF02_ADDR` | `config.h` | 0x70 | SRF02 の工場出荷値 |
| `SRF02_PERIOD_MS` | `config.h` | 70 | SRF02（1 回 約 66 ms。65 ms より早く始めない） |
| `SRF02_WIRE_TIMEOUT_US` | `config.h` | 25000 | **仮** |
| `IO_WDT_TIMEOUT` | `config.h` | `WDTO_500MS` | **仮** |

## 6. 廃止した名前（使い直さない）

| 名前 | 旧版の意味 | v2 での扱い |
| --- | --- | --- |
| `ceil_ok` | カメラ部が計算した天井の許可（指令のフィールド） | 廃止。天井の値そのものを `ceiling` で送る |
| `CEILING`（停止理由） | `ceil_ok` が偽 | `CEILING_NEAR` / `CEILING_STALE` に分けた |
| `LIFT_WS_PATH` | `/ws` | `LIFT_WS_UI_PATH` / `LIFT_WS_MODULE_PATH` |
| `ceiling_permission` | カメラ部の天井の許可の計算 | 廃止（昇降部の `ceiling_check`） |
| `ceiling_margin_mm` / `ceiling_stale_ms` | カメラ部のパラメータ | `CEILING_MARGIN_MM` / `CEILING_STALE_MS`（昇降部） |
| `lift_ws_url` | 昇降部の WS の URL | `lift_host` / `lift_mdns_name` / `lift_port`（パラメータ）と `LIFT_WS_MODULE_PATH`（定数） |
| `rpi_hw.py` の定数（`SG90_*`・`YAW_*`・`SRF02_*`・`LGPIO_CHIP`・`PWM_BASE` 等） | ラズパイの実物 | 廃止（ヨー・ピッチ・天井は Arduino） |
| `srf02_i2c_addr` / `srf02_ranging_wait_ms` | カメラ部のパラメータ | Arduino の `SRF02_ADDR` / `SRF02_PERIOD_MS` |
