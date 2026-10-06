# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## このリポジトリは何か

**th-system の走行部に載せる昇降機と、その上部カメラの制御システム。**
th-system（`../th-system`）の完全設計書が「範囲外（別担当）」としている
**カメラ昇降のピッチ・ヨー・高さ**を担当する（th-system `Spec.md` §1・`Spec-onsite.md` §7.1）。

**2026-09-29 時点で、PC だけでできる実装（昇降部の判定と ESP32 の配線・カメラ部の制御・映像・画面・ラズパイの実物の層）は済み。実機での確認はまだ**（[ImplementationPlan.md](docs/plan/ImplementationPlan.md) §1・[docs/試験項目.md](docs/試験項目.md)）。開発体制は th-system と同じにしてある
（文書の役割分担・herdr／orca ＋ opencode による実装・受け入れ検査・git 運用）。
**構成**: 昇降部（ESP32 ＋ MD10C ＋ 高さの HC-SR04）と無線カメラ部（ラズパイ 4 ＋ 沼津高専 MIRS 由来のシールド基板・Web カメラ・ヨー＝28BYJ-48/ULN2003・ピッチ＝SG90・天井の SRF02（I2C）・モバイルバッテリ）。無線は `th-rpi-ap` 経由（[Spec.md](docs/plan/archive/v1/spec/Spec.md) §5・[DetailedDesign-hardware.md](docs/plan/archive/v1/detailed/DetailedDesign-hardware.md)）。

**2026-10-06 から、使用できる機器が増えたため設計から見直している（ブランチ `redesign/v2`）。**
上の実装と構成は旧版（v1）で、その spec・detailed は `docs/plan/archive/v1/` に移した。
`docs/plan/spec/`・`detailed/` は v2 の骨格から書き直す。**既存コードのコメントにある spec・detailed への参照は旧版を指す**（[spec/README.md](docs/plan/spec/README.md)）。

## 作業開始前のルール

**実装作業に入る前に必ず `git status` を確認し、作業前から存在する未コミットの変更がないか調べること。**

- 未コミットの変更があれば、着手前にユーザーへ提示して扱いを確認する（先にコミットするか、そのまま残すか）。ユーザーが別のターミナルや別セッションで進行中の作業であることが多い。
- 勝手にコミットも破棄もしない。確認せずに作業を始めると、こちらの変更と混ざって切り分けられなくなる。
- コミット時は自分が変更したファイルだけをステージする。`git add -A` / `git add .` は作業前からあった変更を巻き込むため使わない。
- この環境では `git add -p` が使えない（対話的フラグ非対応）ため、1つのファイルに複数の関心事の変更を混ぜると後からコミットを分割できない。無関係な変更を同じファイルに同時に入れないよう作業順序を組む。

## 方針変更時のルール

**完成形（最終的なシステム像・挙動・要件）の正本は `docs/plan/spec/` である**（索引は [spec/README.md](docs/plan/spec/README.md) §1.2）。

**昇降・カメラ制御・安全設計・th-system との関係（独立）・アーキテクチャ全般について、ユーザーから方針変更の指示があった場合は、コードを修正する前に必ず `docs/plan/spec/` の該当箇所を更新すること。** コード修正はその後に行う。spec とコードの内容に矛盾が生じた場合は、ユーザーに確認しどちらが正か明確にしてから作業を進める。

**spec に書くのは挙動の水準だけ**（`spec/README.md` §1.1）。ノード名・トピック名・ファイル構成・ピン番号・アルゴリズムは持ち込まない。それらは `docs/plan/detailed/`（どう実装するか）と `docs/architecture.md`（現状どうなっているか。実装が始まったら作る）の役割。

| 文書 | 役割 |
| --- | --- |
| `docs/plan/spec/` | **完成形の正本。**何が・どう振る舞うべきか |
| `docs/plan/detailed/` | 詳細設計。ノード名・トピック名・ピン・作業パケット |
| `docs/architecture.md` | 現状実装の保守・拡張ガイド(as-built)。**実装開始時に作る**。spec と食い違う場合は spec 側を優先して実装を追いつかせる |
| `docs/plan/ImplementationPlan.md` | **実装の進め方と進み具合。**何を・どの順で・誰が・どう管理するか（下記「実装作業の進め方」） |
| `docs/plan/EXCEPTION-LEDGER.md` | デモ特例で省略・バイパスしたままの事項。コードの `WAIVER(demo):` タグと対。**未クローズが何かはこれが唯一の正** |

`docs/plan/` のうち `spec/` と `detailed/` **以外**は未確定の検討メモで、spec を上書きしない。書き方のルール（本体は結論と表だけにして一目で読める分量を保ち、根拠・詳細は `<テーマ>-<側面>.md` に分ける）は `docs/plan/README.md` に定義してある。plan 配下を編集する前に必ず読むこと。

### th-system との境界

- **th-system から独立させる。通信しない・走行禁止などの連動も設けない**（ユーザー決定 2026-09-25。
  旧版 [Spec.md](docs/plan/archive/v1/spec/Spec.md) §4 `HD-1`。v2 にも引き継ぐ: [Spec-open.md](docs/plan/spec/Spec-open.md) §1）。互いの仕様変更を波及させないため。
  **th-system と信号をやり取りする設計・実装を持ち込まない。**要りそうになったら、先にユーザーに確認して spec を直す。
- 共有するのは機体と無線 AP（`th-rpi-ap`。将来名前が変わる）だけ。**AP 名をコードに直書きしない。**
- **th-system のリポジトリは、ユーザーの指示があるときだけ編集する。**別の正本と運用ルールを持つので、
  触るときは th-system の `CLAUDE.md` に従う（作業前の `git status`・自分のファイルだけステージ）。

## 実装作業の進め方

**正本は [docs/plan/ImplementationPlan.md](docs/plan/ImplementationPlan.md) §2。着手前に読む。**
ここには要点だけ書く。

- **「実装して」と言われたら自分でコードを書かない。**ブリーフを **opencode** に渡して投げる
  （手順・落とし穴は ImplementationPlan §2.1）。体数は固定しない。
  **窓口は herdr と orca の 2 つ**（2026-10-01 から th-system で乗り換えを検討中。orca は th-system で試用済み）。
  **いま開いている方を環境変数で見分けて使う**:
  `TERM_PROGRAM=Orca`／`ORCA_TERMINAL_HANDLE` があれば orca（`orca worktree create --setup skip --agent opencode`）、
  `HERDR_PANE_ID`／`HERDR_ENV` があれば herdr（`ImplementAgent` タブ・`herdr pane split`）。
- **何をやるかは ImplementationPlan §6。**先頭から取る（2026-10-06 に v2 のパケットで書き直した）。
  **取る前に `git log --merges` と突き合わせ、マージ・実機確認・台帳の変更のたびに計画書を更新する**（§2.3「計画書を都度更新する」）。
- **検証は必ず自分でやる**（§2.2）。**実装エージェントの「テストが緑」報告は信用しない。**
  テストを自分で回し、変異チェックを 1〜2 件は自分で再実行する。
- **エージェントの試験が「本番の経路」を縛っているかを、自分の変異で確かめる。**純関数の試験が固くても、
  **呼び出し側の数行**（停止・ウォッチドッグ・リミットの判定を実際に効かせている箇所）は別に縛る。
  th-system ではソースの文字列検査だけの試験が、中核の安全性を壊す変異を素通しした実績がある。
- **git は自分が持つ**（§2.3）。ブランチを切る・`main` へマージ・`push`・掃除は実装管理担当の責任で、
  エージェントは自分の作業ブランチにコミットするところまで。マージは `--no-ff`、**squash しない**。

## 実機マニュアルの保守ルール（実機が動き始めたら適用）

`docs/使い方.md` は**実装を知らない試験担当者が実機を動かすための常設マニュアル**、
`docs/試験項目.md` は**その都度書き換える「今回何を確かめるか」**。どちらも実機で動かす段階になったら作る。

- **実機の挙動・操作手順・画面の文言・パラメータの既定値・確認コマンドを変えたら、
  同じコミットで `docs/使い方.md` も直すこと。** 古いマニュアルは無いより悪い。
- `docs/使い方.md` に「今日は何を試すか」を書かない。それは `docs/試験項目.md` の役割。
- `docs/試験項目.md` に動かし方を書かない。`docs/使い方.md` の該当節へリンクする。

## ビルドとテスト

**リポジトリ直下**から。昇降部は PlatformIO、カメラ部はリポジトリ内の `.venv` で回す（`python3 -m pytest` は使わない。理由は「環境の癖」）。

```bash
python3 -m venv .venv && .venv/bin/pip install -r camera/requirements-dev.txt   # 初回
.venv/bin/pip install -e camera        # `python3 -m hve_camera` で動かすため（camera/pyproject.toml）
pio test -d firmware/lift -e native
pio run  -d firmware/lift -e esp32dev
.venv/bin/python -m pytest camera/tests
.venv/bin/python -m hve_camera --fake --port 18000    # 偽物のモード（立ち上げるだけ）
```

## このファイル自体の保守ルール

- 作業中に判明したこのプロジェクト固有の環境の癖・落とし穴（コマンドの意外な挙動、ツールの制約など）は、ユーザーに確認せず「環境の癖」セクションに追記してよい。
- **CLAUDE.md を更新するたびに、ファイル全体を読み直し、陳腐化した記述・重複・冗長な説明がないか見直すこと。** コンテキストを圧迫しないよう、価値の下がった記述は削除するか簡潔にまとめる。肥大化を優先して情報を積み増すだけにしない。
- 「アーキテクチャ」の節は無い。**`docs/architecture.md`（現状どうなっているか）を実物の実装と入れてから足す。**（「ビルドとテスト」の節は `WP-BASE-01` で足した）

## 環境の癖・注意点

th-system で踏んだもののうち、同じ道具（herdr／orca ＋ opencode・PlatformIO）を使う限りここでも踏むものだけを移した。

- **opencode は「完了」を報告してもコミットしていないことがある**（th-system 2026-09-20）。**完了報告を受けたら、まず `git log main..HEAD` と `git status` を見る。**
- **opencode の `/tmp` 許可プロンプトは、拒否し続けてはいけない。**変異チェックのバックアップと**復元**が両方 `/tmp` 経由だと、拒否すると復元だけ失敗して**変異が入ったままのファイルが残る**。一時ファイルは `.briefs/tmp/` を使わせ、それでも出たら「Allow always」で通し、**あとで作業ツリーを自分で確認する**。
- **opencode の質問画面（選択肢つきの確認）は、ペインが低いと入力欄が画面外に出て、`herdr agent prompt` の文字が入らない**（2026-09-28）。`herdr pane zoom <pane> --on` で広げ、`herdr pane read --source visible` で入力欄を確かめてから `herdr pane send-text` → `herdr pane send-keys <pane> Enter` で答える。終わったら `--off` で戻す。
- **エージェントが作業中の worktree で、自分の変異チェック（ファイルを壊して `cp` で戻す）を回さない。**戻すときに、そのあいだにエージェントが入れた修正をバックアップで上書きして消す（th-system 2026-10-01）。エージェントを待機させてから回すか、検証用に別の worktree を切る。
- **orca で作った worktree には gitignore 済みの `.venv/`・`.briefs/` が無い。**カメラ部の試験は worktree 内で `.venv` を作り直す（または検証は本体側の `.venv` で worktree を指して回す）。`.briefs/tmp/` も作る。
- **既に opencode が動いているペインに `herdr agent start` を打たない。**`start` はシェルプロンプト待ちを期待するため、動作中のセッションに文字列を打ち込んで壊す。`herdr agent rename` だけで登録する。
- **`pip3 install platformio` をホストの `python3 -m pytest` と同じ環境に入れると、依存の `anyio` が pytest プラグインとして自動登録され、`ModuleNotFoundError: No module named '_pytest.scope'` でテストが全滅する**（この環境の `pytest` は 6.2.5）。`python3 -m pytest -p no:anyio ...` で回避できる（th-system 2026-09-05）。
- **リポジトリ直下から `.venv/bin/python -m hve_camera` を動かすには `camera/` を編集可能で入れておく**（`pip install -e camera`）。入れないと `No module named hve_camera` になる（`pytest.ini` の `pythonpath = camera` は pytest だけにも効く）。2026-09-29 WP-CAM-02。
- **`pkill -f <パターン>` は自分のシェルを殺すことがある**（パターンが自分のコマンドラインにマッチする）。PID 指定で止める。
- **カメラ部のラズパイは Raspberry Pi OS Lite（64-bit）Trixie。pigpio は使えない**（公式リポジトリに無い）。GPIO は カーネル PWM・`lgpio`（OS 同梱。pip に Python 3.13 向けが無い）・`smbus2` で扱う。仮想環境は `--system-site-packages` 付きで作る（[DetailedDesign.md](docs/plan/archive/v1/detailed/DetailedDesign.md) §4.4）。
- **ホストの Python は 3.10 で `tomllib` が無い。カメラ部の試験はリポジトリ内の `.venv/` で回す**（§4.5）。システムの `python3 -m pytest` を使うと上の anyio の問題を踏む。
- **この PC には PlatformIO Core が複数入っている**（`pio run` のたびに「Obsolete PIO Core v6.1.19 is used」と出る）。ビルドは通るので無視してよい。ファームの platform は `espressif32@7.0.1` に固定する（2026-09-25 に先行試作がこの版でビルドできることを確認。`ledcSetup` 等の API が版で変わる）。
