# 通信（v2）

[DetailedDesign.md](DetailedDesign.md) §2・§3 の詳細。**th-system とは何も通信しない**（spec [Spec.md](../spec/Spec.md) §4 `HD-1`）。
値の名前と仮値は [-names.md](DetailedDesign-names.md)。旧版は [archive/v1/detailed/DetailedDesign-protocol.md](../archive/v1/detailed/DetailedDesign-protocol.md)。

---

## 1. 経路と周期

| 経路 | 方式 | 向き | 周期 | 鮮度の判定 |
| --- | --- | --- | --- | --- |
| 昇降部の画面 → 昇降部 | WS `/ws/ui` | `hold` / `release` | 押している間 `ui_hold_period_ms` ごと | 昇降部: 持ち主の `hold` が `LIFT_CMD_TIMEOUT_MS` 届かなければ停止（[DetailedDesign.md](DetailedDesign.md) §3.3） |
| カメラモジュール → 昇降部 | WS `/ws/module` | `hello`（繋いだら 1 度）・`hold` / `release` | 昇降を動かしている間 `lift_cmd_period_ms` ごと。**止まっている間は送らない** | 同上 |
| 昇降部 → 両方 | `/ws/ui`・`/ws/module` | `state` | `LIFT_STATE_PERIOD_MS` | 画面: `ui_state_timeout_ms`、カメラモジュール: `lift_state_timeout_ms` 届かなければ「接続切れ」／`LINK_LOST` |
| ブラウザ ⇔ カメラモジュール | WS `/ws` | 旧版と同じ（`hold` / `release` / `zoom` ／ `state`） | 旧版と同じ | 旧版と同じ |
| カメラモジュール → ブラウザ | HTTP `:8080`（`hve_video`） | 映像（MJPEG） | `video_fps` | — |
| UnitV2 → Arduino | UART（`/dev/ttyS1`・`IO_BAUD`） | `M` 行 | **常に `io_cmd_period_ms` ごと**（止まっている間も。生存確認を兼ねる） | Arduino: `IO_CMD_TIMEOUT_MS` 届かなければヨーを止める（[DetailedDesign.md](DetailedDesign.md) §3.4） |
| Arduino → UnitV2 | 同上 | `C` 行（天井の測定ごと）・`B` 行（起動時） | 測定ごと（`SRF02_PERIOD_MS`） | UnitV2: `UnoClock` で直した古さ（§5） |

**正常な切断は待たずに止める。**持ち主の WS が閉じたら昇降部はその場で止める（`OWNER_GONE`）。

## 2. 昇降部の WS

すべて UTF-8 の JSON テキスト 1 フレーム 1 メッセージ。`t` で種類を区別する。

### 2.1 画面・上部モジュール → 昇降部

```json
{"t":"hello","ceiling_sensor":true,"name":"hve-cam","fw":"0.2.0"}
{"t":"hold","press":17,"dir":"up","duty":40,"ceiling":{"status":"MEASURED","mm":1450,"age_ms":80}}
{"t":"release","press":17}
```

| フィールド | 値 |
| --- | --- |
| `hello`（`/ws/module` だけ） | 繋いだら 1 度送る。**2 度目以降は無視**。`/ws/ui` で受けたら無視 |
| `ceiling_sensor` | 距離計を持つか。**欠けている・真偽値でない・`hello` が来ていないときは `true` とみなす**（[DetailedDesign.md](DetailedDesign.md) §3.1） |
| `name` / `fw` | 表示とログのため（判定に使わない）。欠けてよい |
| `press` | 押し始めごとに送り手が 1 増やす整数（接続ごとに独立）。**その接続で前に見た値より大きければ「新しい押し始め」**（§3.3） |
| `dir` | `up` / `down` / `stop` |
| `duty` | 0〜100 の整数 [%]。MD10C の PWM デューティ比。**昇降部は設定の下限〜上限に丸めない**（丸めは送り手の画面の役。昇降部は 0〜`LIFT_DUTY_ABS_MAX_PCT` に丸める） |
| `ceiling` | **距離計を持つ接続の `hold` だけ**意味を持つ。`status` は `MEASURED` / `TOO_NEAR` / `NO_ECHO` / `READ_ERROR`。`mm` は `MEASURED` のときだけ。`age_ms` は送るその瞬間の読み値の古さ。**欠けている・読めない・知らない `status` は `CEILING_STALE`** |

**読めない JSON・知らない `t`・型の違うフィールドを含む `hold` は捨てる**（持ち主の `hold` が途絶えれば `LIFT_CMD_TIMEOUT_MS` で止まる）。
**読めない `release` も捨てる。**読めない `hello` は「`ceiling_sensor: true`」として扱う（その接続で `hello` を受けたことにする）。

### 2.2 昇降部 → 画面・上部モジュール

```json
{"t":"state","seq":88,"dir":"up","duty":40,"reason":"NONE",
 "bottom":false,"height_mm":812,"height_ok":true,"top_detect":false,
 "ceiling":{"used":true,"status":"MEASURED","mm":1450,"age_ms":95,"ok":true,"reason":"NONE"},
 "owner":"module","ui_clients":1,"module":{"connected":true,"ceiling_sensor":true,"ip":"192.168.5.23","name":"hve-cam"},
 "provisional":["CEILING_MARGIN_MM","CEILING_STALE_MS","LIFT_MAX_RUN_MS"],"fw":"0.2.0"}
```

| フィールド | 値 |
| --- | --- |
| `dir` / `duty` | **実際にモータへ出している**方向・デューティ |
| `reason` | 停止理由（[-names.md](DetailedDesign-names.md) §3）。動いていれば `NONE` |
| `bottom` / `height_mm` / `height_ok` | 旧版と同じ。**高さは表示用**（`W-1` の間は判定に使わない） |
| `top_detect` | 上端の検知が有効か。**`W-1` の間は常に `false`**。`false` のとき両方の画面が「上端の検知: 一時無効」を出す |
| `ceiling.used` | いまの持ち主の命令に天井の値を使っているか（持ち主が距離計を持つ上部モジュールのとき `true`） |
| `ceiling.status` / `mm` / `age_ms` | 持ち主の最後の `hold` に載っていた天井の値。古さは受け取ってからの経過を足したもの。持ち主がいなければ、上部モジュールの最後の `hold` の値（無ければ `null`） |
| `ceiling.ok` / `reason` | `ceiling_check()` の結果。`reason` は `NONE` / `CEILING_NEAR` / `CEILING_STALE` / `OUT_OF_RANGE`（反射なし。`ok` は `true`） |
| `owner` | `ui` / `module` / `null` |
| `ui_clients` | `/ws/ui` に繋いでいる画面の数 |
| `module` | 上部モジュールの接続。`connected` が `false` なら他は `null` |
| `provisional` | 仮値のまま動いている昇降部の定数名（`DD-3`） |

## 3. 昇降部の HTTP

| メソッド | パス | 中身 |
| --- | --- | --- |
| `GET` | `/` と画面のファイル | 昇降部の画面（埋め込み。[DetailedDesign.md](DetailedDesign.md) §4.2） |
| `GET` | `/api/settings` | `{"settings":{"lift_up":{…},"lift_down":{…}},"using_defaults":false}` |
| `PUT` | `/api/settings` | `{"lift_up":{"min":10,"max":60,"init":30},"lift_down":{…}}` をまるごと置き換える。検証に通らなければ `400` と `errors` を返し、**保存しない** |

検証（`lift_settings`）: 2 項目が揃う／`min ≦ init ≦ max`／0〜100 の整数。**昇降部が自分で検証する**（カメラモジュール側の検証は便宜のため）。
保存は NVS（`Preferences` の名前空間 `LIFT_NVS_NAMESPACE`）。読めない・検証を通らないときは既定値で動き、`using_defaults` を `true` にする。

## 4. カメラモジュールの HTTP・WS（旧版からの変更だけ）

| 変更 | 中身 |
| --- | --- |
| `GET /api/settings` | 自分のピッチ・ヨーの設定と、**昇降部から取った**昇降の設定をまとめて返す（形は旧版と同じ 4 軸）。昇降部に繋がらないときは `503` |
| `PUT /api/settings` | 4 軸をまとめて受ける。**先に 4 軸とも自分で検証 → 昇降部へ `PUT`（昇降の 2 軸）→ 成功したら自分の 2 軸を保存**。昇降部が `400` ならその `errors` を返し、自分も保存しない。昇降部に繋がらなければ `503` で何も保存しない |
| `state.lift` | 昇降部の `state` のうち `dir`・`duty`・`reason`・`bottom`・`height_mm`・`height_ok`・`top_detect`・`owner`・`ui_clients` を載せる。**昇降部の IP**（`lift_ip`）も載せる（画面に昇降部へのリンクを出す。[DetailedDesign.md](DetailedDesign.md) §4.6） |
| `state.ceiling` | 自分で測った値（`status`・`mm`・`age_ms`）と、**昇降部が返した判定**（`ok`・`reason`）。カメラモジュールは許可を計算しない |
| `state.reason` | 旧版の選び方のうち、天井の理由は昇降部の `ceiling.reason` を使う。`IO_LOST`（Arduino から行が来ない）を `LINK_LOST` の次に足す |

## 5. UART（UnitV2 ⇔ Arduino）

ASCII の 1 行 1 メッセージ。区切りは空白 1 つ、行末は `\n`。1 行は `IO_LINE_MAX` 文字以内。**読めない行は捨てる。**

| 行 | 向き | 形 | 意味 |
| --- | --- | --- | --- |
| `M` | UnitV2 → Arduino | `M <seq> <pitch_ddeg> <yaw_hsps>` | `seq`: 0〜65535 で一周する連番（ログ用）。`pitch_ddeg`: ピッチの目標角 [0.1°]（整数）。`yaw_hsps`: ヨーの半ステップの速さ [1 秒あたり]（整数・符号が向き。0 で止める） |
| `C` | Arduino → UnitV2 | `C <uno_ms> <st> <cm>` | 天井の測定 1 回。`uno_ms`: Arduino の `millis()`（32 bit・符号なし）。`st`: `0`＝読めた・`1`＝I2C で読めない。`cm`: SRF02 の生の値 [cm]（`st` が `1` なら `0`） |
| `B` | Arduino → UnitV2 | `B <uno_ms> <fw>` | 起動した。UnitV2 は `UnoClock` をやり直す |

**汎用の 4 状態への直し方（UnitV2 の `classify_srf02()`）**:

| 入力 | 状態 |
| --- | --- |
| `st` が `1`、または古さが `ceiling_read_stale_ms` を超えた、または行が来ていない | `READ_ERROR` |
| `cm` が `SRF02_NO_ECHO_RAW`（0） | `NO_ECHO` |
| `cm × 10` が `srf02_min_range_mm` 未満 | `TOO_NEAR` |
| それ以外 | `MEASURED`（`mm` ＝ `cm × 10`） |

**古さの測り方（`UnoClock`）**: 行を受け取るたびに `d = 受け取った時刻 − uno_ms` を記録し、直近 `uno_clock_window` 行の `d` の最小を `offset` とする。
読み値の古さ ＝ 今 −（`uno_ms` ＋ `offset`）。`uno_ms` が前の行より小さくなった・`B` を受けたら、記録を捨ててやり直す。
**読むたびに受信バッファを全部読み出し**、天井は最も新しい `uno_ms` の行だけを使う。
