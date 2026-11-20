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
| カメラモジュール → ブラウザ | HTTP `:8080/stream`（`hve_video`） | 映像（MJPEG・480p） | `video_fps` | — |
| カメラモジュール → ブラウザ | HTTP `GET :8080/snapshot`（`hve_video`） | **静止画**（カメラの JPEG 1280×720 そのまま。spec `H-U9`） | 静止画のボタンを押したときだけ | 無ければ 503 |
| UnitV2 → Arduino | UART（`/dev/ttyS1`・`IO_BAUD`） | `M` 行 | **常に `io_cmd_period_ms` ごと**（止まっている間も。生存確認を兼ねる） | Arduino: `IO_CMD_TIMEOUT_MS` 届かなければヨーを止める（[DetailedDesign.md](DetailedDesign.md) §3.4） |
| Arduino → UnitV2 | 同上 | `C` 行（天井の測定ごと）・`B` 行（起動時） | 測定ごと（`SRF02_PERIOD_MS`） | UnitV2: `UnoClock` で直した古さ（§5） |

**正常な切断は待たずに止める。**持ち主の WS が閉じたら昇降部はその場で止める（`OWNER_GONE`）。

**カメラモジュールは、`state` が `lift_state_timeout_ms` 届かなければ、その WS を自分で閉じて繋ぎ直す。**昇降部（ESP32）が再起動すると古い TCP は切断の知らせ無しに死に、受け取りを待つだけでは永久に繋ぎ直さない（2026-10-07 実機。昇降部に書き込んだあと、カメラモジュールが `LINK_LOST` のまま戻らなかった）。繋ぎ直したら `hello` から送り直す。

## 2. 昇降部の WS

すべて UTF-8 の JSON テキスト 1 フレーム 1 メッセージ。`t` で種類を区別する。

**上部モジュールが 1 台の前提の、いまの実物の形。**複数の上部モジュール（fk-system・ss-system）・状態を読むだけの接続・目標の高さへの移動・測定機構を足す案は [-protocol-modules.md](DetailedDesign-protocol-modules.md)（2026-11-05。fk-system・ss-system が確かめ済み。作業パケットは `WP-LIFT-13` ほか）。

### 2.1 画面・上部モジュール → 昇降部

```json
{"t":"hello","ceiling_sensor":true,"name":"hve-cam","fw":"0.2.0"}
{"t":"hold","press":17,"dir":"up","speed":40,"ceiling":{"status":"MEASURED","mm":1450,"age_ms":80}}
{"t":"release","press":17}
```

| フィールド | 値 |
| --- | --- |
| `hello`（`/ws/module` だけ） | 繋いだら 1 度送る。**2 度目以降は無視**。`/ws/ui` で受けたら無視 |
| `ceiling_sensor` | 距離計を持つか。**欠けている・真偽値でない・`hello` が来ていないときは `true` とみなす**（[DetailedDesign.md](DetailedDesign.md) §3.1） |
| `name` / `fw` | 表示とログのため（判定に使わない）。欠けてよい |
| `press` | 押し始めごとに送り手が 1 増やす整数（接続ごとに独立）。**その接続で前に見た値より大きければ「新しい押し始め」**（§3.3） |
| `dir` | `up` / `down` / `stop` |
| `speed` | 0 以上の整数 [mm/s]。昇降の目標の速さ（spec [Spec-ui.md](../spec/Spec-ui.md) §1。**2026-10-15 に `duty`（%）から替えた**）。**昇降部は設定の下限〜上限に丸めない**（丸めは送り手の画面の役。昇降部は 0〜`LIFT_SPEED_ABS_MAX_MM_S` に丸める）。`duty` だけの `hold` は読めない `hold` として捨てる |
| `ceiling` | **距離計を持つ接続の `hold` だけ**意味を持つ。`status` は `MEASURED` / `TOO_NEAR` / `NO_ECHO` / `READ_ERROR`。`mm` は `MEASURED` のときだけ。`age_ms` は送るその瞬間の読み値の古さ。**欠けている・読めない・知らない `status`・型の違う中身は、`hold` を捨てずに `MISSING`（→ `CEILING_STALE`）として受け取る**（捨てると前の `hold` の値で最長 `LIFT_CMD_TIMEOUT_MS` 動き続けるので、その場で止める） |

**読めない JSON・知らない `t`・`ceiling` 以外のフィールドが欠けている・型の違う `hold` は捨てる**（持ち主の `hold` が途絶えれば `LIFT_CMD_TIMEOUT_MS` で止まる）。
**`/ws/ui` で `hello` を受けたら、昇降部はその接続を閉じる**（[DetailedDesign.md](DetailedDesign.md) §3.1）。
**読めない `release` も捨てる。**読めない `hello` は「`ceiling_sensor: true`」として扱う（その接続で `hello` を受けたことにする）。

### 2.2 昇降部 → 画面・上部モジュール

```json
{"t":"state","seq":88,"dir":"up","duty":52,"speed":40,"speed_meas":39,"reason":"NONE",
 "bottom":false,"height_mm":812,"height_ok":true,"top_detect":false,
 "ceiling":{"used":true,"status":"MEASURED","mm":1450,"age_ms":95,"ok":true,"reason":"NONE"},
 "owner":"module","ui_clients":1,"module":{"connected":true,"ceiling_sensor":true,"ip":"192.168.5.23","name":"hve-cam"},
 "provisional":["CEILING_MARGIN_MM","CEILING_STALE_MS","LIFT_MAX_RUN_PCT"],"fw":"0.2.0"}
```

| フィールド | 値 |
| --- | --- |
| `dir` / `duty` | **実際にモータへ出している**方向・デューティ（`SpeedLoop` の出力） |
| `speed` / `speed_meas` | 目標の速さ・エンコーダで測った速さ [mm/s]（`speed_meas` は上向きが正） |
| `reason` | 停止理由（[-names.md](DetailedDesign-names.md) §3）。動いていれば `NONE` |
| `bottom` / `height_mm` / `height_ok` | 旧版と同じ形。**高さはエンコーダから**（[DetailedDesign.md](DetailedDesign.md) §4.1.1）。`height_ok` は**原点を取る前・下端スイッチの故障のとき `false`**。`W-1` の間は判定に使わない |
| `top_detect` | 上端の検知が有効か。**`W-1` の間は常に `false`**。`false` のとき両方の画面が「上端の検知: 一時無効」を出す |
| `ceiling.used` | いまの持ち主の命令に天井の値を使っているか（持ち主が距離計を持つ上部モジュールのとき `true`） |
| `ceiling.status` / `mm` / `age_ms` | 持ち主の最後の `hold` に載っていた天井の値。古さは受け取ってからの経過を足したもの。持ち主がいなければ、上部モジュールの最後の `hold` の値（無ければ `null`） |
| `ceiling.ok` / `reason` | `ceiling_check()` の結果。`reason` は `NONE` / `CEILING_NEAR` / `CEILING_STALE` / `OUT_OF_RANGE`（反射なし。`ok` は `true`） |
| `owner` | `ui` / `module` / `button`（点検用の物理ボタン。2026-10-29。[DetailedDesign.md](DetailedDesign.md) §4.1.3） / `null` |
| `ui_clients` | `/ws/ui` に繋いでいる画面の数 |
| `module` | 上部モジュールの接続。`connected` が `false` なら他は `null` |
| `provisional` | 仮値のまま動いている昇降部の定数名（`DD-3`） |

## 3. 昇降部の HTTP

| メソッド | パス | 中身 |
| --- | --- | --- |
| `GET` | `/` と画面のファイル | 昇降部の画面（埋め込み。[DetailedDesign.md](DetailedDesign.md) §4.2） |
| `GET` | `/api/settings` | `{"settings":{"lift_up":{…},"lift_down":{…},"motor_max_pct":50,"ceiling_standalone":true,"ceiling_margin_mm":500},"using_defaults":false}` |
| `PUT` | `/api/settings` | `{"lift_up":{"min":10,"max":60,"init":30},"lift_down":{…},"motor_max_pct":50}`（速度は **mm/s**、`motor_max_pct` は %）をまるごと置き換える（**3 つとも必須**）。検証に通らなければ `400` と `errors` を返し、**保存しない** |

検証（`lift_settings`）: 3 項目が揃う／`min ≦ init ≦ max`／0〜`LIFT_SPEED_ABS_MAX_MM_S` の整数（mm/s）／`motor_max_pct` は `LIFT_MOTOR_MAX_PCT_MIN`（10）〜`LIFT_DUTY_ABS_MAX_PCT`（100）の整数（2026-10-16。spec [Spec-ui.md](../spec/Spec-ui.md) §2.1）。**昇降部が自分で検証する**（カメラモジュール側の検証は便宜のため）。
保存は NVS（`Preferences` の名前空間 `LIFT_NVS_NAMESPACE`）。読めない・検証を通らないときは既定値で動き、`using_defaults` を `true` にする。
**`motor_max_pct` が無い古い形で保存されていたら、検証を通らないので全体が既定値（`motor_max_pct` は 50）になる**（実機にはまだ mm/s の設定を保存していないので、移し替えは作らない）。

**`ceiling_standalone`**（真偽値。単体の操作でも天井で止めるか。既定 `true`。2026-11-06。spec [Spec-ui.md](../spec/Spec-ui.md) §2.2・[-protocol-modules.md](DetailedDesign-protocol-modules.md) §2.5）は、`motor_max_pct` と違って**欠けていてもよい**:
- **NVS に保存した形に無い**（このファームより前に保存した）→ **`true` として読み、ほかの項目はそのまま使う**（`using_defaults` にしない）。新しい欄を足しただけで、保存してある速さ・出力の上限を既定値に戻さないため。
- **`PUT` の本文に無い**（`WP-CAM-11` より前のカメラモジュールからの中継）→ **いま保存している値を保つ**（`true` に戻さない）。真偽値でない値は `400`。

**`ceiling_margin_mm`**（整数 mm。天井の閾値。既定 `CEILING_MARGIN_MM_DEFAULT` = 500・`CEILING_MARGIN_MM_MIN` 150〜`CEILING_MARGIN_MM_MAX` 3000。2026-11-06。spec [Spec-ui.md](../spec/Spec-ui.md) §2.3）も `ceiling_standalone` と同じく**欠けていてもよい**:
- **NVS に無い** → **500 として読み、ほかの項目はそのまま使う**（`using_defaults` にしない）。
- **`PUT` の本文に無い**（`WP-CAM-12` より前のカメラモジュール・`WP-LIFTUI-05` より前の画面）→ **いまの値を保つ**。整数でない・範囲の外は `400`（`true`・`500.5`・`"500"` も誤り）で、**ほかの項目も保存しない**。
- 判定（`ceiling_check`）は固定の `CEILING_MARGIN_MM` ではなく、**設定の値**を使う（`MEASURED` で `mm ≦ ceiling_margin_mm` → `CEILING_NEAR`）。上部モジュールからの命令・単体の操作の両方。**`PUT` が通った次の周期から**新しい値で判定する（動いている最中に大きくしたら、その場で止まりうる）。
- `state.provisional` から `CEILING_MARGIN_MM` を外す（設定で変えられる値になったため。既定の値を実測で決めることは spec `H-V8` に残る）。

## 4. カメラモジュールの HTTP・WS（旧版からの変更だけ）

| 変更 | 中身 |
| --- | --- |
| `GET /api/settings` | 自分のピッチ・ヨーの設定と、**昇降部から取った**昇降の設定をまとめて返す（4 軸 ＋ 昇降部の `motor_max_pct`）。昇降部に繋がらないときは `503` |
| `PUT /api/settings` | 4 軸と `motor_max_pct`・`ceiling_standalone`・`ceiling_margin_mm`（2026-11-06。`GET` でも昇降部の値を載せる。後ろの 2 つは昇降部が対応しているときだけ中継する）をまとめて受ける。**先に全部自分で検証 → 昇降部へ `PUT`（昇降の 2 軸と `motor_max_pct`）→ 成功したら自分の 2 軸を保存**（`motor_max_pct` は自分では保存しない。持ち主は昇降部）。昇降部が `400` ならその `errors` を返し、自分も保存しない。昇降部に繋がらなければ `503` で何も保存しない |
| `state.lift` | 昇降部の `state` のうち `dir`・`duty`・`speed`・`speed_meas`・`reason`・`bottom`・`height_mm`・`height_ok`・`top_detect`・`owner`・`ui_clients` を載せる。**昇降部の IP**（`lift_ip`）も載せる（画面に昇降部へのリンクを出す。[DetailedDesign.md](DetailedDesign.md) §4.6） |
| `state.ceiling` | 自分で測った値（`status`・`mm`・`age_ms`）と、**昇降部が返した判定**（`ok`・`reason`）。カメラモジュールは許可を計算しない。**判定は、カメラモジュールが持ち主で昇降部が判定を返しているときだけ載せ、それ以外（止まっている・別の画面が持ち主・昇降部が `ceiling` を返さない）は `ok`・`reason` を `null` にする**（昇降部と切れているときは `ok: false`・`reason: LINK_LOST`）。2026-10-07 実機: 判定が無いのに `CEILING_STALE` を入れていたので、止まっている間ずっと画面が「天井 値なし」になり、上昇ボタンも薄くなっていた |
| 画面の天井のバッジ | **判定（`reason`）があればそれで出す。無ければ自分で測った `status` で出す**（`MEASURED` → 距離・`TOO_NEAR` → 近すぎ・`NO_ECHO` → 範囲外・`READ_ERROR`／値が無い → 値なし）。**上昇ボタンを薄くするのは、昇降部と切れているときと、判定が `ok: false` のときだけ**（判定が無いだけでは薄くしない。上昇してよいかは昇降部が決める） |
| `state.reason` | 旧版の選び方のうち、天井の理由は昇降部の `ceiling.reason` を使う。`IO_LOST`（Arduino から行が来ない）を `LINK_LOST` の次に足す |

### 4.1 目標の高さへ・下端へのボタン（2026-11-21。spec [Spec-ui.md](../spec/Spec-ui.md) §1.7）

**どちらの画面も、ボタンを 1 回押したら「移動中」の状態を持ち、`ui_hold_period_ms`（100 ms）ごとに命令を送り続ける。**止めるのは画面の役（昇降部・カメラモジュールは今までどおり途切れたら止まる）。

**昇降部の画面（`/ws/ui` に直接）**:

| ボタン | 送るもの | やめる条件（どれか） |
| --- | --- | --- |
| 目標の高さへ | `{"t":"goto","press":P,"target_mm":T,"speed":S}`（protocol-modules §2.2 と同じ形。`/ws/ui` の `goto` は今のファームが受け付ける。天井は載せない）。`press` は上昇・下降と同じ通し番号で、押し始めに 1 増やす。`S` は `T` がいまの高さより上なら上昇、下なら下降のスライダーの値 | `state.goto` の `press` が `P`・`target_mm` が `T` で `status` が `MOVING` 以外（着いた・着かなかった。理由を出す） |
| 下端へ | 下降の `hold`（押している間と同じ形・同じ `press` の規則） | `state.bottom` が真（着いた）・**押し始めから `ui_goto_grace_ms`（仮 1000 ms）を過ぎて、`state.dir` が `stop` を 2 回続けて見た、または `state.owner` が `ui` でない**（安全の判定で止まった・物理ボタンや上部モジュールに取って代わられた。`state.reason` を出す） |
| 共通 | — | 「停止」・上昇・下降・もう一方のボタンを押した（`release` を送るか、そちらの命令に替わる）・WS が切れた |

**WS が切れたら、画面は移動中をやめる（繋ぎ直しても送り直さない）。**昇降部は `press` を接続ごとに数えるので、新しい接続の最初の命令はどの `press` でも「新しい押し始め」になり、送り直すと人が何もしないまま動き出す（spec §1.7「止まったあと自分からは動き出さない」）。

**カメラモジュールの画面（カメラモジュール経由）**: 画面 → カメラモジュールに新しい種類 `lift_goto` を足す。カメラモジュールが昇降部へ `goto`（天井を載せる。`hold` と同じ `_ceiling_triple`）か下降の `hold` に直す。

```json
{"t":"lift_goto","id":"k3f9a2","mode":"target","target_mm":1200,"speed":80}
{"t":"lift_goto","id":"p81c0d","mode":"bottom","speed":100}
```

| 項目 | 振る舞い |
| --- | --- |
| `id` | 画面が押し始めごとに作る文字列（端末どうしで重ならないよう乱数。1〜32 文字）。**新しい `id` ＝新しい押し始め**（昇降部への `press` を 1 増やす） |
| 受け付け | `hold` と同じ持ち主の規則（最後の操作が勝つ。別の画面の `hold`・`lift_goto` に替わる）。`touch()` に数える。**途切れの扱いも `hold` と同じ**（`hold_timeout_ms` 来なければ止める） |
| `mode: target` | 昇降部へ `goto`（`press`・`target_mm`・`speed`・`ceiling`） |
| `mode: bottom` | 昇降部へ下降の `hold`（`speed`）。昇降部の `state.bottom` が真になったら送るのをやめて `release` |
| 終わった `id` | 着いた・着かなかったあとに同じ `id` が来ても**送らない**（昇降部の「同じ `press` の `goto` は無視」と同じ考え） |
| `release` | 今までどおり。動いている `lift_goto` も止める |
| **繋ぎ直し・途切れ** | **昇降部との接続が切れたら、動いている `lift_goto` を `NOT_ARRIVED`・`LINK_LOST` にして終わった `id` に入れる**（繋ぎ直しても同じ `id` では送らない。新しい接続の最初の命令は昇降部で新しい押し始めになるため）。**画面の `lift_goto` が `hold_timeout_ms` 途切れたときも `NOT_ARRIVED`（`CMD_STOP` 相当）にして終わった `id` に入れる**（途切れたあと同じ `id` が届いても再開しない） |
| 取って代わられた | 昇降部の `owner` が `module` で `owner_name` が `module_name` でなくなった、または `owner` が `module` 以外になった（押し始めから `lift_goto_grace_ms` 以降）→ `NOT_ARRIVED`・`PREEMPTED`（`mode: bottom` でも） |
| **`state.lift_goto`** | `{"id","mode","target_mm","status","reason"}` か `null`。`status` は `MOVING` / `ARRIVED` / `NOT_ARRIVED`。`target` は昇降部の `state.goto`（`by` がカメラモジュールの `module_name`・`press` が自分の送ったもの）から写す。`bottom` は `state.bottom` で `ARRIVED`、`state.dir` が `stop` に替わったら（押し始めから `lift_goto_grace_ms`〈`params.toml`。仮 1000〉以降・2 周期続けて）`NOT_ARRIVED` と `state.reason`。**別の画面・別の操作元に替わったら、前の `id` は `NOT_ARRIVED`・`PREEMPTED`**。次の `lift_goto` まで残す |
| 画面がやめる条件 | `state.lift_goto.id` が自分の `id` で `status` が `MOVING` 以外、**または `id` が自分のものでなくなった**（別の端末が取った） |

## 5. UART（UnitV2 ⇔ Arduino）

ASCII の 1 行 1 メッセージ。区切りは空白 1 つ、行末は `\n`。1 行は `IO_LINE_MAX` 文字以内。**読めない行は捨てる。**

| 行 | 向き | 形 | 意味 |
| --- | --- | --- | --- |
| `M` | UnitV2 → Arduino | `M <seq> <pitch_ddeg> <yaw_hsps>` | `seq`: 0〜65535 で一周する連番（ログ用）。`pitch_ddeg`: ピッチの目標角 [0.1°]（整数）。`yaw_hsps`: ヨーの半ステップの速さ [1 秒あたり]（整数・符号が向き。0 で止める） |
| `C` | Arduino → UnitV2 | `C <uno_ms> <st> <cm>` | 天井の測定 1 回。`uno_ms`: Arduino の `millis()`（32 bit・符号なし）。`st`: `0`＝読めた・`1`＝I2C で読めない。`cm`: SRF02 の生の値 [cm]（`st` が `1` なら `0`） |
| `B` | Arduino → UnitV2 | `B <uno_ms> <fw>` | 起動した。UnitV2 は `UnoClock` をやり直す |
| **`Y`** | Arduino → UnitV2 | `Y <uno_ms> <yaw_hs>` | **ヨーの半ステップの累計（2026-11-16。ss の口）。**起動から回した半ステップを向きつきで足し引きした数（符号つき 32 bit）。`C` 行と同じ周期で送る。UnitV2 は向きの角度に直す（[-protocol-ss.md](DetailedDesign-protocol-ss.md) §5.1）。古い UnitV2 は知らない行として捨てる |
| **`R`** | UnitV2 → Arduino | `R <seq>` | **外し忘れの省電力（2026-11-10）。ピッチの力を抜き、ヨーを止める。次の `M` まで抜いたまま**（途絶えても戻らない）。省電力の間は `M` の代わりに周期で送る。[-power.md](DetailedDesign-power.md) §2 |

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
**前回読んでから `ceiling_read_stale_ms` より長く空いたら、その回の行はすべて捨てる**（次の回に読めた行が来るまで `READ_ERROR`。[DetailedDesign.md](DetailedDesign.md) §3.5）。
