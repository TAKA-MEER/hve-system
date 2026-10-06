# docs/plan/detailed — 詳細設計書（v2）

[完全設計書](../spec/README.md)（`docs/plan/spec/`）を入力とし、**実装できる形に落とした設計書。**

**本体は [DetailedDesign.md](DetailedDesign.md)。2026-10-06 初版。**
ユーザー判断待ちの提案が [-open.md](DetailedDesign-open.md) §1 にある。旧版は [archive/v1/detailed/](../archive/v1/detailed/README.md)。

**既存コードのコメントにある `docs/plan/detailed/…` への参照は旧版を指して書かれた。**v2 で同じファイル名を使っているので、
パケットで触ったファイルから v2 に直していく（[-packets.md](DetailedDesign-packets.md) §0）。

---

## 1. 位置づけ

| 文書 | 役割 |
| --- | --- |
| `docs/plan/spec/` | **完成形の正本。**確定した方針はここが正 |
| **`docs/plan/detailed/`（ここ）** | **詳細設計書。**ファイル構成・メッセージ・ピン・アルゴリズム・作業パケット |
| `docs/plan/archive/v1/` | 旧版。v2 で「旧版のまま」と書いた所はそちらを読む |

**完全設計書を詳細設計書に合わせて書き換えることはしない。**完全設計書が上流である。

## 2. ファイル構成

| ファイル | 内容 | 状態 |
| --- | --- | --- |
| **[DetailedDesign.md](DetailedDesign.md)** | **本体。**設計方針・全体構成・安全の取り決めの落とし方・部分ごとの設計・段階 | 初版 |
| [DetailedDesign-names.md](DetailedDesign-names.md) | **名前辞書・仮値の一覧。**この文書に無い名前を実装で作ってはいけない | 初版 |
| [DetailedDesign-protocol.md](DetailedDesign-protocol.md) | 通信・周期・メッセージ・設定 API・UART | 初版 |
| [DetailedDesign-hardware.md](DetailedDesign-hardware.md) | 機器・ピン（多くが仮）・電源・**UnitV2 で要確認のこと** | 初版 |
| [DetailedDesign-packets.md](DetailedDesign-packets.md) | **作業パケットと受け入れ条件** | 初版 |
| [DetailedDesign-open.md](DetailedDesign-open.md) | **ユーザー判断待ちの提案**・未確定・申し送り・レビュー指摘管理表 | 初版 |

## 3. 実装者への 3 つの約束（旧版と同じ）

| # | 約束 |
| --- | --- |
| **1** | **担当パケットの「読む節」だけ読めば実装できる。**完全設計書を読む必要はない |
| **2** | **名前を発明する必要がない。**必要な名前はすべて [-names.md](DetailedDesign-names.md) にある |
| **3** | **完了したかどうかを自分で判定できる。**受け入れ条件はすべて実行可能なコマンドになっている |

**この 3 つが崩れている箇所を見つけたら、実装を止めて設計書側を直す。**

## 4. 完全設計書が更新されたとき

1. [-open.md](DetailedDesign-open.md) §3 の申し送り表を見直す
2. 変更に対応する詳細設計の節と、影響する作業パケットの受け入れ条件を直す
3. **完全設計書を詳細設計書に合わせて書き換えることはしない**

## 5. レビュー

| 回 | 時期 | 観点 | 結果 |
| --- | --- | --- | --- |
| 1 回目 | 初版完成時（2026-10-06） | 安全・整合 ／ 実装可能性 | 6 件。すべて対応済みか対応しない＋理由（[-open.md](DetailedDesign-open.md) §4） |
