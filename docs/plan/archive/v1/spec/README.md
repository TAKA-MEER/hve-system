# docs/plan/spec — 完全設計書

昇降機・上部カメラ制御システムの**完成形の正本**。

**本体は [Spec.md](Spec.md)。** そこから各詳細ファイルへ辿れる。
**2026-09-25 時点で骨格のみ。**まず [Spec-open.md](Spec-open.md) の未確定事項を潰しながら埋めていく。

---

## 1. 位置づけ

| 文書 | 役割 |
| --- | --- |
| **`docs/plan/spec/`（ここ）** | **完成形の正本。**何が・どう振る舞うべきか。**確定した方針はここが正** |
| `docs/plan/detailed/` | **詳細設計書。**ノード名・トピック名・ピン・ファイル構成・アルゴリズム・作業パケット |
| `CLAUDE.md`（リポジトリ直下） | 作業のしかた・環境の癖・コードの不変ルール |
| `docs/architecture.md` | **現状実装**の保守・拡張ガイド（as-built）。実装開始時に作る |
| `docs/plan/`（`spec/` `detailed/` 以外） | **未確定の検討メモ。**`spec/` を上書きしない |

方針を変えるときは `CLAUDE.md`「方針変更時のルール」に従い、**コードを触る前にここを更新する。**

### 1.1 次の工程

本設計書は**詳細設計の入力**である。したがって次のものは**意図的に書かない**。

* ROS2 のノード名・トピック名・サービス名、通信手段（ROS2 か WebSocket か等）。ただし th-system との**結合の深さと振る舞い**はここで決める（[Spec-open.md](Spec-open.md) `H-A1`）
* ESP32 のピン番号・フレーム形式・ライブラリ
* パラメータファイルのパス・ファイル構成
* WebUI の実装技術
* アルゴリズムの実装方法

書くのは「**何が・どう振る舞うべきか**」と「**どういう値が要るか**」だけである。

### 1.2 どこに何が書いてあるか

| 知りたいこと | 見る文書 |
| --- | --- |
| 対象・用語・目標・範囲 | [Spec.md](Spec.md) |
| **何が決まっていないか** | [Spec-open.md](Spec-open.md) §2 |
| th-system との関係（独立・通信しない） | [Spec.md](Spec.md) §4 ／ [Spec-open.md](Spec-open.md) §1 |
| 先行試作から分かっていること | [Spec-open.md](Spec-open.md) §3 ／ [source/README.md](../source/README.md) |
| ノード名・ピン・作業パケット | [詳細設計書](../detailed/README.md) |
| 実装の順番・進捗 | [ImplementationPlan.md](../ImplementationPlan.md) |
| デモ特例で省略・バイパスしたままの事項 | [EXCEPTION-LEDGER.md](../EXCEPTION-LEDGER.md) |

---

## 2. ファイル構成

| ファイル | 内容 | 状態 |
| --- | --- | --- |
| **[Spec.md](Spec.md)** | **本体。**対象・用語・目標・設計思想・全体構成・目次 | 骨格 |
| **[Spec-open.md](Spec-open.md)** | **未確定事項・決定の記録・th-system との関係** | 更新中 |
| [Spec-safety.md](Spec-safety.md) | 上下端と天井（検知手段・止め方） | 初版 |
| [Spec-ui.md](Spec-ui.md) | 操作画面（速度スライダー・設定画面） | 初版 |
| [mockup/index.html](mockup/index.html) | **操作画面の見た目。**ブラウザで開ける単一ファイル。Spec-ui.md と対 | 初版 |

**領域ごとの詳細ファイルは、書く内容ができた時点で足す。**想定している分け方（確定ではない）:

| 候補 | 内容 |
| --- | --- |
| `Spec-lift.md` | 昇降（高さ）の振る舞い・範囲・速度・止め方 |
| `Spec-camera.md` | カメラ部（ヨー・ピッチ・映像・電源） |
| `Spec-ops.md` | 起動・操作・終了の手順 |
| `Spec-params.md` | パラメータ一覧・逆算の連鎖 |

ファイルを足したらこの表を「ファイル構成」の表へ移す。
`docs/plan/README.md` の運用ルール（本体は結論と表だけ・200 行超で分割）に従う。

---

## 3. 読む順番

| 目的 | 読む順 |
| --- | --- |
| 全体を掴みたい | [Spec.md](Spec.md) だけ |
| **何が決まっていないか知りたい** | [Spec-open.md](Spec-open.md) §2 |
| th-system との関係を知りたい | [Spec.md](Spec.md) §4 → [Spec-open.md](Spec-open.md) §1 |
