// 天井の値の判定そのもの。docs/plan/detailed/DetailedDesign.md §3.2 の表。
#include "ceiling_check.h"

#include <cstdint>

#include "lift_decide.h"  // elapsed_ms（経過時間を数える唯一の関数）

CeilingVerdict ceiling_check(const CeilingReport& report, uint32_t now_ms,
                             bool connection_has_sensor) {
  // 距離計を持たない接続（/ws/ui・hello で ceiling_sensor: false）の命令には
  // 天井の値を使わない（DetailedDesign.md §3.1・spec #4c）
  if (!connection_has_sensor) {
    CeilingVerdict verdict;
    verdict.ok = true;
    verdict.reason = StopReason::NONE;
    return verdict;
  }

  // 古さは「送る瞬間の古さ ＋受け取ってからの経過」（§3.2）。
  // received_at が now より少し新しいときは小さな負の値になるので足さない
  // （誤って古いと見ない）。
  const int32_t transit_ms = elapsed_ms(now_ms, report.received_at_ms);
  const int64_t age_ms =
      static_cast<int64_t>(report.age_ms) + (transit_ms > 0 ? transit_ms : 0);
  if (age_ms > static_cast<int64_t>(CEILING_STALE_MS)) {
    CeilingVerdict verdict;
    verdict.ok = false;
    verdict.reason = StopReason::CEILING_STALE;
    return verdict;
  }

  switch (report.status) {
    case CeilingStatus::MEASURED:
      if (report.mm <= CEILING_MARGIN_MM) {
        CeilingVerdict verdict;
        verdict.ok = false;
        verdict.reason = StopReason::CEILING_NEAR;
        return verdict;
      }
      break;
    case CeilingStatus::TOO_NEAR:
      // 近すぎて測れないので止める（spec #3a）
      {
        CeilingVerdict verdict;
        verdict.ok = false;
        verdict.reason = StopReason::CEILING_NEAR;
        return verdict;
      }
    case CeilingStatus::NO_ECHO: {
      // 反射が返らない（遠い）ので許す。状態に OUT_OF_RANGE を出す（spec #3b）
      CeilingVerdict verdict;
      verdict.ok = true;
      verdict.reason = StopReason::OUT_OF_RANGE;
      return verdict;
    }
    case CeilingStatus::READ_ERROR:
    case CeilingStatus::MISSING:
      // 距離計が読めない・値が無いときは止める（spec #4）
      {
        CeilingVerdict verdict;
        verdict.ok = false;
        verdict.reason = StopReason::CEILING_STALE;
        return verdict;
      }
  }

  CeilingVerdict verdict;
  verdict.ok = true;
  verdict.reason = StopReason::NONE;
  return verdict;
}
