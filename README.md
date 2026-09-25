# hve-system

th-system の走行部に載せて運ぶ**昇降機**と、その**上部カメラ**の制御システム。
配電盤の上部をカメラで確認するために、盤前に停止した機体の上でカメラを持ち上げ・向ける。

**現在は設計段階。**コードはまだ無い。

| 知りたいこと | 見る文書 |
| --- | --- |
| 何を作るのか・どう振る舞うべきか | [docs/plan/spec/README.md](docs/plan/spec/README.md)（完成形の正本） |
| 何が決まっていないか | [docs/plan/spec/Spec-open.md](docs/plan/spec/Spec-open.md) |
| どう実装するか | [docs/plan/detailed/README.md](docs/plan/detailed/README.md) |
| 実装の順番・進み具合 | [docs/plan/ImplementationPlan.md](docs/plan/ImplementationPlan.md) |
| 作業のしかた（人・AI 共通） | [CLAUDE.md](CLAUDE.md) |

関連リポジトリ（同じ `TM/` 配下）:

| リポジトリ | 関係 |
| --- | --- |
| `../th-system` | 移動システム。**この機体を運ぶ側。**盤前への到着を知らせてくる |
| `../elevator-motor-control` | 昇降モータの先行試作（ESP32 + MD10CR3・WebUI 単体操作） |
| `../esp32cam-test` | 無線カメラの先行試作（Freenove ESP32-WROVER CAM / OV2640） |
