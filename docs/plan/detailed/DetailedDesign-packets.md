# 作業パケット（v2）

[DetailedDesign.md](DetailedDesign.md) §5 の詳細。**1 パケット ＝ 1 ブリーフ ＝ 1 ブランチ**
（[ImplementationPlan.md](../ImplementationPlan.md) §2）。旧版は [archive/v1/detailed/DetailedDesign-packets.md](../archive/v1/detailed/DetailedDesign-packets.md)。
**ID は旧版の続きから振った。旧版の ID を別の意味で使わない**（`WP-MEAS-01`〜`05` だけは意味が同じなので引き継ぐ）。

---

## 0. 全パケット共通の約束

旧版 §0 の約束（読む節だけ読む・名前は -names.md にあるものだけ・受け入れはコマンド・変異で赤・th-system に触らない・一時ファイルは `.briefs/tmp/`・仮値は -names.md のまま）に加えて:

- **触ったファイルのコメントにある設計書への参照を v2 に直す**（`docs/plan/detailed/…` は v2 を指すようになった。旧版を指したいときは `docs/plan/archive/v1/…`）
- **カメラモジュールのアプリ（`camera/`）は Python 3.8 で動く書き方にする**（`DD-7`）。試験は `.venv38` で回す
- **`WAIVER(demo): W-1` のタグは台帳と 1 対 1**（[EXCEPTION-LEDGER.md](../EXCEPTION-LEDGER.md)）。勝手に増やさない・消さない

共通のコマンド（リポジトリ直下から。`WP-BASE-02` のあと）:

```bash
pio test -d firmware/lift   -e native      # 昇降部のホスト試験
pio run  -d firmware/lift   -e esp32dev    # 昇降部のビルド
pio test -d firmware/cam_io -e native      # Arduino のホスト試験
pio run  -d firmware/cam_io -e uno         # Arduino のビルド
.venv38/bin/python -m pytest camera/tests  # カメラモジュール（Python 3.8）
.venv/bin/python -m pytest tools/tests     # 道具
node firmware/lift/web/tests/web.test.js   # 昇降部の画面の純関数
```

## 1. 一覧

| ID | 段階 | 内容 | 先に要るもの | 実機 |
| --- | --- | --- | --- | --- |
| `WP-MEAS-06` | 0 | **UnitV2 の実機調査**（[-hardware.md](DetailedDesign-hardware.md) §2.1.1 を全部埋める）。**2026-10-06・07 に実施**（UART の電圧・UNO との双方向も済み。2 の再起動後の STA は繋がらないことがあり、対策 `P-13` を `WP-CAM-05` で確かめる） | — | 要（UnitV2・UNO） |
| `WP-BASE-02` | 0 | `.venv38`・`firmware/cam_io` の骨格・`CLAUDE.md` のビルドと試験の節 | — | 不要 |
| `WP-LIFT-03` | 1 | `lift_core` v2（`ceiling_check`・`LiftArbiter`・`lift_settings`・判定の変更・`W-1`・`cmd_codec`） | BASE-02 | 不要 |
| `WP-LIFTUI-01` | 1 | 昇降部の画面・`tools/fake_lift_server.py`・`tools/lift_probe.py` の v2 化 | LIFT-03（取り決めの確定） | 不要 |
| `WP-LIFT-04` | 1 | ESP32 の実物 v2（WS の 2 つの口・HTTP の設定 API・NVS・画面の埋め込み） | LIFT-03・LIFTUI-01 | 要（昇降部） |
| `WP-IO-01` | 2 | `io_core`（行・ウォッチドッグ・丸め・刻み）とホスト試験 | BASE-02 | 不要 |
| `WP-IO-02` | 2 | UNO の実物（Timer2・Timer1 の PWM・`Wire`・ウォッチドッグ） | IO-01 | 要（UNO・モータ・SRF02） |
| `WP-CAM-04` | 3 | カメラモジュールのアプリを Python 3.8 へ移し、v2 の取り決めにする（`uno_link`・`uno_hw`・`lift_link`・設定の中継・偽物のモード） | BASE-02・LIFT-03・**MEAS-06 の 1・2・5** | 不要 |
| `WP-VIDEO-02` | 3 | 映像を UnitV2 の取り込みに合わせる | MEAS-06 | 要（UnitV2） |
| `WP-UI-02` | 3 | カメラモジュールの画面を v2 にする | CAM-04 | 不要（偽物のモード） |
| `WP-CAM-05` | 3 | UnitV2 への配備・自動起動・実機の結合 | MEAS-06・CAM-04・VIDEO-02・UI-02・IO-02・LIFT-04 | 要（全部） |
| `WP-MEAS-01`〜`05` | 4 | 旧版と同じ（ストローク・遅延・無線の圧迫・古さと天井の余裕・電池） | 各実物 | 要 |
| `WP-LIFT-05` | 5 | **高さをエンコーダにする**（`EncoderHeight`・PCNT・HC-SR04 を外す・`P-14`）。spec 2026-10-15 | — | 要（昇降部・エンコーダ） |
| `WP-LIFT-06` | 5 | **速さのフィードバック**（`SpeedLoop`・`ENCODER_STALL`・`hold` の `speed`・設定を mm/s・NVS の名前空間） | LIFT-05 | 要（同上） |
| `WP-LIFTUI-02` | 5 | 昇降部の画面・偽の昇降部・`lift_probe` を `speed`（mm/s）・`ENCODER_STALL`・原点を取る前の表示に合わせる | LIFT-06（取り決め） | 不要 |
| `WP-CAM-06` | 5 | カメラモジュール（アプリ・画面）を `speed`（mm/s）・`ENCODER_STALL`・原点を取る前の表示に合わせる | LIFT-06（取り決め） | 不要（偽物のモード） |
| `WP-LIFT-08` | 5 | **モータへの出力の上限を設定に**（`motor_max_pct`・`SpeedLoop` の丸め・設定 API・NVS・昇降部の画面・偽の昇降部）。spec 2026-10-16 | LIFT-06 | 不要（実機の確認は管理担当） |
| `WP-CAM-07` | 5 | カメラモジュール（設定の検証・中継・偽物のモード・画面）に `motor_max_pct` を足す | LIFT-08（取り決め） | 不要（偽物のモード） |
| `WP-LIFT-07` | 5 | **上端の検知を戻す**（`LIFT_TOP_DETECT_ENABLED` を `true`・`WAIVER(demo): W-1` を消す・台帳を閉じる） | LIFT-05 の実機確認 | 要（昇降部） |

**段階 5（2026-10-15 の設計変更）**: `WP-LIFTUI-02` と `WP-CAM-06` は取り決め（[-protocol.md](DetailedDesign-protocol.md) §2）が書いてあるので `WP-LIFT-06` と並べて進められる。**ただし `hold` の `duty` → `speed` は互換が無いので、3 つ揃えてから実機に入れる。**
`WP-MEAS-01`（ストローク）で `ENC_PPR` の確認・`LIFT_UM_PER_COUNT`・速さの制御の係数・`LIFT_TOP_MM` を決める。

**`WP-LIFT-08` と `WP-CAM-07`**: 設定 API の `motor_max_pct` は必須なので、片方だけ新しいと `PUT` が `400` になる。**2 つ揃えてから実機に入れる。**`docs/使い方.md` の設定の節は `WP-LIFT-08` が書き、`WP-CAM-07` はカメラモジュールの画面の欄だけ足す（LIFT-08 のマージ後に始める）。

**`WP-MEAS-06` と `WP-LIFT-03` は並べて進められる**（昇降部は UnitV2 の調査に依らない）。
**`WP-CAM-04` は `WP-MEAS-06` の 1（サービスを止めて口を開ける）・2（STA）・5（依存）が通ってから始める。**通らなければ旧版の Python を載せる前提が崩れるので、設計を見直す。
**2026-10-06: 1・2・5 は通った**（`aiohttp` 3.6.2 などは OS に入っていた。[-hardware.md](DetailedDesign-hardware.md) §2.1.1）。

## 2. パケットの中身

### `WP-MEAS-06` UnitV2 の実機調査（管理担当・ユーザー）

- 読む節: [-hardware.md](DetailedDesign-hardware.md) §2・§3、[DetailedDesign.md](DetailedDesign.md) §4.4・§4.6
- やること: §2.1.1 の各行を実機で確かめ、**結果を -hardware.md §2.1.1 と -names.md §5 に書き戻す**。とくに:
  1. 組み込みのサービスを止め、`/dev/video0` と `/dev/ttyS1` を自分で開けること
  2. `th-rpi-ap` に STA で繋がり、再起動しても繋がること
  3. **Grove の UART の電圧（テスタ）とピンの並び**。UNO と分圧を挟んでつなぎ、`M`・`C` 相当の行が往復すること（115200 で化けないか）（**2026-10-07 済み**。黄＝TX・白＝RX・3.3 V・D0/D1・分圧・115200 で双方向とも抜け・化け 0）
  4. 取り込みと 480p の作り直しで何 fps 出るか・CPU・メモリ・温度（**2026-10-07 済み**。1920×1080 は取れず、MJPEG 720p を `v4l2-ctl` で受ける方式に決めた）
  5. `aiohttp`・`pyserial`（と名前解決の手段）を Python 3.8 で使えるか
- 受け入れ: -hardware.md §2.1.1 の全行に結果か「できない」が書かれている。[DetailedDesign.md](DetailedDesign.md) §4.4 の取り込みの方式が実測と合っている

### `WP-BASE-02` 土台 v2

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.7・[-names.md](DetailedDesign-names.md) §1
- 作るもの: `uv` で CPython 3.8 を入れた `.venv38`（`.gitignore`）。`firmware/cam_io` の骨格（`env:uno`・`env:native`。空でない試験 1 件）。`camera/requirements*.txt` を 3.8 で入る版に固定。`CLAUDE.md` の「ビルドとテスト」を §0 の共通コマンドに直す
- 受け入れ: `.venv38/bin/python --version` が 3.8。`pio test -d firmware/cam_io -e native` と `pio run -d firmware/cam_io -e uno` が成功

### `WP-LIFT-03` 昇降部の判定 v2

- 読む節: [DetailedDesign.md](DetailedDesign.md) §3.1〜§3.3・§4.1、[-protocol.md](DetailedDesign-protocol.md) §2・§3、[-names.md](DetailedDesign-names.md) §1・§3・§5.1
- 作るもの: `ceiling_check`・`LiftArbiter`・`lift_settings`・`lift_decide` の v2 の表・`LiftController`（持ち主・天井・判定を偽 HAL のモータに効かせる）・`cmd_codec`（`hello`・`hold`・`release`・`state`）。判定 #4・#6 の所に `WAIVER(demo): W-1`
- 受け入れ: `pio test -d firmware/lift -e native` が成功し、`grep -rn 'WAIVER(demo): W-1' firmware/lift` が 1 か所以上で台帳と一致。**次の変異がそれぞれ赤になる**（`LiftController` を通した試験で縛るものに ※）
  1. ※ `ceiling_check` の古さの判定を消す（`age_ms` だけ見て、受け取ってからの経過を足さない、を含む）
  2. ※ `ceiling` が欠けた `hold`（距離計を持つ接続）で上昇を許す
  3. ※ `hello` を受けていない `/ws/module` の接続を「距離計を持たない」とみなす
  4. 2 度目の `hello` の `ceiling_sensor: false` を受け付ける
  5. ※ `READ_ERROR` を `NO_ECHO` と同じに扱う
  6. ※ 持ち主でない接続の古い `press` の `hold` で持ち主を替える
  7. ※ 持ち主の接続が閉じても止めない
  8. ※ 持ち主が替わったら連続駆動の時間を数え直す
  9. `validate_lift_settings` が `min > init` を通す
  10. ※ `/ws/ui` の接続の上昇で天井の値を求める（spec #4c に反して単体操作で上昇できなくなる）
  11. `/ws/ui` で `hello` を受けても接続を続ける
  12. ※ 途絶（`CMD_TIMEOUT`）・`release`・`OWNER_GONE` で止まったあと、同じ接続の同じ `press` の `hold` で動き出す
  13. ※ `ceiling` が型の違う `hold` を捨てる（前の `hold` の値で動き続ける）

### `WP-LIFTUI-01` 昇降部の画面

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.2、[-protocol.md](DetailedDesign-protocol.md) §2・§3、spec [Spec-ui.md](../spec/Spec-ui.md) §0.1・§0.2・§1・§2
- 作るもの: `firmware/lift/web/`（旧版の `camera/web` の作りに揃える）・`tools/fake_lift_server.py`（`/ws/module` の天井の判定も同じ規則で真似る）・`tools/lift_probe.py` の v2 化・`tools/tests`
- 受け入れ: node の試験・`tools/tests` が成功。偽の昇降部に向けたスクロールの検査が全画面サイズで通る。**変異**: 離したときに `release` を送らない／押し始めで `press` を増やさない → 赤

### `WP-LIFT-04` 昇降部の実物 v2

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.1・§4.2、[-protocol.md](DetailedDesign-protocol.md) §1〜§3
- 作るもの: `main.cpp`（2 つの WS の口・接続の種類を `LiftController` へ渡す・`state` の配信）・HTTP の設定 API と NVS・`scripts/embed_web.py`
- 受け入れ: `pio run -d firmware/lift -e esp32dev` が成功。**実機で管理担当が確かめる**: 昇降部の画面から上昇・下降できる／`tools/lift_probe.py`（`/ws/module`）から天井 `TOO_NEAR` で上昇しない／probe を止めると 600 ms 以内に止まる／画面と probe で最後に押した方が勝つ／設定を変えて再起動しても残る／不正な設定は `400` で残らない

### `WP-IO-01` Arduino の判定

- 読む節: [DetailedDesign.md](DetailedDesign.md) §3.4・§4.5、[-protocol.md](DetailedDesign-protocol.md) §5、[-names.md](DetailedDesign-names.md) §5.4
- 作るもの: `io_codec`・`IoController`（偽 HAL）・`StepRate`
- 受け入れ: `pio test -d firmware/cam_io -e native` が成功し、**次の変異が赤**: ウォッチドッグを消す／途絶でコイルの電流を切らない／途絶でサーボのパルスを止める／ピッチを `PITCH_MIN_DEG`〜`PITCH_MAX_DEG` に丸めない／ヨーを `YAW_HSPS_ABS_MAX` に丸めない／読めない行を `M 0 0 0` として受け付ける（＝読めない行で生存確認が延びる）

### `WP-IO-02` Arduino の実物

- 読む節: [-hardware.md](DetailedDesign-hardware.md) §2.2〜§2.4・§3
- 作るもの: `hal_uno`・`main.cpp`
- 受け入れ: `pio run -d firmware/cam_io -e uno` が成功（`setWireTimeout` があること）。**実機で管理担当が確かめる**: 開発機から `M` 行を送ってヨー・ピッチが動く／送るのを止めると `IO_CMD_TIMEOUT_MS` 以内にヨーが止まりコイルが切れる（ULN2003 の LED）・ピッチは保つ／SRF02 の線を抜くと `C … 1 0` が出続け、`loop` が止まらない

### `WP-CAM-04` カメラモジュールのアプリ v2

- 読む節: [DetailedDesign.md](DetailedDesign.md) §3・§4.3・§4.6、[-protocol.md](DetailedDesign-protocol.md) §2・§4・§5、[-names.md](DetailedDesign-names.md) 全部
- 作るもの: 3.8 への書き直し・`classify_srf02`・`uno_link.py`・`hw/uno_hw.py`・`lift_link.py` v2・`lift_resolve.py`・設定の中継・偽物のモード v2。`hw/rpi_hw.py` と `test_rpi_hw.py` を消す
- **`press` を増やすきっかけ**: 昇降部へ送る昇降の操作が「押し始め」になったとき＝`control.py` の動かす軸が `lift_*` 以外から `lift_*` に替わったとき、`lift_up` と `lift_down` が入れ替わったとき、別の画面（ブラウザの接続）の `hold` に替わったとき
- 受け入れ: `.venv38/bin/python -m pytest camera/tests` が成功。**次の変異が赤**:
  1. `UnoClock` を使わず、受け取った時刻を読み値の時刻にする（溜まった古い行が新しく見える試験で縛る）
  1b. 前回の読み出しから `ceiling_read_stale_ms` より空いたときに、その回の行を捨てない（**受信バッファが溢れて新しい側の行が無い**場合の試験で縛る。窓の全行が遅れた行でも赤になること）
  2. 読むたびの全部の読み出しをやめ 1 行ずつ処理する
  3. `hello` の `ceiling_sensor` を天井の読み値の調子から計算する
  4. 昇降を離したときに `release` を送らない
  5. 設定の `PUT` で、昇降部が `400` を返したのに自分の 2 軸を保存する
  6. `classify_srf02` で `st=1` を `NO_ECHO` にする

### `WP-VIDEO-02` 映像の UnitV2 対応

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.4、`WP-MEAS-06` の結果
- 作るもの: `hve_video` の取り込み元を `MjpegPipeSource`（`v4l2-ctl` の子プロセス。**`ffmpeg`・OpenCV の `VideoCapture` は使わない**）に替え、区切りと作り直しを別スレッドにする。`GET /snapshot`（spec `H-U9`）を足す。3.8 で動く
- 受け入れ: ホストの試験（偽の画像列・`split_mjpeg` に `v4l2-ctl` の文字が頭に付いたバイト列と途中で切れたバイト列を与える）が `.venv38` で成功。UnitV2 で 480p の MJPEG が `video_fps` 前後で出て、倍率が変わり、`/snapshot` が 1280×720 の JPEG を返す。**15 分続けて** fps が落ちず、温度監視（`check_thermal.py`）と `haveged` が生きている
- **変異**（赤になること）: `split_mjpeg` が `FFD8` より前のバイトを捨てない／`/snapshot` が作り直した 480p（や切り出した絵）を返す／`/snapshot` を倍率に合わせて切り出す／作り直しが古い 1 枚を順に処理して遅れる（最新の 1 枚だけを使わない）

### `WP-UI-02` カメラモジュールの画面 v2

- 読む節: [-protocol.md](DetailedDesign-protocol.md) §4、spec [Spec-ui.md](../spec/Spec-ui.md)
- 作るもの: 「上端の検知: 一時無効」・持ち主の表示・昇降部の画面の数・昇降部へのリンク・`IO_LOST`・新しい停止理由の文言・昇降の設定（中継）・**静止画のボタンと重ねた表示**（spec §1.5.1。`:8080/snapshot` を取り寄せる。モックアップにも足す）
- 受け入れ: 旧版の node の試験とスクロールの検査が通る。**変異**: `top_detect` が `false` でも「一時無効」を出さない → 赤

### `WP-CAM-05` 配備と結合

- 読む節: `WP-MEAS-06` の結果・[-hardware.md](DetailedDesign-hardware.md) 全部
- 作るもの: `camera/deploy/`（自動起動）・配備の手順（`docs/使い方.md` の v2 の節）
- 受け入れ: **実機で管理担当が確かめる**: 電源を入れるだけで両方の画面が開ける／カメラモジュールの画面だけで昇降・ヨー・ピッチ・ズーム・全設定ができる／天井に近づくとカメラモジュールからの上昇が止まる／UnitV2 の電源を抜くと昇降が `CEILING_STALE_MS` 以内・ヨーが `IO_CMD_TIMEOUT_MS` 以内に止まる

### `WP-MEAS-*` 測定

旧版の [`WP-MEAS-*`](../archive/v1/detailed/DetailedDesign-packets.md) と同じ。v2 で変わる点: `WP-MEAS-01` は**上端の検知が無い（`W-1`）ので、上昇は人が見ながら短く行う**。`WP-MEAS-04` の天井の余裕・古さは昇降部の定数（`CEILING_MARGIN_MM`・`CEILING_STALE_MS`）を置き換える。**`WP-MEAS-03` は th-system を動かしながら 480p の映像を流し、`video_jpeg_quality`・`video_fps` の上限を決める**（spec `H-V10`・`H-A8`。高画質の映像 `H-V11` は測らない）。

### `WP-LIFT-05` 高さをエンコーダにする

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.1・§4.1.1、[-hardware.md](DetailedDesign-hardware.md) §1.1、[-names.md](DetailedDesign-names.md) §3・§5.1、spec [Spec-safety.md](../spec/Spec-safety.md) §1.2
- 作るもの: `lib/hal_core` の `EncoderHeight`（PCNT の 16 bit の差の足し込み・原点・`P-14`）とホスト試験。`hal_esp32` の PCNT（A/B の 4 逓倍・ノイズ除去）。**HC-SR04 のコード（`sonar_*`・`HeightFilter`・`SONAR_*`）と試験を消す**。`hal.h` のコメント。`config.h` のピン。**`docs/使い方.md`**（エンコーダの配線・HC-SR04 を外す・電源を入れたらまず下端まで下げる）。`docs/試験項目.md` の HC-SR04 の行は実機の試験を組むときに直す。**ブリーフの前に**、`espressif32@7.0.1` の Arduino コアで使える PCNT の API（`driver/pcnt.h` か `pulse_cnt`）をビルド環境で確かめる
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev` が成功。`grep -rn SONAR firmware/lift` が空。**次の変異がそれぞれ赤になる**（`LiftController` を通した試験で縛るものに ※）
  1. ※ 起動直後（下端に一度も着いていない）に `height_ok` を真にする（上昇できてしまう。`W-1` の間でも `state.height_ok` で縛る）
  2. 下端スイッチが押された瞬間に原点を取り直さない（2 回目以降の着地を含む）
  3. スイッチが押されている間、上昇側へ進んだときも原点を動かす（★が働かなくなる）
  4. スイッチが押されたまま原点から `HOME_SLACK_MM` を超えて上昇側へ進んでも `height_ok` を偽にしない（`P-14`）
  5. PCNT の 16 bit の差を符号なしで足す（−1 が +65535 になる）
  6. `ENC_UP_SIGN` を掛け忘れる
- **実機で管理担当が確かめる**: 手で巻き取り軸を回して数が増減する向き・起動直後は `height_ok: false`・下端に着けると 0・上げると増える・A/B の線を 1 本抜くと数が進まない

### `WP-LIFT-06` 速さのフィードバック

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.1・§4.1.2、[-protocol.md](DetailedDesign-protocol.md) §2・§3、[-names.md](DetailedDesign-names.md) §3・§5.1、spec [Spec-ui.md](../spec/Spec-ui.md) §1・[Spec-safety.md](../spec/Spec-safety.md) #4d
- 作るもの: `lib/lift_core` の `SpeedLoop`（PI・前向き・積分の扱い）・`lift_decide` の #8a と #9・`LiftController` で `SpeedLoop` の出力をモータへ・`cmd_codec`（`hold` の `speed`・`state` の `speed`・`speed_meas`・`ENCODER_STALL`）・`lift_settings` の範囲（mm/s）・`LIFT_NVS_NAMESPACE`・**`docs/使い方.md`**（速さの単位・`ENCODER_STALL` で止まったときの扱い）
- 受け入れ: native の試験・esp32dev のビルドが成功。**次の変異がそれぞれ赤になる**（※ は `LiftController` と偽 HAL で、モータに出した値を見て縛る）
  1. ※ 測った速さが目標より遅くても出力を上げない（前向きだけで動く）
  2. ※ 数が進まないのに出力を出し続ける（#8a を消す）
  3. ※ 逆向きに数が進んでも #8a で止めない
  4. ※ 止まった・向きが替わったときに積分を 0 に戻さない
  5. ※ 出力の丸め（`LIFT_DUTY_ABS_MAX_PCT`）を外す
  6. `duty` だけの `hold` を受け付ける
  7. 設定の検証が `LIFT_SPEED_ABS_MAX_MM_S` を超える値を通す
- **実機で管理担当が確かめる**（**`WP-LIFT-08` のマージ後**。それまでは出力が 100 % まで出る）: 同じ速さで上昇・下降の所要時間がほぼ同じ／巻き取り軸からエンコーダを外す（カップリングを抜く）と `ENCODER_STALL` で止まる／`state.speed_meas` が目標に収まる

### `WP-LIFTUI-02` 昇降部の画面・道具を速さ（mm/s）に

- 読む節: [-protocol.md](DetailedDesign-protocol.md) §2・§3、[-names.md](DetailedDesign-names.md) §3、spec [Spec-ui.md](../spec/Spec-ui.md) §1・§2
- 作るもの: `firmware/lift/web/`（`hold` の `speed`・スライダーと設定の単位 mm/s・測った速さの表示・`ENCODER_STALL` と「下端まで下げて高さの原点を取ってください」（`HEIGHT_UNKNOWN`）の文言）・`tools/fake_lift_server.py`・`tools/lift_probe.py`・**`docs/使い方.md`**（画面の文言）
- 受け入れ: node の試験・`tools/tests`・スクロールの検査が通る。**変異**: `hold` に `duty` を送る → 赤

### `WP-CAM-06` カメラモジュールを速さ（mm/s）に

- 読む節: [-protocol.md](DetailedDesign-protocol.md) §2・§4、[-names.md](DetailedDesign-names.md) §3
- 作るもの: `camera/hve_camera`（`lift_link` の `hold` の `speed`・設定の中継の範囲・`state.lift` の `speed`・`speed_meas`）・`camera/web`（単位 mm/s・`ENCODER_STALL`・`HEIGHT_UNKNOWN` の文言）・偽物のモード・**`docs/使い方.md`**（画面の文言）
- 受け入れ: `.venv38/bin/python -m pytest camera/tests` と画面の試験が通る。**変異**: 昇降部へ `duty` を送る／`state.lift` から `speed_meas` を落とす → 赤

### `WP-LIFT-08` モータへの出力の上限を設定に

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.1・§4.1.2・§4.2、[-protocol.md](DetailedDesign-protocol.md) §3、[-names.md](DetailedDesign-names.md) §5.1、spec [Spec-ui.md](../spec/Spec-ui.md) §2・§2.1
- 作るもの: `lift_decide.h` の `LIFT_MOTOR_MAX_PCT_DEFAULT`・`LIFT_MOTOR_MAX_PCT_MIN`・`lift_settings`（`motor_max_pct` の構造体・検証・JSON）・`SpeedLoop::update` に上限の引数（丸めと積分の止め方をその上限で）・`LiftController::set_motor_max_pct`・`main.cpp`（起動時と `PUT` 成功時に渡す・エラーの文言）・`firmware/lift/web/`（設定画面の欄と添え書き）・`tools/fake_lift_server.py`・**`docs/使い方.md`**（設定画面の欄・電源を替えたときの合わせ方）。`motor_duty_to_pwm` は変えない
- 受け入れ: native の試験・esp32dev のビルド・node の試験・`tools/tests`・スクロールの検査が通る。**次の変異がそれぞれ赤になる**（※ は `LiftController` と偽 HAL で、モータに出した値を見て縛る。偽 HAL は数を目標より遅く進め、#8a で止まらずに出力が上がり続ける状況を作る）
  1. ※ `tick` が `SpeedLoop` に上限ではなく `LIFT_DUTY_ABS_MAX_PCT` を渡す（50 % を超えて出る）
  2. ※ `set_motor_max_pct` で上限を下げても、動いている最中の出力が古い上限のまま
  3. `SpeedLoop` が上限に当たっている間も積分を進める（目標に追いついたあと出力が下がらない）
  4. `motor_duty_to_pwm` を上限の値で割る（50 % が PWM の最大になる）
  5. 設定の検証が `motor_max_pct` の欠けた本文・9・101 を通す
  6. 偽の昇降部が `motor_max_pct` の欠けた `PUT` を通す
- **実機で管理担当が確かめる**: 既定で `state.duty` が 50 を超えない（重く引っかけて速さが追いつかない状況）／設定画面で 30 にすると 30 で頭打ち／電源を入れ直しても残る

### `WP-CAM-07` カメラモジュールにモータへの出力の上限を

- 読む節: [-protocol.md](DetailedDesign-protocol.md) §3・§4、spec [Spec-ui.md](../spec/Spec-ui.md) §2・§2.1
- 作るもの: `camera/hve_camera/settings.py`（`motor_max_pct` の検証 10〜100 の整数）・`app.py`（`GET` で昇降部の値を載せる・`PUT` で昇降部へ中継し、自分では保存しない）・偽物のモードの昇降部の設定・`camera/web/settings.js` ほか（設定画面の欄と添え書き）・**`docs/使い方.md`**
- 受け入れ: `.venv38/bin/python -m pytest camera/tests` と画面の試験・スクロールの検査が通る。**変異**: `PUT` で昇降部へ `motor_max_pct` を送らない／検証が 101 を通す／`GET` が `motor_max_pct` を落とす → 赤

### `WP-LIFT-07` 上端の検知を戻す（`W-1` を閉じる）

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.1、[EXCEPTION-LEDGER.md](../EXCEPTION-LEDGER.md)
- 作るもの: `LIFT_TOP_DETECT_ENABLED` を `true`・`WAIVER(demo): W-1` をすべて消す・試験を「判定する」側へ直す。台帳の `W-1` をクローズにしてコミットハッシュを書く（管理担当）。**`docs/使い方.md`**（起動直後は上昇しない・「上端の検知: 一時無効」が出なくなる）
- 受け入れ: native の試験が成功。`grep -rn 'WAIVER(demo): W-1'` が空。**変異**: ※ 原点を取る前に上昇できる／※ `LIFT_TOP_MM` を設定して高さがそれ以上でも上昇できる → 赤
- **実機で管理担当が確かめる**: 起動直後は上昇しない（`HEIGHT_UNKNOWN`）→ 下げて下端に着けると上昇できる／`LIFT_TOP_MM` を仮に低く置いて、そこで止まる
