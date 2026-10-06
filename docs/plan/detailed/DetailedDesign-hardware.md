# 機器・ピン・電源（v2）

[DetailedDesign.md](DetailedDesign.md) の詳細。**部品を買う前・配線する前・ファームのピンを触る前に読む。**
旧版は [archive/v1/detailed/DetailedDesign-hardware.md](../archive/v1/detailed/DetailedDesign-hardware.md)。

**出典の印**: 「M5 文書」＝ M5Stack 公式の UnitV2 の文書（§2 の表の下）。**「要確認」は文書で確かめられなかったもの**で、`WP-MEAS-06` で実機で決める。

---

## 0. 使う部品（spec 2026-10-06）

| 部品 | 型番 | 付く所 | 要点 |
| --- | --- | --- | --- |
| 昇降部 | **旧版と同じ**（ESP32・MD10C・パワーウィンドウモータ・下端リミットスイッチ・HC-SR04） | 昇降部 | §1 |
| カメラ | **M5Stack UnitV2** | カメラモジュール | §2 |
| 制御基板 | **Arduino UNO**（ATmega328P・5 V・16 MHz） | カメラモジュール | §2.2 |
| ヨー | **28BYJ-48**（5 V）＋ ULN2003 | カメラモジュール | 旧版と同じ（半ステップ 1 回転 約 4096・実用の上限 約 60〜90 deg/s・ギアのガタ数度） |
| ピッチ | **MG996R** | カメラモジュール | 標準サーボ（50 Hz・パルス幅で角度）。**SG90 より大きな電流を食う**（§3。値はデータシートで要確認） |
| 天井の距離計 | **SRF02**（I2C） | カメラモジュール | 旧版と同じ（5 V・最小測定距離 約 15 cm・最大 約 6 m・1 回 約 66 ms） |
| 電源 | **リチウムイオンのモバイルバッテリ** | カメラモジュール | §3 |

## 1. 昇降部

**旧版 [§1](../archive/v1/detailed/DetailedDesign-hardware.md) のピン・決まりごとをそのまま使う**（MD10C DIR=GPIO14・PWM=GPIO32、下端=GPIO27（NC 配線）、HC-SR04 TRIG=GPIO25・ECHO=GPIO26（**分圧必須**）、HC-SR04 は ESP32 から 15 cm 以上離す）。
v2 で変わるのは **HC-SR04 の値を判定に使わない**ことだけ（`W-1`。配線は残す）。

## 2. カメラモジュール

### 2.1 UnitV2（M5 文書で確かめたこと）

| 項目 | 値 | 出典 |
| --- | --- | --- |
| SoC | SigmaStar SSD202D（Cortex-A7 2 コア 1.2 GHz） | M5 文書（製品ページ） |
| メモリ・記憶 | 128 MB DDR3 ／ 512 MB NAND | 同 |
| カメラ | GC2145「1080P」・画角 68° | 同（**実際に取り込める解像度と fps は要確認**） |
| 無線 | 2.4 GHz 802.11 b/g/n | 同 |
| 電源 | 5 V・500 mA（USB Type-C） | 同 |
| Python | **3.8**（Jupyter Notebook 付き）。OpenCV が使える | M5 文書（Jupyter Notebook の頁） |
| カメラの口 | `/dev/video0`（`cv2.VideoCapture(0)`） | 同 |
| Grove の UART | `/dev/ttyS1`。文書の例は 115200 bps・`pyserial` | 同 |
| ログイン | SSH `m5stack` ／ `12345678`（root もある） | 同 |
| 既定の無線 | **AP** として起動（SSID `M5UV2_XXX`・パスワード `12345678`）。`10.254.239.1`（USB の有線でも同じ） | M5 文書（組み込みの認識サービスの頁） |
| 組み込みのサービス | 起動すると認識サービスが動き、480p の映像を Web に出し、UART へ JSON を出し続ける | 同 |

出典: <https://docs.m5stack.com/en/unit/unitv2>・<https://docs.m5stack.com/en/guide/ai_camera/unitv2/jupyter_notebook>・
<https://docs.m5stack.switch-science.com/en/guide/ai_camera/unitv2/base_functions>（2026-10-06 に確認）。

### 2.1.1 UnitV2 で要確認（`WP-MEAS-06`）

| 項目 | なぜ要るか | 決まらなかったとき |
| --- | --- | --- |
| **組み込みのサービスの止め方と自動起動の外し方** | サービスが `/dev/video0` と `/dev/ttyS1` を使う（UART へ JSON を出し続ける） | 自分のアプリが動かない |
| **既存の AP に繋ぐ（STA）設定のしかた** | `th-rpi-ap` に乗せる（spec） | — |
| **Grove の UART の電圧とピンの並び** | **UNO の TX は 5 V。UnitV2 が 3.3 V なら直結で壊れる**（§2.3） | 確認できるまで分圧を入れる |
| 1080p の取り込みの fps、1080p 取り込み＋切り出し＋480p の JPEG での CPU・メモリ | [DetailedDesign.md](DetailedDesign.md) §4.4 | 同節の順に下げる |
| `pip` があるか・`aiohttp`・`zeroconf`・`pyserial` を入れられるか（インターネットのある無線で準備できるか） | アプリの依存 | 開発機から持ち込む |
| `.local` の名前解決ができるか | [DetailedDesign.md](DetailedDesign.md) §4.6（できなくてもアプリが自分で引く） | — |
| 自動起動の仕組み（init の種類）・アプリを動かすユーザー・書ける場所と空き容量 | 配備（`WP-CAM-05`） | — |

### 2.2 Arduino UNO のピン

| 用途 | ピン | 状態 | 備考 |
| --- | --- | --- | --- |
| UnitV2 との UART | D0（RX）・D1（TX） | **仮** | ハードウェアの `Serial`。**書き込み中は UnitV2 との線を外す**（USB と共用のため）。デバッグの出力に `Serial` を使わない（UnitV2 へ流れる） |
| ULN2003 IN1〜IN4 | D4・D5・D6・D7 | **仮** | 半ステップの並びは旧版の実機確認どおり（隣り合うコイルを順に。[-names.md](DetailedDesign-names.md) §5） |
| MG996R の信号 | D9 | **仮** | `Servo` ライブラリ（Timer1 を使う。D10 の PWM が使えなくなるが使わない） |
| SRF02 SDA / SCL | A4 / A5 | UNO の I2C | **5 V の I2C なので直結**（レベル変換不要）。アドレスは工場出荷の 0x70 |
| ステッピングの刻み | Timer2 | — | `Servo` が Timer1、`millis` が Timer0 を使うため |

### 2.3 UNO ⇔ UnitV2 の配線

| UNO | UnitV2（Grove） | 備考 |
| --- | --- | --- |
| D1（TX・**5 V**） | RX | **確認できるまで分圧（例 1 kΩ / 2 kΩ）で 3.3 V に落として入れる** |
| D0（RX） | TX | UnitV2 の 3.3 V を UNO がそのまま読めるか（UNO の HIGH の下限は 0.6 × 5 V ＝ 3.0 V で余裕が小さい）を `WP-MEAS-06` で確かめる。だめならレベル変換器 |
| GND | GND | **必ず共通にする** |
| — | 5 V | **使わない**（UNO の電源は §3） |

### 2.4 Arduino の決まりごと

| 部品 | 決まりごと | 理由 |
| --- | --- | --- |
| 28BYJ-48 | 止めている間はコイルの電流を切る。指令が途絶えたときも同じ | 旧版と同じ（発熱・電池） |
| 28BYJ-48 | 刻みは Timer2 の割り込みで、`IO_STEP_TICK_US` ごとに位相を足す | I2C の読み取りや行の解釈でループが止まっても刻みが揺れない |
| MG996R | 指令が途絶えても**パルスを止めない**（その角度で保つ）。起動時は `PITCH_INITIAL_DEG` へ一気に動く | パルスを止めると脱力してカメラが倒れる。起動時の跳びはサーボの性質 |
| SRF02 | `Wire.setWireTimeout()` で I2C の時間切れを付け、時間切れは `st=1` で送る | I2C が固まって `loop` が止まるのを防ぐ（**使っている Arduino AVR コアがこの関数を持つかを `WP-IO-02` のビルドで確かめる**） |
| 全体 | AVR のウォッチドッグ（`IO_WDT_TIMEOUT`）。`loop` が回るたびに叩く | それでも固まったら再起動する。**再起動するとピッチが `PITCH_INITIAL_DEG` へ跳ぶ**（受け入れる。[-open.md](DetailedDesign-open.md) `P-8`） |

## 3. 電源

| 何を | どこから | 非常停止で | 備考 |
| --- | --- | --- | --- |
| 昇降部 | 旧版と同じ | 切れる（モータ）／未確認（ESP32。旧版 `D-3`） | |
| UnitV2 | モバイルバッテリの**出力 B**（USB Type-C） | 切れない | 5 V・500 mA |
| Arduino UNO | **出力 B**（UnitV2 と同じ側。USB-B または 5 V ピン） | 切れない | **サーボと分ける**（下記） |
| **MG996R・28BYJ-48（ULN2003）** | モバイルバッテリの**出力 A**（モータ専用） | 切れない | 大きめのコンデンサ（数百〜1000 µF）を近くに置く |
| SRF02 | UNO の 5 V | 切れない | 消費は小さい |

**モータの電源を UNO・UnitV2 と分ける**: MG996R は動き出しや拘束で大きな電流を流す。同じ出力だと電圧が落ちて **UNO が再起動し、ピッチが初期角へ跳ぶ**（§2.4）・UnitV2 が落ちる。
**出力が 2 つあり、モータ側の出力が MG996R の拘束電流をまかなえるモバイルバッテリ**を使う（[-open.md](DetailedDesign-open.md) `D-9`）。**GND はすべて共通にする。**

## 4. 未確定の部品

| 部品 | 確認すること |
| --- | --- |
| モバイルバッテリ | 出力の数・各出力の電流（MG996R の拘束電流）・容量（`H-V7`）（`D-9`） |
| MG996R | データシートの電流・パルス幅と角度の対応（[-names.md](DetailedDesign-names.md) §5 の `仮`） |
| 下端リミットスイッチ | NC 接点があるか（旧版から） |
