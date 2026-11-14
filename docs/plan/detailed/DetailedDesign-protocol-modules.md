# 通信 — 上部モジュールの複数対応（案）

[DetailedDesign-protocol.md](DetailedDesign-protocol.md) §2（昇降部の WS）を、spec の 2026-11-01 以降の決まりに合わせて広げる案。
**状態: 2026-11-05 に fk-system・ss-system が確かめ済み（§6）。作業パケットは `WP-LIFT-13`・`WP-LIFT-14`・`WP-LIFTUI-03`・`WP-CAM-10`（[-packets.md](DetailedDesign-packets.md)）。新しい名前は [-names.md](DetailedDesign-names.md) へ移した。実装はまだ。**いまの実物は [-protocol.md](DetailedDesign-protocol.md) §2.1・§2.2 のまま（上部モジュールは 1 台の前提）。

- 口の名前と形は本システムが書き、fk-system・ss-system が確かめる（spec [Spec-module.md](../spec/Spec-module.md) §1。2026-11-05 ユーザー決定）。
- 入力は spec [Spec-module.md](../spec/Spec-module.md) §1〜§5（複数の上部モジュール・状態を読むだけの接続・目標の高さへの移動・どの上部モジュールが操作しているか・測定機構）と [Spec-safety.md](../spec/Spec-safety.md) §2。
- 進め方は [ImplementationPlan.md](../ImplementationPlan.md) §6 の行 8。**実装は作業パケットのブリーフに沿って行う**（この文書の名前は [-names.md](DetailedDesign-names.md) にもある）。

---

## 0. 結論

| 項目 | 案 |
| --- | --- |
| 口 | **今の `/ws/module` のまま。**カメラモジュール・fk-system・ss-system が同時に繋ぐ（fk・ss 用の口は作らない） |
| 名乗り | 繋いだら `hello` で**名前・役割・人か自動か**を名乗る。同じ名前で繋ぎ直したら古い接続を閉じる |
| 動かす命令 | 今の `hold` / `release` と、新しい **`goto`**（目標の高さへの移動）。どちらも**送り続けている間だけ続く** |
| 天井 | 距離計を持つ上部モジュール（カメラモジュール）は、**止まっている間も `ceiling` で値を送り続ける**。距離計を持たない fk・ss の上昇は、その値で判定する |
| 配信 | `state` に、操作している元の名前・人か自動か・繋がっている上部モジュールの一覧・原点・天井の出どころ・目標の高さへの移動の結果・測定機構を足す |
| 下端まで下げる | 専用の命令は作らない。`hold` の下降を下端に着くまで送り続ける（spec §2。2026-11-05） |

## 1. 接続

### 1.1 `hello`

```json
{"t":"hello","name":"fk","role":"control","operator":"auto","ceiling_sensor":false,"busy_label":"測定操作中","fw":"0.1.0"}
```

| フィールド | 値 |
| --- | --- |
| `name` | 上部モジュールの名前。**接続どうしを見分けるのに使う**（今は表示とログだけ）。英小文字・数字・`-` の 1〜16 文字。取り決める名前は **`hve-cam`**（カメラモジュール。今と同じ）・**`fk`**・**`ss`**。欠けている・形に合わないときは名前無し（`anon-<接続番号>`）として扱い、§1.2 の置き換えの対象にしない |
| `role` | **`control`**（昇降を命じる）／ **`observe`**（状態を読むだけ。spec §1）。**欠けているときは `control`**（今のカメラモジュールが送っていないため）。**知らない値は `observe`**（命じられない側に倒す） |
| `operator` | **`human`**（人が操作する画面の中継。カメラモジュール）／ **`auto`**（自分で判断して動かす。fk・ss）。欠けている・知らない値は `human`（自動の操作だと名乗っていないものを、人の操作として扱う） |
| `ceiling_sensor` | 今と同じ（欠けている・読めないときは `true`。[DetailedDesign.md](DetailedDesign.md) §3.1）。fk・ss は `false` を送る |
| `busy_label` | その上部モジュールが昇降を動かしている間に、両方の画面に出す文字（例: fk は「測定操作中」、ss は「画像認識で操作中」。spec [Spec-ui.md](../spec/Spec-ui.md) §1.6）。**UTF-8 で 1〜48 バイト。超えた・空・文字列でないときは `busy_label` 無しとして扱い、切り詰めない**（マルチバイトの途中で切ると、`state` に壊れた UTF-8 が載り、ブラウザが WS を切る）。欠けてよい（そのときは名前を出す）。**昇降部・画面が上部モジュールの名前を決め打ちで持たないため** |
| `fw` | 今と同じ（表示とログ） |

- **2 度目以降の `hello` は無視する**（今と同じ。途中で役割や `ceiling_sensor` を変えて守りを外させない）。
- **`/ws/ui` で `hello` を受けたら、その接続を閉じる**（今と同じ）。

### 1.2 同じ名前の繋ぎ直し

`hello` の `name` が、繋がっている**別の接続**と同じなら、**古い接続を閉じる**（spec §1。上部モジュールの再起動で、古い接続が残るのを防ぐ）。
古い接続が持ち主なら、その場で止まる（`OWNER_GONE`）。新しい接続の `press` は、新しい接続の分として数え直す（`press` は接続ごと。今と同じ）。

### 1.3 数の上限

- **いまのライブラリ（ESPAsyncWebServer 3.12.1）は、口ごとに既定 8 本**（`DEFAULT_MAX_WS_CLIENTS`）。**`cleanupClients()` は超えたら、いちばん古い接続を閉じる。**いまのファームは `loop` のたびに呼んでいる。
  上部モジュールの口でこれが起きると、**先に繋いでいたカメラモジュールが閉じられ、天井の値が途絶える**（持ち主なら止まる）。
- 案: `/ws/module` は `LIFT_MODULE_MAX_CONN`（仮 6）本まで。**超えた新しい接続は、その場で閉じる**（古い方を閉じない）。`/ws/ui` は今のまま。
- HTTP と合わせた TCP の本数の上限は、実機で確かめる（`WP-MEAS-03` と一緒に）。

### 1.4 見つけ方

今と同じ `hve-lift.local`（[DetailedDesign.md](DetailedDesign.md) §4.6）。fk-system は th-system の PC の Docker（host network）で動くので、mDNS が引けなければ設定で IP を与える（昇降部の居場所・AP 名をコードに直書きしない。spec `H-M2`）。

## 2. 上部モジュール → 昇降部

### 2.1 `hold` / `release`（形は今のまま）

[-protocol.md](DetailedDesign-protocol.md) §2.1 と同じ。足すのは次のとおり。

- **`role: observe` の接続からの `hold`・`goto`・`release` は捨てる**（spec §1。その接続からは昇降を命じられない）。
- **止める**: 自分が持ち主でなくても止めたいときは、**新しい `press` で `{"t":"hold","dir":"stop",…}` を送って持ち主になり、`release` で離す**（最後の操作が勝つ。spec §4）。物理ボタンが押されている間は無視される（spec [Spec-ui.md](../spec/Spec-ui.md) §0.3）。
- **下端まで下げる**（spec §2。2026-11-05）: `dir: "down"` の `hold` を、下端に着くまで送り続ける。
  - 着いたかは `state` の `bottom: true` と、止めた理由 `BOTTOM` で判断する（原点を取る前は高さが読めないので、高さでは判断しない）。
  - 原点を取る前も使える（下降は止めない。spec [Spec-safety.md](../spec/Spec-safety.md) §2 #4）。
  - 途中で `MAX_RUN`（連続駆動の上限）で止まったら、**新しい `press` で押し直す**。
  - 測定機構の `ARM_LINK`（spec #4e）は**下降も止める**（`ARM_CONFIRM`・`DRUM_LAG` は 2026-11-14 に無くした）。

### 2.2 `goto`（目標の高さへの移動。spec §2.1）

```json
{"t":"goto","press":21,"target_mm":1200,"speed":40,"ceiling":{"status":"MEASURED","mm":1450,"age_ms":80}}
```

| 項目 | 振る舞い |
| --- | --- |
| 送り方 | **`hold` と同じく送り続ける**（`LIFT_CMD_TIMEOUT_MS` より短い間隔）。持ち主の規則も `hold` と同じ（`press`。[DetailedDesign.md](DetailedDesign.md) §3.3）。`ceiling` は距離計を持つ接続だけが載せる |
| 向き | 目標といまの高さから昇降部が決める |
| 着いた | 目標から `LIFT_GOTO_TOL_MM`（仮。spec `H-M6`）以内に入ったら止め、`state.goto.status` を `ARRIVED` にする |
| 着いたあと | **同じ `press` の `goto` は無視する**（動き出さない。spec §4 の「止まったあとは、新しく押し始めた操作でなければ動かない」と同じ）。次の移動は新しい `press` で送る |
| 受け付けない | 高さが読めない（原点を取る前を含む）→ `HEIGHT_UNKNOWN`。目標が 0 未満、または上端の閾値より上（設定されていれば）→ `TARGET_RANGE`。どちらも `NOT_ARRIVED` |
| 着く前に止まった | 安全の判定・命令の途切れ・`release`・接続が閉じた → `NOT_ARRIVED` と、その止めた理由。**取って代わられた → `PREEMPTED`** |

### 2.3 `ceiling`（新しく足す）

```json
{"t":"ceiling","status":"MEASURED","mm":1450,"age_ms":80}
```

- 距離計を持つ接続（`ceiling_sensor: true`）が、**昇降を命じていない間も** `lift_cmd_period_ms`（カメラモジュールは 100 ms）ごとに送る（spec §2「自分が昇降を命じていない間も送る」）。中身は `hold` に載せる `ceiling` と同じ。
- **いまのカメラモジュールは、止まっている間は何も送らない**ので、カメラモジュールも直す（§5）。
- 昇降部は、**天井の値の出どころになる接続**ごとに、最後の天井の値（`hold`・`goto`・`ceiling` のどれから来たものでも）と受け取った時刻を持つ。
- **出どころになるのは、`hello` で `ceiling_sensor: true` を明示し、かつ `role: control` の接続だけ。**`hello` を送っていない・`ceiling_sensor` が欠けた・`observe` の接続の値は、ほかの接続の判定に使わない。`ceiling_sensor: false` の接続が送ってきた `ceiling` は捨てる。
  （§1.1 の「欠けていれば持つとみなす」は、**自分の命令を自分の値で縛る**ための厳しい側の倒し方。ほかの接続の上昇を許す値として貸すときは、逆に**信じない側**に倒す。でないと、名乗っていない接続が `NO_ECHO` を送るだけで、カメラモジュールが繋がっていなくても fk・ss が上がれてしまい、spec #4 より緩くなる）

### 2.4 天井の判定（[DetailedDesign.md](DetailedDesign.md) §3.1 の表を置き換える）

| 持ち主 | 使う天井の値 |
| --- | --- |
| 距離計を持つ上部モジュール | 自分の `hold`・`goto` に載せた値（今のまま） |
| **距離計を持たない上部モジュール（fk・ss）** | **距離計を持つ上部モジュール（§2.3 の出どころになる接続だけ）の最新の値**と、受け取ってからの経過を足した古さ。**無い・古い・距離計を持つ上部モジュールが繋がっていないときは止める（`CEILING_STALE`）**（spec #4。2026-11-01 に `H-X11` を改めた決まり。**今の §3.1 の「`ceiling_sensor: false` なら使わない」は、この行で置き換える**） |
| 昇降部の画面・物理ボタン | **設定 `ceiling_standalone`（既定 `true`）が `true` で、出どころ（§2.3）の新しい値があるときだけ、その値を使う**（§2.5。2026-11-06 spec #4c）。それ以外は使わない |

距離計を持つ上部モジュールが 2 台以上のときは未定（spec `H-M5`）。いまはカメラモジュールだけなので、2 台目が名乗ったら**近い方（厳しい側）**を使う案にしておく。

### 2.5 単体の操作の天井（2026-11-06。spec [Spec-safety.md](../spec/Spec-safety.md) §2 #4c・[Spec-ui.md](../spec/Spec-ui.md) §2.2）

持ち主が昇降部の画面（`ui`）・物理ボタン（`button`）のとき、`tick` のたびに次の順で決める。**周期ごとに決め直す**（上昇中に出どころが古くなれば、次の周期から使わない）。

| # | 条件 | 判定 | `state.ceiling.standalone` |
| --- | --- | --- | --- |
| 1 | 設定 `ceiling_standalone` が `false` | 天井を使わない（`ok`） | `SETTING_OFF` |
| 2 | 出どころ（§2.3）が無い、または**どの出どころの値も**受け取ってからの経過 ＋ `age_ms` が `CEILING_STALE_MS` を超えた | 天井を使わない（`ok`）。**`CEILING_STALE` にしない**（上部モジュールからの命令と逆。spec #4c） | `NO_SOURCE` |
| 3 | それ以外（新しい値がある） | その値を `ceiling_check` に渡す（2 台以上なら新しい値の中で近い方。§2.4）。`MEASURED` の閾値以下・`TOO_NEAR` → `CEILING_NEAR`、`NO_ECHO` → `OUT_OF_RANGE`（`ok`）、**`READ_ERROR`・`MISSING` → `CEILING_STALE`（止める。通信はできていて距離計が読めないため）** | `USE` |

- **天井の値が無い周期（持ち主がいない・出どころが無いなど）の `state.ceiling` は `{"standalone":"…"}` だけ**（`used`・`status` などは無い。`null` にしない。`WP-LIFT-15` で決めた）。受け手は `status`・`reason` が無いものを「値なし」ではなく「—」として扱う（昇降部の画面・偽の昇降部はそうしている。偽の昇降部は同じ場面で `used: false`・`status: null` も載せるが、受け手の扱いは同じ）。
- `state.ceiling.standalone` は、**持ち主が誰でも（いなくても）**、いま単体の操作で上げたらどうなるかを出す（画面が押す前から「天井: 使わない（設定が切）」などと出せるように）。`ceiling.used` は今のとおり「いまの持ち主の命令に天井の値を使っているか」で、持ち主が `ui`・`button` で #3 のとき `true`。
- 上部モジュールからの命令（§2.4）には設定は効かない。
- 2 の「古い」は §2.4 と同じ `CEILING_STALE_MS`。2 と 3 の境目で、古くなった値を `CEILING_STALE` として使わないこと（それをすると #4 になり、通信が途切れたときに単体で上げられなくなる）。

## 3. 昇降部 → 上部モジュール（`state` に足すもの）

```json
{"t":"state", "…今のまま…": "…",
 "owner":"module","owner_name":"fk","owner_operator":"auto","homed":true,
 "ceiling":{"used":true,"from":"hve-cam","status":"MEASURED","mm":1450,"age_ms":95,"ok":true,"reason":"NONE","standalone":"USE"},
 "modules":[{"name":"hve-cam","role":"control","operator":"human","ceiling_sensor":true,"busy_label":null,"ip":"192.168.5.23","fw":"0.2.0"},
            {"name":"fk","role":"control","operator":"auto","ceiling_sensor":false,"busy_label":"測定操作中","ip":"192.168.5.10","fw":"0.1.0"}],
 "goto":{"by":"fk","press":21,"target_mm":1200,"status":"MOVING","reason":"NONE"},
 "arm":{"attached":null,"link":null,"confirm":null,"out":null}}
```

| フィールド | 値 |
| --- | --- |
| `owner` | 今の `ui` / `module` / `button` / `null` に **`arm`** を足す（名前だけ取っておく。アームの制御器とのつなぎ方は spec `H-M7` で、この案の範囲外） |
| **`owner_name`** | `owner` が `module` のとき、その接続の `name`。それ以外は `null` |
| **`owner_operator`** | `human` / `auto` / `null`。`ui`・`button` は `human`、`module` はその接続の `operator`、`arm` は `auto` |
| **`homed`** | 原点を取ったか（電源を入れてから一度下端に着いたか）。`height_ok: false` で `homed: true` なら、下端スイッチの誤りなど（spec [Spec-safety.md](../spec/Spec-safety.md) §1.2） |
| `ceiling.from` | 判定に使った天井の値を送ってきた上部モジュールの `name`（spec §3「どの上部モジュールから来たか」） |
| **`ceiling.standalone`** | `USE` / `SETTING_OFF` / `NO_SOURCE`。単体の操作（昇降部の画面・物理ボタン）で天井を使うか・使わない理由（§2.5。2026-11-06）。**足すだけの変更**なので、fk・ss に確かめ直さなくてよい（読まなければ今のまま） |
| **`modules`** | 繋がっている接続の一覧（`observe` を含む）。**今の `module`（1 つ）を置き換える** |
| **`goto`** | 最後の `goto`。`by`（送った接続の `name`）・`press`・`target_mm`・`status`（`MOVING` / `ARRIVED` / `NOT_ARRIVED`）・`reason`（止めた理由。§3.1）。次の `goto` まで残す。起動してから無ければ `null` |
| **`arm`** | 測定機構（spec §3・§5）。`attached`（アームの制御器と通信できて、付いているとみなしているか）・`link`（いまアームの制御器と通信できているか）・`confirm`（spec #4e で止めていて、昇降部の画面の確認待ちか）・`out`（腕が格納位置にないか）。**名前だけ決め、中身はアームの制御器とのつなぎ方（spec `H-M7`）が決まってから。**分からないときは、どれも **`null`**（spec §3。2026-11-05 ユーザー決定。fk-system の頼み）。**2026-11-14 に改めた**（spec [Spec-module.md](../spec/Spec-module.md) §5。ドラムが昇降部から外れ、コネクタが無くなった）: **`drum` は無くす**（コードから消すのは [ImplementationPlan.md](../ImplementationPlan.md) §6 の行 10）。**`attached` はコネクタの検知ではなくなり、`false` は出さない**（昇降部は「付いていない」と判定できない。`true` か `null`）。`confirm` は `null` / `DETACHED` / `ATTACHED` をやめ、確認待ちかどうかだけにする。`out: false` は「格納位置にあると知らされた」ときだけ。fk-system は `null` を「腕の状態が読めない」として機体を動かさない。**`attached: false` を「付いていない」と読んで機体を動かす決まりは、`false` が出なくなるので fk-system に確かめ直す** |

### 3.1 足す止めた理由（[-names.md](DetailedDesign-names.md) §3 に移した）

| 名前 | 意味 |
| --- | --- |
| `ARRIVED` | 目標の高さに着いた（`goto`） |
| `PREEMPTED` | 取って代わられた（別の操作元が新しく押し始めた）。`goto.reason` に出す（`state.reason` は新しい持ち主の動きを出す） |
| `TARGET_RANGE` | `goto` の目標が範囲の外 |
| `ARM_LINK` | 腕が格納位置にない（または分からない）と最後に知った状態で、アームの制御器と通信できなくなった（spec #4e。2026-11-14 に改めた）。昇降部の画面の確認待ちでもある（`arm.confirm`）。**`ARM_CONFIRM`（spec #4f）と `DRUM_LAG`（spec #4g）は 2026-11-14 に無くした**（どちらも名前を取っておいただけで、実装は無かった） |

### 3.2 周期と古さ

| 項目 | 値 |
| --- | --- |
| `state` の配信 | `LIFT_STATE_PERIOD_MS`（仮 100 ms）ごと。`observe` を含むすべての接続へ |
| 受け手の古さの判定 | カメラモジュールは `lift_state_timeout_ms`（仮 600 ms）。fk の暫定 500 ms でも足りる（配信の 5 回分）。**届かなければ、自分で WS を閉じて繋ぎ直す**（昇降部が再起動すると古い TCP は黙って死ぬ。[-protocol.md](DetailedDesign-protocol.md) §1）。繋ぎ直したら `hello` から |
| 命令の途切れ | `LIFT_CMD_TIMEOUT_MS`（600 ms）届かなければ止まる。送る間隔はカメラモジュールと同じ 100 ms を勧める |

## 4. 操作している元の見分け方（fk-system が使う）

| `owner` | `owner_name` | 誰 | `owner_operator` |
| --- | --- | --- | --- |
| `ui` | — | 人（昇降部の画面） | `human` |
| `button` | — | 人（点検用の物理ボタン） | `human` |
| `module` | `hve-cam` | 人（カメラモジュールの画面） | `human` |
| `module` | `fk` | fk-system | `auto` |
| `module` | `ss` | ss-system | `auto` |
| `arm` | — | アームの制御器 | `auto` |
| `null` | — | 誰も動かしていない | `null` |

**人の割り込みかは `owner_operator` で分かる**（名前の表に頼らなくてよい）。ss が動かしている間かは `owner_name` で分かる。

## 5. 変えると壊れるもの（パケットを切るとき一緒に直す）

| 所 | いま | 直すこと |
| --- | --- | --- |
| カメラモジュール `camera/hve_camera/control.py:281` | **`owner == "module"` を「自分が持ち主」とみなす** | `owner_name == "hve-cam"` で見る（fk・ss が動かしている間に、自分の判定と取り違えない） |
| カメラモジュール `lift_link`（`hello`・送り方） | 止まっている間は何も送らない | §2.3 の `ceiling` を送り続ける。`hello` に `role`・`operator` を足す |
| カメラモジュールの画面 `camera/web/app.js:176` | `owner == "module"` を「カメラモジュール」と出す | `owner_name`・`busy_label` で出す |
| 昇降部の画面 `firmware/lift/web/app.js:136・379` | `owner == "module"` を「上部モジュール」、`state.module` を 1 つ出す | `modules` の一覧と、持ち主の `busy_label` を出す |
| 昇降部 `cmd_codec.cpp`・`main.cpp` | `module` を 1 つだけ持つ | §1〜§3 |
| [DetailedDesign.md](DetailedDesign.md) §3.1 | 距離計を持たない上部モジュールは天井を使わない | §2.4 で置き換える |
| `cleanupClients()` | 超えたら古い方を閉じる | §1.3 |

## 6. fk-system に確かめてほしいこと

**2026-11-05 に fk-system が確かめた: 7 点とも案のとおり。**fk は `name: "fk"`・`operator: "auto"`・`busy_label`「測定操作中」を名乗り、100 ms ごとに送り、`state` が 500 ms 来なければ「読めない」とみなして繋ぎ直す（その間は th-system にも動く指示を出さない）。下端まで下げるのを待つ上限（暫定 60 s）は fk 側にも持つ。`role: observe` は fk には要らない（作ることに異論なし）。あわせて受けた頼みで、`arm` の欄は分からないとき `null` にした（§3）。

**2026-11-05 に ss-system も確かめた（ss のユーザーの承認）: 案のとおり。**ss は `name: "ss"`・`role: "control"`・`operator: "auto"`・`ceiling_sensor: false`・`busy_label`「画像認識で操作中」を名乗る。主に `goto` を使い（始める前に `state` の高さを読んで戻す先にする）、止めるときは送るのをやめて `release` する。人の割り込み（`goto.status` が `PREEMPTED`、または `owner_operator` が `human`）を見たら、すぐ止まり、戻さずに手を離す。命令は 100 ms ごと、`state` が 600 ms 来なければ繋ぎ直す。昇降部の口にほかに要るものは無い（`height`・`homed`・`owner_name`・`owner_operator`・`goto`・`ceiling` で足りる）。

1. 名前 `fk`・`ss` でよいか（ss の分は ss-system にも確かめる）。
2. `operator`・`busy_label` の形（hve の画面が名前を決め打ちしないため）。
3. `goto` の結果の形（`by`・`press`・`status`・`reason`）と、着いたあと同じ `press` では動かないこと。
4. `arm` の欄の名前（中身は spec `H-M7` のあと）。
5. 周期と古さ（§3.2）。
6. 止める・下端まで下げるの使い方（§2.1）。
7. `role: observe` が要るか（fk は `control` の接続でも `state` を読める。spec §1 にあるので作る）。

## 7. この案で決めないもの

| もの | どこで |
| --- | --- |
| アームの制御器とのつなぎ方・`arm` の中身 | spec `H-M7` |
| 着いたとみなす幅 `LIFT_GOTO_TOL_MM` | spec `H-M6`（実機） |
| 距離計を持つ上部モジュールが 2 台以上のとき | spec `H-M5` |
| 繋げる本数の上限 `LIFT_MODULE_MAX_CONN` | 実機（`WP-MEAS-03`） |
