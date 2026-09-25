# 作業パケット

[DetailedDesign.md](DetailedDesign.md) §5 の詳細。**1 パケット ＝ 1 ブリーフ ＝ 1 ブランチ**
（[ImplementationPlan.md](../ImplementationPlan.md) §2）。状態は ImplementationPlan §4 で管理する。

---

## 0. 全パケット共通の約束

- **読むのは、そのパケットの「読む節」だけ。**名前は [-names.md](DetailedDesign-names.md) にあるものだけを使う
- **受け入れ条件はすべてコマンド。**「変異」の行は、**そのとおりに実装を壊したら試験が赤くなる**ことを求める
  （管理担当が自分で壊して確かめる）
- **th-system のリポジトリには触らない**
- 一時ファイルは `.briefs/tmp/`。`/tmp` を使わない
- 仮値は [-names.md](DetailedDesign-names.md) §5 の値をそのまま使う。変えるなら先にそちらを直す

共通のコマンド（リポジトリ直下から）:

```bash
pio test -d firmware/lift -e native                   # 昇降部のホスト試験
pio run  -d firmware/lift -e esp32dev                 # 昇降部のビルド
python3 -m pytest -p no:anyio camera/tests            # カメラ部の試験（-p no:anyio は CLAUDE.md「環境の癖」）
```

## 1. 一覧

| ID | 段階 | 内容 | 先に要るもの | 実機 |
| --- | --- | --- | --- | --- |
| `WP-BASE-01` | 0 | リポジトリの骨格・試験の土台・`CLAUDE.md` のビルドと試験の節 | — | 不要 |
| `WP-MEAS-01` | 0 | **先行試作で**ストローク・全行程の時間・高さの超音波の読み値を測り、`LIFT_TOP_MM` の候補を出す | — | 要 |
| `WP-LIFT-01` | 1 | `lift_core`（判定・コントローラ・コーデック）とホスト試験 | BASE-01 | 不要 |
| `WP-LIFT-02` | 1 | ESP32 の実物（HAL・`main.cpp`・WS）。`tools/lift_probe.py` | LIFT-01 | 要 |
| `WP-CAM-01` | 2 | カメラ部の純ロジック（天井・設定・軸） | BASE-01 | 不要 |
| `WP-CAM-02` | 2 | 制御ループ・昇降部との接続・アプリ・**偽物のモード** | CAM-01 | 不要 |
| `WP-CAM-03` | 2 | 実物のサーボ・ステッピング・超音波・ustreamer・systemd | CAM-02 | 要 |
| `WP-UI-01` | 3 | 操作画面と設定画面 | CAM-02 | 不要（偽物のモード） |
| `WP-MEAS-02` | 4 | 映像の遅延（`H-V6`） | CAM-03 | 要 |
| `WP-MEAS-03` | 4 | **th-system と同時に動かして無線を圧迫しないか**（`H-A8`） | CAM-03 | 要（th-system も） |
| `WP-MEAS-04` | 4 | 「古い」とみなす時間・天井の余裕の実測（`H-V8`） | LIFT-02・CAM-03 | 要 |
| `WP-MEAS-05` | 4 | モバイルバッテリの持ち時間（`H-V7`） | CAM-03 | 要 |

**`WP-MEAS-01` は今すぐできる**（先行試作 `../elevator-motor-control` が動くため）。`LIFT_TOP_MM` が決まらないと上昇が試せない（提案 `P-2`）。

## 2. パケットの中身

### `WP-BASE-01` 骨格

- 読む節: [DetailedDesign.md](DetailedDesign.md) §2・[-names.md](DetailedDesign-names.md) §1
- 作るもの: §1 のファイル構成の空の骨格。`env:native` に空でない試験 1 件、pytest に 1 件。`CLAUDE.md` に「ビルドとテスト」節（上の共通コマンド）
- 受け入れ: 共通コマンド 3 つが成功する

### `WP-LIFT-01` 昇降部の判定

- 読む節: [DetailedDesign.md](DetailedDesign.md) §3・§4.1・[-protocol.md](DetailedDesign-protocol.md) §2.2・§2.3
- 作るもの: `lift_decide()`・`LiftController`（偽 HAL を差して `step(now)` でモータへ出す）・`cmd_codec`
- 受け入れ: `pio test -d firmware/lift -e native` が成功し、**次の変異がそれぞれ赤になる**
  1. `ceil_ok` の判定を消す／反転する
  2. `cmd_codec` で `ceil_ok` が欠けたときに `true` とみなす
  3. 高さの読み値が古い・無効でも上昇を許す
  4. ウォッチドッグ（`CMD_TIMEOUT`）を消す
  5. `LiftController` が `lift_decide()` の結果を**モータへ渡さず指令をそのまま渡す**（呼び出し側の変異）
  6. 下端スイッチを見ずに下降を許す

### `WP-LIFT-02` 昇降部の実物

- 読む節: [-hardware.md](DetailedDesign-hardware.md) §1・[-protocol.md](DetailedDesign-protocol.md) §1〜§2.3
- 作るもの: `hal_esp32`・`main.cpp`（**組み立てと呼び出しだけ**）・WS サーバ（先行試作を流用。静的な画面は持たない）・`tools/lift_probe.py`（PC から `cmd` を送り `state` を表示する確認用）
- 受け入れ: `pio run -d firmware/lift -e esp32dev` が成功。実機で管理担当が確かめる: 上昇・下降／`lift_probe.py` を止めると `LIFT_CMD_TIMEOUT_MS` で止まる／`ceil_ok:false` で上昇しない／下端スイッチで下降しない／高さの超音波を外すと上昇しない

### `WP-CAM-01` カメラ部の純ロジック

- 読む節: [DetailedDesign.md](DetailedDesign.md) §3・§4.2・[-protocol.md](DetailedDesign-protocol.md) §3
- 作るもの: `ceiling.py`・`settings.py`・`axes.py`・`params.py`
- 受け入れ: pytest が成功し、変異が赤になる: 天井の古さの判定を消す／`min ≦ init ≦ max` の検証を消す／軸が可動範囲を越える

### `WP-CAM-02` 制御ループ・アプリ・偽物のモード

- 読む節: [DetailedDesign.md](DetailedDesign.md) §3・§4.2・[-protocol.md](DetailedDesign-protocol.md) 全部
- 作るもの: `control.py`・`lift_link.py`・`app.py`・`hw/base.py`・`hw/fake_hw.py`・`hw/fake_lift.py`・`__main__.py`（`--fake`）
- 受け入れ: pytest が成功。**試験は aiohttp の試験クライアント（ブラウザ役）と偽の ESP32 WS サーバを立て、本物の経路で確かめる。**変異が赤になる:
  1. **送る直前に `ceil_ok` を計算せず、前回の値を使い回す**（天井センサの値が止まったのに `true` が送られ続ける）
  2. 止まっている間の `stop` の定期送信を消す
  3. `hold` の鮮度判定（`HOLD_TIMEOUT`）を消す
  4. ブラウザの WS が閉じても止めない
  5. 設定の上限を越える `speed` を丸めずに送る
- 加えて: `python3 -m hve_camera --fake` が起動し、`curl http://localhost/api/settings` が設定を返す（ポートは起動引数で変えられること）

### `WP-CAM-03` カメラ部の実物

- 読む節: [-hardware.md](DetailedDesign-hardware.md) §2・§3・[DetailedDesign.md](DetailedDesign.md) §4.2（依存の入れ方）
- 作るもの: `hw/pigpio_hw.py`・`systemd/*.service`（:80 で待つため `AmbientCapabilities=CAP_NET_BIND_SERVICE`。root で動かさない）・ラズパイの準備手順（`docs/使い方.md` を作る）
- 受け入れ: ラズパイで `systemctl status hve-camera hve-ustreamer` が active。実機で: ピッチ・ヨーが deg/s で動き範囲で止まる／天井の超音波を手で塞ぐと上昇できない／超音波の線を抜くと `CEILING_STALE`

### `WP-UI-01` 画面

- 読む節: spec [Spec-ui.md](../spec/Spec-ui.md)・[-protocol.md](DetailedDesign-protocol.md) §2.1・§2.4・§3
- 作るもの: `web/` の全部。映像の上に上昇・下降・ピッチ・ヨーのボタン（押している間だけ）と速度スライダー 4 本、状態（高さ・天井・停止理由・仮値・偽物のモード）、設定画面
- 受け入れ: `--fake` で起動し、管理担当がブラウザで確かめる: スライダーの範囲と初期位置が設定どおり／開き直すと初期値に戻る／設定の不正値が保存されない／ボタンを押したままタブを閉じると止まる／停止理由が出る。**外部への読み込みが無い**（`grep -rE 'https?://' camera/web` が空）。**スクロールが出ない**（spec [Spec-ui.md](../spec/Spec-ui.md) §0.1）: `docs/plan/spec/mockup/check_noscroll.js` を実装の画面向けに状態の作り方だけ差し替えて（URL 引数の代わりに `--fake` の `/api/fake` で状態を作る）全サイズで通す

### `WP-MEAS-*` 測定

測定手順と結果は `docs/試験項目.md` に書き、結果で [-names.md](DetailedDesign-names.md) §5 の `仮` を外す。
