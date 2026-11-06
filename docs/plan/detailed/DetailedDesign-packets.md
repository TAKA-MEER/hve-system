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
| `WP-VIDEO-03` | 5 | **配信を 1 か所の作り直しにまとめ、送れない接続を切る**（2026-10-19 の実機調査） | VIDEO-02 | 不要（実機の確認は管理担当） |
| `WP-UI-03` | 5 | **1 回の確かめの失敗で「映像がありません」を出さない**（2026-10-19。Wi-Fi の端末で数分に 1 回一瞬出た） | UI-02 | 不要（実機の確認は管理担当） |
| `WP-CAM-08` | 5 | **昇降部の `state` を溜め続けない**（2026-10-19。`LiftLink.state_history` が 10 Hz で増え続け、`hve_camera` が約 58 MB/時で膨らんで空きメモリ 7〜10 MB） | CAM-04 | 不要（実機の確認は管理担当） |
| `WP-CAM-09` | 5 | **UNO のファームを UnitV2 経由で書き込む**（2026-10-27。組み上げたあとは UNO に USB をつなぎにくい。UART の Grove とリセットボタンだけで書く） | CAM-05 | 不要（実機の確認は管理担当） |
| `WP-UI-04` | 5 | **静止画の表示中に、その端末の中だけで拡大・移動できる**（2026-10-28 ユーザー決定。spec §1.5.1） | UI-02 | 不要（実機の確認は管理担当） |
| `WP-UI-02` | 3 | カメラモジュールの画面を v2 にする | CAM-04 | 不要（偽物のモード） |
| `WP-CAM-05` | 3 | UnitV2 への配備・自動起動・実機の結合 | MEAS-06・CAM-04・VIDEO-02・UI-02・IO-02・LIFT-04 | 要（全部） |
| `WP-MEAS-01`〜`05` | 4 | 旧版と同じ（ストローク・遅延・無線の圧迫・古さと天井の余裕・電池） | 各実物 | 要 |
| `WP-LIFT-05` | 5 | **高さをエンコーダにする**（`EncoderHeight`・PCNT・HC-SR04 を外す・`P-14`）。spec 2026-10-15 | — | 要（昇降部・エンコーダ） |
| `WP-LIFT-06` | 5 | **速さのフィードバック**（`SpeedLoop`・`ENCODER_STALL`・`hold` の `speed`・設定を mm/s・NVS の名前空間） | LIFT-05 | 要（同上） |
| `WP-LIFTUI-02` | 5 | 昇降部の画面・偽の昇降部・`lift_probe` を `speed`（mm/s）・`ENCODER_STALL`・原点を取る前の表示に合わせる | LIFT-06（取り決め） | 不要 |
| `WP-CAM-06` | 5 | カメラモジュール（アプリ・画面）を `speed`（mm/s）・`ENCODER_STALL`・原点を取る前の表示に合わせる | LIFT-06（取り決め） | 不要（偽物のモード） |
| `WP-LIFT-08` | 5 | **モータへの出力の上限を設定に**（`motor_max_pct`・`SpeedLoop` の丸め・設定 API・NVS・昇降部の画面・偽の昇降部）。spec 2026-10-16 | LIFT-06 | 不要（実機の確認は管理担当） |
| `WP-CAM-07` | 5 | カメラモジュール（設定の検証・中継・偽物のモード・画面）に `motor_max_pct` を足す | LIFT-08（取り決め） | 不要（偽物のモード） |
| `WP-LIFT-10` | 5 | **下端スイッチの一瞬の誤りで原点を取らない**（2026-10-28。上端付近で高さが 0 に戻った。`LIFT_BOTTOM_DEBOUNCE_MS`・`LIFT_HOME_ACCEPT_MM`） | LIFT-09 | 不要（実機の確認は管理担当） |
| `WP-LIFT-11` | 5 | **連続駆動の上限を目標の速さから都度求める**（2026-10-29 ユーザー決定。固定 10 秒では全行程を一度で動かせない。`LIFT_MAX_RUN_PCT`・`LIFT_STROKE_FALLBACK_MM`） | LIFT-07 | 不要（実機の確認は管理担当） |
| `WP-LIFT-12` | 5 | **点検用の物理ボタン**（2026-10-29 ユーザー決定。本番のファームで組み立て用と同じスイッチを読み、安全の判定を通して動かす。押している間だけ遠隔より優先。`ButtonInput`・`button_step`・`ConnKind::BUTTON`・`owner: button`・両方の画面の表示） | LIFT-11 | 不要（実機の確認は管理担当） |
| `WP-LIFT-09` | 5 | **高さの換算を機構の設計値にする**（`LIFT_UM_PER_COUNT` 100 → 26・`LIFT_STALL_MIN_COUNTS` を mm から決め直す・試験の換算）。2026-10-25 | LIFT-05・LIFT-06 | 不要（実機の確認は管理担当） |
| `WP-LIFT-07` | 5 | **上端の検知を戻す**（`LIFT_TOP_DETECT_ENABLED` を `true`・`WAIVER(demo): W-1` を消す・台帳を閉じる） | LIFT-05 の実機確認 | 要（昇降部） |
| `WP-LIFT-13` | 6 | **上部モジュールの複数対応（接続・名乗り・天井・`state`）**（2026-11-05。fk-system・ss-system が繋ぐ。`hello` の `role`・`operator`・`busy_label`・同じ名前の置き換え・`observe`・`ceiling`・距離計を持たない上部モジュールの天井・`modules` ほか・`LIFT_MODULE_MAX_CONN`） | LIFT-12 | 不要（実機の確認は管理担当） |
| `WP-LIFT-14` | 6 | **目標の高さへの移動（`goto`）**（spec §2.1。`ARRIVED`・`PREEMPTED`・`TARGET_RANGE`・`state.goto`・`LIFT_GOTO_TOL_MM`） | LIFT-13 | 不要（実機の確認は管理担当） |
| `WP-LIFTUI-03` | 6 | 昇降部の画面・偽の昇降部・`lift_probe` を複数の上部モジュールに（`modules`・`owner_name`・`busy_label`・「腕が出ています」・名乗り・`goto`） | LIFT-13（取り決め） | 不要 |
| `WP-CAM-10` | 6 | カメラモジュール（アプリ・画面・偽の昇降部）を複数の上部モジュールに（`hello` の `role`・`operator`・止まっている間の `ceiling`・`owner_name` で自分を見分ける・持ち主の表示） | LIFT-13（取り決め） | 不要（偽物のモード） |
| `WP-LIFT-15` | 7 | **単体の操作でも天井で止める**（2026-11-06。設定 `ceiling_standalone`・単体の操作の天井の判定・`state.ceiling.standalone`） | LIFT-13 | 不要（実機の確認は管理担当） |
| `WP-LIFTUI-04` | 7 | 昇降部の画面・偽の昇降部に `ceiling_standalone`（設定画面の切り替え・天井の扱いの表示） | LIFT-15（取り決め）・LIFTUI-03 | 不要 |
| `WP-CAM-11` | 7 | カメラモジュールに `ceiling_standalone`（設定の中継・設定画面の切り替え） | LIFT-15（取り決め）・CAM-10 | 不要（偽物のモード） |
| `WP-LIFT-16` | 8 | **天井の閾値を設定に**（2026-11-06。`ceiling_margin_mm`・判定に設定の値・`provisional` から外す） | LIFT-15 | 不要（実機の確認は管理担当） |
| `WP-LIFTUI-05` | 8 | 昇降部の画面・偽の昇降部に `ceiling_margin_mm`（設定画面の欄・偽の昇降部の判定） | LIFT-16（取り決め）・LIFTUI-04 | 不要 |
| `WP-CAM-12` | 8 | カメラモジュールに `ceiling_margin_mm`（設定の中継・設定画面の欄・偽物の昇降部） | LIFT-16（取り決め）・CAM-11 | 不要（偽物のモード） |

**段階 5（2026-10-15 の設計変更）**: `WP-LIFTUI-02` と `WP-CAM-06` は取り決め（[-protocol.md](DetailedDesign-protocol.md) §2）が書いてあるので `WP-LIFT-06` と並べて進められる。**ただし `hold` の `duty` → `speed` は互換が無いので、3 つ揃えてから実機に入れる。**
`WP-MEAS-01`（ストローク）で `LIFT_UM_PER_COUNT`（設計値は `WP-LIFT-09` で入れる）・速さの制御の係数・`LIFT_TOP_MM` を決める。

**`WP-LIFT-08` と `WP-CAM-07`**: 設定 API の `motor_max_pct` は必須なので、片方だけ新しいと `PUT` が `400` になる。**2 つ揃えてから実機に入れる。**`docs/使い方.md` の設定の節は `WP-LIFT-08` が書き、`WP-CAM-07` はカメラモジュールの画面の欄だけ足す（LIFT-08 のマージ後に始める）。

**段階 6（2026-11-05。上部モジュールの複数対応。[-protocol-modules.md](DetailedDesign-protocol-modules.md)）**: `WP-LIFTUI-03` と `WP-CAM-10` は取り決めが書いてあるので `WP-LIFT-13` と並べて進められる。**ただし `state` の `module` → `modules` は互換が無く、カメラモジュールは `owner_name` で自分を見分けるようになるので、`WP-LIFT-13`・`WP-LIFTUI-03`・`WP-CAM-10` の 3 つ揃えてから実機に入れる。**`WP-LIFT-14` は `WP-LIFT-13` のあと（`goto` を受けない昇降部は `goto` を知らない `t` として捨てるだけなので、あとから入れてよい）。
**揃うまで、fk-system・ss-system は今の昇降部へ繋いで上昇させない**（今のファームは、距離計を持たない接続の上昇に天井を使わない。fk・ss に伝え済み）。

**段階 7（2026-11-06。単体の操作でも天井で止める。[-protocol-modules.md](DetailedDesign-protocol-modules.md) §2.5）**: 段階 6 の 3 つ（`WP-LIFT-13`・`WP-LIFTUI-03`・`WP-CAM-10`）のあと。単体の操作で使う天井の値は、`WP-LIFT-13` の出どころと `WP-CAM-10` の止まっている間の `ceiling` が無いと届かない。`ceiling_standalone` は設定 API で欠けてもよい（[-protocol.md](DetailedDesign-protocol.md) §3）ので、**3 つは互換を崩さず別々に入れてよい**（`WP-CAM-11` の前のカメラモジュールは設定を中継しないだけ）。

**段階 8（2026-11-06。天井の閾値を設定に。[-protocol.md](DetailedDesign-protocol.md) §3）**: 3 つは互換を崩さず別々に入れてよい（`ceiling_margin_mm` は設定 API で欠けてもよい）。**3 つとも、設定画面に行が 1 つ増えるので背の低い画面のスクロール検査に気をつける**（spec のモックアップは、背の低い画面で仮値の一覧を省いて収めた）。

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

### `WP-VIDEO-03` 配信のまとめと送れない接続の始末

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.4（「配り方」「送れない接続を切る」）、[-names.md](DetailedDesign-names.md) §5（`video_send_timeout_ms`）、[-hardware.md](DetailedDesign-hardware.md) §2.1.1（2026-10-19 の行）
- 作るもの: `camera/hve_video/server.py`（作り直しを 1 か所にまとめて全接続へ配る・接続 0 本では作り直さない・書き込みのタイムアウトで接続を捨てる）・`__main__.py`（`video_send_timeout_ms` を渡す）・`camera/config/params.toml`（`video_send_timeout_ms` と `provisional`）。`pipeline.py`・`sources.py`・`/snapshot`・`/zoom` の振る舞いは変えない。画面（`camera/web/`）は変えない
- 受け入れ: `.venv38/bin/python -m pytest camera/tests` が通る。**次の変異がそれぞれ赤になる**（※ は偽の pipeline で `jpeg_bytes` の呼ばれた回数を数える、または書き込みが返らない偽の接続を使う）
  1. ※ 接続ごとに `jpeg_bytes` を呼ぶ（2 本つなぐと 1 周期に 2 回になる）
  2. ※ 接続が 0 本でも作り直し続ける
  3. ※ 書き込みのタイムアウトを外す（返らない接続のハンドラが抜けない）
  4. ※ タイムアウトしたとき、捨てずに `close()` で閉じる（送り残しを待つので閉じない）
  5. ※ 1 本の接続の書き込みが返らないと、他の接続にも次の 1 枚が届かない
  6. ※ つないですぐ切る接続（画面の状態確認の真似）があると、別の接続が受け取る枚数が減る
- **実機で管理担当が確かめる**: 受け取らない接続が `video_send_timeout_ms` に少し足した時間で `netstat` から消える（`FIN_WAIT1` で残るなら手当てを考える）／2 本つないでも `hve_video` の CPU が増えない／状態確認の真似を足しても最大間隔が延びない／基準値（[-hardware.md](DetailedDesign-hardware.md) §2.1.1）より fps が落ちない

### `WP-UI-02` カメラモジュールの画面 v2

- 読む節: [-protocol.md](DetailedDesign-protocol.md) §4、spec [Spec-ui.md](../spec/Spec-ui.md)
- 作るもの: 「上端の検知: 一時無効」・持ち主の表示・昇降部の画面の数・昇降部へのリンク・`IO_LOST`・新しい停止理由の文言・昇降の設定（中継）・**静止画のボタンと重ねた表示**（spec §1.5.1。`:8080/snapshot` を取り寄せる。モックアップにも足す）
- 受け入れ: 旧版の node の試験とスクロールの検査が通る。**変異**: `top_detect` が `false` でも「一時無効」を出さない → 赤

### `WP-UI-04` 静止画の拡大・移動

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md) §1.5・§1.5.1（「表示中の拡大」の行）
- 背景: 2026-10-28 の実機で、静止画の表示中にズームできなかった。いまの静止画の重ね（`#snapshot`・z-index 3）は映像の上の「＋」「−」を覆い、映像の倍率も静止画には効かない（映像の拡大はカメラモジュールが切り出す方式で、ブラウザでは拡大しない）
- 作るもの: `camera/web/index.html`・`app.js`・`style.css`。**静止画の重ねの中に、映像と同じ位置・見た目の「＋」「1 倍に戻す（倍率の表示）」「−」を置き、静止画をブラウザの中で拡大する**（CSS の `transform`。倍率 1〜4・1 段 0.5。映像の `zoom_max`・`zoom_step` を使い回す）。**ドラッグ（マウス・指。Pointer Events）で拡大した中をずらす**。ずらせるのは静止画の端が枠の内側に入らない範囲まで（1 倍ではずれない）。閉じると 1 倍・ずれ 0 に戻す。**`{"t":"zoom"}` は送らない**（映像の倍率・ほかの端末に影響しない）。倍率・ずれの丸めは純関数にして `module.exports` に足す（例: 次の倍率・ずれを範囲に収める）。`object-fit: contain` で余白があるときの範囲の計算に気をつける
- 受け入れ: `node --test camera/web/tests/web.test.js` と `node camera/web/tests/browser_paths.js`（全部。もとから NG の試験 9 は除く）・`check_noscroll_web.js` が通る。**次の変異がそれぞれ赤になる**（※ は `browser_paths.js` の経路で縛る）
  1. ※ 静止画の「＋」で `{"t":"zoom"}` を送る（映像の倍率が変わる）
  2. ※ 静止画の「＋」を押しても静止画の見た目の大きさが変わらない
  3. ※ 閉じて開き直しても前の倍率のまま
  4. ずれの範囲の丸めを外す（端を越えてずらせる）
  5. ※ 1 倍でドラッグすると静止画がずれる
- **実機で管理担当が確かめる**: スマートフォンとPCで、静止画を拡大して指・マウスでずらせる。ほかの端末の映像の倍率が変わらない

### `WP-UI-03` 映像の途絶えの見つけ方

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.4.1、[-names.md](DetailedDesign-names.md) §5.3.1
- 作るもの: `camera/web/app.js`（`video_probe_fail_count`・続けて失敗した回数の数え方を純関数にして `module.exports` に足す・`probeStream` がそれを使う）・`camera/web/tests/web.test.js`（純関数）・`camera/web/tests/browser_paths.js`（経路。Playwright の `page.route` で `/stream` のうち**確かめの `fetch` だけ**を落とす。`<img>` の取り込みは落とさない）。名前は -names.md §1.4 に足す（管理担当）
- 受け入れ: `node --test camera/web/tests/web.test.js` と `browser_paths.js`（全部）が通る。**次の変異がそれぞれ赤になる**（※ は `browser_paths.js` の経路で縛る）
  1. ※ 1 回の失敗で「映像がありません」を出す（いまの振る舞い）
  2. ※ 何回続けて失敗しても出さない
  3. 成功しても続けて失敗した回数を 0 に戻さない（続かない 2 回の失敗で出る）
  4. ※ 出したあと確かめが成功しても取り直さない（「映像がありません」が消えない）
- **実機で管理担当が確かめる**: Wi-Fi の端末で数分開いても一瞬の「映像がありません」が出ない／`hve_video` を止めると約 5 秒以内に出て、戻すと消える

### `WP-CAM-08` 昇降部の `state` を溜め続けない

- 読む節: [-hardware.md](DetailedDesign-hardware.md) §2.1.1（2026-10-19 のメモリの行）
- 作るもの: `camera/hve_camera/lift_link.py` の `state_history`（受けた `state` を全部溜める。どこからも読まれていない）を消す。他に受けたものを際限なく溜める所があれば同じく直す。試験の偽の昇降部（`camera/tests/cam_support.py` の `state_history`）は試験の道具なので残してよい
- 受け入れ: `.venv38/bin/python -m pytest camera/tests` が通る。**変異**: `LiftLink` が受けた `state` を溜める（リストに足す）→ 赤（`state` を数千件受けさせ、`tracemalloc` などで `LiftLink` まわりの確保が件数に比例して増えないことを縛る）
- **実機で管理担当が確かめる**: 昇降部と繋いだまま 5 分、`hve_camera` の RSS が増え続けない

### `WP-CAM-09` UNO を UnitV2 経由で書き込む

- 背景: 2026-10-27 に手作りのスクリプトで書けた（ピッチの向きの修正 `5712574`）。UNO の optiboot（115200 bps・STK500v1）は**リセット直後の最初のバイトが形に合わないとすぐアプリへ戻る**ので、同期（`0x30 0x20`）を送り続けると失敗した。**アプリの定期出力（約 70 ms ごとの `C …` の行）が 150 ms 途切れたらリセットとみて、1 組だけ送って返事を 0.6 秒待つ**のをくり返すと書けた。UnitV2 から UNO の RESET には線が無いので、リセットボタンは人が押す
- 作るもの:
  - `camera/deploy/uno_flash.py`（UnitV2 の上で動く。Python 3.8・pyserial 3.4。**`.venv38` で試験できる形**）: Intel HEX を読む → 上の同期 → シグネチャ（ATmega328P＝`1e 95 0f`）を確かめる → 128 バイトずつ書く → 全部読み戻して比べる → アプリを起動（`0x51 0x20`）。待つ秒数を引数で。結果は終了コードで返す（成功 0・同期できない・シグネチャ違い・読み戻し違いをそれぞれ別の値）
  - `camera/deploy/flash_uno.sh [UnitV2 のアドレス] [hex]`（PC で動かす）: アドレス・ユーザー・パスワードの決め方は `install.sh` と同じ。hex の既定は `firmware/cam_io/.pio/build/uno/firmware.hex`（無ければ `pio run -d firmware/cam_io -e uno` を促して止まる）。`uno_flash.py` と hex を `cat | ssh` で送る（scp は使えない）→ `S86hve stop`（`/dev/ttyS1` を空ける）→ 「UNO のリセットを押す」と出して書き込む → **成功しても失敗しても `S86hve start` で戻す**
  - `docs/使い方.md` の V2 の節に「UNO のファームを書き込む」を足す（USB で書く場合は D0 の黄を外すことも書く）
- 受け入れ: `.venv38/bin/python -m pytest camera/tests` が通る。optiboot を真似た偽のシリアル（リセットまではアプリの行を出し、リセット後の最初の 2 バイトが `0x30 0x20` でなければアプリへ戻る・ページを覚えて読み戻しに返す）で縛る。**変異**:
  1. 出力の途切れを待たずに同期を送り続ける → 赤
  2. 読み戻しの比較を外す（偽のシリアルが 1 ページだけ壊す）→ 赤
  3. シグネチャを確かめない（偽のシリアルが別のシグネチャを返す）→ 赤
  4. `flash_uno.sh` で、書き込みが失敗したときに `S86hve start` を呼ばない → 赤（`PATH` に偽の `sshpass` を置いて、送られたコマンドを記録する）
- **実機で管理担当が確かめる**: `camera/deploy/flash_uno.sh <IP>` でリセットを 1 回押すだけで書けて、カメラのアプリが戻る

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

### `WP-LIFT-13` 上部モジュールの複数対応（接続・名乗り・天井・`state`）

- 読む節: spec [Spec-module.md](../spec/Spec-module.md) §1〜§4（§3 の測定機構の行を含む）・[Spec-safety.md](../spec/Spec-safety.md) §2 #3〜#4c、[-protocol-modules.md](DetailedDesign-protocol-modules.md) §0〜§5（**§2.2 の `goto` は `WP-LIFT-14`**）、[DetailedDesign.md](DetailedDesign.md) §3.1〜§3.3、[-names.md](DetailedDesign-names.md) §4 の「上部モジュールの複数対応」の表と §5.1 の `LIFT_MODULE_MAX_CONN`・`LIFT_STATE_TEXT_MAX`
- 作るもの:
  - `cmd_codec`: `HelloMsg` に `role`・`operator`・`busy_label`（欠けている・知らない値の扱いは -protocol-modules §1.1。`name` は形に合わなければ名前無し）。`decode_ceiling`（`t: "ceiling"`）。`state_encode`・`state_decode` に `owner_name`・`owner_operator`・`homed`・`modules`（配列）・`ceiling.from`・`arm`（**中身はすべて `null`**。-protocol-modules §3）。**`module` は消す**
  - `lift_controller`: 接続ごとに `role`・`operator`・`busy_label` を持つ。`on_hello` は、**同じ名前の別の接続があれば、その接続番号を返して呼び出し側に閉じさせる**（名前無しは置き換えない）。`observe` の接続の `hold`・`release` は捨てる。**天井の値の出どころになる接続（`hello` で `ceiling_sensor: true` を明示した `control` の接続だけ。-protocol-modules §2.3）ごとに、最後の天井の値（`hold`・`ceiling` のどちらから来たものでも）と受け取った時刻を持つ。**持ち主が**距離計を持たない上部モジュール**のときは、その値を `ceiling_check` に渡す（2 台以上なら近い方。無ければ `MISSING` で `CEILING_STALE`）。`busy_label` は UTF-8 で 48 バイトを超えたら無しとして扱い、切り詰めない（-protocol-modules §1.1）。昇降部の画面・物理ボタンは今のまま使わない。`kMaxConns` を `LIFT_UI_CLIENTS_MAX`・`LIFT_MODULE_MAX_CONN` と物理ボタンが入る数にする
  - `LiftHal::homed`（`hal.h`・偽の HAL・`hal_esp32` は `EncoderHeight` から）
  - `src/main.cpp`: `/ws/module` の接続が `LIFT_MODULE_MAX_CONN` を超えたら**新しい方をその場で閉じる**。`ws_module.cleanupClients()` に**古い接続を閉じさせない**（上限より大きい値を渡すなど。消えた接続の後始末は続ける）。`on_hello` が返した古い接続を閉じる。`ceiling` を振り分ける。`state` が大きくなるので `LIFT_STATE_TEXT_MAX` を測って直し、[-names.md](DetailedDesign-names.md) の値も直す
  - `lift_core_version` を上げる
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev`・`-e esp32dev_manual` が成功。**次の変異がそれぞれ赤になる**（※ は `LiftController` と偽 HAL を通した試験で縛る。純関数の試験だけで済ませない）
  1. ※ 距離計を持たない上部モジュールの上昇に天井を使わない（今の作りに戻す）。カメラモジュールが `ceiling` で `MEASURED` 300 mm を送っている間に、`fk` の上昇が止まらない
  2. ※ カメラモジュールが繋がっていない（または値が `CEILING_STALE_MS` より古い）のに、`fk` の上昇を許す
  3. ※ カメラモジュールの値の古さに、受け取ってからの経過を足さない
  4. ※ 止まっている間に届いた `ceiling` を使わず、`hold` に載った値だけ使う
  5. ※ `observe` の接続の `hold` で動く
  6. ※ `role` の知らない値を `control` とみなす
  7. ※ 同じ名前で繋ぎ直しても古い接続を閉じない／名前無しどうしを置き換える
  8. ※ 2 度目の `hello` で `role`・`ceiling_sensor` が変わる
  9. ※ `fk` が持ち主のとき、`owner_name` が `fk` にならない・`owner_operator` が `auto` にならない
  10. ※ `operator` が欠けた `hello` を `auto` とみなす
  11. `arm.attached` に `false` を出す（`null` でない）
  12. ※ `hello` を送っていない接続（または `ceiling_sensor` が欠けた `hello` の接続）の `ceiling`（`NO_ECHO`）で、カメラモジュールが繋がっていないのに `fk` の上昇を許す
  13. ※ `observe` の接続・`ceiling_sensor: false` の接続の `ceiling` を、`fk` の判定に使う
  14. `busy_label` を 48 バイトで切り詰める（`a` と日本語 16 文字〈49 バイト〉の `busy_label` で、切ると文字の途中で切れ、`state` に壊れた UTF-8 が載る。無しとして扱えば緑）
- **実機で管理担当が確かめる**（`WP-LIFTUI-03` の `lift_probe` と `WP-CAM-10` が揃ってから）: カメラモジュールと `lift_probe` 2 本（`fk`・`ss` を名乗る）で繋ぎ、`fk` の上昇がカメラモジュールの天井で止まる／カメラモジュールの電源を落とすと `fk` の上昇が `CEILING_STALE` で止まる／同じ名前で繋ぎ直すと古い方が閉じる／上限を超えた接続が閉じられ、カメラモジュールは切れない／`state` の `modules` に繋いだものが並ぶ

### `WP-LIFT-14` 目標の高さへの移動（`goto`）

- 読む節: spec [Spec-module.md](../spec/Spec-module.md) §2・§2.1・§4、[-protocol-modules.md](DetailedDesign-protocol-modules.md) §2.2・§3 の `goto`・§3.1、[DetailedDesign.md](DetailedDesign.md) §3.3、[-names.md](DetailedDesign-names.md) の `LIFT_GOTO_TOL_MM`・`ARRIVED`・`PREEMPTED`・`TARGET_RANGE`
- 作るもの:
  - `cmd_codec`: `decode_goto`。`state` の `goto`（`by`・`press`・`target_mm`・`status`・`reason`）
  - `lift_arbiter`: `goto` も `hold` と同じ `press` の規則で持ち主を決める。持ち主が替わったことを `lift_controller` が知れるようにする
  - `lift_controller`: 目標といまの高さから向きを決め、`LIFT_GOTO_TOL_MM` 以内で止めて `ARRIVED`。**着いたあと同じ `press` の `goto` は無視する**。高さが読めない → `HEIGHT_UNKNOWN`、目標が範囲の外 → `TARGET_RANGE` で受け付けない。取って代わられた → `PREEMPTED`。ほかの止め方（安全の判定・命令の途切れ・`release`・接続が閉じた）は、その止めた理由で `NOT_ARRIVED`。速さは `goto` の `speed`
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev`・`-e esp32dev_manual` が成功。**次の変異がそれぞれ赤になる**（※ は `LiftController` と偽 HAL〈エンコーダの数を進める〉を通した試験で縛る）
  1. ※ 着いたあと、同じ `press` の `goto` で動き出す
  2. ※ 原点の前（高さが読めない）の `goto` を受け付ける
  3. ※ 目標が上端の閾値より上でも受け付ける
  4. ※ 取って代わられたとき `NOT_ARRIVED` にしない・理由が `PREEMPTED` でない
  5. ※ `goto` の上昇に天井の判定が効かない（`fk` の `goto` がカメラモジュールの天井で止まらない）
  6. ※ `goto` が途切れても止まらない（`LIFT_CMD_TIMEOUT_MS`）
  7. ※ 目標より下にいるのに下降する（向きの取り違え）
  8. `goto.by`・`press` が送り手と合わない
- **実機で管理担当が確かめる**: `lift_probe` で `goto` 1000 mm → 着いて `ARRIVED`／途中で昇降部の画面から押すと `PREEMPTED`／電源を入れ直した直後は `HEIGHT_UNKNOWN`

### `WP-LIFTUI-03` 昇降部の画面・偽の昇降部・`lift_probe` を複数の上部モジュールに

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md) §0.2・§1.6、[Spec-module.md](../spec/Spec-module.md) §1〜§4、[-protocol-modules.md](DetailedDesign-protocol-modules.md) §1〜§4
- 作るもの:
  - 昇降部の画面 `firmware/lift/web/app.js`: `modules` の一覧（名前・IP・人か自動か）。持ち主の表示は `owner_name` と `busy_label`（`fk` なら「測定操作中」）で出し、**`owner == "module"` を「上部モジュール」と決め打ちしない**。`arm.out` が `true` のとき「腕が出ています」（`null`・`false` では出さない）。純関数の試験
  - 偽の昇降部 `tools/fake_lift_server.py`: 複数の接続・`hello` の新しい欄・同じ名前の置き換え・`observe`・`ceiling`・距離計を持たない上部モジュールの天井（`WP-LIFT-13` と同じ判定）・`goto`・`state` の新しい欄（`arm` は `null`）
  - `tools/lift_probe.py`: `--name`・`--role`・`--operator`・`--no-ceiling-sensor`・`--busy-label` で名乗れる。`goto` を送れる
  - **`docs/使い方.md`** の昇降部の画面の節（繋がっている上部モジュールの一覧・持ち主の表示）
- 受け入れ: `.venv/bin/python -m pytest tools/tests`・`node firmware/lift/web/tests/web.test.js` が成功。**次の変異がそれぞれ赤になる**
  1. 画面が `busy_label` でなく `owner_name` だけを出す（`busy_label` があるとき）
  2. 画面が `owner_name` を見ずに「上部モジュール」と出す
  3. 画面が `modules` を 1 つしか出さない
  4. 画面が `arm.out` の `null` で「腕が出ています」を出す
  5. 偽の昇降部が、距離計を持たない上部モジュールの上昇に天井を使わない
  6. `lift_probe` の名乗りが `hello` に載らない

### `WP-CAM-10` カメラモジュールを複数の上部モジュールに

- 読む節: [-protocol-modules.md](DetailedDesign-protocol-modules.md) §1.1・§2.3・§3・§4・§5、[DetailedDesign.md](DetailedDesign.md) §3.1・§3.2、[-names.md](DetailedDesign-names.md) の `MODULE_ROLE`・`MODULE_OPERATOR`・`module_name`
- 作るもの:
  - `camera/hve_camera/lift_link.py`: `hello` に `role: "control"`・`operator: "human"`（定数）。**止まっている間も `lift_cmd_period_ms` ごとに `ceiling` を送る**（中身は `hold` に載せるものと同じ。古さの扱いも同じ）
  - `camera/hve_camera/control.py`: **自分が持ち主かを `owner == "module"` でなく `owner_name == module_name` で見る**（`fk`・`ss` が動かしている間に、昇降部の天井の判定を自分のものと取り違えない）
  - 画面 `camera/web/app.js`: 持ち主の表示を `owner_name`・`busy_label` で出す（`hve-cam` のときは今の「カメラモジュール」）。`arm.out` が `true` のとき「腕が出ています」。純関数の試験
  - 偽の昇降部 `camera/hve_camera/hw/fake_lift.py`: `state` の新しい形（`modules` など）と `ceiling` を受ける
  - **`docs/使い方.md`** のカメラモジュールの画面の節（持ち主の表示）
- 受け入れ: `.venv38/bin/python -m pytest camera/tests`・`node --test camera/web/tests/web.test.js` が成功。**次の変異がそれぞれ赤になる**
  1. 止まっている間に `ceiling` を送らない
  2. `ceiling` の古さを送らない・読めない値を `MEASURED` で送る
  3. 自分が持ち主かを `owner == "module"` のままで見る（`fk` が持ち主のとき、カメラモジュールの画面に昇降部の天井の判定が出る）
  4. `hello` に `operator` を載せない・`auto` を載せる
  5. 画面が `fk` の `busy_label` を出さない
- **実機で管理担当が確かめる**（`WP-LIFT-13`・`WP-LIFTUI-03` と揃えて）: カメラモジュールが止まっている間も、昇降部の `state.ceiling.from` が `hve-cam` で古くならない／`lift_probe`（`fk`）が動かしている間、カメラモジュールの画面に「測定操作中」と出て、天井の判定は自分のものとして出ない

### `WP-LIFT-15` 単体の操作でも天井で止める

- 読む節: spec [Spec-safety.md](../spec/Spec-safety.md) §2 #3〜#4c・[Spec-ui.md](../spec/Spec-ui.md) §2・§2.2・[Spec-module.md](../spec/Spec-module.md) §2・§3、[-protocol-modules.md](DetailedDesign-protocol-modules.md) §2.3〜§2.5・§3、[-protocol.md](DetailedDesign-protocol.md) §3、[-names.md](DetailedDesign-names.md) の「単体の操作でも天井で止める」の表
- 作るもの:
  - `lift_settings`: `ceiling_standalone`（`LIFT_CEILING_STANDALONE_DEFAULT` = `true`）。**JSON に無ければ `true` で読み、ほかの項目は捨てない**（NVS の古い形）。`PUT` 用に「欠けていれば今の値を保つ」読み方。真偽値でなければ検証の誤り
  - `lift_controller`: `set_ceiling_standalone`。持ち主が `ui`・`button` のとき -protocol-modules §2.5 の表で判定する（出どころの値は `WP-LIFT-13` のものを使う）。`state.ceiling.standalone` を毎周期出す
  - `cmd_codec`: `state` の `ceiling.standalone`
  - `src/main.cpp`: 起動時（NVS を読んだあと）と `PUT /api/settings` の成功時に `set_ceiling_standalone` を呼ぶ。`GET` に載せる
  - `lift_core_version` を上げる
  - **今の試験のうち「昇降部の画面・物理ボタンの上昇は天井を使わない」を確かめているもの（#4c・`WP-LIFT-12`）は、既定が `true` になると赤になる。消したり緩めたりせず、`ceiling_standalone = false` の場合の試験に書き換える**（`true` の場合は下の変異の試験で足す）
  - **`docs/使い方.md`**: 物理ボタンの節の「天井の距離計は見ない」と、昇降部の画面の天井の扱いを直す（このパケットだけで実機の挙動が変わる。既定で入なので単体の上昇が天井で止まるようになる）
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev`・`-e esp32dev_manual` が成功。**次の変異がそれぞれ赤になる**（※ は `LiftController` と偽 HAL を通した試験で縛る。純関数だけで済ませない）
  1. ※ 設定が `true` で、カメラモジュールが `ceiling` で `MEASURED` 300 mm を送っている間に、昇降部の画面（`ui`）の上昇が止まらない
  2. ※ 同じ条件で、物理ボタン（`button`）の上昇が止まらない
  3. ※ 設定が `false` でも天井で止まる
  4. ※ カメラモジュールが繋がっていない・値が `CEILING_STALE_MS` より古いとき、`ui` の上昇を `CEILING_STALE` で止める
  5. ※ `ui` で上昇している最中にカメラモジュールの値が古くなっても、前の判定のまま止め続ける（周期ごとに決め直さない）
  6. ※ 新しい値が `READ_ERROR` のとき `ui` の上昇を許す
  7. ※ 設定が上部モジュールからの命令に効く（`false` にすると `fk` の上昇が天井で止まらない）
  8. NVS に `ceiling_standalone` の無い形で保存してあると、全体が既定値になる・`false` で読む
  9. `ceiling_standalone` の無い `PUT` で、保存してある `false` が `true` に戻る
  10. `state.ceiling.standalone` が、設定が `false` のとき `SETTING_OFF` にならない・出どころが無いとき `NO_SOURCE` にならない
- **実機で管理担当が確かめる**（`WP-LIFTUI-04` と揃えて）: カメラモジュールを繋いだまま昇降部の画面と物理ボタンで上げ、天井（板をかざす）で `CEILING_NEAR` で止まる／カメラモジュールの電源を落とすと天井を使わずに上げられ、画面に「通信なし」と出る／設定を切ると天井で止まらない／電源を入れ直しても設定が残る

### `WP-LIFTUI-04` 昇降部の画面・偽の昇降部に `ceiling_standalone`

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md) §0.1・§0.2・§2・§2.2、[-protocol-modules.md](DetailedDesign-protocol-modules.md) §2.5・§3、[-protocol.md](DetailedDesign-protocol.md) §3
- 作るもの:
  - 昇降部の画面 `firmware/lift/web/`: 設定画面に「単体の操作でも天井で止める」の切り替え。天井の表示に `ceiling.standalone`（`SETTING_OFF` → 「天井: 使わない（設定が切）」・`NO_SOURCE` → 「天井: 使わない（通信なし）」）。純関数の試験。スクロールの検査
  - 偽の昇降部 `tools/fake_lift_server.py`: 設定の `ceiling_standalone`（欠けた `PUT` は今の値を保つ）・単体の操作の天井の判定（`WP-LIFT-15` と同じ）・`state.ceiling.standalone`
  - **`docs/使い方.md`** の昇降部の画面の節（設定の欄・天井の表示）
- 受け入れ: `.venv/bin/python -m pytest tools/tests`・`node firmware/lift/web/tests/web.test.js`・スクロールの検査が通る。**変異**: 画面が `NO_SOURCE` で「使わない」を出さない／保存で `ceiling_standalone` を送らない／偽の昇降部が単体の操作に天井を使わない／偽の昇降部が欠けた `PUT` で `true` に戻す → 赤

### `WP-CAM-11` カメラモジュールに `ceiling_standalone`

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md) §0.1・§2・§2.2、[-protocol.md](DetailedDesign-protocol.md) §3・§4
- 作るもの: `camera/hve_camera/settings.py`（真偽値の検証）・`app.py`（`GET` で昇降部の値を載せる・`PUT` で昇降部へ中継し、自分では保存しない。`motor_max_pct` と同じ）・偽物のモードの昇降部の設定・`camera/web/settings.js` ほか（設定画面の切り替え）・`state.lift` に昇降部の `ceiling.standalone` を載せ、天井のバッジは**昇降部の画面・物理ボタンが持ち主で `USE` でないとき**「天井: 使わない（設定が切／通信なし）」と出す・**`docs/使い方.md`**
- 受け入れ: `.venv38/bin/python -m pytest camera/tests` と画面の試験・スクロールの検査が通る。**変異**: `PUT` で昇降部へ `ceiling_standalone` を送らない／自分で保存する／検証が `"yes"` を通す／`GET` が `ceiling_standalone` を落とす → 赤

### `WP-LIFT-16` 天井の閾値を設定に

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md) §2・§2.3・[Spec-safety.md](../spec/Spec-safety.md) §2 #3、[-protocol.md](DetailedDesign-protocol.md) §3（`ceiling_margin_mm`）、[-names.md](DetailedDesign-names.md) の `CEILING_MARGIN_MM_*`・`LiftSettings::ceiling_margin_mm`
- 作るもの: `ceiling_check.h` の定数（改名と範囲）・`ceiling_check` に閾値の引数（呼び出し側すべてに設定の値を渡す。単体の操作の出どころ選び〈`pick_source`〉の判定も同じ値）・`lift_settings`（欄・検証・NVS に無ければ 500・`PUT` に無ければ今の値）・`LiftController::apply_settings` で持たせる・`cmd_codec` の `provisional` から外す・`main.cpp`（`GET` に載せる）・**`docs/使い方.md`** の設定の節（L-5）・`lift_core_version` を上げる
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev`・`-e esp32dev_manual` が成功。**次の変異がそれぞれ赤になる**（※ は `LiftController` と偽 HAL を通した試験）
  1. ※ 判定が設定ではなく固定の 500 を使う（閾値 1000 にして、カメラモジュールが 800 mm を送っている間に `fk` の上昇が止まらない）
  2. ※ 単体の操作（`ui`）の判定だけ固定の 500 を使う
  3. ※ `apply_settings` が閾値を渡さない
  4. 検証が 149・3001・小数・文字列・真偽値を通す
  5. NVS に欄が無いと全体が既定値になる・500 以外で読む
  6. 欄の無い `PUT` で保存してある 1000 が 500 に戻る
  7. 範囲の外の `PUT` で、ほかの項目だけ保存される
  8. `provisional` に `CEILING_MARGIN_MM` が残る
- **実機で管理担当が確かめる**: 設定で 1000 にすると、カメラモジュールの天井まで 1000 mm で `CEILING_NEAR` ／電源を入れ直しても残る

### `WP-LIFTUI-05` 昇降部の画面・偽の昇降部に `ceiling_margin_mm`

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md) §0.1・§0.2・§2・§2.3、[-protocol.md](DetailedDesign-protocol.md) §3
- 作るもの: 昇降部の画面の設定画面に欄（mm・150〜3000 の整数の検証・添え書き）・偽の昇降部 `tools/fake_lift_server.py`（欄・検証・欠けた `PUT` は今の値・判定に設定の値・`provisional` から外す）・`tools/lift_probe.py` のコメント・**`docs/使い方.md`** の昇降部の設定の節
- 受け入れ: `.venv/bin/python -m pytest tools/tests`・`node firmware/lift/web/tests/web.test.js`・スクロール検査・`browser_paths.js` が通る。**変異**: 画面が保存で `ceiling_margin_mm` を送らない（`browser_paths.js` で縛る）／画面の検証が 149 を通す／偽の昇降部が固定の 500 で判定する／偽の昇降部が欠けた `PUT` で 500 に戻す → 赤

### `WP-CAM-12` カメラモジュールに `ceiling_margin_mm`

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md) §0.1・§2・§2.3、[-protocol.md](DetailedDesign-protocol.md) §3・§4
- 作るもの: `camera/hve_camera/settings.py`（検証）・`app.py`（`GET` で昇降部の値を載せる・`PUT` で昇降部が対応しているときだけ中継・自分では保存しない。`ceiling_standalone` と同じ）・偽物のモードの昇降部（`hw/fake_lift.py` の判定に設定の値）・`camera/web/settings.js` ほか（設定画面の欄）・**`docs/使い方.md`**
- 受け入れ: `.venv38/bin/python -m pytest camera/tests`・`node --test camera/web/tests/web.test.js`・スクロール検査・`browser_paths.js`（9 は前から失敗）が通る。**変異**: `PUT` で昇降部へ送らない（偽の昇降部へ繋いだ経路で縛る）／自分で保存する／検証が 3001 を通す／`GET` が落とす／偽物の昇降部が固定の 500 で判定する → 赤

### `WP-LIFT-12` 点検用の物理ボタン

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md) §0.3（受け入れているリスクまで）・[Spec-module.md](../spec/Spec-module.md) §2〜§4・[Spec-safety.md](../spec/Spec-safety.md) §2 #4c、[DetailedDesign.md](DetailedDesign.md) §3.1・§3.3（★の 2 行）・§4.1.3、[-hardware.md](DetailedDesign-hardware.md) §1.2・§1.3、[-names.md](DetailedDesign-names.md) の `ButtonInput`・`button_step`・`LIFT_BUTTON_CONN_ID`・`LIFT_SW_*_PIN`、[-protocol.md](DetailedDesign-protocol.md) §2.2 の `owner`
- 作るもの:
  - `lib/lift_core/button_input.{h,cpp}`: `ButtonInput`（中で `ManualSwitch` を使う）と `button_step`。`Arduino.h` を include しない
  - `lift_arbiter`: `ConnKind::BUTTON` と §3.3 の★の 2 行
  - `lift_controller`: `BUTTON` は距離計を持たない（`sensor_of`）。`state.owner_kind` に 2（`button`）
  - `cmd_codec`: `state_encode`・`state_decode` に `owner: "button"`
  - `src/main.cpp`: 起動時に 2 本を `INPUT_PULLUP`、`loop()` でピンを読んで `button_step` を呼ぶだけ。`src/config.h` のピンの定数を `LIFT_SW_UP_PIN`・`LIFT_SW_DOWN_PIN` に改め、`src/manual/` も同じ名前を使う（組み立て用ファームの振る舞いは変えない）
  - 画面: `firmware/lift/web/app.js`・`camera/web/app.js` の持ち主の表示に `button`（「物理ボタン（点検）」）、`owner` が `button` の間は「物理ボタンで操作中（遠隔からは動かせません）」を出す（両画面の純関数の試験）
  - **`docs/使い方.md`** に L 節を 1 つ足す（点検用の物理ボタン。組み立て用 L-4 との違い・効く安全装置・遠隔との関係・受け入れているリスク）
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev`・`-e esp32dev_manual`・`.venv38/bin/python -m pytest camera/tests`・`.venv/bin/python -m pytest tools/tests`・`node firmware/lift/web/tests/web.test.js`・`node --test camera/web/tests/web.test.js` が成功。**次の変異がそれぞれ赤になる**（※ は `button_step` と `LiftController`・偽 HAL を通した試験で縛る）
  1. ※ 離したときにもチャタリング除けを掛ける（離してから止まるまで遅れる）
  2. ※ 起動したときに入っていたボタンで動く
  3. ※ 両方 LOW でも動く
  4. ※ 物理ボタンを押している間に、遠隔の新しい `press` で持ち主が替わる
  5. ※ 物理ボタンを離したあと、押し続けていた遠隔の `hold`（同じ `press`）で動き出す
  6. ※ 物理ボタンの命令に天井の値を使う（`BUTTON` を距離計ありにすると、天井 `MISSING` で上昇しない）
  7. ※ 物理ボタンの命令で下端（`BOTTOM`）・原点の前の上昇（`HEIGHT_UNKNOWN`）が効かない
  8. ※ 速さに設定の初期値でなく固定の値を使う
  9. `state` の `owner` が `button` にならない
- **実機で管理担当が確かめる**: スイッチを倒したまま電源を入れると動かない→離して倒し直すと動く／下端で `BOTTOM`・原点の前は上昇しない／押している間、画面から押しても動かず「物理ボタンで操作中」が両方の画面に出る／画面で押し続けている間に物理ボタンを押すと物理ボタンに替わり、離すと画面は押し直すまで動かない／上昇中にスイッチの線を外すと止まる

### `WP-LIFT-11` 連続駆動の上限を目標の速さから都度求める

- 読む節: spec [Spec-safety.md](../spec/Spec-safety.md) §2 #4b とその下の 2026-10-29 の表・受け入れているリスク、[DetailedDesign.md](DetailedDesign.md) §4.1 の表 #8 と「連続駆動の上限の数え方」、[-names.md](DetailedDesign-names.md) §5.1 の `LIFT_MAX_RUN_PCT`・`LIFT_STROKE_FALLBACK_MM`、[-protocol.md](DetailedDesign-protocol.md) §2.2 の `provisional`
- 作るもの:
  - `lift_decide.h`: `LIFT_MAX_RUN_MS` を消し、`LIFT_MAX_RUN_PCT`（150）・`LIFT_STROKE_FALLBACK_MM`（1900）を置く。`LiftDecideInput::run_ms` を `run_travel_um`（目標の速さで進んだとみなした距離 [µm]）に替える
  - `lift_decide.cpp` の表 8: 全行程は `top_mm` が 0 以上ならそれ、-1 なら `LIFT_STROKE_FALLBACK_MM`。`run_travel_um` が `全行程 × 10 × LIFT_MAX_RUN_PCT` を超えたら `MAX_RUN`
  - `lift_controller`: `run_ms_` を `run_travel_um_` に替える。**実際に回していた間（`turning_`）だけ**、直前の `step` で決めた目標の速さ × 経過 [ms] を足す（足してから判定）。0 に戻すときは今までどおり（停止指令・向きの変化。持ち主の交代では戻さない）。飽和させる。**表 8a（`ENCODER_STALL`）の数え方には触らない**
  - `cmd_codec.cpp` の `provisional`: `LIFT_MAX_RUN_MS` → `LIFT_MAX_RUN_PCT`
  - 偽物: `camera/hve_camera/hw/fake_lift.py`・`tools/fake_lift_server.py` を同じ数え方に（`provisional` の名前も）
  - 画面: `camera/web/app.js` の `MAX_RUN` の文言から「（10 秒）」を消す（`firmware/lift/web/app.js` と同じ文言に）
  - **`docs/使い方.md`** の L-3 に、連続駆動の上限（目標の速さで全行程を進む時間の約 1.5 倍で `MAX_RUN`。出力の上限に当たって遅いと手前で止まることがある。押し直せば動く）を足す
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev`・`-e esp32dev_manual`・`.venv38/bin/python -m pytest camera/tests`・`.venv/bin/python -m pytest tools/tests`・`node firmware/lift/web/tests/web.test.js`・`node --test camera/web/tests/web.test.js` が成功。**次の変異がそれぞれ赤になる**（※ は `LiftController` と偽 HAL〈エンコーダの数を進める〉を通した試験で縛る）
  1. ※ 上限を固定の 10 秒に戻す（30 mm/s で全行程 1800 mm を上げる 60 秒の間に止まる）
  2. ※ 測った速さで数える（偽 HAL が目標の半分の速さで数を進めても、目標の速さで数えた時刻に止まる）
  3. ※ 「経過時間 × いまの目標の速さ」で数える（10 mm/s で長く動いたあと 60 mm/s に上げた途端に止まる）
  4. ※ `top_mm` を見ない（`LiftController` を `top_mm` 400 で作り、100 mm/s で**下降**し続けると 6 秒を超えて `MAX_RUN`。1900 や 1800 で数えると止まらない。上昇だと高さ 400 mm の `TOP` が先に当たるので下降で縛る）
  5. `top_mm` が -1 のとき `LIFT_STROKE_FALLBACK_MM` を使わない
  6. ※ 回していない間（上昇を `CEILING_NEAR` などで止めている間）も足す
  7. ※ 持ち主が替わったら数え直す
- **実機で管理担当が確かめる**: 30 mm/s で下端から上端（`TOP`）まで一度で上がる・上端から下端（`BOTTOM`）まで一度で下がる（どちらも `MAX_RUN` で止まらない）／設定の出力の上限を 10 % にして 100 mm/s で上げると、上端の手前・約 27 秒で `MAX_RUN`・反対方向へは動く

### `WP-LIFT-10` 下端スイッチの一瞬の誤りで原点を取らない

- 読む節: spec [Spec-safety.md](../spec/Spec-safety.md) §1.2（「スイッチの誤り」の行）、[DetailedDesign.md](DetailedDesign.md) §4.1.1 の表（2026-10-28 の 2 行と本文）、[-names.md](DetailedDesign-names.md) の `LIFT_BOTTOM_DEBOUNCE_MS`・`LIFT_HOME_ACCEPT_MM`
- 作るもの: `lib/hal_core` の `EncoderHeight::update` に時刻を渡し（`hal_esp32` の `poll(now_ms)` から）、表の 2 行を入れる。定数は `hal_core.h`。**`LiftHal::bottom_pressed()`（下降の `BOTTOM`）は読んだそのままのまま変えない**。ホスト試験（`test_hal_core`）。`LiftController` を通した試験で、上昇中の一瞬の「押された」で高さが 0 に戻らないこと・高い所で押され続けると上昇しないことを縛る
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev`・`-e esp32dev_manual` が成功。**次の変異がそれぞれ赤になる**（※ は `LiftController` を通した試験で縛る）
  1. ※ 時間の続きを見ない（1 回読めただけで原点を取る）
  2. ※ `LIFT_HOME_ACCEPT_MM` を見ない（高い所でも原点を取る）
  3. 高い所で押されたとき `height_ok` を偽にしない（原点にしないだけ）
  4. 押されている間に `LIFT_HOME_ACCEPT_MM` 以下へ下がっても原点を取らない（真に戻らない）
  5. ※ 下降の `BOTTOM` にも時間の続きを掛ける（スイッチに着いても止まるのが遅れる）
  6. `hal_esp32` が `update` に時刻を渡さない（固定値）→ ビルドか試験で見つかる形にする
- **実機で管理担当が確かめる**: 下端で `BOTTOM` と原点が今までどおり／上端付近まで 100 mm/s で上げても高さが 0 に戻らない／下端スイッチの線を上昇中に一瞬外すと、原点にならず上昇が止まる（`HEIGHT_UNKNOWN`）・下げて下端に着けると戻る

### `WP-LIFT-09` 高さの換算を機構の設計値にする

- 読む節: [-hardware.md](DetailedDesign-hardware.md) §1.1（分解能・高さへの換算）、[-names.md](DetailedDesign-names.md) §5.1、[DetailedDesign.md](DetailedDesign.md) §4.1.1・§4.1.2
- 作るもの: `hal_core.h` の `LIFT_UM_PER_COUNT` を 26 に（コメントを「設計値。`WP-MEAS-01` で全行程を測って直す」に）・`config.h` の `ENC_PPR` のコメントから「要確認」を消す・`LIFT_STALL_MIN_COUNTS` を **mm で決めた値（いまと同じ 0.8 mm）から `LIFT_UM_PER_COUNT` で割って出す**形に（`LIFT_STALL_MIN_UM` などを置く）。試験の換算（`test_hal_core` の `kCountsPerMm = 1000 / LIFT_UM_PER_COUNT` は 26 で割り切れない。`600` カウント＝10 mm のような直書きの数も）を µm で計算する形に直す。**`docs/使い方.md`** に高さの換算の記述があれば直す
- 受け入れ: `pio test -d firmware/lift -e native`・`pio run -d firmware/lift -e esp32dev` が成功。**次の変異がそれぞれ赤になる**
  1. `LIFT_UM_PER_COUNT` を 100 に戻す（試験が定数の値そのものではなく、カウント → mm の換算と #8a の止まる進みを縛る）
  2. ※ `LIFT_STALL_MIN_COUNTS` を 8 に戻す（0.2 mm しか進まなくても `ENCODER_STALL` で止まらない。`LiftController` と偽 HAL で縛る）
  3. ※ 速さの測定で `LIFT_UM_PER_COUNT` を掛け忘れる（`state.speed_meas` が約 38 倍になる）
- **実機で管理担当が確かめる**: 昇降機を測れる距離（例 300 mm）だけ上げて、`state` の高さと巻尺の差を記録する（`WP-MEAS-01` の前段）

### `WP-LIFT-07` 上端の検知を戻す（`W-1` を閉じる）

- 読む節: [DetailedDesign.md](DetailedDesign.md) §4.1、[EXCEPTION-LEDGER.md](../EXCEPTION-LEDGER.md)
- 作るもの: `LIFT_TOP_DETECT_ENABLED` を `true`・**`LIFT_TOP_MM` を 1800**（2026-10-28 ユーザー決定。-names.md）・`WAIVER(demo): W-1` をすべて消す・試験を「判定する」側へ直す。台帳の `W-1` をクローズにしてコミットハッシュを書く（管理担当）。**`docs/使い方.md`**（起動直後は上昇しない・「上端の検知: 一時無効」が出なくなる）
- 受け入れ: native の試験が成功。`grep -rn 'WAIVER(demo): W-1'` が空。**変異**: ※ 原点を取る前に上昇できる／※ `LIFT_TOP_MM` を設定して高さがそれ以上でも上昇できる → 赤
- **実機で管理担当が確かめる**: 起動直後は上昇しない（`HEIGHT_UNKNOWN`）→ 下げて下端に着けると上昇できる／`LIFT_TOP_MM` を仮に低く置いて、そこで止まる
