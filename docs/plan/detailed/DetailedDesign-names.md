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
| `firmware/lift/test/test_lift_core/`・`test_lift_decide/`・`test_lift_controller/`・`test_cmd_codec/` | Unity の試験（`env:native`。1 ディレクトリ 1 試験で、それぞれ `test_main.cpp` を持つ） |
| `camera/hve_camera/` | `__main__.py`・`app.py`・`control.py`・`lift_link.py`・`ceiling.py`・`settings.py`・`axes.py`・`params.py`・`hw/{base,rpi_hw,fake_hw,fake_lift}.py` |
| `camera/hve_video/` | `__main__.py`・`crop.py`・`pipeline.py`・`server.py`・`sources.py`（実物の V4L2 と、試験用の偽の画像列） |
| `camera/web/` | `index.html`・`app.js`・`settings.js`・`style.css`。**設定画面は `index.html` のオーバーレイ**（別ページではない。モックアップと同じ） |
| `camera/config/params.toml` | パラメータ（§5 のカメラ部の行） |
| `camera/requirements.txt` ／ `camera/requirements-dev.txt` | ラズパイで pip で入れるもの（`aiohttp`・`smbus2`） ／ ホストの試験用（[DetailedDesign.md](DetailedDesign.md) §4.5） |
| `camera/tests/` | pytest |
| `camera/systemd/` | `hve-camera.service`・`hve-video.service` |
| `tools/` | 実機の確認用スクリプト（`lift_probe.py` 等） |

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

`WP-UI-01` で足した関数（§0 の命名規則に従う。**画面は素の HTML・CSS・JavaScript。ビルドも外部の読み込みもしない**ので、
ブラウザでは上位スコープの宣言がそのまま共有される。Node の試験からは `module.exports` 越しに使う）:

| 名前 | 置き場 | 何か |
| --- | --- | --- |
| `holdMessage` | `camera/web/app.js` | 送る `hold` の JSON を組み立てる純関数（protocol §2.1） |
| `releaseMessage` / `zoomMessage` | `camera/web/app.js` | 送る `release` / `zoom` の JSON |
| `zoomTarget` | `camera/web/app.js` | 「＋」「−」で次に送る倍率。1〜`zoom_max` に丸め `zoom_step` の倍数にそろえる |
| `speedFor` | `camera/web/app.js` | 軸 → 送る速度。`pitch_*` は `pitch`・`yaw_*` は `yaw` を見る（`pitch_up` と `pitch_down` は同じ枠） |
| `sliderSpec` | `camera/web/app.js` | スライダーの範囲（設定の下限〜上限）と初期位置（設定の初期値）。**画面を開くたびに初期値**（spec [Spec-ui.md](../spec/Spec-ui.md) §1） |
| `formatSpeed` | `camera/web/app.js` | 速度の数値の書式（`%` は整数・`deg/s` は小数 1 桁） |
| `streamUrl` | `camera/web/app.js` | 映像（`hve_video` の `/stream`）の URL。**宿主は画面と同じにする**（protocol §2.4 の `video_port`） |
| `reasonText` | `camera/web/app.js` | 停止理由（[-names.md](DetailedDesign-names.md) §3）→ `{色, 文言}`。`NONE` は `null`（帯を出さない） |
| `ceilingText` | `camera/web/app.js` | 天井のバッジの `{色, 文言}`（距離・値なし・範囲外。spec [Spec-safety.md](../spec/Spec-safety.md) §2 #3b） |
| `heightText` | `camera/web/app.js` | 高さの OSD の文言（読めない値・値なしを含む） |
| `holdBlocked` | `camera/web/app.js` | その軸のボタンを薄くするか。昇降部と切れている・天井の `ok` が `false` なら上昇、下端なら下降 |
| `stateStale` | `camera/web/app.js` | `state` が `ui_state_timeout_ms` 届かないか（「接続切れ」を出す） |
| `SETTING_AXES` | `camera/web/settings.js` | 設定画面の項目（名前・単位・絶対範囲）。4 項目（spec [Spec-ui.md](../spec/Spec-ui.md) §2） |
| `validateSettingsDraft` | `camera/web/settings.js` | 設定の検証。`min ≦ init ≦ max` と絶対範囲を見て、理由の一覧を返す（保存前に画面側でも確かめる） |
| `settingsErrorText` | `camera/web/settings.js` | 検証の理由の一覧を 1 行の文言にする（行を増やして画面からはみ出さない） |

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
| `ceiling_margin_mm` | カメラ部 `params.toml` | 500 | **仮**（`H-V8`） |
| `ceiling_stale_ms` | カメラ部 | 600 | **仮**（spec [Spec-safety.md](../spec/Spec-safety.md) §2） |
| `hold_timeout_ms` | カメラ部 | 400 | **仮** |
| `lift_cmd_period_ms` | カメラ部 | 100 | **仮** |
| `lift_state_timeout_ms` | カメラ部 | 600 | **仮** |
| `state_period_ms` | カメラ部 | 100 | **仮** |
| `ui_hold_period_ms` / `ui_state_timeout_ms` | 画面 | 100 / 1000 | **仮** |
| `lift_gauge_full_mm` | 画面 | 1800 | **仮**（操作画面の高さのゲージの満量。`lift.top_mm` が設定されたらその値を使う。`WP-MEAS-01` で決まる `LIFT_TOP_MM` が確定したら置き換え） |
| `axis_speed_abs_max_dps` | カメラ部 | 60 | 28BYJ-48 の実用の上限（約 60〜90 deg/s）の下側。**実測で確定** |
| `yaw_steps_per_rev` | カメラ部 | 4096 | 28BYJ-48 の半ステップ（資料により 4076 とも。**実測で確定**） |
| `srf02_i2c_addr` | カメラ部 | 0x70（7 bit） | SRF02 の工場出荷値 |
| `srf02_min_range_mm` / `srf02_max_range_mm` | カメラ部 | 150 / 6000 | SRF02 のデータシート。扱いは spec [Spec-safety.md](../spec/Spec-safety.md) §2 #3a・#3b |
| `srf02_ranging_wait_ms` | カメラ部 | 70 | SRF02 のデータシート（測定に約 66 ms） |
| `pitch_min_deg` / `pitch_max_deg` | カメラ部 | -45 / 45 | **仮**（`H-V5`・`H-X5`。SG90 自体は約 ±90°） |
| `zoom_max` / `zoom_step` | カメラ部（画面も「＋」「−」の 1 段に使う） | 4 / 0.5 | spec [Spec-ui.md](../spec/Spec-ui.md) §1.5（2026-09-25 決定） |
| `settings_path` | カメラ部 | `~/hve_data/settings.json` | — |
| `lift_ws_url` | カメラ部 | `ws://hve-lift.local/ws` | §2 の `hve-lift`（アドレスの決め方は未確定。[-open.md](DetailedDesign-open.md) `D-1`） |
| `video_port` | カメラ部 | 8080 | [-protocol.md](DetailedDesign-protocol.md) §1・[DetailedDesign.md](DetailedDesign.md) §4.3 |
| `provisional` | カメラ部 `params.toml` | 下の 13 個の配列 | `DD-3`。**この表の「カメラ部」と「`hve_video`」の行のうち `仮` と書いてあるもの全部**を並べたもの。画面に出す（[-protocol.md](DetailedDesign-protocol.md) §2.4）。実測（`WP-MEAS-*`）で置き換えたらここから外す |
| 設定の既定値 | カメラ部 | [-protocol.md](DetailedDesign-protocol.md) §3 の例の値 | **仮** |
| `video_capture_width` / `video_capture_height` | `hve_video` | 1920 / 1080 | **仮**（カメラの型番未定。[-hardware.md](DetailedDesign-hardware.md) §2） |
| `video_out_height` | `hve_video` | 480（幅は取り込みの縦横比に合わせる） | **仮**（`H-A8`） |
| `video_fps` / `video_jpeg_quality` | `hve_video` | 10 / 60 | **仮**（`H-A8`） |

## 6. th-system 側の名前（参照のみ）

**th-system とは通信しない**ので、実装でこれらを使うことは無い。th-system の文書を読むときの手がかりとしてだけ残す。

| 名前 | 何か | 出典 |
| --- | --- | --- |
| `AT_PANEL` | th-system の盤前のモード。状態は `IDLE_P` ／ `WORKING`（作業中ボタン ON）／ `PAUSE`（ジョグ中） | th-system `docs/plan/detailed/DetailedDesign-names.md` §2・§3 |
| `th-rpi-ap` | th-system のラズパイが出す AP（192.168.5.1）。PC は 192.168.5.50 固定 | th-system `docs/network.md` |
