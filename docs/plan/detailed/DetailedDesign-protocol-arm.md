# 通信 — 昇降部とアームの制御器の取り決め（案。spec `H-M7`）

[ImplementationPlan.md](../ImplementationPlan.md) §6 の行 10。spec [Spec-module.md](../spec/Spec-module.md) §5（昇降部とアームの制御器のあいだの通信の取り決めは本システムが書き、fk-system が確かめる）・[Spec-safety.md](../spec/Spec-safety.md) §2 #4e・[Spec-ui.md](../spec/Spec-ui.md) §0.2（確認ダイアログ）・§1.6。
**状態: 2026-11-17 に案を書いた。輸送路は同日にユーザーが決めた（`th-rpi-ap` を通す。spec [Spec-module.md](../spec/Spec-module.md) §6 `H-M7`）。残りは fk-system に確かめてもらう（§9）。実装はまだ。**
新しい名前は、確かめが済んだら [-names.md](DetailedDesign-names.md) へ移す（[-protocol-modules.md](DetailedDesign-protocol-modules.md) と同じ手順）。

- 昇降部の口は [-protocol-modules.md](DetailedDesign-protocol-modules.md)（確かめ済み）を**そのまま使い、足すだけ**にする（安全の判定の入口を 1 つに保つ。spec §1「口の名前と形」）。
- アームの制御器のファーム（ドラム・腕の画面を含む）は fk-system が書く（spec §5）。この案は、昇降部から見えるところだけを決める。

---

## 0. 結論

| 項目 | 案 |
| --- | --- |
| **輸送路**（2026-11-17 ユーザー決定。§1） | **`th-rpi-ap` を通し、昇降部の `/ws/module` に繋ぐ**（制御器どうしを直接つながない） |
| 名乗り | `hello` で **`role: "arm"`** を名乗る。昇降部が受けるアームの接続は **1 本だけ**（§2） |
| 動かす命令 | 上部モジュールと同じ `hold` / `goto` / `release`。持ち主は **`owner: "arm"`**。天井はカメラモジュールの値で判定する（§3） |
| 腕の状態の知らせ | アームの制御器は **`arm` を 100 ms ごとに送る**（`out`: 格納位置にないか）。**`LIFT_ARM_LINK_TIMEOUT_MS`（仮 1000 ms）届かなければ「通信できない」**（§4） |
| 覚えておくもの（`H-X15`） | **「格納位置にない、または分からないと最後に知った」の 1 ビットだけを NVS に保存する**（§5） |
| **腕を出す前の約束**（§6） | **アームの制御器は、昇降部の `state.arm` が `link: true`・`out: true` になったのを読んでから、腕を格納位置から動かす**（昇降部は NVS に書き終えてから `state` に出す） |
| 確認ダイアログ | `/ws/ui` からだけ受ける。**止めるたびに振る番号を付けて返す**（§7） |
| 腕の画面を `th-rpi-ap` を通さずに開く形 | **fk-system が決める。昇降部への条件は無い**（§1.2） |

## 1. 輸送路

### 1.1 比べたもの（2026-11-17 に A をユーザーが採った）

| | **A. `th-rpi-ap` を通す（案）** | B. 制御器どうしを直接つなぐ（ESP-NOW など） |
| --- | --- | --- |
| AP が落ちたとき | アームの制御器と通信できなくなる。腕が出ていれば #4e で昇降機が止まる（人が確認で解く） | 昇降部とアームの制御器のあいだは生き延びる |
| 安全の判定の入口 | `/ws/module` の 1 つのまま（命令の持ち主・`press`・天井の判定をそのまま使う） | 2 つになる（ESP-NOW の命令にも持ち主・`press`・天井の判定を別に付ける） |
| 無線の作り | 今と同じ（WS） | 昇降部の ESP32 は `th-rpi-ap` の STA のまま ESP-NOW を併用するので、**AP のチャネルに縛られる**（AP のチャネルが変わると両方が追う必要がある）。届いたかの確かめ・順番・暗号を自分で作る |
| fk-system の作り | 昇降部へも fk-system へも同じ AP で繋ぐ | AP にも繋ぎ、昇降部とは ESP-NOW で話す |
| AP が落ちたときの測定 | **どのみち止まる**（fk-system はアームの制御器と AP 越しでしか話せず、通信できないときは何も動かさない。fk-system [Spec-arm.md](../../../../fk-system/docs/plan/spec/Spec-arm.md) §1） | 同じく止まる（fk-system が話せない） |

**A を勧める。**B で得られるのは「AP が落ちたとき、腕が出ていても昇降機を動かせる」ことだが、そのとき fk-system は止まっていて、昇降機を動かすのは人である。人は #4e の確認ダイアログで腕を見ながら動かせる（spec §5）。入口を 2 つにする手間と、2 つの入口の判定を揃え続ける危うさに見合わない。

spec は直した（[Spec-module.md](../spec/Spec-module.md) §5・§6 `H-M7`・[Spec-open.md](../spec/Spec-open.md) の記録。2026-11-17）。

### 1.2 腕の画面を `th-rpi-ap` を通さずに開く形（spec §5「腕の画面」）

アームの制御器が予備の AP を持つ（STA と AP を同時に開く）などは、**アームの制御器の作りで、昇降部の振る舞いに効かない**。fk-system が決める。
ただし ESP32 で STA と AP を同時に開くと、AP のチャネルは STA（`th-rpi-ap`）に合わせて動く。fk-system の作りの注意として渡す。

### 1.3 見つけ方

上部モジュールと同じ（[-protocol-modules.md](DetailedDesign-protocol-modules.md) §1.4。`hve-lift.local`、引けなければ設定で IP を与える。居場所をコードに直書きしない）。

## 2. 接続

### 2.1 `hello`

```json
{"t":"hello","name":"fk-arm","role":"arm","operator":"auto","ceiling_sensor":false,"busy_label":"測定操作中","fw":"0.1.0"}
```

| フィールド | 値 |
| --- | --- |
| `role` | **`arm`**（新しく足す）。**今のファーム（`WP-LIFT-13` 以降）は知らない値を `observe` に倒す**ので、この案を入れる前の昇降部にアームの制御器が繋いでも、昇降を命じられず、`arm` も読まれない（安全側。§6 で、アームの制御器は腕を出さない） |
| `name` | 取り決める名前は **`fk-arm`**。ほかは [-protocol-modules.md](DetailedDesign-protocol-modules.md) §1.1 と同じ |
| `operator` | `auto` 以外を送っても `auto` として扱う（spec §3 の「いま操作している元」でアームの制御器は自動） |
| `ceiling_sensor` | `false` を送る。`true` を送っても **`false` として扱う**（アームの制御器の値を天井の出どころにしない。先端の距離計は天面を測るもので天井ではない） |
| `busy_label` | 上部モジュールと同じ（アームの制御器が昇降を動かしている間に、両方の画面に出す。spec [Spec-ui.md](../spec/Spec-ui.md) §1.6「測定操作中」） |

### 2.2 1 本だけ

- **`role: arm` の接続は 1 本だけ受ける。**新しく `role: arm` を名乗る接続が来たら、**古い方を閉じる**（名前が違っても。アームの制御器が再起動して名前が変わっても、古い TCP が残るため）。
- **置き換えは「通信できない」とみなさない。**新しい接続が `hello` を送った時点で、古い接続から最後に受けた `out` と受けた時刻を新しい接続へ引き継ぐ。§4 の時間切れは引き継いだ時刻から数える（新しい接続がすぐ `arm` を送れば #4e にならない。送らなければ時間切れで #4e になる）。
- 古い接続が持ち主なら、その場で止まる（`OWNER_GONE`。上部モジュールの繋ぎ直しと同じ）。
- 本数は上部モジュールと合わせて数える（`hve-cam`・`fk`・`ss`・`fk-arm` の 4 本。上限 `LIFT_MODULE_MAX_CONN` は仮 6 本で足りる。[-protocol-modules.md](DetailedDesign-protocol-modules.md) §1.3）。

## 3. 動かす命令

- **`hold`・`goto`・`release` は上部モジュールと同じ形・同じ規則**（[-protocol-modules.md](DetailedDesign-protocol-modules.md) §2.1・§2.2。spec §5「アームの制御器からの昇降の命令は、上部モジュールからの命令と同じに扱う」）。送り続けている間だけ続き、`LIFT_CMD_TIMEOUT_MS` 届かなければ止まる。最後の操作が勝つ。
- 持ち主になったら `state.owner` は **`arm`**、`owner_name` は接続の `name`（`fk-arm`）、`owner_operator` は `auto`（[-protocol-modules.md](DetailedDesign-protocol-modules.md) §3 では `owner_name` を `module` のときだけとしていたのを、`arm` にも広げる）。
- 天井は**距離計を持たない上部モジュールと同じ**（[-protocol-modules.md](DetailedDesign-protocol-modules.md) §2.4。カメラモジュールの値。無い・古ければ上昇しない `CEILING_STALE`）。
- **取って代わられたことは、`state.owner` が `arm` でなくなること**（`goto` なら `goto.status` の `PREEMPTED`）で知る。アームの制御器は、それを見たら止まる（spec §5）。
- **アームの制御器の命令も #4e で止まる**（アームの制御器と通信できているあいだは #4e にならないので、実際には効かない）。

## 4. 腕の状態の知らせ `arm`

```json
{"t":"arm","out":true}
```

| 項目 | 振る舞い |
| --- | --- |
| 送り方 | **`LIFT_ARM_PERIOD_MS`（仮 100 ms）ごと**。動かしているかどうかに関わらず送り続ける |
| `out` | **`false`: 格納位置のスイッチが押されているときだけ。`true`: それ以外で、腕の状態が分かっている。`null`: アームの制御器自身が分からない**（起動直後・スイッチの読みが食い違うなど）。**昇降部は `null` を `true` と同じに扱う**（spec #4e「格納位置にない、または分からない」）。欠けている・型が違う `arm` は `null` として受ける（捨てない。捨てると最後の `false` のまま時間切れまで動ける） |
| **通信できている** | **最後に `arm` を受けてから `LIFT_ARM_LINK_TIMEOUT_MS`（仮 1000 ms。送る周期の 10 回分）以内**。接続が開いているかでは判定しない（相手が黙って死ぬと TCP は閉じない。[-protocol-modules.md](DetailedDesign-protocol-modules.md) §3.2 と同じ理由）。接続が閉じたら、その場で「通信できない」 |
| **付いているとみなす** | 起動してから一度でも `arm` を受けたら（spec §5「通信できたら付いているとみなす」）。`hello` だけでは数えない。確認で人が答えたら、次に `arm` を受けるまで「分からない」に戻す |
| `role: arm` 以外から来た `arm` | 捨てる |

## 5. 覚えておくもの（spec `H-X15`）

- **NVS に 1 ビットだけ持つ: `arm_out_latched`（「格納位置にない、または分からないと最後に知った」）。**
  「格納位置にあると最後に知った」と「一度も通信できていない」は、起動したあとの扱いが同じ（どちらも止めない。spec §5）なので分けない。
- **立てる**: `out` が `true` か `null` の `arm` を受け、いまの値が立っていないとき。
- **下ろす**: `out: false` の `arm` を受けたとき・**確認で人が答えたとき**（spec §5「覚えたものを消すのは…」の 2 つだけ）。接続が閉じた・時間切れ・置き換えでは触らない。
- **書くのは値が変わったときだけ**（腕を出す・格納するたびに 1 回ずつ。NVS の書き込み回数は問題にならない）。
- **NVS に書き終えてから `state` に出す**（§6 の約束のため）。書けなかったときは、`state.arm.out` を変えずに `null` のままにし、**#4e と同じに止める**（`ARM_LINK`。覚えられないまま腕を出させない）。
- 起動したとき `arm_out_latched` が立っていれば、**`arm` を受けるまで #4e で止める**（上昇も下降も。**原点を取る前の下降より先に効く**。spec [Spec-safety.md](../spec/Spec-safety.md) §1.2）。このとき `state.arm` は `attached: null`・`link: false`・`confirm` が確認待ち・`out: null`（[-protocol-modules.md](DetailedDesign-protocol-modules.md) §3。覚えていた値を `out` に出さない）。

## 6. 腕を出す前の約束（アームの制御器が守る）

**アームの制御器は、昇降部の `state.arm` が `link: true` かつ `out: true` になったのを読んでから、腕を格納位置から動かす。**

- 流れ: アームの制御器は腕を動かす前に `out: true` を送り始め、`state`（`role: arm` の接続にも配信される）で `arm.out` が `true` になるのを待つ。来なければ腕を出さない（fk-system が決めた「腕を出す前に、アームの制御器が昇降機の制御器と通信できていることを確かめる」の中身。fk-system [Spec-arm.md](../../../../fk-system/docs/plan/spec/Spec-arm.md) §1）。
- 理由: これが無いと、腕を出した直後に通信が切れたとき、昇降部が最後に知ったのは「格納位置にある」のままで、#4e が効かず止めない。**spec が受け入れた既知のリスクは「通信が切れている間に人が腕の画面から腕を起こす」ときだけ**（spec [Spec-safety.md](../spec/Spec-safety.md) §2 の #4e の「受け入れているリスク」）。
- `state` に出るのは NVS に書いたあと（§5）なので、**`state` で見えたことが、昇降部が電源を入れ直しても覚えていることの確認を兼ねる。**
- この案を入れる前の昇降部（`arm` がどれも `null`）では、アームの制御器は腕を出さない。
- 腕の画面から人が腕を起こすときも、同じ確かめを通すことを fk-system に勧める（通信できているのに約束を飛ばす理由が無いため。通信できないときの人の操作は spec の既知のリスクのまま）。

## 7. 確認ダイアログ（spec [Spec-ui.md](../spec/Spec-ui.md) §0.2）

```json
{"t":"arm_confirm","stop":3}
```

| 項目 | 振る舞い |
| --- | --- |
| 番号 | 昇降部は **#4e で止め始めるたびに `arm_stop` を 1 増やし**、`state.arm.confirm` に載せる（確認待ちでなければ `null`）。起動したときの値は 1 から（再起動のたびに 1 に戻ってよい。前の起動のダイアログは WS が切れて消えるため） |
| 受け付ける | **`/ws/ui` から来て、`stop` がいまの `arm_stop` と同じときだけ。**`/ws/module` から来た `arm_confirm` は捨てる（spec §5「確認できるのは昇降部の画面だけ」）。番号が違えば捨てる（端末が複数あるので、古いダイアログの遅れた押下で新しい止めを解かない。`press` と同じ考え） |
| 答えたあと | #4e を解き、`arm_out_latched` を下ろし（§5）、「付いているとみなす」を「分からない」に戻す（`attached: null`・`out: null`）。昇降機は新しく押し始めた操作で動く（止めたあとと同じ） |
| 通信が戻ったとき | `arm` を受けたら、答えなくても #4e を解き、`confirm` を `null` にする（ダイアログは消える。spec [Spec-ui.md](../spec/Spec-ui.md) §0.2） |
| 物理ボタン | #4e の間は動かさない（spec #4e）。確認は物理ボタンからはできない |

`state.arm` の形（[-protocol-modules.md](DetailedDesign-protocol-modules.md) §3 の `arm` の中身を決める）:

| 場面 | `attached` | `link` | `confirm` | `out` | 止めるか |
| --- | --- | --- | --- | --- | --- |
| 起動してから一度も `arm` を受けていない・覚えていない | `null` | `null` | `null` | `null` | 止めない |
| 通信できていて格納位置 | `true` | `true` | `null` | `false` | 止めない |
| 通信できていて腕が出ている・分からない | `true` | `true` | `null` | `true`（`null` も `true` に倒して出す） | 止めない（「腕が出ています」。spec [Spec-ui.md](../spec/Spec-ui.md) §1.6） |
| 通信が切れた・最後に格納位置 | `true` | `false` | `null` | `null` | 止めない |
| 通信が切れた・最後に出ている／分からない | `true` | `false` | 番号 | `null` | **#4e `ARM_LINK`** |
| 起動したとき覚えていた（`arm` をまだ受けていない） | `null` | `false` | 番号 | `null` | **#4e `ARM_LINK`** |
| 確認で答えたあと（次の `arm` まで） | `null` | `null` | `null` | `null` | 止めない |

## 8. 判定に足すもの

[DetailedDesign.md](DetailedDesign.md) §4.1 の判定の表の**先頭（#1 の前）に置く**: **#4e が立っていれば、持ち主・向きに関わらず止める（`ARM_LINK`）。**
#4e は下降も止める唯一の判定で、原点を取る前の下降・下端まで下げる `hold` より先に効く（spec [Spec-safety.md](../spec/Spec-safety.md) §2 #4e）。

**「原点の前に腕が出ている」警告**（高さが読めず、通信できていて `out: true`）は**止めない**（spec [Spec-ui.md](../spec/Spec-ui.md) §1.6）。#4e と取り違えない。画面が `height_ok: false` と `arm.link: true`・`arm.out: true` から出す。

## 9. fk-system に確かめてほしいこと

1. 輸送路（§1。`th-rpi-ap` を通す。hve のユーザーが決めた）と、名前 `fk-arm`・`role: arm`。
2. `arm` を 100 ms ごとに送ること・`out` の 3 つの値の意味（§4）・1000 ms で通信できないとみなすこと。
3. **腕を出す前に `state.arm` が `link: true`・`out: true` になるのを待つこと**（§6）。腕の画面からの操作にも通すか。
4. アームの制御器の命令は上部モジュールと同じ `hold`・`goto`・`release` で足りるか。取って代わられたことを `state.owner` で知ること。
5. 腕の画面を `th-rpi-ap` を通さずに開く形は fk-system が決めること（§1.2）。

## 10. この案で決めないもの

| もの | どこで |
| --- | --- |
| 腕の画面を `th-rpi-ap` を通さずに開く形 | fk-system |
| 引っかかりが外れて昇降機が落ちるとき、腕が出ていたら | spec `H-X13` |
| 周期・時間切れの実際の値 | 実機（仮値。`WP-MEAS-03` と一緒に） |
| 作業パケット | 確かめが済んだら [-packets.md](DetailedDesign-packets.md) に切る（昇降部のファーム・昇降部の画面〈確認ダイアログ・腕の表示〉・カメラモジュールの画面〈腕の表示〉・偽の昇降部） |
