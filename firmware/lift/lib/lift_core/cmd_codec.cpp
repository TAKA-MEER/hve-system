#include "cmd_codec.h"

#include <ArduinoJson.h>

#include <cstring>

#include "lift_core_version.h"

namespace {

const char* dir_name(LiftDir dir) {
  switch (dir) {
    case LiftDir::UP:
      return "up";
    case LiftDir::DOWN:
      return "down";
    case LiftDir::STOP:
      break;
  }
  return "stop";
}

bool dir_from_name(const char* name, LiftDir* out) {
  if (name == nullptr) {
    return false;
  }
  if (std::strcmp(name, "up") == 0) {
    *out = LiftDir::UP;
    return true;
  }
  if (std::strcmp(name, "down") == 0) {
    *out = LiftDir::DOWN;
    return true;
  }
  if (std::strcmp(name, "stop") == 0) {
    *out = LiftDir::STOP;
    return true;
  }
  return false;  // 知らない値は読めなかったのと同じ扱い
}

const char* reason_name(StopReason reason) {
  switch (reason) {
    case StopReason::CMD_STOP:
      return "CMD_STOP";
    case StopReason::CMD_TIMEOUT:
      return "CMD_TIMEOUT";
    case StopReason::CEILING:
      return "CEILING";
    case StopReason::HEIGHT_UNKNOWN:
      return "HEIGHT_UNKNOWN";
    case StopReason::TOP:
      return "TOP";
    case StopReason::BOTTOM:
      return "BOTTOM";
    case StopReason::MAX_RUN:
      return "MAX_RUN";
    case StopReason::NONE:
      break;
  }
  return "NONE";
}

bool reason_from_name(const char* name, StopReason* out) {
  if (name == nullptr) {
    return false;
  }
  if (std::strcmp(name, "NONE") == 0) {
    *out = StopReason::NONE;
    return true;
  }
  if (std::strcmp(name, "CMD_STOP") == 0) {
    *out = StopReason::CMD_STOP;
    return true;
  }
  if (std::strcmp(name, "CMD_TIMEOUT") == 0) {
    *out = StopReason::CMD_TIMEOUT;
    return true;
  }
  if (std::strcmp(name, "CEILING") == 0) {
    *out = StopReason::CEILING;
    return true;
  }
  if (std::strcmp(name, "HEIGHT_UNKNOWN") == 0) {
    *out = StopReason::HEIGHT_UNKNOWN;
    return true;
  }
  if (std::strcmp(name, "TOP") == 0) {
    *out = StopReason::TOP;
    return true;
  }
  if (std::strcmp(name, "BOTTOM") == 0) {
    *out = StopReason::BOTTOM;
    return true;
  }
  if (std::strcmp(name, "MAX_RUN") == 0) {
    *out = StopReason::MAX_RUN;
    return true;
  }
  return false;
}

LiftCmd stop_cmd() {
  LiftCmd cmd;
  cmd.dir = LiftDir::STOP;
  cmd.duty = 0;
  cmd.ceil_ok = false;
  return cmd;
}

}  // namespace

bool cmd_decode(const char* text, LiftCmd* out) {
  *out = stop_cmd();  // 最後まで読めなかったときは「止まる」側にして返す
  if (text == nullptr) {
    return false;
  }

  JsonDocument doc;
  if (deserializeJson(doc, text) != DeserializationError::Ok) {
    return false;
  }
  JsonObject obj = doc.as<JsonObject>();
  if (obj.isNull()) {
    return false;
  }
  // 知らない t は接受しない（DetailedDesign-protocol.md §2）。seq は「判定には使わない」
  // ので読まない（欠落や型の違う seq でもここで落とさない）
  if (!obj["t"].is<const char*>() || std::strcmp(obj["t"].as<const char*>(), "cmd") != 0) {
    return false;
  }
  // dir が読めない・知らない値なら stop
  LiftCmd parsed;
  parsed.dir = LiftDir::STOP;
  parsed.duty = 0;
  parsed.ceil_ok = false;
  if (!obj["dir"].is<const char*>() || !dir_from_name(obj["dir"].as<const char*>(), &parsed.dir)) {
    return false;
  }
  // 型の違うフィールドも stop（DetailedDesign-protocol.md §2）
  if (!obj["duty"].is<int>()) {
    return false;
  }
  parsed.duty = obj["duty"].as<int>();

  // ceil_ok が無ければ・読めなければ false（欠落は上昇させない）。
  // dir と duty は読めた値を残し、止めた理由を CEILING で出せるようにする。
  const bool ceil_ok_present = obj["ceil_ok"].is<bool>();
  parsed.ceil_ok = ceil_ok_present ? obj["ceil_ok"].as<bool>() : false;
  *out = parsed;
  return ceil_ok_present;
}

size_t state_encode(const LiftState& state, char* out, size_t out_size) {
  if (out == nullptr || out_size == 0) {
    return 0;
  }
  JsonDocument doc;
  JsonObject obj = doc.to<JsonObject>();
  obj["t"] = "state";
  obj["seq"] = state.seq;
  obj["dir"] = dir_name(state.dir);
  obj["duty"] = state.duty;
  obj["reason"] = reason_name(state.reason);
  obj["bottom"] = state.bottom;
  obj["height_mm"] = state.height_mm;
  obj["height_ok"] = state.height_ok;
  if (state.top_mm >= 0) {
    obj["top_mm"] = state.top_mm;
  } else {
    obj["top_mm"] = nullptr;  // 未設定は null（DetailedDesign-protocol.md §2.3）
  }
  obj["cmd_age_ms"] = state.cmd_age_ms;
  obj["fw"] = lift_core_version();
  // バッファが足りないと ArduinoJson は途中で切って終端もしない。切る前に 0 を返す
  const size_t needed = measureJson(doc);
  if (needed >= out_size) {
    return 0;
  }
  return serializeJson(doc, out, out_size);
}

bool state_decode(const char* text, LiftState* out) {
  *out = LiftState();
  if (text == nullptr) {
    return false;
  }

  JsonDocument doc;
  if (deserializeJson(doc, text) != DeserializationError::Ok) {
    return false;
  }
  JsonObject obj = doc.as<JsonObject>();
  if (obj.isNull()) {
    return false;
  }
  if (!obj["t"].is<const char*>() || std::strcmp(obj["t"].as<const char*>(), "state") != 0) {
    return false;
  }
  if (!obj["seq"].is<int>() || !obj["dir"].is<const char*>() || !obj["duty"].is<int>() ||
      !obj["reason"].is<const char*>() || !obj["bottom"].is<bool>() ||
      !obj["height_mm"].is<int>() || !obj["height_ok"].is<bool>() ||
      !obj["cmd_age_ms"].is<int>()) {
    return false;
  }
  if (!dir_from_name(obj["dir"].as<const char*>(), &out->dir)) {
    return false;
  }
  if (!reason_from_name(obj["reason"].as<const char*>(), &out->reason)) {
    return false;
  }

  out->seq = obj["seq"].as<int>();
  out->duty = obj["duty"].as<int>();
  out->bottom = obj["bottom"].as<bool>();
  out->height_mm = obj["height_mm"].as<int>();
  out->height_ok = obj["height_ok"].as<bool>();
  out->cmd_age_ms = obj["cmd_age_ms"].as<int>();
  // top_mm は未設定なら null（LIFT_TOP_MM）。別の型なら読めなかったことにする
  if (obj["top_mm"].isNull()) {
    out->top_mm = LIFT_TOP_MM;
  } else if (obj["top_mm"].is<int>()) {
    out->top_mm = obj["top_mm"].as<int>();
  } else {
    return false;
  }
  return true;
}
