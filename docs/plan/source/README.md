# docs/plan/source — 原典・先行試作への参照

spec に統合する前の材料。**仕様の正ではない。**spec に取り込んだら、取り込んだ先を §2 に書く。

## 1. 先行試作（別リポジトリ・参照のみ）

**写さない。**ここに複製すると、試作側が直ったときに古い写しが残る。

| 試作 | 場所 | 中身 |
| --- | --- | --- |
| 昇降モータ制御（**昇降機の仮動作用プログラム**） | `../elevator-motor-control/設計書.md` ／ `src/` | ESP32 + MD10 系（DIR+PWM）。ブラウザから WebSocket で上昇・下降・停止。デッドマン 600 ms・連続駆動上限 10 s（暫定）。リミットスイッチ無し |
| 無線カメラ（**不採用**） | `../esp32cam-test/src/main.cpp` | Freenove ESP32-WROVER CAM（OV2640）。ポート 80 で閲覧ページ、81 で MJPEG 配信。カメラ部はラズパイ 4 ＋ Web カメラに決まったので経緯としてのみ残す |

パスはリポジトリ直下（`hve-system/`）基準。

## 1.5 外部の資料

| 資料 | 場所 | 使い方 |
| --- | --- | --- |
| 沼津高専 MIRS「RaspberryPi シールド基板の製作」（MIRSMG5D-ELEC-0003） | https://www2.denshi.numazu-ct.ac.jp/mirsdoc2/mirsmg5d/elec/num0003a/ | **カメラ部の基板の元**（[DetailedDesign-hardware.md](../detailed/DetailedDesign-hardware.md) §2.1） |
| 沼津高専 MIRS「RaspberryPi へのデバイス接続」（MIRSMG5D-SYST-0005） | https://www2.denshi.numazu-ct.ac.jp/mirsdoc2/mirsmg5d/syst/num0005b/ | SRF02 と I2C-LVL01 の接続 |

## 2. th-system 側の関連記述

| 記述 | 場所 |
| --- | --- |
| 範囲外（カメラ昇降は別担当） | `../th-system/docs/plan/spec/Spec.md` §1 ／ `Spec-onsite.md` §7.1 |
| 暫定インターフェース「作業中」ボタン | `../th-system/docs/plan/spec/Spec-onsite.md` §7.2 |
| 位置決め許容差 `O-a1`・正式インターフェース `O-a6` | `../th-system/docs/plan/spec/Spec-open.md` §5.1 |
| 旧設計での結合構想（到着通知・完了通知・盤前作業中の安全） | `../th-system/th_system_mobility_design_detail.md` §4.4 |
| 現状の実装（到着通知のみ） | `../th-system/docs/architecture.md`「カメラ昇降システムとの連携（TBD）」 |

## 3. spec への取り込み状況

| 材料 | 取り込み先 |
| --- | --- |
| 上記すべて | [Spec-open.md](../spec/Spec-open.md) §1・§3 に要約（2026-09-25） |
