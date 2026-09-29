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
| `firmware/lift/lib/hal_core/` | `hal_core.{h,cpp}`。`hal_esp32` のうち**ハードウェアに触らない計算だけ**（定数は §5・関数は §1）。**`Arduino.h` を include しない**ので `env:native` で試験できる |
| `firmware/lift/src/` | `main.cpp`・`config.h`・`hal_esp32.{h,cpp}` |
| `firmware/lift/include/secrets.h.example` | SSID・パスワードの雛形（`secrets.h` は gitignore 済み） |
| `firmware/lift/test/test_lift_core/`・`test_lift_decide/`・`test_lift_controller/`・`test_cmd_codec/`・`test_hal_core/` | Unity の試験（`env:native`。1 ディレクトリ 1 試験で、それぞれ `test_main.cpp` を持つ） |
| `camera/hve_camera/` | `__main__.py`・`app.py`・`control.py`・`lift_link.py`・`ceiling.py`・`settings.py`・`axes.py`・`params.py`・`hw/{base,rpi_hw,fake_hw,fake_lift}.py` |
| `camera/hve_video/` | `__main__.py`・`crop.py`・`pipeline.py`・`server.py`・`sources.py`（実物の V4L2 と、試験用の偽の画像列） |
| `camera/web/` | `index.html`・`settings.html`・`app.js`・`settings.js`・`style.css` |
| `camera/config/params.toml` | パラメータ（§5 のカメラ部の行） |
| `camera/requirements.txt` ／ `camera/requirements-dev.txt` | ラズパイで pip で入れるもの（`aiohttp`・`smbus2`） ／ ホストの試験用（[DetailedDesign.md](DetailedDesign.md) §4.5） |
| `camera/tests/` | pytest |
| `camera/systemd/` | `hve-camera.service`・`hve-video.service` |
| `tools/` | 実機の確認用スクリプト（`lift_probe.py` 等） |
| `tools/tests/` | pytest（`tools/lift_probe.py` のキーの解釈と `cmd` の組み立ての試験。`camera/tests/` と同じ `.venv` で回す） |

関数・型（`WP-BASE-01`・`WP-CAM-01` で足したもの。§0 の命名規則に従う。型は UpperCamel）:

| 名前 | 置き場 | 何か |
| --- | --- | --- |
| `lift_core_version` | `firmware/lift/lib/lift_core/lift_core_version.h` | 文字列を返すだけの土台。`Arduino.h` を include しない。後のパケットでここに判定を書く |
| `load_params` | `camera/hve_camera/params.py` | `camera/config/params.toml` を読んで dict で返す。`tomllib` が無ければ `tomli` を使う |
| `CeilingReading` | `camera/hve_camera/ceiling.py` | 天井の読み値。状態・距離 mm・時刻 ms を持つ。**状態は `MEASURED`（測れた）・`NO_ECHO`（反射なし）・`READ_ERROR`（I2C の読み取り失敗）の 3 つで、別々の値として受け取る**（`READ_ERROR` と `NO_ECHO` を取り違えると天井へ突っ込む） |
| `ceiling_permission` | `camera/hve_camera/ceiling.py` | 読み値・現在時刻・パラメータから、天井の許可 `(ok, 理由)` を返す純関数 |
| `validate_settings` | `camera/hve_camera/settings.py` | 設定の検証（[-protocol.md](DetailedDesign-protocol.md) §3）。通らなかった理由の一覧を返す |
| `load_settings` | `camera/hve_camera/settings.py` | 設定の読み込み。`(設定, 既定値で動いているか)` を返す |
| `save_settings` | `camera/hve_camera/settings.py` | 検証を通る設定だけを原子的に保存する |
| `pitch_step` | `camera/hve_camera/axes.py` | ピッチの目標角を deg/s で積分し可動範囲に収める。`(角度, 理由)` を返す |
| `yaw_step_rate` | `camera/hve_camera/axes.py` | ヨーの deg/s と向きから `(1 秒あたりの半ステップ数, 向き)` を返す |
| `clamp_speed` | `camera/hve_camera/axes.py` | 速度を設定の `min`〜`max` に丸める |

`WP-VIDEO-01` で足した関数・クラス（§0 の命名規則に従う）。**パラメータは `load_params` と同じ `params.toml` を読む**（`hve_video` 側で読み込み直さない）:

| 名前 | 置き場 | 何か |
| --- | --- | --- |
| `crop_rect` | `camera/hve_video/crop.py` | `crop_rect(取り込みの幅, 高さ, 倍率, zoom_max, zoom_step)` → 切り出す矩形 `(x, y, w, h)`。倍率を 1〜`zoom_max` に丸め `zoom_step` の倍数にそろえる。中央・取り込みと同じ縦横比 |
| `FakeSource` | `camera/hve_video/sources.py` | 偽の画像列（numpy で作る。中央に目印があり、フレームごとに変わる） |
| `V4L2Source` | `camera/hve_video/sources.py` | 実物の V4L2 カメラ。OpenCV の `VideoCapture`。MJPEG を要求し `video_capture_width`／`video_capture_height` を求める |
| `open_source` | `camera/hve_video/sources.py` | `open_source(fake, 取り込みの幅, 取り込みの高さ)`。`fake` なら `FakeSource`、でなければ `V4L2Source` を作る |
| `VideoPipeline` | `camera/hve_video/pipeline.py` | 取り込み → 切り出し → 出力の大きさへ縮小 → JPEG。倍率を持つ。**出力の大きさは倍率によらず一定** |
| `create_app` | `camera/hve_video/server.py` | `create_app(pipeline, video_fps)` → aiohttp のアプリ（`GET /stream`・`POST /zoom`） |
| `is_local_peer` | `camera/hve_video/server.py` | 接続元が `127.0.0.1` かどうか。`/zoom` の 403 の判定に使う |
| `main` | `camera/hve_video/__main__.py` | `python3 -m hve_video [--fake] [--port N]` の入口 |

`WP-LIFT-01` で足した関数・型（§0 の命名規則に従う）:

| 名前 | 置き場 | 何か |
| --- | --- | --- |
| `lift_decide` | `firmware/lift/lib/lift_core/lift_decide.{h,cpp}` | 純関数。[DetailedDesign.md](DetailedDesign.md) §4.1 の表の順に、方向・デューティ・停止理由を決める |
| `StopReason` | `firmware/lift/lib/lift_core/lift_decide.h` | 停止理由の列挙。名前は §3 の名前そのもの |
| `LiftDir` | `firmware/lift/lib/lift_core/lift_decide.h` | 方向の列挙。値は [-protocol.md](DetailedDesign-protocol.md) §2.2 の `up` / `down` / `stop` |
| `LiftCmd` | `firmware/lift/lib/lift_core/lift_decide.h` | 指令の構造体（`dir` / `duty` / `ceil_ok`）。`seq` は判定に使わないので持たない |
| `LiftState` | `firmware/lift/lib/lift_core/lift_decide.h` | 状態の構造体（[-protocol.md](DetailedDesign-protocol.md) §2.3 のフィールド） |
| `LiftDecideInput` / `LiftDecideResult` | `firmware/lift/lib/lift_core/lift_decide.h` | `lift_decide()` の入力と出力 |
| `has_cmd` | `firmware/lift/lib/lift_core/lift_decide.h` | `LiftDecideInput` の要素。**指令を一度も受けていない**ことを表す（起動直後は `CMD_TIMEOUT` 扱い） |
| `run_ms` | `firmware/lift/lib/lift_core/lift_decide.h` | `LiftDecideInput` の要素。現在の指令方向について**実際にモータを回した**時間 |
| `height_at_ms` | `firmware/lift/lib/lift_core/lift_decide.h` | 高さの読み値を得た時刻。`height_ok`（有効か）と組で鮮度を測る |
| `LiftHal` | `firmware/lift/lib/lift_core/hal.h` | ハードウェアの抽象。モータ・下端スイッチ・高さ（値・有効性・時刻） |
| `LiftController` | `firmware/lift/lib/lift_core/lift_controller.{h,cpp}` | 指令の受付・ウォッチドッグ・`lift_decide()` の結果をモータへ出す |
| `cmd_decode` | `firmware/lift/lib/lift_core/cmd_codec.{h,cpp}` | 指令の JSON → `LiftCmd`。壊れた入力は「上昇させない」側にして `false` |
| `state_encode` / `state_decode` | `firmware/lift/lib/lift_core/cmd_codec.{h,cpp}` | 状態の JSON ⇔ `LiftState`。`fw` は `lift_core_version()` から入れる |
| `elapsed_ms` | `firmware/lift/lib/lift_core/lift_decide.{h,cpp}` | **経過時間（ms）を数える唯一の関数**。`static_cast<int32_t>(now_ms - then_ms)` の形だけ。`uint32` のまま引いてから `int32` にするので、`millis()` が一周（約 49.7 日）しても正しい。割り込みや WS のタスクが `now` より少し新しい時刻を書いた場合は小さな負の値になり、誤って停止しない。`lift_decide` と `lift_controller` の両方から使う |
| `height_is_fresh` | `firmware/lift/lib/lift_core/lift_decide.{h,cpp}` | 純関数。高さの読み値が `HEIGHT_STALE_MS` 以内か。`elapsed_ms` を使う。**`lift_decide` の表 4 と `LiftController` の `state.height_ok` はこの 1 か所来判断する**（同じ規則を 2 か所に書かない） |

`WP-LIFT-02` で足した関数・型（§0 の命名規則に従う。**`lift_core` は変えていない**）:

| 名前 | 置き場 | 何か |
| --- | --- | --- |
| `sonar_echo_to_mm` | `firmware/lift/lib/hal_core/hal_core.{h,cpp}` | 純関数。HC-SR04 の ECHO のパルス幅（µs）を mm にして有効性を返す。**測定範囲の外は `false` を返し `*out_mm` には書かない**（呼び出し側に無効な値を渡さない） |
| `bottom_pressed_from_level` | `firmware/lift/lib/hal_core/hal_core.{h,cpp}` | 純関数。ピンのレベル（`HIGH` = 1 / `LOW` = 0）から下端スイッチが押されているかを返す。`BOTTOM_PRESSED_LEVEL` のときだけ `true` |
| `motor_duty_to_pwm` | `firmware/lift/lib/hal_core/hal_core.{h,cpp}` | 純関数。デューティ [%] を LEDC に書く値（0〜`(1 << resolution) - 1`）にする。停止（0%）は必ず 0 になる |
| `LiftEsp32Hal` | `firmware/lift/src/hal_esp32.{h,cpp}` | `LiftHal` の実物。MD10C（LEDC）・下端スイッチ・HC-SR04（ECHO は割り込みで測る）。`begin()` でピンを整え、**モータを停止から始める**。`poll(now_ms)` は `loop()` から測定を出すために呼ぶ |
| `key_action` | `tools/lift_probe.py` | 純関数。キーを `KEY_ACTIONS` の操作名に変える。知らないキーは `None` |
| `clamp_duty` | `tools/lift_probe.py` | 純関数。デューティを 0〜100 に丸める（[-protocol.md](DetailedDesign-protocol.md) §2.2） |
| `apply_action` | `tools/lift_probe.py` | 純関数。操作名を `state` へ適用した新しい `state` を返す（元は変えない） |
| `build_cmd` | `tools/lift_probe.py` | 純関数。`state` から送る `cmd` の JSON オブジェクトを作る。**`dir` が `up` / `down` でなくても `ceil_ok` を必ず載せる**（[-protocol.md](DetailedDesign-protocol.md) §2.2） |
| `format_state` | `tools/lift_probe.py` | 純関数。受け取った `state` を 1 行の文字列にする（試験と画面出力で使う） |
| `main` | `tools/lift_probe.py` | `python3 tools/lift_probe.py ws://<ESP32>/ws` の入口 |

`WP-CAM-02` で足した関数・クラス（§0 の命名規則に従う）:

| 名前 | 置き場 | 何か |
| --- | --- | --- |
| `HardwareBase` | `camera/hve_camera/hw/base.py` | ハードウェアの抽象。ピッチのサーボ・ヨーのステッピング・天井の距離計。**判定は持たない**（天井の許可は `ceiling.py`、上下端は昇降部側。`DD-4`） |
| `read_ceiling` | `camera/hve_camera/hw/base.py` | 天井の距離を 1 回測って `CeilingReading` で返す。**測れなかったことと、新しい測定が無いことをどちらも `None` で表す**（最後の読み値は呼び出し側が保持する） |
| `set_pitch` | `camera/hve_camera/hw/base.py` | ピッチのサーボへ角度を出す |
| `drive_yaw` | `camera/hve_camera/hw/base.py` | ヨーを 1 秒あたりの半ステップ数と向きで回す |
| `stop_yaw` | `camera/hve_camera/hw/base.py` | ヨーを止める |
| `close` | `camera/hve_camera/hw/base.py` | ハードウェアを片付ける |
| `FakeHardware` | `camera/hve_camera/hw/fake_hw.py` | `HardwareBase` の偽物。出した値を記録し、天井の読み値は外から差し替える |
| `set_ceiling` | `camera/hve_camera/hw/fake_hw.py` | 天井の「次の測定値」（状態と距離 mm）を差し込む |
| `freeze_ceiling` | `camera/hve_camera/hw/fake_hw.py` | 以降新しい天井の測定値を返さない（読み値が止まった・古い状態を再現する） |
| `fake_lift_decide` | `camera/hve_camera/hw/fake_lift.py` | 偽の昇降部の判定（純関数）。[DetailedDesign.md](DetailedDesign.md) §4.1 の表を Python で写す。**本物の判定は ESP32 側**（`firmware/lift`）で、ここは画面を動かすための真似 |
| `FakeLift` | `camera/hve_camera/hw/fake_lift.py` | プロセス内の偽の昇降部。**`LiftPort` と同じ使い方ができる**（偽物のモード用） |
| `set_height_mm` / `set_bottom` | `camera/hve_camera/hw/fake_lift.py` | 偽の昇降の高さと下端スイッチ |
| `LiftPort` | `camera/hve_camera/lift_link.py` | 昇降部への出口の抽象。`LiftLink` と `FakeLift` が実装する（「同じ使い方ができる」の根拠） |
| `start` / `close` | `camera/hve_camera/lift_link.py` | 昇降部と繋がり始める（`LiftLink` は切れたら再接続し続ける）／片付ける |
| `send_cmd` | `camera/hve_camera/lift_link.py` | 昇降部へ `cmd` を送る。**`seq` はここが数える**（[-protocol.md](DetailedDesign-protocol.md) §2.2） |
| `link_ok` | `camera/hve_camera/lift_link.py` | 昇降部から `state` が `lift_state_timeout_ms` 以内に届いていれば `True`。**`False` の間が `LINK_LOST`** |
| `latest_state` / `state_received_at_ms` | `camera/hve_camera/lift_link.py` | 最後に受けた `state` とその受信時刻 |
| `LiftLink` | `camera/hve_camera/lift_link.py` | 昇降部への WS クライアント。切れても再接続し続ける（その間は `LINK_LOST`） |
| `ControlLoop` | `camera/hve_camera/control.py` | 制御ループ。操作の鮮度・動かす軸・速度を組み立て、昇降部への指令・ピッチ・ヨーを効かせ、状態を集める |
| `hold` / `release` | `camera/hve_camera/control.py` | 画面から受け取った操作を覚える／押していない状態に戻す |
| `set_zoom` | `camera/hve_camera/control.py` | 倍率を 1〜`zoom_max`・`zoom_step` の倍数に丸めて持ち、**変わったときだけ** `VideoZoom` へ渡す |
| `step` | `camera/hve_camera/control.py` | 制御ループ 1 回。**昇降部への指令を先に送り、そのあと天井を測る**（100 ms の指令を I2C の待ち时间和せない） |
| `build_state` | `camera/hve_camera/control.py` | 画面へ配る `state`（[-protocol.md](DetailedDesign-protocol.md) §2.4）を組み立てる |
| `CameraApp` | `camera/hve_camera/app.py` | 画面・設定 API・ブラウザとの WS を持つアプリ |
| `create_app` | `camera/hve_camera/app.py` | `CameraApp` を作って aiohttp の `Application` を返す（**起動と試験が同じ物を使う**） |
| `control_step` / `broadcast_state` | `camera/hve_camera/app.py` | 制御ループ 1 回／`state` を全画面へ配る。**立ち上げたタスクも試験も同じ関数を使う** |
| `VideoZoom` | `camera/hve_camera/app.py` | 倍率を `hve_video` へ送る。偽物に差し替えられる。**`hve_video` が居なくてもアプリは止まらない**（逢わなかった倍率は次に送る）。`close` でセッションを閉じる |
| `send_zoom` | `camera/hve_camera/app.py` | `VideoZoom` の送り口。倍率を受け取って `POST http://127.0.0.1:<video_port>/zoom`。**失敗しても例外を投げない**（次の `send_zoom` で送り直す。送れたかどうかを返す） |
| `main` | `camera/hve_camera/__main__.py` | `python3 -m hve_camera [--fake] [--port N]` の入口 |

`WP-CAM-03` で足した関数・型（§0 の命名規則に従う）:

| 名前 | 置き場 | 何か |
| --- | --- | --- |
| `pitch_to_pulse_ns` | `camera/hve_camera/hw/rpi_hw.py` | 純関数。ピッチの角度を SG90 のパルス幅 [ns] にする。0° を `SG90_PULSE_CENTER_NS` として 1 度につき `SG90_NS_PER_DEG` を足し、`SG90_PULSE_MIN_NS`〜`SG90_PULSE_MAX_NS` に丸める（外側へ出さない） |
| `srf02_reading` | `camera/hve_camera/hw/rpi_hw.py` | 純関数。SRF02 の返り値（cm）または I2C の例外を `CeilingReading` にする。**例外は `READ_ERROR`**・`0`（反射なし）と `SRF02_BUSY_RAW`（測定中）は別の扱い・`srf02_max_range_mm` を超える値は `NO_ECHO`・それ以外は `MEASURED`（mm） |
| `RpiHardware` | `camera/hve_camera/hw/rpi_hw.py` | `HardwareBase` の実物。SG90（カーネル PWM）・28BYJ-48（`lgpio`・専用スレッド）・SRF02（`smbus2`・専用スレッド）。**`lgpio`・`smbus2`・PWM の sysfs はコンストラクタで差し替えられる**（ホストの試験は偽物を差す。**ホストでは `lgpio` を import しない**） |
| `PwmChannel` | `camera/hve_camera/hw/rpi_hw.py` | カーネルの PWM 1 チャネル（`/sys/class/pwm/pwmchipN/pwm0`）。`export` → `period` → `duty_cycle` → `enable` の順に書く |
| `find_pwm_chip` | `camera/hve_camera/hw/rpi_hw.py` | `PWM_BASE` の中から使う PWM チップのディレクトリを 1 つ選ぶ |
| `LgpioPort` | `camera/hve_camera/hw/rpi_hw.py` | `lgpio` のチップハンドル分销。4 つの `YAW_IN*_PIN` を出力として確保し、書き換える。**`lgpio` はこの中でだけ import する**（ホストには入っていない） |
| `open_srf02_bus` | `camera/hve_camera/hw/rpi_hw.py` | `smbus2.SMBus` を作る。**`smbus2` はこの中でだけ import する** |
| `is_raspberry_pi` | `camera/hve_camera/hw/rpi_hw.py` | `RPI_MODEL_PATH` に"Raspberry Pi"と書いてあるか。ラズパイ以外で `--fake` 無しの起動を止めるのに使う |

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
| `TOP` | 昇降部 | 上端に達した |
| `BOTTOM` | 昇降部 | 下端スイッチが押されている |
| `MAX_RUN` | 昇降部 | 連続駆動の上限（spec [Spec-safety.md](../spec/Spec-safety.md) §2 #4b） |
| `CEILING_NEAR` | カメラ部 | 天井が近い |
| `CEILING_STALE` | カメラ部 | 天井の値が読めない・古い |
| `HOLD_TIMEOUT` | カメラ部 | 画面からの操作が途絶えた |
| `LINK_LOST` | カメラ部 | 昇降部と繋がっていない |
| `AXIS_LIMIT` | カメラ部 | ピッチが可動範囲の端（ヨーには範囲が無い） |
| `OUT_OF_RANGE` | カメラ部（天井の理由のみ。**停止理由ではない**） | 天井の距離計に反射が返らない。上昇は許す |

## 4. メッセージ

種類 `t` は `hold` / `release` / `cmd` / `state`。フィールドは [-protocol.md](DetailedDesign-protocol.md) §2 が正。

`POST /api/fake`（[-protocol.md](DetailedDesign-protocol.md) §3。**偽物のモードのときだけ存在する**）のフィールド:

| フィールド | 値 |
| --- | --- |
| `ceiling` | 偽の天井。`{"status": "MEASURED" / "NO_ECHO" / "READ_ERROR", "mm": 1450}`（`status`・`mm` は省略可。`NO_ECHO` / `READ_ERROR` では `mm` を要らない）。**`{"stale": true}` なら「以降新しい測定値を返さない」**（`freeze_ceiling`） |
| `height_mm` | 偽の昇降の高さ [mm] |
| `bottom` | 偽の下端スイッチ |

**指定しなかったものは動かない。**`ceiling` の `status`・`mm` は画面へ配る `state` の `ceiling`（[-protocol.md](DetailedDesign-protocol.md) §2.4）と、
`height_mm`・`bottom` は昇降部の `state`（同 §2.3）と同じ名前・同じ意味。

`GET` / `PUT /api/settings`（同 §3）の返り値。**検証に通らなければ 400 と理由の一覧**を返し、**保存しない**:

| フィールド | 値 |
| --- | --- |
| `settings` | 現在の設定（4 軸の `min` / `max` / `init`） |
| `using_defaults` | 設定ファイルが無い・壊れている・検証を通らないので**既定値で動いている**か（`true` の間だけ画面に出す） |
| `errors` | **`400` のときだけ**入る。通らなかった理由の一覧（`validate_settings` の戻り値そのまま） |

## 5. パラメータと仮値

**`仮` は根拠の無い仮置き。**実測（`WP-MEAS-*`）で置き換えたら `仮` を外し、出どころを書く。

**昇降部の判定に使う定数は `config.h` ではなく `firmware/lift/lib/lift_core/lift_decide.h` に置く**
（`Arduino.h` を include しない `lift_core` のなかでホスト試験するため。2026-09-28 `WP-LIFT-01` で直した）。

| 名前 | 置き場 | 値 | 出どころ |
| --- | --- | --- | --- |
| `LIFT_CMD_TIMEOUT_MS` | `lift_core/lift_decide.h` | 600 | 先行試作・th-system のウォッチドッグの実績 |
| `HEIGHT_STALE_MS` | `lift_core/lift_decide.h` | 600 | **仮**（spec [Spec-safety.md](../spec/Spec-safety.md) §2） |
| `LIFT_TOP_MM` | `lift_core/lift_decide.h`（既定値。設定値は `LiftController` の引数） | 未設定（`-1`）。**未設定の間は上端で止めない** | `WP-MEAS-01` で決める（spec [Spec-safety.md](../spec/Spec-safety.md) §2 #2a） |
| `LIFT_MAX_RUN_MS` | `lift_core/lift_decide.h` | 10000 | **仮**・先行試作の値。全行程の実測の約 1.5 倍に置き換える（spec #4b） |
| `LIFT_DUTY_ABS_MAX_PCT` | `lift_core/lift_decide.h` | 100 | MD10C の上限 |
| `LIFT_STATE_PERIOD_MS` | 昇降部 `config.h` | 100 | **仮** |
| `SONAR_PERIOD_MS` | 両方 | 100 | **仮** |
| `PWM_FREQ_HZ` / `PWM_RESOLUTION` | 昇降部 `config.h` | 5000 / 8 | 先行試作 |
| `PWM_CH` | 昇降部 `config.h` | 0 | 先行試作（LEDC チャネル 0） |
| `SONAR_ECHO_TIMEOUT_US` | `hal_core/hal_core.h` | 30000 | **仮**（**割り込み**で ECHO の時間切れを判定する時間。4 m 往復の約 23.5 ms より長く、HC-SR04 の 1 回 60 ms の周期より短い。実機で確認する） |
| `SONAR_MIN_RANGE_MM` | `hal_core/hal_core.h` | 20 | HC-SR04 のデータシート（約 2 cm。**これより近い値は「高さが読めない」**） |
| `SONAR_MAX_RANGE_MM` | `hal_core/hal_core.h` | 4000 | HC-SR04 のデータシート（約 4 m。**これより遠い値・時間切れも「高さが読めない」**） |
| `BOTTOM_PRESSED_LEVEL` | `hal_core/hal_core.h` | 1（`HIGH`） | [-hardware.md](DetailedDesign-hardware.md) §1（**常時閉（NC）配線**。押すと開いて HIGH。断線も HIGH なので「押されている」側に倒れる）。`0` にすると押されたら LOW の配線になる |
| `MOTOR_DIR_PIN` / `MOTOR_PWM_PIN` | 昇降部 `config.h` | 14 / 32 | [-hardware.md](DetailedDesign-hardware.md) §1（先行試作と同じ） |
| `MOTOR_DIR_UP_LEVEL` | 昇降部 `config.h` | `true` | **仮**（DIR=HIGH で上昇かは**実機未確認**。`false` にすると逆向きになる） |
| `BOTTOM_PIN` | 昇降部 `config.h` | 27 | **仮**（[-hardware.md](DetailedDesign-hardware.md) §1。`INPUT_PULLUP`） |
| `SONAR_TRIG_PIN` / `SONAR_ECHO_PIN` | 昇降部 `config.h` | 25 / 26 | **仮**（同上。**ECHO は分圧して 3.3 V にしてから入れる**） |
| `LIFT_MDNS_NAME` | 昇降部 `config.h` | `hve-lift` | §2 |
| `LIFT_HTTP_PORT` / `LIFT_WS_PATH` | 昇降部 `config.h` | 80 / `/ws` | [-protocol.md](DetailedDesign-protocol.md) §1 |
| `LIFT_STATE_TEXT_MAX` | 昇降部 `config.h` | 256 | **仮**（`state` の JSON は 200 バイト未満。`state_encode` のバッファ） |
| `LIFT_WIFI_POLL_MS` | 昇降部 `config.h` | 5000 | **仮**（無線が落ちていたら張り直すだけなので短くなくてよい） |
| `ceiling_margin_mm` | カメラ部 `params.toml` | 500 | **仮**（`H-V8`） |
| `ceiling_stale_ms` | カメラ部 | 600 | **仮**（spec [Spec-safety.md](../spec/Spec-safety.md) §2） |
| `hold_timeout_ms` | カメラ部 | 400 | **仮** |
| `lift_probe_period_ms` | `tools/lift_probe.py` | 100 | **仮**（下の `lift_cmd_period_ms` と同じ。停止中も `stop` を送り続ける） |
| `lift_probe_duty_pct` | `tools/lift_probe.py` | 40 | **仮**（[-protocol.md](DetailedDesign-protocol.md) §2.2 の例の値） |
| `lift_probe_duty_step_pct` | `tools/lift_probe.py` | 5 | **仮**（`+` / `-` での増減） |
| `lift_probe_keys` | `tools/lift_probe.py`（`KEY_ACTIONS`） | `u`=上昇 / `d`=下降 / `s`=停止 / `c`=`ceil_ok` の反転 / `+` と `=`=デューティを 5 増やす / `-`=5 減らす / `x`=送信を止める・再開する / `q`=終了 | 昇降部の操作は将来画面が担うので、ここは**開発用の**最小限の集合 |
| `lift_cmd_period_ms` | カメラ部 | 100 | **仮** |
| `lift_state_timeout_ms` | カメラ部 | 600 | **仮** |
| `state_period_ms` | カメラ部 | 100 | **仮** |
| `ui_hold_period_ms` / `ui_state_timeout_ms` | 画面 | 100 / 1000 | **仮** |
| `axis_speed_abs_max_dps` | カメラ部 | 60 | 28BYJ-48 の実用の上限（約 60〜90 deg/s）の下側。**実測で確定** |
| `yaw_steps_per_rev` | カメラ部 | 4096 | 28BYJ-48 の半ステップ（資料により 4076 とも。**実測で確定**） |
| `srf02_i2c_addr` | カメラ部 | 0x70（7 bit） | SRF02 の工場出荷値 |
| `srf02_min_range_mm` / `srf02_max_range_mm` | カメラ部 | 150 / 6000 | SRF02 のデータシート。扱いは spec [Spec-safety.md](../spec/Spec-safety.md) §2 #3a・#3b |
| `srf02_ranging_wait_ms` | カメラ部 | 70 | SRF02 のデータシート（測定に約 66 ms） |
| `pitch_min_deg` / `pitch_max_deg` | カメラ部 | -45 / 45 | **仮**（`H-V5`・`H-X5`。SG90 自体は約 ±90°） |
| `zoom_max` / `zoom_step` | カメラ部 | 4 / 0.5 | spec [Spec-ui.md](../spec/Spec-ui.md) §1.5（2026-09-25 決定） |
| `settings_path` | カメラ部 | `~/hve_data/settings.json` | — |
| `lift_ws_url` | カメラ部 | `ws://hve-lift.local/ws` | §2 の `hve-lift`（アドレスの決め方は未確定。[-open.md](DetailedDesign-open.md) `D-1`） |
| `video_port` | カメラ部 | 8080 | [-protocol.md](DetailedDesign-protocol.md) §1・[DetailedDesign.md](DetailedDesign.md) §4.3 |
| `provisional` | カメラ部 `params.toml` | 下の 13 個の配列 | `DD-3`。**この表の「カメラ部」と「`hve_video`」の行のうち `仮` と書いてあるもの全部**を並べたもの。画面に出す（[-protocol.md](DetailedDesign-protocol.md) §2.4）。実測（`WP-MEAS-*`）で置き換えたらここから外す |
| 設定の既定値 | カメラ部 | [-protocol.md](DetailedDesign-protocol.md) §3 の例の値 | **仮** |
| `video_capture_width` / `video_capture_height` | `hve_video` | 1920 / 1080 | **仮**（カメラの型番未定。[-hardware.md](DetailedDesign-hardware.md) §2） |
| `video_out_height` | `hve_video` | 480（幅は取り込みの縦横比に合わせる） | **仮**（`H-A8`） |
| `video_fps` / `video_jpeg_quality` | `hve_video` | 10 / 60 | **仮**（`H-A8`） |

**カメラ部の実物の定数（`WP-CAM-03`）は `camera/hve_camera/hw/rpi_hw.py` に置く。**
`params.toml` に入れなかったのは、部品のデータシートの値、または配線で決まる値だから。
**データシートの出典は Devantech「SRF02 Ultrasonic Range Finder Technical Specification — I2C Mode」**
（`http://www.robot-electronics.co.uk/htm/srf02techI2C.htm`）。

| 名前 | 置き場 | 値 | 出どころ |
| --- | --- | --- | --- |
| `SG90_PERIOD_NS` | `hw/rpi_hw.py` | 20000000 | SG90 のデータシート（50 Hz） |
| `SG90_PULSE_CENTER_NS` | `hw/rpi_hw.py` | 1500000 | 同（0° ＝ 正面・水平。[-hardware.md](DetailedDesign-hardware.md) §2.3「動いても害の無い向きを初期角」）。`control.py` の初期角 0° と揃える |
| `SG90_NS_PER_DEG` | `hw/rpi_hw.py` | 10000 | 同（1 度あたり 10 µs。0.5〜2.4 ms が約 -100°〜+90° に対応する） |
| `SG90_PULSE_MIN_NS` / `SG90_PULSE_MAX_NS` | `hw/rpi_hw.py` | 500000 / 2400000 | 同。**この外側へパルス幅を出さない**（角度は `pitch_min_deg`〜`pitch_max_deg` で止める。`axes.py` の `pitch_step`） |
| `SG90_PWM_PIN` | `hw/rpi_hw.py` | 18 | [-hardware.md](DetailedDesign-hardware.md) §2.2（**仮**） |
| `YAW_IN1_PIN` / `YAW_IN2_PIN` / `YAW_IN3_PIN` / `YAW_IN4_PIN` | `hw/rpi_hw.py` | 23 / 24 / 25 / 16 | 同（**仮**） |
| `YAW_HALF_STEP_SEQUENCE` | `hw/rpi_hw.py` | `(0b0001, 0b0101, 0b0100, 0b0110, 0b0010, 0b1010, 0b1000, 0b1001)` | ULN2003 の 2 相励磁の半ステップ。**下位ビットから IN1〜IN4**。区間をまたぐ相（1 本のコイルと 2 本のコイルが切り替わる所）で脱調しやすいので実機で確認する |
| `YAW_STEP_SIGN` | `hw/rpi_hw.py` | `left` = +1 / `right` = -1 | **仮**（どちらが左かは配線と、ギアの減速比の向きで決まる。実機で確認する。昇降部の `MOTOR_DIR_UP_LEVEL` と同じ扱い） |
| `YAW_MIN_STEP_INTERVAL_S` | `hw/rpi_hw.py` | 0.0002 | **仮**（設定の上限 `axis_speed_abs_max_dps` 60 deg/s なら 1.46 ms 止まり。**それより短い間隔は刻まない**） |
| `SRF02_COMMAND_REGISTER` / `SRF02_RESULT_REGISTER` | `hw/rpi_hw.py` | 0x00 / 0x02 | Devantech SRF02 I2C 仕様。**書けるのはロケーション 0 だけ**。結果はロケーション 2・3 の 16 bit（上位バイト先頭） |
| `SRF02_RANGING_CMD_CM` | `hw/rpi_hw.py` | 0x51 | 同（ranging を始めて cm で返すコマンド。**mm を返すコマンドは無い**。 hasilnya cm なので `MM_PER_CM` で mm にする） |
| `SRF02_BUSY_RAW` | `hw/rpi_hw.py` | 0xFFFF | 同（**ranging 中は応答が無く 255 が返る**ので「測定中」は `0xFFFF` で分かる。`0`（反射なし）とは別の `READ_ERROR` にする。仕様の `#3b` と `#4` を取り違えないため） |
| `MM_PER_CM` | `hw/rpi_hw.py` | 10 | SRF02 は inches・cm・µs の 3 種類しか返さない（データシート）。天井の判定は mm なので cm を受ける |
| `PWM_BASE` | `hw/rpi_hw.py` | `/sys/class/pwm` | Linux のカーネル PWM。**書けないときは udev の規則が要る**（[DetailedDesign.md](DetailedDesign.md) §4.4） |
| `RPI_MODEL_PATH` | `hw/rpi_hw.py` | `/proc/device-tree/model` | ラズパイかどうかの判定（ラズパイ以外で `--fake` 無しの起動を止める） |

## 6. th-system 側の名前（参照のみ）

**th-system とは通信しない**ので、実装でこれらを使うことは無い。th-system の文書を読むときの手がかりとしてだけ残す。

| 名前 | 何か | 出典 |
| --- | --- | --- |
| `AT_PANEL` | th-system の盤前のモード。状態は `IDLE_P` ／ `WORKING`（作業中ボタン ON）／ `PAUSE`（ジョグ中） | th-system `docs/plan/detailed/DetailedDesign-names.md` §2・§3 |
| `th-rpi-ap` | th-system のラズパイが出す AP（192.168.5.1）。PC は 192.168.5.50 固定 | th-system `docs/network.md` |
