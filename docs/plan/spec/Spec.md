# 完全設計書 — 配電盤上部確認用ロボット 昇降・カメラシステム

**実装（ノード名・トピック名・ピン・ファイル構成）は書かない。次工程の詳細設計に回す。**
読み方・位置づけは [README.md](README.md)。

> **2026-09-25 時点で骨格のみ。**出どころのある事実だけを書き、決まっていない所は
> `未定` とし [Spec-open.md](Spec-open.md) の ID を付けた。**推測で埋めない**
> （推測は次のセッションで別の推測になる）。

---

## 1. 対象

配電盤上部を確認するロボットのうち、**カメラを盤上部へ持っていき、向け、撮るまで**のすべて。
移動は th-system が担当し、本システムはその走行部に載って運ばれる。

| 担当 | 範囲 | 出典 |
| --- | --- | --- |
| **本システム** | カメラ昇降の**高さ**、カメラの**ピッチ・ヨー**、撮影 | th-system [Spec.md](../../../../th-system/docs/plan/spec/Spec.md) §1「範囲外」・[Spec-onsite.md](../../../../th-system/docs/plan/spec/Spec-onsite.md) §7.1 |
| th-system | 保管場所⇔試験場の移動、盤前への移動・停止、機体の非常停止 | 同上 |
| 未定 | 盤前での**低速並進**（th-system は「担当する可能性があるがまだ含めない」としている） | th-system [Spec-onsite.md](../../../../th-system/docs/plan/spec/Spec-onsite.md) §7.1 ／ 本書 [Spec-open.md](Spec-open.md) `H-S3` |

**操作画面・撮影画像の保存と閲覧をどこまで含むかは未定**（[Spec-open.md](Spec-open.md) `H-S1`・`H-S2`）。

---

## 2. 用語

th-system [Spec.md](../../../../th-system/docs/plan/spec/Spec.md) §2 の用語（機体・試験員・試験場・盤 など）をそのまま使う。
ここには本システムで新たに要る語だけを足す。

| 用語 | 意味 |
| --- | --- |
| 昇降機 | 機体に載り、カメラを上下させる機構 |
| 上部カメラ | 昇降機の上端に付き、盤上部を撮るカメラ |
| 移動システム | th-system のこと |
| 未定 | （昇降の最低位置・走行可能な位置・作業位置などの呼び名は [Spec-open.md](Spec-open.md) で決まってから足す） |

---

## 3. 目標

**未定**（[Spec-open.md](Spec-open.md) `H-G1`）。

th-system 側の目標で、本システムが関わるもの（出典: th-system [Spec.md](../../../../th-system/docs/plan/spec/Spec.md) §3）:

| th-system の目標 | 本システムとの関係 |
| --- | --- |
| **G1** 人・物への接触 0 件、意図しない挙動 0 件。他のすべてに優先 | **本システムにも同じ水準を課すかは未定**（`H-G1`）。昇降機が載ることで重心が高くなる予想が th-system 側にある（th-system [Spec-safety.md](../../../../th-system/docs/plan/spec/Spec-safety.md) §3） |
| **G2** カメラ昇降が要求する許容差内に、手動微調整なしで停止できる | **許容差を決めるのは本システム。**th-system 側で未取得（`O-a1`）。本書 [Spec-open.md](Spec-open.md) §1 |

---

## 4. 設計思想

**未定。**th-system の設計思想（SD-1〜SD-9。特に SD-1「操作系は WebUI のみ」・SD-3「非常停止は常に動作し何にも覆われない」）を
引き継ぐかどうかから決める（[Spec-open.md](Spec-open.md) `H-S1`）。

---

## 5. 全体構成

**未定**（[Spec-open.md](Spec-open.md) §2.2 の構成の問い）。

---

## 6. 目次

| 章 | ファイル | 状態 |
| --- | --- | --- |
| 未確定事項・th-system からの要求 | [Spec-open.md](Spec-open.md) | 初版 |
| 領域ごとの詳細 | [README.md](README.md) §2 の候補 | 未作成 |
