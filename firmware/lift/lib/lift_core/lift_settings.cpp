#include "lift_settings.h"

#include <ArduinoJson.h>

namespace {

bool axis_ok(const LiftAxisSettings& axis) {
  if (axis.min_mm_s < 0 || axis.min_mm_s > LIFT_SPEED_ABS_MAX_MM_S) {
    return false;
  }
  if (axis.max_mm_s < 0 || axis.max_mm_s > LIFT_SPEED_ABS_MAX_MM_S) {
    return false;
  }
  if (axis.init_mm_s < 0 || axis.init_mm_s > LIFT_SPEED_ABS_MAX_MM_S) {
    return false;
  }
  return axis.min_mm_s <= axis.init_mm_s && axis.init_mm_s <= axis.max_mm_s;
}

bool read_axis(JsonObject obj, const char* key, LiftAxisSettings* out) {
  if (!obj[key].is<JsonObject>()) {
    return false;
  }
  JsonObject axis = obj[key].as<JsonObject>();
  if (!axis["min"].is<int>() || !axis["max"].is<int>() || !axis["init"].is<int>()) {
    return false;
  }
  out->min_mm_s = axis["min"].as<int>();
  out->max_mm_s = axis["max"].as<int>();
  out->init_mm_s = axis["init"].as<int>();
  return true;
}

void write_axis(JsonObject obj, const char* key, const LiftAxisSettings& axis) {
  JsonObject child = obj[key].to<JsonObject>();
  child["min"] = axis.min_mm_s;
  child["max"] = axis.max_mm_s;
  child["init"] = axis.init_mm_s;
}

}  // namespace

bool validate_lift_settings(const LiftSettings& settings) {
  if (settings.motor_max_pct < LIFT_MOTOR_MAX_PCT_MIN ||
      settings.motor_max_pct > LIFT_DUTY_ABS_MAX_PCT) {
    return false;
  }
  return axis_ok(settings.up) && axis_ok(settings.down);
}

bool lift_settings_from_json(const char* text, LiftSettings* out) {
  *out = LiftSettings();
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
  LiftSettings parsed;
  if (!read_axis(obj, "lift_up", &parsed.up) || !read_axis(obj, "lift_down", &parsed.down)) {
    return false;
  }
  // motor_max_pct は必須。欠けた・整数でない・範囲外は通さない。
  // 真偽値は ArduinoJson で int として読めるので除く
  if (!obj["motor_max_pct"].is<int>() || obj["motor_max_pct"].is<bool>()) {
    return false;
  }
  parsed.motor_max_pct = obj["motor_max_pct"].as<int>();
  if (!validate_lift_settings(parsed)) {
    return false;
  }
  *out = parsed;
  return true;
}

size_t lift_settings_to_json(const LiftSettings& settings, char* out, size_t out_size) {
  if (out == nullptr || out_size == 0) {
    return 0;
  }
  JsonDocument doc;
  JsonObject obj = doc.to<JsonObject>();
  write_axis(obj, "lift_up", settings.up);
  write_axis(obj, "lift_down", settings.down);
  obj["motor_max_pct"] = settings.motor_max_pct;
  const size_t needed = measureJson(doc);
  if (needed >= out_size) {
    return 0;
  }
  return serializeJson(doc, out, out_size);
}
