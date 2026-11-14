#include "cmd_codec.h"

#include <ArduinoJson.h>

#include <cstring>

#include "lift_core_version.h"

namespace {

const char* kProvisional[] = {
    "CEILING_STALE_MS",
    "LIFT_MAX_RUN_PCT",
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
    case StopReason::ENCODER_STALL:
      return "ENCODER_STALL";
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
      {"ENCODER_STALL", StopReason::ENCODER_STALL},
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

const char* standalone_ceiling_name(StandaloneCeiling value) {
  switch (value) {
    case StandaloneCeiling::USE:
      return "USE";
    case StandaloneCeiling::SETTING_OFF:
      return "SETTING_OFF";
    case StandaloneCeiling::NO_SOURCE:
      break;
  }
  return "NO_SOURCE";
}

bool standalone_ceiling_from_name(const char* name, StandaloneCeiling* out) {
  if (name == nullptr) {
    return false;
  }
  if (std::strcmp(name, "USE") == 0) {
    *out = StandaloneCeiling::USE;
    return true;
  }
  if (std::strcmp(name, "SETTING_OFF") == 0) {
    *out = StandaloneCeiling::SETTING_OFF;
    return true;
  }
  if (std::strcmp(name, "NO_SOURCE") == 0) {
    *out = StandaloneCeiling::NO_SOURCE;
    return true;
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

// 名前は英小文字・数字・`-` の 1〜16 文字だけ（protocol-modules §1.1）。
// 形に合わなければ名前無しとして扱う（置き換えの対象にしない）
bool valid_module_name(const char* text) {
  if (text == nullptr) {
    return false;
  }
  const size_t len = std::strlen(text);
  if (len < 1 || len > 16) {
    return false;
  }
  for (size_t i = 0; i < len; ++i) {
    const char c = text[i];
    if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-') {
      continue;
    }
    return false;
  }
  return true;
}

// busy_label は UTF-8 で 1〜48 バイト。超えた・空・文字列でないときは無しとして扱い、
// 切り詰めない（マルチバイトの途中で切ると state に壊れた UTF-8 が載る）
void copy_busy_label(char* out, size_t out_size, JsonVariantConst value) {
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
  const size_t len = std::strlen(text);
  if (len < 1 || len > 48) {
    return;
  }
  std::strncpy(out, text, out_size - 1);
  out[out_size - 1] = '\0';
}

const char* module_role_name(ModuleRole role) {
  return role == ModuleRole::OBSERVE ? "observe" : "control";
}

const char* module_operator_name(ModuleOperator op) {
  return op == ModuleOperator::AUTO ? "auto" : "human";
}

// ceiling の中身（status・mm・age_ms）を読む。読めなければ false。
// 呼び出し側は false のとき MISSING のままにする
bool read_ceiling(JsonVariantConst value, CeilingReport* out, uint32_t received_at_ms) {
  out->status = CeilingStatus::MISSING;
  out->mm = 0;
  out->age_ms = 0;
  out->received_at_ms = received_at_ms;
  if (!value.is<JsonObjectConst>()) {
    return false;
  }
  JsonObjectConst ceiling = value.as<JsonObjectConst>();
  CeilingStatus status = CeilingStatus::MISSING;
  if (!ceiling["status"].is<const char*>() ||
      !ceiling_status_from_name(ceiling["status"].as<const char*>(), &status)) {
    return false;
  }
  if (!ceiling["age_ms"].is<int>() || ceiling["age_ms"].as<int>() < 0) {
    return false;
  }
  if (status == CeilingStatus::MEASURED) {
    if (!ceiling["mm"].is<int>() || ceiling["mm"].as<int>() < 0) {
      return false;
    }
    out->mm = ceiling["mm"].as<int>();
  }
  out->status = status;
  out->age_ms = static_cast<uint32_t>(ceiling["age_ms"].as<int>());
  return true;
}

}  // namespace

bool decode_hello(const char* text, HelloMsg* out) {
  *out = HelloMsg();  // 読めなかったときは「距離計を持つ」側にして返す
  JsonDocument doc;
  JsonObject obj;
  if (!read_object(text, &doc, &obj) || !has_type(obj, "hello")) {
    return false;
  }
  // ceiling_sensor は欠けている・真偽値でないとき true とみなす（§3.1）。
  // ほかの接続に貸す値の出どころにできるのは、true を明示した接続だけ（§2.3）
  out->has_sensor = !obj["ceiling_sensor"].is<bool>() || obj["ceiling_sensor"].as<bool>();
  out->sensor_explicit = obj["ceiling_sensor"].is<bool>();
  // role は欠けていれば control、知らない値は observe（命じられない側に倒す）
  if (obj["role"].is<const char*>()) {
    out->role = std::strcmp(obj["role"].as<const char*>(), "control") == 0
                    ? ModuleRole::CONTROL
                    : ModuleRole::OBSERVE;
  } else {
    out->role = ModuleRole::CONTROL;
  }
  // operator は欠けている・知らない値を human（自動と名乗っていないものは人として扱う）
  out->op = (obj["operator"].is<const char*>() &&
             std::strcmp(obj["operator"].as<const char*>(), "auto") == 0)
                ? ModuleOperator::AUTO
                : ModuleOperator::HUMAN;
  if (obj["name"].is<const char*>() && valid_module_name(obj["name"].as<const char*>())) {
    copy_text_field(out->name, sizeof(out->name), obj["name"]);
  }
  copy_text_field(out->fw, sizeof(out->fw), obj["fw"]);
  copy_busy_label(out->busy_label, sizeof(out->busy_label), obj["busy_label"]);
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
  // speed（0 以上の整数）を読む。duty だけの hold は読めない hold として捨てる
  if (!obj["speed"].is<int>() || obj["speed"].as<int>() < 0) {
    return false;
  }
  parsed.speed = obj["speed"].as<int>();

  // ceiling は距離計を持つ接続の hold だけ意味を持つ。
  // 欠けている・読めない・知らない status・型の違う中身は hold を捨てずに
  // MISSING として受け取る（捨てると前の hold の値で動き続けるので、その場で止める）
  parsed.has_ceiling = read_ceiling(obj["ceiling"], &parsed.ceiling, received_at_ms);
  *out = parsed;
  return true;
}

bool decode_ceiling(const char* text, CeilingReport* out, uint32_t received_at_ms) {
  *out = CeilingReport();
  out->received_at_ms = received_at_ms;
  JsonDocument doc;
  JsonObject obj;
  if (!read_object(text, &doc, &obj) || !has_type(obj, "ceiling")) {
    return false;
  }
  return read_ceiling(obj, out, received_at_ms);
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
  obj["speed"] = state.speed;
  obj["speed_meas"] = state.speed_meas;
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
    // 判定に使った値を送ってきた上部モジュールの名前（無ければ null）
    if (state.ceiling_from[0] != '\0') {
      ceiling["from"] = state.ceiling_from;
    } else {
      ceiling["from"] = nullptr;
    }
    ceiling["standalone"] = standalone_ceiling_name(state.ceiling_standalone);
  } else {
    // 天井の値が無くても、単体の操作の天井（標準で押す前から画面が出す）は毎周期出す。
    // 値の欄（used・status など）は無い
    JsonObject ceiling = obj["ceiling"].to<JsonObject>();
    ceiling["standalone"] = standalone_ceiling_name(state.ceiling_standalone);
  }
  if (state.has_owner) {
    obj["owner"] = state.owner_kind == 1 ? "module" : state.owner_kind == 2 ? "button" : "ui";
  } else {
    obj["owner"] = nullptr;
  }
  // 持ち主の名前（module のときだけ）と、人か自動か（持ち主がいるときだけ）
  if (state.has_owner && state.owner_kind == 1 && state.owner_name[0] != '\0') {
    obj["owner_name"] = state.owner_name;
  } else {
    obj["owner_name"] = nullptr;
  }
  if (state.has_owner) {
    obj["owner_operator"] = module_operator_name(state.owner_operator);
  } else {
    obj["owner_operator"] = nullptr;
  }
  obj["homed"] = state.homed;
  obj["ui_clients"] = state.ui_clients;
  JsonArray modules = obj["modules"].to<JsonArray>();
  const int module_count = state.module_count < LIFT_MODULE_LIST_MAX ? state.module_count
                                                                      : LIFT_MODULE_LIST_MAX;
  for (int i = 0; i < module_count; ++i) {
    const ModuleInfo& info = state.modules[i];
    JsonObject item = modules.add<JsonObject>();
    item["name"] = info.name;
    item["role"] = module_role_name(info.role);
    item["operator"] = module_operator_name(info.op);
    item["ceiling_sensor"] = info.ceiling_sensor;
    if (info.busy_label[0] != '\0') {
      item["busy_label"] = info.busy_label;
    } else {
      item["busy_label"] = nullptr;
    }
    if (info.ip[0] != '\0') {
      item["ip"] = info.ip;
    } else {
      item["ip"] = nullptr;
    }
    item["fw"] = info.fw;
  }
  // 測定機構（spec §3）。中身はアームの制御器とのつなぎ方（spec H-M7）が決まるまで、
  // すべて null（「付いていない」と読まれないように。false を出さない）
  JsonObject arm = obj["arm"].to<JsonObject>();
  arm["attached"] = nullptr;
  arm["link"] = nullptr;
  arm["confirm"] = nullptr;
  arm["out"] = nullptr;
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

// state の ceiling のうち、値の欄（used・status・mm・age_ms・ok・reason・from）を読む
static bool read_state_ceiling_values(JsonObject ceiling, LiftState* out) {
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
  if (ceiling["from"].is<const char*>()) {
    copy_text_field(out->ceiling_from, sizeof(out->ceiling_from), ceiling["from"]);
  } else if (!ceiling["from"].isNull()) {
    return false;
  }
  return true;
}

bool state_decode(const char* text, LiftState* out) {
  *out = LiftState();
  JsonDocument doc;
  JsonObject obj;
  if (!read_object(text, &doc, &obj) || !has_type(obj, "state")) {
    return false;
  }
  if (!obj["seq"].is<int>() || !obj["dir"].is<const char*>() || !obj["duty"].is<int>() ||
      !obj["speed"].is<int>() || !obj["speed_meas"].is<int>() ||
      !obj["reason"].is<const char*>() || !obj["bottom"].is<bool>() ||
      !obj["height_mm"].is<int>() || !obj["height_ok"].is<bool>() ||
      !obj["top_detect"].is<bool>() || !obj["ui_clients"].is<int>() ||
      !obj["modules"].is<JsonArray>() || !obj["homed"].is<bool>() ||
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
  out->speed = obj["speed"].as<int>();
  out->speed_meas = obj["speed_meas"].as<int>();
  out->bottom = obj["bottom"].as<bool>();
  out->height_mm = obj["height_mm"].as<int>();
  out->height_ok = obj["height_ok"].as<bool>();
  out->top_detect = obj["top_detect"].as<bool>();
  out->ui_clients = obj["ui_clients"].as<int>();
  out->cmd_age_ms = static_cast<uint32_t>(obj["cmd_age_ms"].as<int>());

  if (obj["ceiling"].is<JsonObject>()) {
    JsonObject ceiling = obj["ceiling"].as<JsonObject>();
    // standalone は足すだけの欄。欠けていれば今までの形（読み手が無視してよい）
    if (ceiling["standalone"].is<const char*>()) {
      if (!standalone_ceiling_from_name(ceiling["standalone"].as<const char*>(),
                                        &out->ceiling_standalone)) {
        return false;
      }
    } else if (!ceiling["standalone"].isNull()) {
      return false;
    }
    // 値の欄（status）が無く standalone だけの形は「天井の値は無い」（ceiling_present は偽）
    const bool value_less = ceiling["status"].isNull() && !ceiling["standalone"].isNull();
    if (!value_less) {
      if (!read_state_ceiling_values(ceiling, out)) {
        return false;
      }
      out->ceiling_present = true;
    }
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
    } else if (std::strcmp(owner, "button") == 0) {
      out->has_owner = true;
      out->owner_kind = 2;
    } else {
      return false;
    }
  } else {
    return false;
  }

  out->homed = obj["homed"].as<bool>();
  if (obj["owner_name"].is<const char*>()) {
    copy_text_field(out->owner_name, sizeof(out->owner_name), obj["owner_name"]);
  } else if (!obj["owner_name"].isNull()) {
    return false;
  }
  if (obj["owner_operator"].is<const char*>()) {
    const char* op = obj["owner_operator"].as<const char*>();
    if (std::strcmp(op, "auto") == 0) {
      out->owner_operator = ModuleOperator::AUTO;
    } else if (std::strcmp(op, "human") == 0) {
      out->owner_operator = ModuleOperator::HUMAN;
    } else {
      return false;
    }
  } else if (!obj["owner_operator"].isNull()) {
    return false;
  }

  JsonArray modules = obj["modules"].as<JsonArray>();
  if (modules.size() > static_cast<size_t>(LIFT_MODULE_LIST_MAX)) {
    return false;
  }
  for (JsonObject item : modules) {
    if (!item["name"].is<const char*>() || !item["role"].is<const char*>() ||
        !item["operator"].is<const char*>() || !item["ceiling_sensor"].is<bool>() ||
        !item["fw"].is<const char*>()) {
      return false;
    }
    ModuleInfo& info = out->modules[out->module_count];
    copy_text_field(info.name, sizeof(info.name), item["name"]);
    const char* role = item["role"].as<const char*>();
    if (std::strcmp(role, "control") == 0) {
      info.role = ModuleRole::CONTROL;
    } else if (std::strcmp(role, "observe") == 0) {
      info.role = ModuleRole::OBSERVE;
    } else {
      return false;
    }
    const char* op = item["operator"].as<const char*>();
    if (std::strcmp(op, "auto") == 0) {
      info.op = ModuleOperator::AUTO;
    } else if (std::strcmp(op, "human") == 0) {
      info.op = ModuleOperator::HUMAN;
    } else {
      return false;
    }
    info.ceiling_sensor = item["ceiling_sensor"].as<bool>();
    copy_text_field(info.busy_label, sizeof(info.busy_label), item["busy_label"]);
    copy_text_field(info.ip, sizeof(info.ip), item["ip"]);
    copy_text_field(info.fw, sizeof(info.fw), item["fw"]);
    out->module_count++;
  }
  return true;
}
