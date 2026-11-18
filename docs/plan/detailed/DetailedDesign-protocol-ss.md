# 通信 — カメラモジュールが ss-system に開く口（案）

[ImplementationPlan.md](../ImplementationPlan.md) §6 の行 8a。spec [Spec-module.md](../spec/Spec-module.md) §1（口の名前と形は本システムが書き、ss-system が確かめる。2026-11-05）。
**状態: 2026-11-16 に ss-system が確かめた（ss のユーザーの承認。ss-system `8760b4e`。問いへの答え `d448eda`・`ff790fb` を含む）。実装はまだ（作業パケットはこれから切る）。**
ss の使い方: `yaw_zeroed` が `false`、または `boot_id` が変わったら、動かさずに（途中なら止めて）依頼を返す。人の割り込みを見たら、すぐ止まり、戻さずに手を離す。静止画は `X-Still-Ms` < `X-Frame-Age-Ms` なら撮り直す。後片付けは、始める前の `pitch_deg`・`yaw_deg` へ `aim` で戻す。

- 入力: ss-system の要るもの C1〜C7（2026-11-15 の相談）、spec [Spec.md](../spec/Spec.md) §1.1（`H-A12`・`H-V14`）、[Spec-ui.md](../spec/Spec-ui.md) §1.4（ヨー。2026-11-16）・§1.5.1（静止画。デモは 1 秒 5 枚まで）、[Spec-power.md](../spec/Spec-power.md)。
- **昇降部の口（[-protocol-modules.md](DetailedDesign-protocol-modules.md)）と同じ形にそろえる**（ss が同じ書き方で両方を扱えるように）: `hello` で名乗る・`press` で持ち主を決める・送り続けている間だけ続く・`state` で結果を返す。

---

## 0. 結論

| 項目 | 案 |
| --- | --- |
| 口 | カメラモジュール（`hve_camera`。ポート 80）の **WS `/ws/module`**（画面の `/ws` とは分ける）と **HTTP `GET /api/snapshot`** |
| 名乗り | `hello`（`name: "ss"`・`operator: "auto"`・`busy_label`）。形は昇降部と同じ |
| 向ける | **`aim`**: ピッチの目標角とヨーの目標の向きを渡す。送り続けている間だけ動き、着いたら `ARRIVED` |
| 読む | `state`（100 ms ごと）: 起動の番号・ピッチの目標角・ヨーの向き（原点を合わせる前は `null`）・持ち主・省電力・最後の `aim` の結果 |
| 人の割り込み | 画面でヨー・ピッチを押すと人が持ち主になり、ss の `aim` は `PREEMPTED`。`owner_operator` が `human` になる |
| 静止画 | `GET /api/snapshot` が 1280×720 の JPEG と、撮ってからの経過（ms）を返す。省電力を戻す（`touch()`） |

## 1. 接続 `/ws/module`

### 1.1 `hello`

```json
{"t":"hello","name":"ss","role":"control","operator":"auto","busy_label":"画像認識で操作中","fw":"0.1.0"}
```

昇降部の `hello`（[-protocol-modules.md](DetailedDesign-protocol-modules.md) §1.1）と同じ読み方（`name` の形・`role` の既定・`operator` の既定・`busy_label` の長さ・2 度目以降は無視・同じ名前の繋ぎ直しは古い方を閉じる）。`ceiling_sensor` は無い。
上限は仮 4 本。超えた新しい接続はその場で閉じる。

### 1.2 見つけ方

カメラモジュール（UnitV2）の名前を mDNS で引けるかは実機で確かめる。引けないときは ss の設定で IP を与える（spec `H-M2` と同じく、居場所をコードに直書きしない）。昇降部の `state.modules` に、カメラモジュール（`hve-cam`）の `ip` が載っているので、それを使ってもよい。

## 2. ss → カメラモジュール

### 2.1 `aim`（向ける）

```json
{"t":"aim","press":7,"pitch_deg":-20.0,"yaw_deg":90.0}
```

| 項目 | 振る舞い |
| --- | --- |
| 欄 | `pitch_deg`（ピッチの目標角 [°]。0 が水平、**正が見上げ・負が見下ろし**。画面の「上」ボタンで増える向き）・`yaw_deg`（ヨーの目標の向き [°]。-180 以上 180 未満。0 が人の合わせた機体の前、**正が左（上から見て反時計回り）**。画面の「左」ボタンで増える向き。2026-11-16 ss の問い）。**片方だけでもよい**（無い軸は動かさない） |
| 送り方 | **送り続ける**（100 ms ごとを勧める）。`aim_cmd_timeout_ms`（仮 600 ms）届かなければヨーは止まる。ピッチはその角度で止まる（サーボはその目標角を保つ） |
| 持ち主 | 昇降部の `press` と同じ規則（[DetailedDesign.md](DetailedDesign.md) §3.3）。新しい `press` で持ち主になる。止まったあとは新しい `press` でなければ動かない |
| ピッチ | 目標角を設定の範囲（可動範囲）に収めてサーボへ渡す。範囲の外は `TARGET_RANGE` で受け付けない（丸めない。ss は目標角を実際の角度として使うため。`H-V14`） |
| ヨー | 今の向きから**近い回り方**で、設定の速さ（ヨーの上限）で回す。`aim_yaw_tol_deg`（仮 1°）以内に入ったら止める |
| 着いた | 両方の軸が着いたら `aim.status` を `ARRIVED` にし、**持ち主を離す**（昇降部の `goto` と同じ）。ピッチは目標角を渡した周期で着いたとみなす（サーボの動きは読めない。ss は動き終わるまで待つ時間を自分で持つ）。**2026-11-18 から（ss-system が確かめた。`WP-CAM-16`）**: 遊びの片寄せ（[DetailedDesign.md](DetailedDesign.md) §4.3.1）を入れ、ピッチは**戻りの指令を出してから、動く角度から見積もった待ち時間が過ぎたら**着いたとみなす（正しい向きから来たときも見積もりだけ待つ）。`X-Still-Ms` は指令が変わるたび（行き過ぎ点・戻り）に数え直す。`pitch_deg` は目標角のまま |
| 受け付けない | **ヨーの原点を合わせる前の `yaw_deg`** → `YAW_UNKNOWN`。`pitch_deg` が範囲の外 → `TARGET_RANGE`。どちらも `NOT_ARRIVED` |
| 着く前に止まった | 取って代わられた → `PREEMPTED`。途切れた → `CMD_TIMEOUT`。`release` → `CMD_STOP`。接続が閉じた → `OWNER_GONE`。Arduino から行が来ない → `IO_LOST` |
| 省電力 | `aim` は `touch()` を呼ぶ（操作に数える。[-power.md](DetailedDesign-power.md)）。省電力の間に来た `aim` は、ピッチの力を戻してから動く |

### 2.2 `release`

```json
{"t":"release","press":7}
```

持ち主の `press` と合えば止めて離す（昇降部と同じ）。

### 2.3 ss が持たないもの

- **ヨーの原点合わせ（0° にする）は人だけ**（画面のボタン。spec Spec-ui §1.4）。ss からはできない。
- ズーム（ss は静止画を使うので要らない）・設定の変更。

## 3. カメラモジュール → ss（`state`）

100 ms ごと。`/ws/module` のすべての接続へ。

```json
{"t":"state","seq":1234,"boot_id":"5f1c…","uptime_ms":812345,
 "pitch_deg":-20.0,"yaw_deg":88.5,"yaw_zeroed":true,
 "owner":"module","owner_name":"ss","owner_operator":"auto",
 "power":"ACTIVE","io":"ok",
 "aim":{"by":"ss","press":7,"pitch_deg":-20.0,"yaw_deg":90.0,"status":"MOVING","reason":"NONE"}}
```

| フィールド | 値 |
| --- | --- |
| `boot_id` | `hve_camera` が起動するたびに変わる文字列。**変わったら再起動した（ピッチの目標角は 0° に戻り、ヨーの向きは `null` に戻った）**（C2・`H-V14`） |
| `uptime_ms` | 起動からの経過（表示とログ） |
| `pitch_deg` | **ピッチの目標角**（実際の角度ではない。`H-V14`）。省電力で力を抜いている間も、戻ったときの目標角を出す |
| `yaw_deg` | ヨーの向き（-180 以上 180 未満）。**原点を合わせる前と再起動のあとは `null`**。モータを回した量から数えるので、脱調するとずれる（spec Spec-ui §1.4） |
| `pitch_min_deg`・`pitch_max_deg` | ピッチの可動範囲（`TARGET_RANGE` の境目。今は仮の -45・45。`params.toml`）。動かす前に読める（2026-11-16 ss の問い） |
| `yaw_dps` | ヨーを `aim` で回す速さ（設定のヨーの初期値 [deg/s]。今は既定 10・設定の範囲 1〜30）。全周で 12〜36 s の見込み（実機で未測定） |
| `yaw_zeroed` | 原点を合わせたか。**`aim` を送る前（持ち主でない間）も読める**ので、ss は `false` なら依頼を受けずに理由を付けて返せる（2026-11-16 ss の頼み） |
| `owner` | `ui`（画面）／ `module`（`/ws/module` の接続）／ `null` |
| `owner_name`・`owner_operator` | 昇降部と同じ（[-protocol-modules.md](DetailedDesign-protocol-modules.md) §4）。画面は `human` |
| `power` | `ACTIVE` ／ `SAVE`（省電力。映像を止め、ピッチの力を抜いている）。電源断の前は WS が切れる |
| `io` | `ok` ／ `lost`（Arduino から行が来ない。ヨー・ピッチが動かない） |
| `aim` | 最後の `aim`。次の `aim` まで残す。起動してから無ければ `null` |

**後片付け（C6）**: ss は始める前に `pitch_deg`・`yaw_deg` を読んで覚え、終わったら `aim` でそこへ戻す。`yaw_deg` が `null` なら、ヨーは動かさない（角度で向けられないため）。

## 4. 静止画 `GET /api/snapshot`

| 項目 | 振る舞い |
| --- | --- |
| 返すもの | カメラの JPEG（1280×720）そのまま（`hve_video` の `/snapshot` を `hve_camera` が取り次ぐ。spec `H-U9`）。倍率に関係しない |
| 撮った時刻 | 応答ヘッダ **`X-Frame-Age-Ms`**（返す時点で、撮ってからの経過 ms）と **`X-Boot-Id`** |
| 向き（2026-11-16 ss の頼み） | 応答ヘッダ **`X-Pitch-Deg`**（返す時点のピッチの目標角）・**`X-Yaw-Deg`**（返す時点のヨーの向き。`null` なら付けない）・**`X-Still-Ms`**（ヨー・ピッチの目標が最後に変わってからの経過 ms）。**`X-Still-Ms` が `X-Frame-Age-Ms` より小さければ、その 1 枚は動いている間（か、動く前の向き）に撮ったもの**。ss は使わずに撮り直す。持ち主を離したあと人が動かして離しても、この応答だけで見分けられる |
| 名乗り | クエリ `?by=ss`（ログと間隔の判定に使う） |
| 省電力 | `touch()` を呼ぶ。取り込みを止めている間は起こして、新しい 1 枚が来るまで最大 `snapshot_wake_wait_ms` 待つ（[-power.md](DetailedDesign-power.md)）。来なければ `503` |
| 間隔 | **ss からの要求は 200 ms に 1 枚まで**（spec Spec-ui §1.5.1。デモの上限 1 秒 5 枚）。早すぎる要求は `429`。本番は要るときだけ要求する（`H-A12`） |
| 無いとき | まだ 1 枚も無い → `503` |

## 5. 画面（人）の側

| 項目 | 振る舞い |
| --- | --- |
| ss が動かしている間 | カメラモジュールの画面に `busy_label`（「画像認識で操作中」）を出す（昇降の表示と同じ）。**人の操作は拒否しない**（押せば取って代わる） |
| 人がヨー・ピッチを押した | 画面が持ち主になる。ss の `aim` は `PREEMPTED`。ss はすぐ止まり、戻さずに手を離す（ss の決まり） |
| ヨーの原点 | 画面に「ヨーの正面を 0° にする」ボタン（spec Spec-ui §1.4）。押すと `yaw_deg = 0`・`yaw_zeroed = true`。ss が動かしている間に押したら、ss の `aim` は `PREEMPTED` |
| ヨーの向きの表示 | 画面にも `yaw_deg` を出すか（`H-A11` の残り）は画面の作業パケットで決める |

## 5.1 実装の形（2026-11-16。ss の確認のあと）

| 所 | 形 |
| --- | --- |
| ヨーの向きの数え方 | **UNO が、回した半ステップの数（起動から足し引きした累計。符号つき 32 bit）を新しい行 `Y <uno_ms> <yaw_hs>` で返す**（[-protocol.md](DetailedDesign-protocol.md) §5。`C` 行と同じ周期）。`hve_camera` は、原点を合わせたときの `yaw_hs` を覚え、`yaw_deg ＝ (yaw_hs − 原点) × 360 ÷ yaw_steps_per_rev` を -180 以上 180 未満に収める。**画面の「左」で `yaw_hs` が増える向きに数える**（符号が逆なら `hve_camera` の符号の定数で直す）。**`B` 行（UNO の再起動）を受けたら原点を捨てる**（`yaw_zeroed: false`・`yaw_deg: null`）。`hve_camera` が再起動しても原点は捨てる（`boot_id` が変わる） |
| `aim` のヨー | 今までどおり `M` 行の速さで回す。`hve_camera` が `yaw_deg` と目標の差から、近い回り方の向きと `yaw_dps`（設定のヨーの初期値）の速さを決め、`aim_yaw_tol_deg` 以内で 0 を送る |
| `aim` のピッチ | `M` 行の `pitch_ddeg` に目標角を入れる（今と同じ）。範囲は `pitch_min_deg`〜`pitch_max_deg` |
| 持ち主 | `hve_camera` のヨー・ピッチの持ち主を、画面（`/ws`）と `/ws/module` の接続で 1 つにする（`press` の規則）。画面が押せば取って代わる |
| 静止画の撮った時刻 | `hve_video` の `/snapshot` が応答ヘッダ `X-Frame-Age-Ms`（最後の JPEG を受けてからの経過）を付ける。`hve_camera` の `GET /api/snapshot` が `127.0.0.1:8080/snapshot` を取り次ぎ、`X-Boot-Id`・`X-Pitch-Deg`・`X-Yaw-Deg`・`X-Still-Ms` を足す |
| 名前 | `aim_cmd_timeout_ms`（仮 600）・`aim_yaw_tol_deg`（仮 1）・`module_ws_max_conn`（仮 4）・`snapshot_min_interval_ms`（200。spec Spec-ui §1.5.1）・`YAW_UNKNOWN`（停止理由） |

## 6. ss-system に確かめてほしいこと

1. 口（`/ws/module` と `GET /api/snapshot`）と名乗り（`hello` は昇降部と同じ）。
2. `aim` の形（両軸を 1 つの命令で・片方だけでもよい・送り続ける・着いたら持ち主を離す）。ピッチは目標角を渡した周期で `ARRIVED` にする（動き終わりは ss が待つ）でよいか。
3. ヨーの原点を合わせる前は `yaw_deg` が `null` で、`aim` の `yaw_deg` は `YAW_UNKNOWN` で受け付けないこと。原点合わせは人だけ。
4. 再起動は `boot_id` の変化で知る、でよいか。
5. 静止画の撮った時刻を `X-Frame-Age-Ms` で返す、でよいか。デモの間隔の上限（200 ms に 1 枚。超えたら `429`）。
6. 省電力: `aim`・静止画の要求で戻る。静止画は最大 `snapshot_wake_wait_ms` 待って返す。`aim` はピッチの力を戻してから動く。`state.power` で読める。

## 7. 決めないもの

| もの | どこで |
| --- | --- |
| ヨーの原点を自動で取る方法 | spec `H-A11`（あとで） |
| `aim_cmd_timeout_ms`・`aim_yaw_tol_deg`・`snapshot_wake_wait_ms` の値 | 実機 |
| カメラモジュールの名前を mDNS で引けるか | 実機 |
| 静止画を 1 秒 5 枚で返したときの UnitV2 の負荷 | 実機（`WP-MEAS-07` と一緒に） |
