# 詳細設計書 — 昇降・カメラシステム（v2）

[完全設計書](../spec/Spec.md) を実装できる形に落とす。読み方は [README.md](README.md)。旧版は [archive/v1/detailed/](../archive/v1/detailed/DetailedDesign.md)。

**2026-10-06 初版。**ユーザーから詳細設計を任された。**spec に無い振る舞いは勝手に決めていない**
——決める必要があったものは [-open.md](DetailedDesign-open.md) §1 に**提案**として置いた。

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
  hve_video （Python 3.8・別プロセス）             └ 高さ: HC-SR04（表示だけ。W-1）
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
| 映像 | 旧版の `camera/hve_video` を UnitV2 へ移す。**取り込みは 1080p、配信は 480p**（spec `H-V9`） | 中央を切り出すデジタルズームをそのまま使える。**UnitV2 の負荷で成り立つかは `WP-MEAS-06` で確かめる**（§4.4） |
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
| `release`（持ち主から） | 止める（`CMD_STOP`）。持ち主を空にする |
| `release`（持ち主でないものから） | 無視する（[-open.md](DetailedDesign-open.md) `P-9`） |
| 持ち主の `hold` が `LIFT_CMD_TIMEOUT_MS` 届かない | 止める（`CMD_TIMEOUT`）。持ち主を空にする |
| 持ち主の接続が閉じた | **その場で**止める（`OWNER_GONE`）。持ち主を空にする |

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
- Arduino の時計が戻った（再起動した）ら、対応をやり直す

## 4. 部分ごとの設計

### 4.1 昇降部（`firmware/lift/`）

| 層 | 中身 | ホストで試験 |
| --- | --- | --- |
| `lift_decide()` | 純関数。持ち主の命令・天井の判定結果・下端・経過時間から「方向・デューティ・停止理由」を決める | ○ |
| `ceiling_check()` | 純関数。天井の値・古さ・閾値・その接続が距離計を持つか → `(ok, 理由)`（§3.2） | ○ |
| `LiftArbiter` | 操作の持ち主（§3.3）。接続 ID・`press`・受け取った時刻を持つ | ○ |
| `LiftController` | 受け付け・持ち主・ウォッチドッグ・`lift_decide()` の結果を**モータに効かせる**。ハードウェアは `hal.h` の抽象を通す | ○（偽 HAL） |
| `lift_settings` | 昇降の速度の設定の検証（下限 ≦ 初期値 ≦ 上限・0〜100）と JSON | ○ |
| `cmd_codec` | JSON ⇔ 構造体。読めない `hold` は捨てる（持ち主の `hold` が途絶えれば止まる）。読めない `hello` は「距離計を持つ」 | ○ |
| `hal_esp32` ／ `main.cpp` | 実物の MD10C・スイッチ・超音波・WiFi・WS・HTTP・NVS。**`main.cpp` は組み立てて呼ぶだけ** | × |
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
| 8 | 同じ方向へ `LIFT_MAX_RUN_MS` 超 連続 | 停止 | `MAX_RUN`（spec #4b） |
| 9 | それ以外 | 指令どおり。デューティは `0`〜`LIFT_DUTY_ABS_MAX_PCT` に丸める | `NONE` |

**`W-1`（上端の検知の一時無効）**: 判定 #4・#6 は**消さずに** `LIFT_TOP_DETECT_ENABLED`（`false`）で外す。
その場所に `WAIVER(demo): W-1 上端の検知を一時無効` を置く（[EXCEPTION-LEDGER.md](../EXCEPTION-LEDGER.md)）。
`state` の `top_detect` は `false` を返し、両方の画面が「上端の検知: 一時無効」を常に出す。**高さは測って `state` に載せ続ける。**

**連続駆動の上限（#8）は持ち主が替わっても数え直さない。**同じ方向が続く限り数え続ける（持ち主を交互に替えて上限を逃れられないように）。

### 4.2 昇降部の画面（`firmware/lift/web/`）

spec [Spec-ui.md](../spec/Spec-ui.md) §0.2。**カメラモジュールの画面から映像・ヨー・ピッチ・ズームを除いたもの**。

| 持つもの | 中身 |
| --- | --- |
| 上昇・下降のボタン | 押している間 `ui_hold_period_ms` ごとに `hold`、離したら `release`（[-protocol.md](DetailedDesign-protocol.md) §2.1） |
| 速度スライダー（上昇・下降） | 範囲と初期値は `GET /api/settings` |
| 表示 | 高さ・下端・「上端の検知: 一時無効」・天井の扱い・停止理由・**いまの持ち主**（この画面／上部モジュール）・接続している画面の数と上部モジュールの有無・仮値で動作中 |
| 設定画面 | 上昇・下降の速度の下限・上限・初期値（オーバーレイ） |

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

旧版と同じ（取り込み → 中央の切り出し → 縮小 → JPEG → MJPEG）。取り込み元は UnitV2 の `/dev/video0`（[-hardware.md](DetailedDesign-hardware.md) §2）。

| 項目 | 値 | 状態 |
| --- | --- | --- |
| 取り込み | 1920×1080（spec `H-V9`） | **UnitV2 で取れるか・何 fps 出るかは要確認**（`WP-MEAS-06`） |
| 配信 | 縦 480 | spec `H-V9` |
| 細かさが保たれる倍率 | 1080 ÷ 480 ＝ **約 2.25 倍まで**。それより上は拡大するほど粗くなる（`zoom_max` 4 は変えない） | spec [Spec-ui.md](../spec/Spec-ui.md) §1.5 |

**UnitV2 の CPU（Cortex-A7 2 コア）とメモリ（128 MB）で、1080p の取り込み・切り出し・480p の JPEG が目標の fps で回るかは分からない。**
`WP-MEAS-06` で測り、回らなければ次の順に下げる（下げたらここと [-names.md](DetailedDesign-names.md) §5 を直す）:
1. `video_fps` を下げる
2. 取り込みを 1280×720 に下げる（細かさが保たれるのは 1.5 倍までになる）
3. それでも足りなければユーザーに相談する（UnitV2 の組み込みの配信を使う等。spec に関わる）

### 4.5 Arduino（`firmware/cam_io/`）

| 層 | 中身 | ホストで試験 |
| --- | --- | --- |
| `io_codec` | 行の読み取り（`M`）・組み立て（`C`・`B`）。**読めない行は捨てる**（指令が途絶えれば止まる） | ○ |
| `IoController` | 指令の受付・`IO_CMD_TIMEOUT_MS` のウォッチドッグ・ピッチの角度を `PITCH_MIN_DEG`〜`PITCH_MAX_DEG` に丸める（**UnitV2 側の可動範囲とは別の、2 つ目の守り**）・ヨーの速さを上限で丸める。ハードウェアは `io_hal.h` の抽象を通す | ○（偽 HAL） |
| `StepRate` | 一定周期の割り込みで、半ステップの速さ（1 秒あたり）を刻みに直す（位相の足し算） | ○ |
| `hal_uno` ／ `main.cpp` | Timer2 の割り込みで 28BYJ-48 を刻む・`Servo` で MG996R・`Wire` で SRF02（**時間切れ付き**）・AVR のウォッチドッグ | × |

**SRF02 を Arduino に付ける理由**: UNO は 5 V で、5 V の I2C の SRF02 をレベル変換なしで直結できる（旧版はラズパイの 3.3 V のため I2C-LVL01 が要った）。

### 4.6 昇降部の見つけ方（spec `H-M2`。詳細設計に任された）

| 項目 | 決めたこと | 理由 |
| --- | --- | --- |
| 昇降部の名前 | mDNS の `hve-lift.local`（旧版と同じ） | AP の設定に手を入れない（旧版 `D-1`: 固定 IP は th-system の網と干渉しうる） |
| カメラモジュールからの解決 | **アプリが自分で mDNS を引く**（`zeroconf`。純 Python）。OS の名前解決が `.local` を引けるかに頼らない | UnitV2 の OS が `.local` を引けるか分からない（要確認） |
| 上書き | `params.toml` の `lift_host` に IP を書けばそれを使う | mDNS が使えない網のため |
| 操作端末から昇降部の画面を開く | **カメラモジュールの画面に昇降部の IP とリンクを出す** | Android は mDNS を引けない（旧版 `D-1`） |
| 操作端末からカメラモジュールの画面を開く | 昇降部の画面に、繋いでいる上部モジュールの IP を出す | どちらか一方を開ければ、もう一方へ辿れる |

**最初の 1 台をどう開くか**（IP をどう知るか）は旧版 `D-1` のまま未確定（[-open.md](DetailedDesign-open.md) §2）。

### 4.7 開発機（ホスト）の試験環境

| 環境 | 用途 | 作り方 |
| --- | --- | --- |
| `.venv/`（Python 3.10・旧版から） | 旧版のコードの試験・`tools/` | 旧版のまま |
| **`.venv38/`（Python 3.8・新）** | **カメラモジュールのアプリの試験**（`DD-7`） | `uv`（単体のバイナリ）で CPython 3.8 を入れて作る。手順は `WP-BASE-02` で `CLAUDE.md` に書く |
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
