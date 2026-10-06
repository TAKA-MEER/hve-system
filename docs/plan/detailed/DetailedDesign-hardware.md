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
| カメラ | GC2145「1080P」・画角 68° | 同（**実機では 1920×1080 が取れない**。§2.1.1） |
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

### 2.1.1 UnitV2 の実機調査の結果（`WP-MEAS-06`。2026-10-06）

PC から USB の有線（`10.254.239.1`）と SSH で調べた。**機器に残る変更はしていない**（組み込みのサービスは一度止めて、元どおり起動し直した）。

| 項目 | 結果 | 設計への影響 |
| --- | --- | --- |
| OS・Python | Linux 4.9.84（armv7l）・glibc・BusyBox。**Python 3.8.6** | `DD-7` のとおり |
| 入っている依存 | **`aiohttp` 3.6.2・`pyserial` 3.4・OpenCV 3.4.9・numpy 1.16.4・PyYAML 5.3.1**（`zeroconf`・`tomli` は無い） | **OS のものをそのまま使う**。ホストの `.venv38` も同じ版に揃える（[-names.md](DetailedDesign-names.md) §1） |
| 依存の追加 | `m5stack` の `pip3` は権限エラーで動かない。**純 Python の wheel はアプリの置き場に展開して `PYTHONPATH` で読める**（`tomli` で確認）。UnitV2 からインターネットへは出ない（`th-rpi-ap` の既定経路の先が無い） | 足すのは純 Python のものだけ・開発機で落として展開して持ち込む。**C 拡張の追加は避ける** |
| 権限 | **`/dev/video0`・`/dev/ttyS1`・`/dev/null` まで root だけ**が読み書きできる。`sudo` は `m5stack` のパスワードで通る | アプリは **root で動かす**（組み込みのサービスと同じ） |
| 組み込みのサービス | `/etc/init.d/S85runpayload` が `python3 server.py` を起動し、その子の `server_core.py` が **:80（Flask）と `/dev/ttyS1`（115200）** を持つ。カメラは選んだ機能のときだけ `bin/` の別プログラムが使う。**`server.py`・`server_core.py` の 2 つを止めると :80 が空く**（確認） | 配備では `S85runpayload` を自動起動から外す（`WP-CAM-05`）。**`S85runpayload stop` は `killall -9 python3` で温度監視（`check_thermal.py`）まで止めるので使わない** |
| 自動起動 | BusyBox の init が `/etc/init.d/S??*` を順に起動する | アプリの起動は `S86hve` のような init スクリプトにする |
| 既存の AP（STA） | **すでに `th-rpi-ap` に繋がっている**（`wlan0`・DHCP で `192.168.5.129`・`/etc/wpa_supplicant.conf`）。aiohttp の HTTP・WS を :80 で出し、**PC から無線で届いた**（WS の往復 中央値 6.6 ms・最大 12.6 ms） | 決まり |
| **自前の AP** | **同時に `hostapd` が AP `M5UV2_4afd` を `wlan1` で出している**（`th-rpi-ap` と同じ 1 ch・20 MHz） | **`th-rpi-ap` の帯域を食いうる（spec `H-A8`）。配備で止めるかをユーザーに確認**（[-open.md](DetailedDesign-open.md) `P-11`） |
| `.local` の名前解決 | Python（glibc）からは `.local` を引けない（nss-mdns が無い）。`avahi-daemon` と `avahi-resolve-host-name` はあるが、**`/etc/avahi/avahi-daemon.conf` の `allow-interfaces=eth0, wlan1` で `wlan0`（`th-rpi-ap`）を見ていない** | `zeroconf` をやめ、**avahi の `allow-interfaces` に `wlan0` を足し、アプリは `avahi-resolve-host-name -4` で引く**（[DetailedDesign.md](DetailedDesign.md) §4.6・`P-12`） |
| **カメラの解像度** | **1920×1080 は取れない**（要求すると 1280×720 になる）。取れたのは 640×480（約 15 fps）・**1280×720（約 20 fps）**・**1600×1200（約 4.7 fps）** | **spec `H-V9`（1080p の前提）が成り立たない。ユーザーに確認**（spec `H-V10`） |
| 映像の負荷 | 1 スレッドで 取り込み → 中央の切り出し → 480p へ縮小 → JPEG（品質 60）: **1280×720 で 約 5〜13 fps**（縮小の方式による。取り込みの 1 枚に約 46 ms、処理に 33〜85 ms）。1600×1200 で 約 3.6〜5 fps。**そのあいだ CPU 1 コアが 100 %**。メモリは最大 74 MB・空き 75 MB。温度 72〜74 ℃ | 取り込みと処理を別スレッドにすれば 1280×720 で約 10 fps の見込み（未測定）。[DetailedDesign.md](DetailedDesign.md) §4.4 |
| 温度 | `check_thermal.py` が 95 ℃ 以上で CPU を省電力にし、組み込みのサービスを止める（自前のアプリは止めない） | 長時間の負荷は `WP-MEAS-02`・`05` で見る |
| 書ける場所 | ルート 117 MB 空き（UBIFS）・SD カード `/media/sdcard`（14.5 GB 空き。カードが挿さっていれば） | アプリはルートに置ける大きさ |
| **Grove の UART の電圧とピンの並び** | **未確認**（テスタと UNO が要る。ユーザーに依頼） | **確認できるまで分圧を入れる**（§2.3） |
| UNO との往復・115200 で化けないか | **未確認**（UNO をつないでから） | `IO_BAUD` は仮のまま |

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
