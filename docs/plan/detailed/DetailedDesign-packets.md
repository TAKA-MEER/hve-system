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
| `WP-IO-02` | 2 | UNO の実物（Timer2・`Servo`・`Wire`・ウォッチドッグ） | IO-01 | 要（UNO・モータ・SRF02） |
| `WP-CAM-04` | 3 | カメラモジュールのアプリを Python 3.8 へ移し、v2 の取り決めにする（`uno_link`・`uno_hw`・`lift_link`・設定の中継・偽物のモード） | BASE-02・LIFT-03・**MEAS-06 の 1・2・5** | 不要 |
| `WP-VIDEO-02` | 3 | 映像を UnitV2 の取り込みに合わせる | MEAS-06 | 要（UnitV2） |
| `WP-UI-02` | 3 | カメラモジュールの画面を v2 にする | CAM-04 | 不要（偽物のモード） |
| `WP-CAM-05` | 3 | UnitV2 への配備・自動起動・実機の結合 | MEAS-06・CAM-04・VIDEO-02・UI-02・IO-02・LIFT-04 | 要（全部） |
| `WP-MEAS-01`〜`05` | 4 | 旧版と同じ（ストローク・遅延・無線の圧迫・古さと天井の余裕・電池） | 各実物 | 要 |

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
