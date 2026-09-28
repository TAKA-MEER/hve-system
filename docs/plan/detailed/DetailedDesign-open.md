# DetailedDesign-open — 提案・未確定・申し送り・レビュー指摘

[DetailedDesign.md](DetailedDesign.md) の詳細。

---

## 1. ユーザー判断待ちの提案

詳細設計を書くうえで決める必要があったが、**spec に無い振る舞い**なので勝手に確定しなかったもの。
いまの詳細設計は「提案」の列で書いてある。**覆ったら該当箇所を直す。**採否が出たら spec に書き、この表から消す。

| ID | 問い | 提案 | 理由 | 影響する所 |
| --- | --- | --- | --- | --- |
| — | なし（`P-1`〜`P-4` は 2026-09-25〜28 にすべて決定。spec [Spec-open.md](../spec/Spec-open.md) §4） | | | |

## 2. 詳細設計側の未確定事項

| ID | 問い | 選択肢・状況 |
| --- | --- | --- |
| **`D-1`** | **機器のアドレスの決め方** | 固定 IP は th-system の網（192.168.5.x。ラズパイ .1・PC .50 固定）の中で空きを決める必要があり、**th-system と干渉しうる**ので勝手に決めない。当面は DHCP ＋ mDNS（`hve-lift.local`・`hve-cam.local`）。ただし **Android は mDNS を引けない**（先行試作 `設計書.md` §3）ので、操作端末が Android なら AP 側の DHCP 予約が要る |
| `D-2` | 映像の解像度・フレームレートの上限 | 仮 640×480・10 fps。`WP-MEAS-03` で th-system と同時に動かして決める |
| `D-3` | 昇降部の ESP32 の電源をどこから取るか | [-hardware.md](DetailedDesign-hardware.md) §3 |
| `D-5` | Web カメラの型番（他の部品は 2026-09-28 に決定） | [-hardware.md](DetailedDesign-hardware.md) §4 |

## 3. 完全設計書からの申し送り

| 完全設計書の ID | 詳細設計で要ること |
| --- | --- |
| `H-V8` | 実測（`WP-MEAS-01`・`-04`）で [-names.md](DetailedDesign-names.md) §5 の仮値を置き換える |
| `H-A8` | `WP-MEAS-03` |
| `H-V6` | `WP-MEAS-02`（**ズーム込み**。取り込みの解像度ごとのラズパイの負荷と遅延も測る）。遅延が大きければ寸動（[operation-ideas.md](../operation-ideas.md) 案 3）を spec に上げる |

## 4. レビュー指摘管理表

| # | 回 | 指摘 | 対応 |
| --- | --- | --- | --- |
| — | — | — | — |
