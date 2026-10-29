# 詳細設計書 — 昇降・カメラシステム（v2）

[完全設計書](../spec/Spec.md) を実装できる形に落とす。読み方は [README.md](README.md)。旧版は [archive/v1/detailed/](../archive/v1/detailed/DetailedDesign.md)。

**2026-10-06 初版。**ユーザーから詳細設計を任された。**spec に無い振る舞いは勝手に決めていない**
——決める必要があったものは [-open.md](DetailedDesign-open.md) §1 に**提案**として置き、2026-10-06 にすべて採用された。

> **既存コードのコメントにある `docs/plan/detailed/…` への参照は旧版（v1）を指す。**v1 と v2 で同じファイル名を使っているので、
> コメントのリンクは黙って v2 の別の中身を指す。**パケットで触ったファイルは、コメントの参照を v2 に直す**（[-packets.md](DetailedDesign-packets.md) §0）。

---

## 1. 設計方針

`DD-1`〜`DD-5` は旧版から引き継ぐ（[旧版 §1](../archive/v1/detailed/DetailedDesign.md)。中身は下の表で読み替えた）。

| ID | 方針 |
| --- | --- |
| **`DD-1`** | **名前を発明する余地をゼロにする。**必要な名前が無いと分かったら、実装する前に [-names.md](DetailedDesign-names.md) へ行を足す |
| **`DD-2`** | **判定ロジックはハードウェア・通信から切り離し、ホストで試験する。**さらに**呼び出し側（判定結果を実際にモータへ効かせる数行）を偽物のハードウェアで縛る試験**を別に持つ |
| **`DD-3`** | **裸の数値を書かない。**仮値は [-names.md](DetailedDesign-names.md) §5 で `仮` と印を付け、画面に「仮値で動作中」と出す |
| **`DD-4`** | **安全の判定は、止める相手を動かしている機器が自分で行う。**昇降の停止（下端・天井・連続駆動・途絶）は**すべて昇降部**が決める（spec `H-M4`）。ヨー・ピッチの途絶は**Arduino が自分で**止める。上部モジュールは判定を肩代わりしない |
| **`DD-5`** | **インターネットに出られない前提で作る。**`th-rpi-ap` には既定経路が無い。画面は外部の CDN・フォントを読まない |
| **`DD-6`** | **昇降部はカメラを知らない。**昇降部が上部モジュールについて知るのは [-protocol.md](DetailedDesign-protocol.md) §2 の取り決めだけ。SRF02 の最小測定距離のような**機種固有の値を昇降部に持ち込まない**（上部モジュールが汎用の 4 状態に直して送る。§3） |
| **`DD-7`** | **カメラモジュールのアプリは Python 3.8 で動かす**（UnitV2 の Python が 3.8。[-hardware.md](DetailedDesign-hardware.md) §2）。3.9 以降の書き方を使わない。ホストでも 3.8 で試験を回す（§4.7） |

---

## 2. 全体構成

```
操作端末（ブラウザ）                     操作端末（ブラウザ）
  │ HTTP :80  カメラモジュールの画面・設定 API    │ HTTP :80  昇降部の画面・設定 API
  │ WS   :80/ws 操作・状態                       │ WS   :80/ws/ui 操作・状態
  │ HTTP :8080 映像（MJPEG）                     ▼
  ▼                                          昇降部 ESP32  hve_lift
カメラモジュール UnitV2                         ├ MD10C → パワーウィンドウモータ
  hve_camera（Python 3.8）──WS :80/ws/module──→ ├ 下端: リミットスイッチ
  hve_video （Python 3.8・別プロセス）             └ 高さ: エンコーダ E6A2-CW3C（PCNT。W-1 の間は表示だけ）
  │ UART /dev/ttyS1（Grove）
  ▼
Arduino UNO  hve_cam_io
  ├ ヨー: 28BYJ-48（ULN2003）
  ├ ピッチ: MG996R
  └ 天井: SRF02（I2C）
```

| 部分 | 何で作るか | 理由 |
| --- | --- | --- |
| 昇降部ファーム | 旧版と同じ（PlatformIO ＋ Arduino・**`espressif32@7.0.1`**・ESPAsyncWebServer）。`lift_core` を広げる | ハードウェアが同じ（spec 2026-10-06）。判定の土台と試験を使い回す |
| 昇降部の画面 | 素の HTML ＋ JavaScript。**ファームに埋め込む**（ビルド時に gzip して C の配列にする。[-open.md](DetailedDesign-open.md) `P-6`） | ファイルシステムを別に書き込む手順を増やさない |
| 昇降部の設定 | ESP32 の NVS（`Preferences`） | spec `H-U7`（昇降の速度は昇降部に保存） |
| 上部モジュール ⇔ 昇降部 | WebSocket（ESP32 がサーバ）＋ JSON。**口を画面用（`/ws/ui`）と上部モジュール用（`/ws/module`）に分ける**（§3） | 「命令の出どころ」を接続に結びつけ、1 通ごとのフィールドで変わらないようにする |
| カメラモジュールのアプリ | 旧版の `camera/hve_camera`（`aiohttp`）を **Python 3.8 へ移し**、ラズパイの GPIO の層を **Arduino との UART の層**に置き換える | 制御・画面・設定 API・偽物のモードを使い回す |
| 映像 | 旧版の `camera/hve_video` を UnitV2 へ移す。**カメラの 720p の MJPEG を `v4l2-ctl` で受け、480p に作り直して配信**。静止画はカメラの JPEG をそのまま返す（spec `H-V10`・`H-U9`。§4.4） | 中央を切り出すデジタルズームをそのまま使える。OpenCV 3.4.9 では MJPEG を選べず、`ffmpeg` は OOM を起こす（[-hardware.md](DetailedDesign-hardware.md) §2.1.1） |
| Arduino ファーム | PlatformIO ＋ Arduino（`atmelavr`・`uno`）。`io_core`（純ロジック）とホスト試験 | 昇降部と同じ作り方。ステッピングの刻みを Python から外せる |
| 画面（カメラモジュール） | 旧版の `camera/web` を直す | spec「旧版とほぼ同じ」 |

## 3. 安全の取り決めを通信に落とす（最重要）

spec [Spec-safety.md](../spec/Spec-safety.md) §2・[Spec-module.md](../spec/Spec-module.md) §2 を、**取り違えが起きない形**で通信に落とす。

### 3.1 命令の出どころは「接続」で決める

| 接続 | 出どころ | 天井の距離 |
| --- | --- | --- |
| `/ws/ui` | 昇降部の画面（単体での操作） | **使わない**（spec #4c） |
| `/ws/module`、`hello` で `ceiling_sensor: false` を送ってきた | 距離計を持たない上部モジュール | **使わない**（spec `H-X11`） |
| `/ws/module`、`hello` で `ceiling_sensor: true` を送ってきた、**または `hello` をまだ受けていない・読めない** | 距離計を持つ上部モジュール | **使う**（spec #3〜#4） |
| （接続ではない）`ConnKind::BUTTON` | 昇降部の点検用の物理ボタン（2026-10-29。§4.1.3） | **使わない**（spec #4c） |

- **口はコードの定数**（`LIFT_WS_UI_PATH`・`LIFT_WS_MODULE_PATH`。カメラモジュール側も `LIFT_WS_MODULE_PATH` と同じ値の定数）。**設定で変えられるようにしない**（書き間違えて `/ws/ui` に繋ぐと、天井を見ないまま昇降できてしまう）。
- **昇降部は `/ws/ui` で `hello` を受けたら、その接続を閉じる。**上部モジュールが誤って画面の口に繋いだら、黙って動かずに大きく失敗させる。
- **`ceiling_sensor` は接続ごとに 1 度だけ受け付ける。**2 度目以降の `hello` は無視する（途中で「持たない」に変えて守りを外させない）。
- **欠けている・読めないときは、厳しい側（持つ）に倒す。**上部モジュールの不具合ひとつで天井の守りが外れないようにする。
- 上部モジュールは「持つか」を**いまセンサが読めるかどうかから計算しない**（センサが壊れた瞬間に「持たない」扱いになり、守りが外れる）。
  カメラモジュールは常に `true` を送る（[-names.md](DetailedDesign-names.md) §5 `module_ceiling_sensor`）。

### 3.2 天井の値は命令と一緒に運ぶ（旧版 §3 の考え方を引き継ぐ）

1. 上部モジュールは、昇降の命令（`hold`）を送る**その瞬間に**、最後の天井の読み値と**その古さ**（`age_ms`）を命令に載せる
2. 昇降部は、受け取った `age_ms` に**受け取ってからの経過時間を足した古さ**で判定する（`ceiling_check`。[-names.md](DetailedDesign-names.md) §1）
3. 閾値（`CEILING_MARGIN_MM`）と古さの上限（`CEILING_STALE_MS`）は**昇降部が持つ**（spec `H-M4`）
4. 天井の状態は**汎用の 4 状態**で送る（`DD-6`）:

| 状態 | 意味 | 昇降部の判定（距離計を持つ接続からの上昇） |
| --- | --- | --- |
| `MEASURED` | 測れた（`mm` 付き） | `mm ≦ CEILING_MARGIN_MM` なら止める（`CEILING_NEAR`）。それ以外は許す |
| `TOO_NEAR` | 近すぎて測れない | 止める（`CEILING_NEAR`。spec #3a） |
| `NO_ECHO` | 反射が返らない（遠い） | **許す**。状態に `OUT_OF_RANGE` を出す（spec #3b） |
| `READ_ERROR` | 距離計が読めない | 止める（`CEILING_STALE`。spec #4） |
| （欠けている・知らない値・古い） | — | 止める（`CEILING_STALE`。spec #4） |

→ **「天井の値が古い」と「命令が古い」が同じ事象になる。**上部モジュールが固まっても、無線が切れても、センサが死んでも、
上昇は最長 `CEILING_STALE_MS` 以内に止まる（命令の途絶は `LIFT_CMD_TIMEOUT_MS`）。

### 3.3 操作の持ち主（最後の操作が勝つ。spec `H-M1`）

旧版は止まっている間も `stop` を送り続けて生存確認にしていた。**v2 でそれをすると、上部モジュールの生存確認が昇降部の画面の操作を毎回打ち消す。**
そこで「押し始め・押し続け・離す」を分け、**押し始めた操作元だけが持ち主になる**。

| 受け取ったもの | 振る舞い |
| --- | --- |
| `hold`（その接続で**新しい** `press`） | **持ち主をこの接続・この `press` に替える**（最後の操作が勝つ） |
| `hold`（持ち主と同じ接続・同じ `press`） | 押し続け。方向・デューティ・天井の値を更新し、受け取った時刻を更新する |
| `hold`（持ち主でない接続の、古い `press`） | **無視する**（取って代わられた側が押し続けているだけ） |
| `hold`（**持ち主が空のとき**の、その接続で前に見た `press` 以下のもの） | **無視する。**止まったあと（離した・途絶えた・接続が閉じた）は、**より新しい `press` でなければ持ち主になれない**（UnitV2 が固まって溜まった `hold` が、再開後に遅れて届いても動き出さない） |
| `release`（持ち主から） | 止める（`CMD_STOP`）。持ち主を空にする |
| `release`（持ち主でないものから） | 無視する（[-open.md](DetailedDesign-open.md) `P-9`） |
| 持ち主の `hold` が `LIFT_CMD_TIMEOUT_MS` 届かない | 止める（`CMD_TIMEOUT`）。持ち主を空にする |
| 持ち主の接続が閉じた | **その場で**止める（`OWNER_GONE`）。持ち主を空にする |
| ★`hold`（**持ち主が物理ボタン**のときの、物理ボタン以外の接続から。新しい `press` でも） | **無視する**（2026-10-29。spec [Spec-ui.md](../spec/Spec-ui.md) §0.3）。**ただし、その `press` を接続の「前に見た `press`」に記録する**（物理ボタンを離して持ち主が空になったあと、押し続けていた遠隔の操作が同じ `press` で持ち主にならない。上の「持ち主が空のとき」の行で無視される） |
| ★物理ボタンの `hold`（新しい `press`） | 今までどおり持ち主を替える（遠隔の操作から取って代わる） |

この規則は純ロジック `LiftArbiter` にまとめ、ホストで試験する（[-names.md](DetailedDesign-names.md) §1）。

### 3.4 ヨー・ピッチの途絶（Arduino）

- UnitV2 は Arduino へ `io_cmd_period_ms` ごとに指令を送る（止まっている間も送る。**相手は 1 つなので生存確認を兼ねてよい**）
- Arduino は `IO_CMD_TIMEOUT_MS` 指令が届かなければ、**ヨーを止めてコイルの電流を切り、ピッチはその角度で保つ**（[-open.md](DetailedDesign-open.md) `P-8`）
- Arduino の `loop` が固まったとき（I2C の固まりなど）は、AVR のウォッチドッグで再起動する（[-hardware.md](DetailedDesign-hardware.md) §2.4）

### 3.5 UART の古い行を「今」の値と取り違えない

UnitV2 が一時的に固まると、Arduino の天井の行が受信バッファに溜まる。**再開後に溜まった行を 1 行ずつ「今」受け取ったものとして扱うと、古い天井の値が新しく見える。**

- Arduino は天井の行に**自分の時計（`millis`）**を付ける
- UnitV2 は**読むたびにバッファを全部読み出し**、行ごとに「受け取った時刻 − Arduino の時計」の**直近の最小値**で Arduino の時計を自分の時計に直す（`UnoClock`）。
  遅れて読んだ行はこの差が大きいので、古さが正しく大きく出る
- **前回読んでから `ceiling_read_stale_ms` より長く空いたときは、その回に読み出した行をすべて捨て、次の回に読めた行が来るまで `READ_ERROR` にする。**
  受信バッファが溢れて新しい側の行が捨てられると、残った中で一番新しい行も実は古い。窓の全行が遅れた行になると最小差の基準ごと遅れ、古さを 0 近くと見誤る。昇降部は送られた `age_ms` を信じるしかないので、ここで止める
- Arduino の時計が戻った（再起動した）ら、対応をやり直す

## 4. 部分ごとの設計

### 4.1 昇降部（`firmware/lift/`）

| 層 | 中身 | ホストで試験 |
| --- | --- | --- |
| `lift_decide()` | 純関数。持ち主の命令・天井の判定結果・下端・経過時間から「方向・デューティ・停止理由」を決める | ○ |
| `ceiling_check()` | 純関数。天井の値・古さ・閾値・その接続が距離計を持つか → `(ok, 理由)`（§3.2） | ○ |
| `LiftArbiter` | 操作の持ち主（§3.3）。接続 ID・`press`・受け取った時刻を持つ | ○ |
| `LiftController` | 受け付け・持ち主・ウォッチドッグ・`lift_decide()` の結果を**モータに効かせる**。ハードウェアは `hal.h` の抽象を通す | ○（偽 HAL） |
| `lift_settings` | 昇降の設定の検証（速度は下限 ≦ 初期値 ≦ 上限・0〜`LIFT_SPEED_ABS_MAX_MM_S`、モータへの出力の上限 `motor_max_pct` は `LIFT_MOTOR_MAX_PCT_MIN`〜`LIFT_DUTY_ABS_MAX_PCT`）と JSON | ○ |
| `cmd_codec` | JSON ⇔ 構造体。読めない `hold` は捨てる（持ち主の `hold` が途絶えれば止まる）。読めない `hello` は「距離計を持つ」 | ○ |
| `EncoderHeight` | 純関数に近い小さな状態。エンコーダの数と下端スイッチ → 高さ・読めるか（§4.1.1） | ○ |
| `SpeedLoop` | 速さのフィードバック（PI）。目標 mm/s・測った mm/s・経過時間 → デューティ（§4.1.2） | ○ |
| `hal_esp32` ／ `main.cpp` | 実物の MD10C・スイッチ・エンコーダ（PCNT）・WiFi・WS・HTTP・NVS。**`main.cpp` は組み立てて呼ぶだけ** | × |
| `web/` ＋ `scripts/embed_web.py` | 昇降部の画面。ビルド時に `src/web_assets.h`（生成物・除外済み）へ埋め込む | 画面の純関数は node で試験 |

判定の規則（上から順に、最初に当たったもので止める）。**旧版から変わった行に ★**:

| # | 条件 | 結果 | 停止理由 |
| --- | --- | --- | --- |
| 1 | 持ち主がいない（命令を受けていない・離した・途絶えた・接続が閉じた） | 停止 | `CMD_STOP` ／ `CMD_TIMEOUT` ／ ★`OWNER_GONE` |
| 2 | 持ち主の命令が `stop` | 停止 | `CMD_STOP` |
| 3 | 上昇 かつ ★`ceiling_check()` が `ok` でない（距離計を持つ接続のときだけ判定。§3.1） | 停止 | ★`CEILING_NEAR` ／ `CEILING_STALE` |
| 4 | ★上昇 かつ 高さが読めない・古い | **`W-1` の間は判定しない**（`LIFT_TOP_DETECT_ENABLED` が偽） | `HEIGHT_UNKNOWN` |
| 6 | ★上昇 かつ 上端の閾値が設定済み かつ 高さ ≧ 閾値 | **`W-1` の間は判定しない** | `TOP` |
| 7 | 下降 かつ 下端スイッチが押されている | 停止 | `BOTTOM` |
| 8 | ★同じ方向へ連続して回した間に、**目標の速さで進んだとみなした距離**が全行程（`LIFT_TOP_MM`。未設定の -1 なら `LIFT_STROKE_FALLBACK_MM`）の `LIFT_MAX_RUN_PCT` % を超えた（2026-10-29。下の「連続駆動の上限」） | 停止 | `MAX_RUN`（spec #4b） |
| 8a | ★同じ方向の命令が続く間に、モータへ出力しているのに `LIFT_STALL_MS` の間エンコーダが進行方向へ `LIFT_STALL_MIN_COUNTS` 未満しか進まない（**逆向きに進んだ場合も**） | 停止。数え方は #8 と同じく、命令が `stop` になるか向きが替わるまで続ける（反対方向へは動かせる） | ★`ENCODER_STALL`（spec #4d） |
| 9 | それ以外 | 指令どおりの向き。**速さ `speed`（mm/s）を `0`〜`LIFT_SPEED_ABS_MAX_MM_S` に丸め、`SpeedLoop` がデューティ（`0`〜設定の `motor_max_pct`）に直す**（§4.1.2） | `NONE` |

**`W-1`（上端の検知の一時無効）**: 判定 #4・#6 は**消さずに** `LIFT_TOP_DETECT_ENABLED`（`false`）で外す。
その場所に `WAIVER(demo): W-1 上端の検知を一時無効` を置く（[EXCEPTION-LEDGER.md](../EXCEPTION-LEDGER.md)）。
`state` の `top_detect` は `false` を返し、両方の画面が「上端の検知: 一時無効」を常に出す。**高さは測って `state` に載せ続ける。**
**エンコーダで高さを測る実装（`WP-LIFT-05`）と実機での確認が済んだら、`WP-LIFT-07` で `LIFT_TOP_DETECT_ENABLED` を `true` にして `W-1` を閉じる**（spec [Spec-safety.md](../spec/Spec-safety.md) §1.1）。
判定 #4・#6 の中身は変えない（高さの出どころが替わるだけ）。

#### 4.1.1 エンコーダの高さ（`EncoderHeight`。spec [Spec-safety.md](../spec/Spec-safety.md) §1.2）

`hal_esp32` が `loop` ごとに PCNT の数（16 bit）の**前回からの差**を足し込み、下端スイッチの状態と一緒に `EncoderHeight` へ渡す。
`LiftHal` の `height_mm()`・`height_ok()`・`height_at_ms()` の形は変えない（`height_at_ms` は渡した時刻。`HEIGHT_STALE_MS` は「`loop` が回っていない」の守りとして残す）。

| 状況 | 高さ | `height_ok` |
| --- | --- | --- |
| 起動・再起動の直後（下端に一度も着いていない） | 数えるが原点が無い | **偽**（判定 #4 `HEIGHT_UNKNOWN`。上昇しない） |
| 下端スイッチが押されたと読めたが、`LIFT_BOTTOM_DEBOUNCE_MS` 続かずに離れた（2026-10-28） | **何もしない**（押されたとみなさない。下の行の「押された」はすべてこの時間続いたもの） | 変えない |
| 下端スイッチが**押された瞬間**（起動したとき既に押されていれば、起動時） | **そのときの数を原点にする**（高さ 0） | 真 |
| ★**原点を取ったあと、高さが `LIFT_HOME_ACCEPT_MM` を超える所で押された**（2026-10-28） | **原点にしない**（数え続ける） | **偽**（判定 #4 `HEIGHT_UNKNOWN`。上昇しない・下降はできる）。押されている間に高さが `LIFT_HOME_ACCEPT_MM` 以下へ下がったら、そこで押された瞬間と同じに原点を取って真に戻す。離れてから押し直されたときも同じ判定をやり直す |
| 押されている間 | 下降側へ進んだときだけ原点を下げる（高さは 0 のまま）。**上昇側へは原点を動かさない** | 真 |
| 原点を取ったあと | `(数 − 原点の数) × ENC_UP_SIGN × LIFT_UM_PER_COUNT ÷ 1000` | 真 |
| ★**スイッチが押されたまま、原点から上昇側へ `HOME_SLACK_MM` を超えて進んだ** | — | **偽（スイッチの故障とみなす）。**一度離れて押し直されるまで戻らない（`P-14`） |

★の行は**下端スイッチの線が切れたとき**の守り。NC 配線なので断線は「押されている」に見え、そのままだと上がっても高さが 0 に張り付き、上端で止まらない。
その状態では上昇（`HEIGHT_UNKNOWN`）も下降（`BOTTOM`）もできず止まったままになるが、それを受け入れる（配線を直す）。
**起動したときすでに断線していると、途中の高さを 0 と取ってしまう**。上がり始めて `HOME_SLACK_MM` 進んだところで★に当たって止まる。
**2026-10-28 の実機**: 上端付近（表示 1926 mm）を 100 mm/s で上昇中に、下端スイッチが一瞬押されたと読め、高さが 0 に戻った（`bottom` は 10 Hz の `state` では一度も真にならなかった）。NC 配線なので、線・コネクタが一瞬開いても同じに見える。時間の続き（`LIFT_BOTTOM_DEBOUNCE_MS`）と高さの見込み（`LIFT_HOME_ACCEPT_MM`）の 2 段で守る。**`BOTTOM`（下降を止める）は今までどおり読んだそのままで効かせる**（止める側に倒れる）。`LIFT_HOME_ACCEPT_MM` の行で `height_ok` が偽のまま下端で動けなくなったら（エンコーダが滑って高さが大きくずれた）、再起動する（起動時に押されていれば原点を取る）。

**連続駆動の上限（#8）は持ち主が替わっても数え直さない。**同じ方向が続く限り数え続ける（持ち主を交互に替えて上限を逃れられないように）。

**連続駆動の上限の数え方（2026-10-29。spec #4b）**: 固定の時間（旧 `LIFT_MAX_RUN_MS` 10000）をやめ、目標の速さから都度求める。

| 項目 | 決まり |
| --- | --- |
| 数えるもの | `LiftController` が `run_travel_um_`（µm）を持つ。**実際にモータを回していた間（今までの `turning_`）だけ**、直前の `step` で決めた目標の速さ [mm/s] × 経過 [ms]（＝ µm）を足す。足してから判定する（今までの `run_ms_` と同じ順） |
| 判定 | `lift_decide` の入力 `run_travel_um` が `全行程 [mm] × 10 × LIFT_MAX_RUN_PCT` [µm] を**超えたら** `MAX_RUN`。速さが一定なら「全行程 ÷ 目標の速さ × 1.5」の時間で止まる |
| 0 に戻すとき | 今までの `run_ms_` と同じ（停止指令・向きの変化。持ち主の交代では戻さない） |
| 測った速さ | **使わない**（エンコーダに頼らない守りのため） |
| 溢れ | 足すときは上限の 1 つ上で飽和させる（今までの `kRunStopMs` と同じ考え） |

#### 4.1.2 速さのフィードバック（`SpeedLoop`。spec [Spec-ui.md](../spec/Spec-ui.md) §1・[Spec-safety.md](../spec/Spec-safety.md) #4d）

| 項目 | 決まり |
| --- | --- |
| 測った速さ | エンコーダの数の直近 `LIFT_SPEED_WINDOW_MS` の差 × `LIFT_UM_PER_COUNT` から mm/s。**原点の有無に依らない**（原点を取る前の下降でも効く） |
| 出力 | `duty = 前向き（目標 × LIFT_FF_UP/DOWN_PCT_PER_MM_S） ＋ Kp × 偏差 ＋ Ki × 偏差の積分`。**`0`〜`motor_max_pct`（設定。既定 `LIFT_MOTOR_MAX_PCT_DEFAULT` = 50）に丸める**（spec [Spec-ui.md](../spec/Spec-ui.md) §2.1）。前向きの係数を上昇・下降で別に持つ（重力の分） |
| モータ保護の上限の渡し方 | `LiftController::set_motor_max_pct()` で持たせ、`tick` が `SpeedLoop::update` の引数で渡す。`main.cpp` は起動時（NVS を読んだあと）と `PUT /api/settings` の成功時に呼ぶ。**動いている最中に下げたら、次の周期から新しい上限で丸める** |
| デューティ → PWM | `motor_duty_to_pwm` は**今までどおり 100 % ＝ PWM の最大**（`LIFT_DUTY_ABS_MAX_PCT` で割る）。上限は % の側で掛ける。**上限の値で割らない**（割ると 50 % が PWM の最大になり、24 V がそのままかかる） |
| 周期 | `LIFT_SPEED_CTRL_MS` ごと（`LiftController::tick` の中。`loop` が詰まったら経過時間で割る） |
| 積分の扱い | 出力が丸め（`0` と `motor_max_pct`）に当たっている間は積分を進めない。**止まった・向きが替わった・持ち主が替わったら 0 に戻す**（前の向きの積分を持ち越さない） |
| 測った速さが逆向き | 偏差として扱い（出力を上げる方向に効く）、#8a が止める。`ENC_UP_SIGN` の取り違えをここで捕まえる |
| 出しすぎの守り | 出力の上限 `motor_max_pct`（モータの定格）と #8a（回らない・逆に回る）と #8（連続駆動の上限） |

**呼び出し側を縛る**: `SpeedLoop` の単体試験だけでなく、`LiftController` を偽 HAL（数を進める・進めない）で回し、
「出力が上がって速さが目標に近づく」「数が進まなければ `ENCODER_STALL` で 0 になる」をモータに出した値で確かめる（[-packets.md](DetailedDesign-packets.md) `WP-LIFT-06`）。

#### 4.1.3 点検用の物理ボタン（`ButtonInput`。spec [Spec-ui.md](../spec/Spec-ui.md) §0.3。2026-10-29）

| 項目 | 決まり |
| --- | --- |
| 読み方 | `ButtonInput`（`lib/lift_core/button_input.{h,cpp}`。純ロジック）。`loop` ごとに 2 本のピンの電圧と時刻を渡す。読みの部分（入ってから動き出すまでのチャタリング除け 30 ms・両方 LOW は止める・反転は 150 ms の停止を挟む・**離したらすぐ止める**）は組み立て用の `ManualSwitch`（[-hardware.md](DetailedDesign-hardware.md) §1.2）を中で使う |
| 起動時 | **両方が HIGH（離れている）と一度読めるまでは、何を読んでも止まり**（起動したときに入っていた・固着した） |
| `press` | 止まりから動きへ替わるたびに 1 つ増やす（`hold` の `press` と同じ役割） |
| `LiftController` への渡し方 | 純関数 **`button_step`**（`ButtonInput`・`LiftController`・2 本の電圧・`LiftSettings`・時刻を受け取る）が、動いている間は**毎回** `on_hold(LIFT_BUTTON_CONN_ID, …)`（速さは設定の上昇・下降の `init_mm_s`・天井の値は `MISSING`）を、止まりに替わった回に `on_release` を呼ぶ。**`main.cpp` の `loop()` はピンを読んでこれを呼ぶだけ**（呼び出し側の判断を native で縛るため） |
| 接続 | `ConnKind::BUTTON`・接続番号 **`LIFT_BUTTON_CONN_ID`**（-1。WS の接続番号と重ならない）。**距離計を持たない**（天井を使わない。spec #4c）。閉じることは無い |
| 持ち主の規則 | §3.3 の★の 2 行。`loop` が `LIFT_CMD_TIMEOUT_MS` より長く詰まったら `CMD_TIMEOUT` で止まり、同じ `press` では動き直さない（押し直す。止まる側） |
| `state` | 持ち主が物理ボタンなら `owner` は **`button`**（[-protocol.md](DetailedDesign-protocol.md) §2.2） |
| 画面 | 昇降部の画面・カメラモジュールの画面とも、`owner` が `button` の間は「物理ボタンで操作中（遠隔からは動かせません）」を出す。持ち主の表示は「物理ボタン（点検）」 |

### 4.2 昇降部の画面（`firmware/lift/web/`）

spec [Spec-ui.md](../spec/Spec-ui.md) §0.2。**カメラモジュールの画面から映像・ヨー・ピッチ・ズームを除いたもの**。

| 持つもの | 中身 |
| --- | --- |
| 上昇・下降のボタン | 押している間 `ui_hold_period_ms` ごとに `hold`、離したら `release`（[-protocol.md](DetailedDesign-protocol.md) §2.1） |
| 速度スライダー（上昇・下降） | 範囲と初期値は `GET /api/settings` |
| 表示 | 高さ・下端・「上端の検知: 一時無効」・天井の扱い・停止理由・**いまの持ち主**（この画面／上部モジュール）・接続している画面の数と上部モジュールの有無・仮値で動作中 |
| 設定画面 | 上昇・下降の速度の下限・上限・初期値と、モータへの出力の上限 [%]（「モータの定格電圧 ÷ 電源電圧。12 V のモータを 24 V で回すなら 50」と添える）（オーバーレイ） |

spec §0.1（スクロールさせない）を守る。検査は旧版の `check_noscroll` の方式を使う（[-packets.md](DetailedDesign-packets.md) `WP-LIFTUI-01`）。
**ESP32 無しで画面を作れるように、偽の昇降部 `tools/fake_lift_server.py` が同じ口（画面・`/ws/ui`・`/ws/module`・設定 API）を出す。**

### 4.3 カメラモジュールのアプリ（`camera/hve_camera/`）

旧版の層をそのまま使い、次だけ変える。

| 層 | v2 での中身 | ホストで試験 |
| --- | --- | --- |
| `ceiling.py` | **許可の計算をやめる**（昇降部が決める）。SRF02 の生の値を汎用の 4 状態に直す `classify_srf02()` と、古さを測る | ○ |
| `uno_link.py`（新） | Arduino との行の組み立て・読み取り（`encode_io_cmd`・`parse_io_line`）と `UnoClock`（§3.5） | ○ |
| `hw/uno_hw.py`（新） | `/dev/ttyS1` を開き、`io_cmd_period_ms` ごとに指令を送り、読むたびに全部読み出す。**旧版の `hw/rpi_hw.py` を置き換える** | ×（`uno_link.py` で試験） |
| `axes.py` | 旧版のまま（ピッチは角度を積分して可動範囲で止める・ヨーは角度を持たない）。可動範囲は `MG996R` 向けに見直す（[-names.md](DetailedDesign-names.md) §5） | ○ |
| `lift_link.py` | `/ws/module` へ繋ぐ。繋いだら `hello`。昇降を動かしている間だけ `hold`、離したら `release`（§3.3）。昇降部の名前の解決（§4.6） | ○（偽の昇降部） |
| `control.py` | 旧版のまま＋ `press` の採番・天井の値を `hold` に載せる | ○ |
| `settings.py` ／ `app.py` | **ピッチ・ヨーはここで保存、昇降は昇降部へ中継**（[-protocol.md](DetailedDesign-protocol.md) §4） | ○ |
| `hw/fake_*` | 偽物のモード。**偽の昇降部は v2 の取り決め（§3）を話す** | — |

### 4.4 映像（`camera/hve_video/`）

旧版と同じ流れ（取り込み → 中央の切り出し → 縮小 → JPEG → MJPEG）。**取り込みだけ作り直す**（spec `H-V10`。2026-10-07 決定）。

| 項目 | 値 | 根拠 |
| --- | --- | --- |
| 取り込み | **カメラの MJPEG 1280×720 を `video_capture_fps`（10）で受ける。**`v4l2-ctl -d /dev/video0 --set-fmt-video=width=1280,height=720,pixelformat=MJPG --set-parm=10 --stream-mmap=4 --stream-to=-` を子プロセスで動かし、標準出力を `FFD8`〜`FFD9` で 1 枚ずつに区切る（`MjpegPipeSource`）。**展開できない 1 枚は捨てる** | OpenCV 3.4.9 からは MJPEG を選べない。**`ffmpeg` は使わない**（バッファを大量に取って OOM を起こし、温度監視まで殺した）。30 fps で受けると区切るだけで CPU を食い、作り直しが 7.4 fps に落ちた（[-hardware.md](DetailedDesign-hardware.md) §2.1.1） |
| 作り直し | 展開 → 中央の切り出し → 縦 480 へ縮小（`INTER_LINEAR`）→ JPEG（`video_jpeg_quality` 80。**仮**）。**区切りと作り直しは別スレッド**。作り直しは最新の 1 枚だけを使い、追いつかない分は飛ばす | 1 枚に 約 100 ms（展開 50・縮小 20・JPEG 32）。1 コアで約 10 fps |
| **配り方**（`WP-VIDEO-03`。2026-10-19） | **作り直しは 1 か所で行い、全接続に同じ JPEG を配る。**`video_fps` ごとに 1 回だけ作り直し、接続が 0 本のあいだは作り直さない。各接続には、前の 1 枚を送り終えていれば最新の 1 枚を送り、送っている途中なら飛ばす（古い 1 枚を順に溜めない） | 接続ごとに作り直すと、画面の数だけ CPU を使い、「どの 1 枚まで取ったか」を共有するので 1 枚を取り合う（画面の状態確認の接続が本物の映像の 1 枚を奪っていた。[-hardware.md](DetailedDesign-hardware.md) §2.1.1） |
| **送れない接続を切る**（`WP-VIDEO-03`） | 1 枚の書き込みが `video_send_timeout_ms` を超えたら、その接続を**捨てて**（送り残しを待たずに）閉じる | 受け取らなくなった端末（画面を消した・電波が切れた）の接続を、いつまでも 1 枚分のバッファごと抱えていた。60 本溜まって 4.35 fps・空きメモリ 20 MB まで落ちた（同上） |
| 細かさが保たれる倍率 | 720 ÷ 480 ＝ **1.5 倍まで**。それより上は拡大するほど粗くなる（`zoom_max` 4 は変えない） | spec [Spec-ui.md](../spec/Spec-ui.md) §1.5 |
| 帯域の見込み | 480p の 1 枚は品質 60 で 26〜38 KB（絵柄による）。品質 80 で その 1.5 倍前後 → **10 fps で 3〜5 Mbps、`th-rpi-ap` の電波は 2 倍使う**（UnitV2 → AP → 端末）。実効 約 30 Mbps（th-system の実測）の 2〜3 割 | 品質 80 の 1 枚の大きさは 1 つの絵でしか測っていない。**上限は `WP-MEAS-03` で決める**（spec `H-A8`） |
| **静止画**（spec `H-U9`） | **`GET :8080/snapshot` が、最後に受けたカメラの JPEG（1280×720）をそのまま返す。**作り直さない・切り出さない。まだ 1 枚も無ければ 503。外の端末から受ける（`/zoom` と違う） | 1 枚 約 160 KB。押したときだけなので帯域にほぼ効かない |
| 高画質の映像（spec `H-V11`） | **作らない。**作るときは、`MjpegPipeSource` の 1 枚をそのまま `/stream` に流す切り替えを足せばよい（展開も作り直しも要らない。CPU は約 0.2 コア） | 720p 10 fps で 12.6 Mbps（電波は 2 倍） |

偽物のモード（`FakeSource`）も静止画を返せるように、作った絵をその場で JPEG にする。

#### 4.4.1 画面が映像の途絶えを見つける（`camera/web/app.js`。`WP-UI-03`。2026-10-19）

MJPEG の `<img>` は配信が止まっても `error` が来ず最後の 1 枚が残るので、画面は `video_probe_ms` ごとに `/stream` へ別に接続し、`video_probe_ms` 以内に応答の先頭が来るかを見る（来たらすぐ切る）。

| 場面 | 振る舞い |
| --- | --- |
| 確かめが **`video_probe_fail_count` 回続けて**失敗した | 映像を隠して「映像がありません」を出す |
| それより少ない失敗 | **何も変えない**（映像は見せたまま。次の確かめを待つ） |
| 確かめが成功した | 続けて失敗した回数を 0 に戻す。「映像がありません」を出していた（映像が来ていない）なら取り直す |

**1 回の失敗で出さない理由**: Wi-Fi の端末では、一瞬の途切れで応答の先頭が `video_probe_ms` を超えることがある。1 回で隠すと、映像が流れているのに数分に 1 回「映像がありません」が一瞬出た（2026-10-19。有線では 196 秒で 101 回とも成功・最大 134 ms）。途絶えの検知は最大 `video_probe_ms` × (`video_probe_fail_count` ＋ 1) ほど遅れる（約 4.5 秒）。

### 4.5 Arduino（`firmware/cam_io/`）

| 層 | 中身 | ホストで試験 |
| --- | --- | --- |
| `io_codec` | 行の読み取り（`M`）・組み立て（`C`・`B`）。**読めない行は捨てる**（指令が途絶えれば止まる） | ○ |
| `IoController` | 指令の受付・`IO_CMD_TIMEOUT_MS` のウォッチドッグ・ピッチの角度を `PITCH_MIN_DEG`〜`PITCH_MAX_DEG` に丸める（**UnitV2 側の可動範囲とは別の、2 つ目の守り**）・ヨーの速さを上限で丸める。ハードウェアは `io_hal.h` の抽象を通す | ○（偽 HAL） |
| `StepRate` | 一定周期の割り込みで、半ステップの速さ（1 秒あたり）を刻みに直す（位相の足し算） | ○ |
| `hal_uno` ／ `main.cpp` | Timer2 の割り込みで 28BYJ-48 を刻む・Timer1 のハードウェア PWM で MG996R・`Wire` で SRF02（**時間切れ付き**）・AVR のウォッチドッグ | × |

**SRF02 を Arduino に付ける理由**: UNO は 5 V で、5 V の I2C の SRF02 をレベル変換なしで直結できる（旧版はラズパイの 3.3 V のため I2C-LVL01 が要った）。

### 4.6 昇降部の見つけ方（spec `H-M2`。詳細設計に任された）

| 項目 | 決めたこと | 理由 |
| --- | --- | --- |
| 昇降部の名前 | mDNS の `hve-lift.local`（旧版と同じ） | AP の設定に手を入れない（旧版 `D-1`: 固定 IP は th-system の網と干渉しうる） |
| カメラモジュールからの解決 | **`avahi-resolve-host-name -4 hve-lift.local` を呼ぶ**。UnitV2 の avahi に `wlan0` を見させる設定（`allow-interfaces` に `wlan0` を足す）を配備で入れる（[-open.md](DetailedDesign-open.md) `P-12`） | 実機調査: Python（glibc）からは `.local` を引けない。avahi は入っているが `wlan0` を見ていない（[-hardware.md](DetailedDesign-hardware.md) §2.1.1）。`zeroconf` は入っておらず、足さずに済む |
| 上書き | `params.toml` の `lift_host` に IP を書けばそれを使う | mDNS が使えない網のため |
| 操作端末から昇降部の画面を開く | **カメラモジュールの画面に昇降部の IP とリンクを出す** | Android は mDNS を引けない（旧版 `D-1`） |
| 操作端末からカメラモジュールの画面を開く | 昇降部の画面に、繋いでいる上部モジュールの IP を出す | どちらか一方を開ければ、もう一方へ辿れる |

**最初の 1 台をどう開くか**（IP をどう知るか）は旧版 `D-1` のまま未確定（[-open.md](DetailedDesign-open.md) §2）。

### 4.7 開発機（ホスト）の試験環境

| 環境 | 用途 | 作り方 |
| --- | --- | --- |
| `.venv/`（Python 3.10・旧版から） | 旧版のコードの試験・`tools/` | 旧版のまま |
| **`.venv38/`（Python 3.8・新）** | **カメラモジュールのアプリの試験**（`DD-7`） | `uv`（単体のバイナリ）で CPython 3.8 を入れて作る。**依存は UnitV2 の OS の版に揃える**（`aiohttp` 3.6.2・`pyserial` 3.4・numpy 1.16.4・OpenCV 3.4 系。[-hardware.md](DetailedDesign-hardware.md) §2.1.1）。手順は `WP-BASE-02` で `CLAUDE.md` に書く |
| `pio test -e native` | 昇降部・Arduino の純ロジック | 旧版と同じ |
| node | 両方の画面の純関数・スクロールの検査 | 旧版と同じ |

## 5. 段階と作業パケット

**各段階の終わりに必ず「動くもの」が残る。**一覧・受け入れ条件は [-packets.md](DetailedDesign-packets.md)。

| 段階 | 残るもの | パケット |
| --- | --- | --- |
| 0 土台・調査 | **UnitV2 で何ができるかが分かる**。Python 3.8 の試験環境・Arduino の骨格 | `WP-MEAS-06`・`WP-BASE-02` |
| 1 昇降部 | 昇降部が単体で、自分の画面から動く。上部モジュールの口がある | `WP-LIFT-03`・`WP-LIFTUI-01`・`WP-LIFT-04` |
| 2 Arduino | UNO がヨー・ピッチ・天井を扱い、途絶で止まる | `WP-IO-01`・`WP-IO-02` |
| 3 カメラモジュール | 偽物のモードで v2 の取り決めが通る → UnitV2 の実物・映像 | `WP-CAM-04`・`WP-VIDEO-02`・`WP-UI-02`・`WP-CAM-05` |
| 4 結合・測定 | 実機で盤前の操作が通る。仮値を実測で置き換える | `WP-MEAS-02`〜`05` |

## 6. 目次

| ファイル | 中身 |
| --- | --- |
| [-names.md](DetailedDesign-names.md) | 名前辞書・仮値の一覧 |
| [-protocol.md](DetailedDesign-protocol.md) | 通信（ブラウザ ⇔ 昇降部・カメラモジュール ⇔ 昇降部・UnitV2 ⇔ Arduino）・設定 API |
| [-hardware.md](DetailedDesign-hardware.md) | 機器・ピン（**多くが仮**）・電源・UnitV2 で確かめること |
| [-packets.md](DetailedDesign-packets.md) | 作業パケットと受け入れ条件 |
| [-open.md](DetailedDesign-open.md) | **ユーザー判断待ちの提案**・未確定・申し送り |
