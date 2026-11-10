# 外し忘れの省電力・電源断（v2）

spec [Spec-power.md](../spec/Spec-power.md) を実装できる形に落としたもの（2026-11-10）。名前は [-names.md](DetailedDesign-names.md) に登録した。
作業パケットは [-packets.md](DetailedDesign-packets.md) 段階 9（`WP-IO-03`・`WP-VIDEO-04`・`WP-CAM-13`・`WP-UI-05`・`WP-MEAS-07`）。

**効き目は実測するまで分からない**（spec `H-V7`）。UnitV2 を `poweroff` したあとに 5 V の消費がどこまで落ちるかは確かめていない。UNO と SRF02 は動き続ける。**「電源断で消費が 0 になる」とは約束しない**（`WP-MEAS-07`）。

---

## 0. 結論

| 部分 | やること |
| --- | --- |
| 無操作の判定 | `hve_camera` の純関数のクラス **`IdlePower`**（`camera/hve_camera/power.py`）。操作のたびに `touch()`、`control_step` のたびに `step()` |
| ピッチの力を抜く | UART に **`R` 行**を足す（§2）。省電力の間は `M` の代わりに `R` を周期で送り続ける。**UNO は `R` を受けたら力を抜き、次の `M` が来るまで抜いたまま**（途絶えても戻らない） |
| 映像 | `hve_video` に **`POST /capture`**（127.0.0.1 だけ）を足し、取り込みの子プロセスを止める・戻す（§3） |
| 機体の無線 | **`wlan0` が AP につながっているか**で見る（§4。AP の名前は読まない） |
| 電源断 | `IdlePower` が決め、**差し替えられる口 `PowerHw.poweroff()`** を呼ぶ。偽物のモード・試験では実物を呼ばない（§5） |
| 画面 | `state.power` を出し、省電力中は重ね表示（§6） |

## 1. 無操作の判定（`IdlePower`）

**時刻を引数で受ける純関数のクラス。**ハードウェアにも時計にも触らない（ホストで試験する）。

| 入力 | 中身 |
| --- | --- |
| `touch(now_ms)` | 操作があった。**省電力中なら戻る** |
| `step(now_ms, lift_moving, wlan_up)` | 周期の判定。`lift_moving` は昇降部の `state.lift.dir` が `stop` 以外（誰が動かしていても）。`wlan_up` は §4 |

**`touch()` を呼ぶところ**（spec §1。**画面を開いているだけ・映像の接続・状態を読むだけの接続では呼ばない**）:

| 入口 | 置き場 |
| --- | --- |
| 画面の WS の `hold`・`release`・`zoom`・**`touch`**（新。静止画のボタンで送る。静止画は `hve_video` へ直接取りに行くので、`hve_camera` に知らせるため） | `app.py` の `_on_message` |
| `PUT /api/settings` | `app.py` |
| **ss-system の口**（ヨー・ピッチの操作・静止画の要求。行 8a でこれから書く） | **その口の案に「`touch()` を呼ぶ」を書く。静止画の要求も `hve_camera` を通すか、`hve_camera` に知らせる形にする** |
| `step()` で `lift_moving` が真 | `IdlePower` の中（最後の操作の時刻を進める） |

**状態と移り変わり**（`idle` ＝ 最後の操作からの時間）:

| 状態 | 入る条件 | 出すもの |
| --- | --- | --- |
| `ACTIVE` | 起動したとき・`touch()` | ふつう（`M` 行・取り込み） |
| `SETTLING` | `ACTIVE` で `idle` ≥ `idle_save_ms` | 取り込みを止める。**`M` 行のピッチを `power_save_pitch_deg` にして送る**（`H-V15`。倒れてもよい角度へ動かす）。軸の目標角（`axes.py`）は書き換えない |
| `SAVING` | `SETTLING` に入って `power_save_settle_ms` 経った | **`M` の代わりに `R` 行を送り続ける**（ピッチの力を抜く・ヨーは止める） |
| `OFF` | `SETTLING`・`SAVING` で、**`idle` ≥ `idle_save_ms` かつ `wlan_up` が偽の状態が `ap_absent_ms` 続いた** | `R` 行を送り、`PowerHw.poweroff()` を **1 回だけ**呼ぶ |

- `SETTLING`・`SAVING` で `touch()` → `ACTIVE`。**`M` 行を軸の目標角で送り直す**ので、ピッチは力が入ってもとの角度へ動く（spec §2）。取り込みを戻す
- `wlan_up` が一度でも真になったら、無線が無い時間を数え直す（一瞬の不調で切らない。spec `H-V16`）
- **時間はアプリの起動から数える。**`wpa_supplicant` が固まって（[-hardware.md](DetailedDesign-hardware.md) §2.1.1・`P-13`）つながらなかった起動では、30 分＋`ap_absent_ms` で切れる。そのままでも使えない状態なので受け入れる
- 昇降部は `OFF` を知らなくてよい。カメラモジュールが落ちれば天井の距離が来なくなり、上部モジュールからの上昇は昇降部が拒む（spec [Spec-safety.md](../spec/Spec-safety.md) §2 #4）

## 2. UART の `R` 行（UnitV2 → Arduino）

[-protocol.md](DetailedDesign-protocol.md) §5 に足す。

| 行 | 形 | 意味 |
| --- | --- | --- |
| `R` | `R <seq>` | **ピッチの力を抜き（パルスを止める）、ヨーを止めてコイルの電流を切る。**`seq` は `M` と同じ連番。**生存確認も兼ねる**（`io_cmd_period_ms` ごとに送る） |

**UNO（`IoController`）の振る舞い:**

| 受けたもの | ピッチ | ヨー |
| --- | --- | --- |
| `M` | パルスを出す（力が入る） | 指令どおり |
| `R` | **パルスを止める（`IoHal::servo_off()`）。次の `M` まで止めたまま** | 止めてコイルを切る |
| 途絶え（`IO_CMD_TIMEOUT_MS`） | **最後に受けたのが `M` ならその角度で保つ（今までどおり `P-8`）。`R` なら止めたまま** | 止めてコイルを切る |
| 起動 | 今までどおり `PITCH_INITIAL_DEG` でパルスを出す（`P-8`） | 止める |

- **`R` を一度きりにしない理由**: 読めない行は捨てる設計なので、一度きりの行は落ちうる。送り続ければ次の周期で効く
- **互換**: 古い UNO は `R` を読めない行として捨てる → 途絶えとみなし、ピッチを保つ（力は抜けないが今までと同じ）。新しい UNO に古い UnitV2 は `R` を送らない → 今までと同じ。**どちらの順に入れてもよい**
- **残る穴**: UnitV2 が電源を切ったあとに UNO が再起動すると（電池の電圧降下・ウォッチドッグ）、起動時の `P-8` でパルスが入り、サーボが力を出し続ける。塞ぐかは提案 **`P-15`**（[-open.md](DetailedDesign-open.md) §1。spec の「途絶ではピッチを保つ」を変えるのでユーザー判断）

## 3. 映像の取り込みを止める（`hve_video`）

| 口 | 中身 |
| --- | --- |
| `POST /capture`（127.0.0.1 だけ。`/zoom` と同じ扱い） | 本文 `{"on": true|false}`。`false` で取り込みの子プロセス（`v4l2-ctl`）を止め、**最後の 1 枚を捨てる**。`true` で起こす。同じ値を何度受けてもよい |
| `GET /snapshot`（取り込みを止めている間） | **古い 1 枚を返さない。**取り込みを起こし、新しい 1 枚が来るまで最大 `snapshot_wake_wait_ms` 待って返す。来なければ `503`。起こしたままにはしない（`hve_camera` が次に送る `/capture` に従う） |
| `/stream`（止めている間） | 接続は保つ。1 枚も送らない |

- `hve_camera` は **望む値を `power_check_period_ms` ごとに送り直す**（`hve_video` だけ再起動したときも揃う。`VideoZoom` の送り直しと同じ考え方）
- 画面の映像の確かめ（[DetailedDesign.md](DetailedDesign.md) §4.4.1）は止めている間に失敗しうるが、画面は省電力の重ね表示（§6）を出すので構わない

## 4. 機体の無線（`wlan_up`）

- **`/sys/class/net/wlan0/operstate` が `up` なら真**（`wpa_supplicant` がつながっているとき）。読めない・`up` 以外は偽
- **AP の名前を読まない**ので、AP 名を直書きしない決まり（spec [Spec.md](../spec/Spec.md) §5）をそのまま満たす。th-system とは通信しない（`HD-1`）
- **UnitV2 で AP が消えたときに本当に `up` 以外になるか・何秒で変わるかは確かめていない**（`WP-MEAS-07`）。変わらなければ `wpa_cli -i wlan0 status` の `wpa_state=COMPLETED` に替える

## 5. 電源断の口（`PowerHw`）

| 実装 | `wlan_up()` | `poweroff()` |
| --- | --- | --- |
| `hw/power_hw.py`（実物。UnitV2 で root） | §4 | `/sbin/poweroff` を呼ぶ |
| `hw/fake_power.py`（偽物のモード・試験） | `POST /api/fake` の `wlan_up` で切り替える（既定は真） | **ログを出して回数を数えるだけ**。実物を呼ばない |

- **開発機で `poweroff` を叩かないこと**が最重要。偽物のモードは `wlan0` が無い PC で動くので、実物の `wlan_up()` だと「無線なし」になり、30 分で開発機を落としうる
- 実物を選ぶのは、今の偽物のモードと同じ切り替え（`--fake` が無いとき）だけ

## 6. 画面（`state.power`）

| フィールド | 中身 |
| --- | --- |
| `state.power.mode` | `active` / `saving`（`SETTLING`・`SAVING` はどちらも `saving`） |

- `saving` の間、映像の上に「**省電力中です。操作すると戻ります。機体の無線が無くなると電源を切ります**」を重ねる。重ね表示を押す・ボタンを押すと戻る（押した操作はそのまま効く。ピッチは力が入ってから動く）
- `docs/使い方.md`: 外し忘れの省電力と電源断・戻し方（電池の挿し直し）を書く
