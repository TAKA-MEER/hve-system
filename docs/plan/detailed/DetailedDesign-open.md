# DetailedDesign-open — 提案・未確定・申し送り・レビュー指摘（v2）

[DetailedDesign.md](DetailedDesign.md) の詳細。**ID は旧版（`P-1`〜`P-4`・`D-1`〜`D-7`）の続きから振る。**旧版の ID を別の意味で使わない。

---

## 1. ユーザー判断待ちの提案

詳細設計を書くうえで決める必要があったが、**spec に無い振る舞い**なので確定しなかったもの。
いまの詳細設計は「提案」の列で書いてある。**覆ったら該当箇所を直す。**採否が出たら spec に書き、この表から消す。

| ID | 問い | 提案 | 理由 | 影響する所 |
| --- | --- | --- | --- | --- |
| **`P-11`** | UnitV2 が自分で出している AP（`M5UV2_4afd`。`th-rpi-ap` と同じ 1 ch）を、配備で止めるか | **止める**（`/etc/init.d/S90wifi-conf` の `hostapd` の行を外す）。USB の有線（`10.254.239.1`）での保守は残る | 同じチャネルにもう 1 つ AP があると、ビーコンと接続の分だけ `th-rpi-ap` の帯域を食う（spec `H-A8`・`HD-1` の「干渉しない」）。止めると、無線で UnitV2 に入る手段は `th-rpi-ap` 経由だけになる | `WP-CAM-05`・[-hardware.md](DetailedDesign-hardware.md) §2.1.1 |
| **`P-12`** | UnitV2 の OS の設定を変えて、avahi に `th-rpi-ap` 側（`wlan0`）を見させるか | **変える**（`/etc/avahi/avahi-daemon.conf` の `allow-interfaces` に `wlan0` を足す）。変えた差分は `camera/deploy/` に置く | 昇降部を `hve-lift.local` で見つけるため（[DetailedDesign.md](DetailedDesign.md) §4.6）。Python からは `.local` を引けない。足すと UnitV2 も `unitv2.local` として `th-rpi-ap` に名乗る | `WP-CAM-05`・§4.6 |
| — | `P-5`・`P-6`・`P-8`・`P-9`・`P-10` は 2026-10-06 にすべて採用。`P-6` 以外は spec [Spec-open.md](../spec/Spec-open.md) §3 に記録。`P-6`（画面をファームに埋め込む）は実装の選択なので詳細設計に残す） | | | |

（`P-7` は欠番。昇降部の見つけ方は spec `H-M2` で任されたので、提案にせず [DetailedDesign.md](DetailedDesign.md) §4.6 で決めた）

## 2. 詳細設計側の未確定事項

| ID | 問い | 選択肢・状況 |
| --- | --- | --- |
| **`D-1`**（旧版から） | **最初の 1 台をどう開くか**（操作端末が昇降部・カメラモジュールの IP をどう知るか） | 旧版と同じ制約（固定 IP は th-system の網と干渉しうるので勝手に決めない・Android は mDNS を引けない）。v2 では**片方を開ければもう片方へ辿れる**（[DetailedDesign.md](DetailedDesign.md) §4.6）。最初の 1 台は、th-system のラズパイの DHCP の払い出し一覧を見るか、AP 側の DHCP 予約が要る |
| `D-2`（旧版から） | 映像の解像度・フレームレートの上限 | 配信 480p・取り込み 720p の MJPEG は決定（spec `H-V9`・`H-V10`）。**品質と fps の上限は `WP-MEAS-03`（無線の圧迫）で決める**（いまは品質 80・10 fps の仮。作り直しは 1 コアで約 10 fps） |
| `D-3`（旧版から） | 昇降部の ESP32 の電源をどこから取るか | 旧版のまま |
| `D-7`（旧版から） | 画面が映像の途絶えを見つけるため `/stream` へ接続を張っては切る負荷 | UnitV2 では CPU に余裕が無いかもしれない。`WP-MEAS-06` で見る |
| **`D-8`** | UnitV2 で確かめること | **2026-10-06 に UART の電圧・ピンの並び・UNO との往復を除いて確かめた**（[-hardware.md](DetailedDesign-hardware.md) §2.1.1）。残りはテスタと UNO が要る |
| **`D-9`** | モバイルバッテリの型（出力の数・各出力の電流・容量） | [-hardware.md](DetailedDesign-hardware.md) §3。MG996R の拘束電流をモータ側の出力でまかなえること |

## 3. 完全設計書からの申し送り

| 完全設計書の ID | 詳細設計で要ること |
| --- | --- |
| `H-X9`（`W-1`） | 上端の手段が決まったら: `LIFT_TOP_DETECT_ENABLED` を戻す・判定 #4・#6 を手段に合わせて直す・`WAIVER(demo): W-1` を消して台帳を閉じる |
| `H-V8` | 実測（`WP-MEAS-01`・`-04`）で -names.md §5 の仮値を置き換える |
| `H-A8` | `WP-MEAS-03` |
| `H-V6` | `WP-MEAS-02`（UnitV2 で。spec では「簡単な動作確認で問題なし」） |
| `H-V5`・`H-X5` | MG996R のピッチの可動域（`pitch_*_deg`・`PITCH_*_DEG`） |

## 4. レビュー指摘管理表

| # | 回 | 指摘 | 対応 |
| --- | --- | --- | --- |
| 1 | 1 回目（2026-10-06・安全・整合） | WS の口を `params.toml` で変えられ、書き間違えると天井の守りが黙って外れる | **対応済み**: 口を定数にし、`/ws/ui` で `hello` を受けたら接続を閉じる（[DetailedDesign.md](DetailedDesign.md) §3.1・`WP-LIFT-03` 変異 11） |
| 2 | 同 | 持ち主が空になったあと、同じ `press` の `hold` が遅れて届くと動き出しうる | **対応済み**: より新しい `press` でなければ持ち主になれない（§3.3・変異 12） |
| 3 | 同 | `UnoClock` の窓が遅れて読んだ行だけで埋まると、古い天井の値を新しいと見誤る（受信バッファの溢れ） | **対応済み**: 読み出しの間隔が空いたらその回の行を捨てる（§3.5・[-protocol.md](DetailedDesign-protocol.md) §5・`WP-CAM-04` 変異 1b） |
| 4 | 同（実装可能性） | `WP-CAM-04` が `WP-MEAS-06` の結果を待たずに始められ、移植が無駄になりうる | **対応済み**: MEAS-06 の 1・2・5 を入る条件にし、決まらないときは設計を見直すと書いた |
| 5 | 同（整合） | 壊れた `ceiling` を -protocol §2.1 の表は `CEILING_STALE`、下の段落は「捨てる」としていた。`press` を増やすきっかけが未定 | **対応済み**: `hold` を捨てずに `MISSING` として受ける（変異 13）。きっかけを `WP-CAM-04` に書いた |
| 6 | 同 | DetailedDesign.md が 200 行を超えた | **対応しない**（今回）: 初版で節の境目がまだ動くため。§3 が育ったら文章を変えずに切り出す |
