# 通信

[DetailedDesign.md](DetailedDesign.md) §2 の詳細。**th-system とは何も通信しない**（[Spec.md](../spec/Spec.md) §4 `HD-1`）。
ここにあるのは本システムの中の通信だけ。値の名前と仮値は [-names.md](DetailedDesign-names.md)。

---

## 1. 経路と周期

| 経路 | 方式 | 向き | 周期 | 鮮度の判定 |
| --- | --- | --- | --- | --- |
| ブラウザ → カメラ部 | WS `/ws` | `hold` / `release` | 押している間 `ui_hold_period_ms` ごと | カメラ部: 最後の `hold` から `hold_timeout_ms` 超で「押していない」扱い |
| カメラ部 → ブラウザ | WS `/ws` | `state` | `state_period_ms` | ブラウザ: `ui_state_timeout_ms` 届かなければ「接続切れ」表示 |
| カメラ部 → 昇降部 | WS（ESP32 の `:80/ws`） | `cmd` | **常に `lift_cmd_period_ms` ごと**（止まっている間も `stop` を送る＝生存確認） | 昇降部: 最後の `cmd` から `LIFT_CMD_TIMEOUT_MS` 超で停止 |
| 昇降部 → カメラ部 | 同上 | `state` | `LIFT_STATE_PERIOD_MS` | カメラ部: `lift_state_timeout_ms` 届かなければ `LINK_LOST` |
| カメラ部 → ブラウザ | HTTP `:8080`（`hve_video`） | 映像（MJPEG） | `video_fps` | — |
| `hve_camera` → `hve_video` | HTTP `127.0.0.1:8080/zoom`（`POST {"level":2.5}`） | 倍率 | 変わったとき・`hve_video` の再起動を検知したとき | 外からは受けない |

**正常な切断は待たずに止める。**ブラウザの WS が閉じたらカメラ部はその場で `release` 扱い、
カメラ部との WS が閉じたら昇降部はその場で停止（先行試作と同じ）。

## 2. メッセージ

すべて UTF-8 の JSON テキスト 1 フレーム 1 メッセージ。`t` で種類を区別する。
**知らない `t`・読めない JSON・型の違うフィールドは、昇降部では `stop` として扱う**（`cmd_codec`）。

### 2.1 ブラウザ → カメラ部

```json
{"t":"hold","axis":"lift_up","speed":40}
{"t":"release"}
{"t":"zoom","level":2.5}
```

| フィールド | 値 |
| --- | --- |
| `axis` | `lift_up` / `lift_down` / `pitch_up` / `pitch_down` / `yaw_left` / `yaw_right`。**一度に動かすのは 1 軸だけ**（最後に届いた `hold` の軸） |
| `level`（`zoom`） | 倍率。カメラ部が 1〜`zoom_max` に丸め、`zoom_step` の倍数にそろえる。**全画面で共通** |
| `speed` | スライダーの値。単位は軸による（`lift_*` は %・`pitch_*` / `yaw_*` は deg/s）。カメラ部が設定の下限〜上限に丸める |

### 2.2 カメラ部 → 昇降部

```json
{"t":"cmd","seq":1234,"dir":"up","duty":40,"ceil_ok":true}
```

| フィールド | 値 |
| --- | --- |
| `seq` | 送るたびに 1 増やす（ログで欠落を見るため。判定には使わない） |
| `dir` | `up` / `down` / `stop` |
| `duty` | 0〜100 の整数 [%]。MD10C の PWM デューティ比そのもの |
| `ceil_ok` | **送るその瞬間に計算した天井の許可**（[DetailedDesign.md](DetailedDesign.md) §3）。`dir` が `up` 以外でも毎回載せる |

### 2.3 昇降部 → カメラ部

```json
{"t":"state","seq":88,"dir":"up","duty":40,"reason":"NONE","bottom":false,"height_mm":812,"height_ok":true,"top_mm":1500,"cmd_age_ms":35,"fw":"0.1.0"}
```

| フィールド | 値 |
| --- | --- |
| `dir` / `duty` | **実際にモータへ出している**方向・デューティ（指令ではない） |
| `reason` | 停止理由（[-names.md](DetailedDesign-names.md) §3）。動いていれば `NONE` |
| `bottom` | 下端スイッチが押されている |
| `height_mm` / `height_ok` | 高さの超音波の最後の読み値と、それが `HEIGHT_STALE_MS` 以内の有効値か |
| `top_mm` | 上端の閾値。未設定なら `null` |
| `cmd_age_ms` | 最後の `cmd` からの経過 |
| `fw` | ファームの版 |

### 2.4 カメラ部 → ブラウザ

```json
{"t":"state",
 "lift":{"link":"ok","dir":"stop","duty":0,"reason":"CMD_STOP","bottom":true,"height_mm":102,"height_ok":true,"top_mm":null},
 "ceiling":{"mm":1450,"age_ms":80,"ok":true,"reason":"NONE"},
 "pitch_deg":0.0,"yaw_deg":0.0,"zoom":1.0,"active_axis":null,"reason":"NONE",
 "provisional":["ceiling_margin_mm","ceiling_stale_ms"],"fake":false,"clients":1}
```

`reason` は画面に出す停止理由（カメラ部の判断 `HOLD_TIMEOUT` / `LINK_LOST` / 天井の理由 / 昇降部の `reason` のうち最も上流のもの）。
`provisional` は仮値のまま動いているパラメータ名（`DD-3`）。`clients` は繋いでいる画面の数（提案 `P-3`）。

## 3. 設定 API（カメラ部）

| メソッド | パス | 中身 |
| --- | --- | --- |
| `GET` | `/api/settings` | 現在の設定 |
| `PUT` | `/api/settings` | 設定をまるごと置き換える。検証に通らなければ `400` と理由の一覧を返し、**保存しない** |
| `POST` | `/api/fake` | **偽物のモードのときだけ**存在する。天井・高さ・下端の偽の値を変える |

```json
{"lift_up":  {"min":10,"max":60,"init":30},
 "lift_down":{"min":10,"max":60,"init":30},
 "pitch":    {"min":1, "max":30,"init":10},
 "yaw":      {"min":1, "max":30,"init":10}}
```

検証: 4 項目すべてが揃う／`min ≦ init ≦ max`／`lift_*` は 0〜100・`pitch` / `yaw` は 0 超〜`axis_speed_abs_max_dps`。
保存は一時ファイルに書いてから置き換える（書き込み途中の電源断で壊さない）。
ファイルが無い・壊れているときは既定値（[-names.md](DetailedDesign-names.md) §5）で動き、画面に出す。
