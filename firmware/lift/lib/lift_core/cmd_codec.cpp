#include "cmd_codec.h"

#include <ArduinoJson.h>

#include <cstring>

#include "lift_core_version.h"

namespace {

const char* kProvisional[] = {
    "CEILING_MARGIN_MM",
    "CEILING_STALE_MS",
    "LIFT_MAX_RUN_MS",
};

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
    case StopReason::OWNER_GONE:
      return "OWNER_GONE";
    case StopReason::CEILING_NEAR:
      return "CEILING_NEAR";
    case StopReason::CEILING_STALE:
      return "CEILING_STALE";
    case StopReason::HEIGHT_UNKNOWN:
      return "HEIGHT_UNKNOWN";
    case StopReason::TOP:
      return "TOP";
    case StopReason::BOTTOM:
      return "BOTTOM";
    case StopReason::MAX_RUN:
      return "MAX_RUN";
    case StopReason::OUT_OF_RANGE:
      return "OUT_OF_RANGE";
    case StopReason::NONE:
      break;
  }
  return "NONE";
}

bool reason_from_name(const char* name, StopReason* out) {
  if (name == nullptr) {
    return false;
  }
  static const struct {
    const char* name;
    StopReason reason;
  } table[] = {
      {"NONE", StopReason::NONE},
      {"CMD_STOP", StopReason::CMD_STOP},
      {"CMD_TIMEOUT", StopReason::CMD_TIMEOUT},
      {"OWNER_GONE", StopReason::OWNER_GONE},
      {"CEILING_NEAR", StopReason::CEILING_NEAR},
      {"CEILING_STALE", StopReason::CEILING_STALE},
      {"HEIGHT_UNKNOWN", StopReason::HEIGHT_UNKNOWN},
      {"TOP", StopReason::TOP},
      {"BOTTOM", StopReason::BOTTOM},
      {"MAX_RUN", StopReason::MAX_RUN},
      {"OUT_OF_RANGE", StopReason::OUT_OF_RANGE},
  };
  for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); ++i) {
    if (std::strcmp(name, table[i].name) == 0) {
      *out = table[i].reason;
      return true;
    }
  }
  return false;
}

const char* ceiling_status_name(CeilingStatus status) {
  switch (status) {
    case CeilingStatus::MEASURED:
      return "MEASURED";
    case CeilingStatus::TOO_NEAR:
      return "TOO_NEAR";
    case CeilingStatus::NO_ECHO:
      return "NO_ECHO";
    case CeilingStatus::READ_ERROR:
      return "READ_ERROR";
    case CeilingStatus::MISSING:
      break;
  }
  return "MISSING";
}

bool ceiling_status_from_name(const char* name, CeilingStatus* out) {
  if (name == nullptr) {
    return false;
  }
  static const struct {
    const char* name;
    CeilingStatus status;
  } table[] = {
      {"MEASURED", CeilingStatus::MEASURED},
      {"TOO_NEAR", CeilingStatus::TOO_NEAR},
      {"NO_ECHO", CeilingStatus::NO_ECHO},
      {"READ_ERROR", CeilingStatus::READ_ERROR},
  };
  for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); ++i) {
    if (std::strcmp(name, table[i].name) == 0) {
      *out = table[i].status;
      return true;
    }
  }
  return false;  // 知らない値は MISSING（呼び出し側が付ける）
}

void copy_text_field(char* out, size_t out_size, JsonVariantConst value) {
  if (out_size == 0) {
    return;
  }
  out[0] = '\0';
  if (!value.is<const char*>()) {
    return;
  }
  const char* text = value.as<const char*>();
  if (text == nullptr) {
    return;
  }
  std::strncpy(out, text, out_size - 1);
  out[out_size - 1] = '\0';
}

// JSON テキストをオブジェクトとして読む。読めなければ false
bool read_object(const char* text, JsonDocument* doc, JsonObject* obj) {
  if (text == nullptr) {
    return false;
  }
  if (deserializeJson(*doc, text) != DeserializationError::Ok) {
    return false;
  }
  *obj = doc->as<JsonObject>();
  return !obj->isNull();
}

bool has_type(JsonObject obj, const char* type) {
  return obj["t"].is<const char*>() && std::strcmp(obj["t"].as<const char*>(), type) == 0;
}

}  // namespace

bool decode_hello(const char* text, HelloMsg* out) {
  *out = HelloMsg();  // 読めなかったときは「距離計を持つ」側にして返す
  JsonDocument doc;
  JsonObject obj;
  if (!read_object(text, &doc, &obj) || !has_type(obj, "hello")) {
    return false;
  }
  // ceiling_sensor は欠けている・真偽値でないとき true とみなす（§3.1）
  out->has_sensor = !obj["ceiling_sensor"].is<bool>() || obj["ceiling_sensor"].as<bool>();
  copy_text_field(out->name, sizeof(out->name), obj["name"]);
  copy_text_field(out->fw, sizeof(out->fw), obj["fw"]);
  return true;
}

bool decode_hold(const char* text, HoldMsg* out, uint32_t received_at_ms) {
  *out = HoldMsg();
  out->ceiling.received_at_ms = received_at_ms;
  JsonDocument doc;
  JsonObject obj;
  if (!read_object(text, &doc, &obj) || !has_type(obj, "hold")) {
    return false;
  }
  HoldMsg parsed;
  parsed.ceiling.received_at_ms = received_at_ms;
  if (!obj["press"].is<int>()) {
    return false;
  }
  parsed.press = obj["press"].as<int>();
  if (!obj["dir"].is<const char*>() || !dir_from_name(obj["dir"].as<const char*>(), &parsed.dir)) {
    return false;
  }
  if (!obj["duty"].is<int>()) {
    return false;
  }
  parsed.duty = obj["duty"].as<int>();

  // ceiling は距離計を持つ接続の hold だけ意味を持つ。
  // 欠けている・読めない・知らない status・型の違う中身は hold を捨てずに
  // MISSING として受け取る（捨てると前の hold の値で動き続けるので、その場で止める）
  parsed.has_ceiling = false;
  parsed.ceiling.status = CeilingStatus::MISSING;
  if (obj["ceiling"].is<JsonObject>()) {
    JsonObject ceiling = obj["ceiling"].as<JsonObject>();
    CeilingStatus status = CeilingStatus::MISSING;
    bool status_ok = ceiling["status"].is<const char*>() &&
                     ceiling_status_from_name(ceiling["status"].as<const char*>(), &status);
    const bool age_ok = ceiling["age_ms"].is<int>() && ceiling["age_ms"].as<int>() >= 0;
    if (status_ok && age_ok) {
      if (status == CeilingStatus::MEASURED) {
        if (ceiling["mm"].is<int>() && ceiling["mm"].as<int>() >= 0) {
          parsed.has_ceiling = true;
          parsed.ceiling.status = status;
          parsed.ceiling.mm = ceiling["mm"].as<int>();
          parsed.ceiling.age_ms = static_cast<uint32_t>(ceiling["age_ms"].as<int>());
        }
      } else {
        parsed.has_ceiling = true;
        parsed.ceiling.status = status;
        parsed.ceiling.mm = 0;
        parsed.ceiling.age_ms = static_cast<uint32_t>(ceiling["age_ms"].as<int>());
      }
    }
  }
  *out = parsed;
  return true;
}

bool decode_release(const char* text, ReleaseMsg* out) {
  *out = ReleaseMsg();
  JsonDocument doc;
  JsonObject obj;
  if (!read_object(text, &doc, &obj) || !has_type(obj, "release")) {
    return false;
  }
  if (!obj["press"].is<int>()) {
    return false;
  }
  out->press = obj["press"].as<int>();
  return true;
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
  obj["top_detect"] = state.top_detect;
  if (state.ceiling_present) {
    JsonObject ceiling = obj["ceiling"].to<JsonObject>();
    ceiling["used"] = state.ceiling_used;
    ceiling["status"] = ceiling_status_name(state.ceiling_status);
    if (state.ceiling_status == CeilingStatus::MEASURED) {
      ceiling["mm"] = state.ceiling_mm;
    } else {
      ceiling["mm"] = nullptr;
    }
    ceiling["age_ms"] = state.ceiling_age_ms;
    ceiling["ok"] = state.ceiling_ok;
    ceiling["reason"] = reason_name(state.ceiling_reason);
  } else {
    obj["ceiling"] = nullptr;
  }
  if (state.has_owner) {
    obj["owner"] = state.owner_kind == 1 ? "module" : "ui";
  } else {
    obj["owner"] = nullptr;
  }
  obj["ui_clients"] = state.ui_clients;
  JsonObject module = obj["module"].to<JsonObject>();
  module["connected"] = state.module_connected;
  if (state.module_connected) {
    module["ceiling_sensor"] = state.module_has_sensor;
    module["ip"] = state.module_ip;
    module["name"] = state.module_name;
  } else {
    module["ceiling_sensor"] = nullptr;
    module["ip"] = nullptr;
    module["name"] = nullptr;
  }
  JsonArray provisional = obj["provisional"].to<JsonArray>();
  for (size_t i = 0; i < sizeof(kProvisional) / sizeof(kProvisional[0]); ++i) {
    provisional.add(kProvisional[i]);
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
  JsonDocument doc;
  JsonObject obj;
  if (!read_object(text, &doc, &obj) || !has_type(obj, "state")) {
    return false;
  }
  if (!obj["seq"].is<int>() || !obj["dir"].is<const char*>() || !obj["duty"].is<int>() ||
      !obj["reason"].is<const char*>() || !obj["bottom"].is<bool>() ||
      !obj["height_mm"].is<int>() || !obj["height_ok"].is<bool>() ||
      !obj["top_detect"].is<bool>() || !obj["ui_clients"].is<int>() ||
      !obj["module"].is<JsonObject>() || !obj["cmd_age_ms"].is<int>()) {
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
  out->top_detect = obj["top_detect"].as<bool>();
  out->ui_clients = obj["ui_clients"].as<int>();
  out->cmd_age_ms = static_cast<uint32_t>(obj["cmd_age_ms"].as<int>());

  if (obj["ceiling"].is<JsonObject>()) {
    JsonObject ceiling = obj["ceiling"].as<JsonObject>();
    if (!ceiling["used"].is<bool>() || !ceiling["status"].is<const char*>() ||
        !ceiling["age_ms"].is<int>() || !ceiling["ok"].is<bool>() ||
        !ceiling["reason"].is<const char*>()) {
      return false;
    }
    if (!ceiling_status_from_name(ceiling["status"].as<const char*>(), &out->ceiling_status)) {
      // MISSING は encode しない値なので、読めたことにはならない
      if (std::strcmp(ceiling["status"].as<const char*>(), "MISSING") != 0) {
        return false;
      }
      out->ceiling_status = CeilingStatus::MISSING;
    }
    if (!reason_from_name(ceiling["reason"].as<const char*>(), &out->ceiling_reason)) {
      return false;
    }
    if (ceiling["mm"].is<int>()) {
      out->ceiling_mm = ceiling["mm"].as<int>();
    } else if (!ceiling["mm"].isNull()) {
      return false;
    }
    out->ceiling_used = ceiling["used"].as<bool>();
    out->ceiling_age_ms = static_cast<uint32_t>(ceiling["age_ms"].as<int>());
    out->ceiling_ok = ceiling["ok"].as<bool>();
    out->ceiling_present = true;
  } else if (!obj["ceiling"].isNull()) {
    return false;
  }

  if (obj["owner"].isNull()) {
    out->has_owner = false;
  } else if (obj["owner"].is<const char*>()) {
    const char* owner = obj["owner"].as<const char*>();
    if (std::strcmp(owner, "ui") == 0) {
      out->has_owner = true;
      out->owner_kind = 0;
    } else if (std::strcmp(owner, "module") == 0) {
      out->has_owner = true;
      out->owner_kind = 1;
    } else {
      return false;
    }
  } else {
    return false;
  }

  JsonObject module = obj["module"].as<JsonObject>();
  if (!module["connected"].is<bool>()) {
    return false;
  }
  out->module_connected = module["connected"].as<bool>();
  if (out->module_connected) {
    if (!module["ceiling_sensor"].is<bool>() || !module["ip"].is<const char*>() ||
        !module["name"].is<const char*>()) {
      return false;
    }
    out->module_has_sensor = module["ceiling_sensor"].as<bool>();
    copy_text_field(out->module_ip, sizeof(out->module_ip), module["ip"]);
    copy_text_field(out->module_name, sizeof(out->module_name), module["name"]);
  }
  return true;
}
