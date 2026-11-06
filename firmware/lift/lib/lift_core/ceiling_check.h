// 天井の値の判定（純関数）。Arduino.h は include しない（docs/plan/detailed/DetailedDesign-names.md §1）。
// 規則は docs/plan/detailed/DetailedDesign.md §3.2。定数の値は DetailedDesign-names.md §5.1。
#pragma once

#include <cstdint>

// 停止理由（DetailedDesign-names.md §3 の名前そのもの）
enum class StopReason : uint8_t {
  NONE = 0,
  CMD_STOP,
  CMD_TIMEOUT,
  OWNER_GONE,  // 持ち主の接続が閉じた
  CEILING_NEAR,  // 天井が近い（MEASURED で閾値以下・TOO_NEAR）
  CEILING_STALE,  // 天井の値が無い・読めない・古い
  HEIGHT_UNKNOWN,  // 高さが読めない・古い（原点を取る前を含む）。上昇しない
  TOP,             // 高さが LIFT_TOP_MM に達した。上昇しない
  BOTTOM,
  MAX_RUN,
  ENCODER_STALL,  // モータへ出力しているのにエンコーダが回らない・逆に回る（spec #4d）
  OUT_OF_RANGE,  // 天井の理由のみ（停止理由ではない）。反射が返らない。上昇は許す
};

// 判定に使う定数（DetailedDesign-names.md §5.1）
// 天井の閾値。2026-11-06 から設定 ceiling_margin_mm（Spec-ui §2.3）で変えられる。
// 既定は仮（H-V8）。判定は設定の値を引数で受ける
constexpr int CEILING_MARGIN_MM_DEFAULT = 500;
constexpr int CEILING_MARGIN_MM_MIN = 150;
constexpr int CEILING_MARGIN_MM_MAX = 3000;
constexpr uint32_t CEILING_STALE_MS = 600;    // 仮（H-V8）

// 天井の状態（DetailedDesign-protocol.md §2.1 の status）。
// MISSING は「欠けている・読めない・知らない値」（cmd_codec が付ける）。
enum class CeilingStatus : uint8_t {
  MEASURED = 0,  // 測れた（mm 付き）
  TOO_NEAR,      // 近すぎて測れない
  NO_ECHO,       // 反射が返らない（遠い）
  READ_ERROR,    // 距離計が読めない
  MISSING,       // 欠けている・読めない
};

// hold に載る天井の値（DetailedDesign-names.md §1）。
// age_ms は送り手が「送るその瞬間に」測った読み値の古さ。
// received_at_ms は昇降部がその hold を受け取った時刻。
struct CeilingReport {
  CeilingStatus status = CeilingStatus::MISSING;
  int mm = 0;  // MEASURED のときだけ意味を持つ
  uint32_t age_ms = 0;
  uint32_t received_at_ms = 0;
};

// ceiling_check() の結果。ok が偽のとき reason は CEILING_NEAR / CEILING_STALE。
// ok が真のとき reason は NONE（NO_ECHO の反射なしは OUT_OF_RANGE）。
struct CeilingVerdict {
  bool ok = false;
  StopReason reason = StopReason::CEILING_STALE;
};

// 値が古いか（age_ms ＋受け取ってからの経過が CEILING_STALE_MS を超えた）。
// ceiling_check の「古い」と、単体の操作で出どころを選ぶときの「古い」は、ここだけを共有する
bool ceiling_is_stale(const CeilingReport& report, uint32_t now_ms);

// 天井の値・古さ・その接続が距離計を持つか → (ok, 理由)（DetailedDesign.md §3.2）。
// 距離計を持たない接続なら常に ok。古さは age_ms ＋受け取ってからの経過時間。
// margin_mm は天井の閾値（設定 ceiling_margin_mm。MEASURED で mm ≦ margin_mm → CEILING_NEAR）。
// 既定値は付けない（呼び出し側が必ず設定の値を渡す）
CeilingVerdict ceiling_check(const CeilingReport& report, uint32_t now_ms,
                             bool connection_has_sensor, int margin_mm);
