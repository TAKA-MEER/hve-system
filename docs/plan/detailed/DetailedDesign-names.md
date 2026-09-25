# 名前辞書

[DetailedDesign.md](DetailedDesign.md) の詳細。**この文書に無い名前を実装で作ってはいけない。**

> **`DD-1`**: 名前を発明する余地をゼロにする。必要な名前が無いと分かったら、
> **実装する前にこのファイルへ行を足す。**

**2026-09-25 時点で空。**構成（[Spec-open.md](../spec/Spec-open.md) `H-A1`）が決まったら §0 の命名規則から埋める。

---

## 0. 命名規則

未定（`H-A7` の後）。

## 1. th-system 側の名前（参照のみ）

**th-system とは通信しない**（[Spec.md](../spec/Spec.md) §4 `HD-1`）ので、実装でこれらを使うことは無い。
th-system 側の文書を読むときの手がかりとしてだけ残す。**正は th-system の文書とコード。**

| 名前 | 何か | 出典 |
| --- | --- | --- |
| `AT_PANEL` | 盤前のモード（現行の体系でも有効）。状態は `IDLE_P` ／ `WORKING`（作業中ボタン ON）／ `PAUSE`（ジョグ中） | th-system `docs/plan/detailed/DetailedDesign-names.md` §2・§3 ／ `spec/Spec-modes.md` §3.1.2 |
| `working` | `SystemState.msg` のフィールド。作業中ボタンの状態 | th-system `docs/plan/detailed/DetailedDesign-names.md` |
| `/panel_navigator/arrived` | 盤前到着の通知（as-built に実装あり。新設計では `panel_navigator` は `venue_navigator` に置き換わる予定なので、使う前に th-system 側で存続を確認する） | 同上 |
| `/panel_navigator/complete_inspection` | 作業完了を th-system に伝えるサービス（構想） | th-system `th_system_mobility_design_detail.md` §4.4 |
