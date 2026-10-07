// 昇降部ファームの組み立て（WP-LIFT-04）。DetailedDesign.md §4.1 の
// 「main.cpp は組み立てて呼ぶだけ」に従い、判定も安全の判断もここに書かない。
// ここで持つのは WS の 2 つの口・HTTP（画面の配信と設定 API）・設定の NVS 保存・
// WS のタスクと loop() の受け渡しだけ。
// 判定そのものは lib/lift_core（LiftController / lift_decide）の中。
#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPAsyncWebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/portmacro.h>

#include <cstring>

#include "cmd_codec.h"
#include "config.h"
#include "hal_esp32.h"
#include "lift_arbiter.h"
#include "lift_controller.h"
#include "lift_decide.h"
#include "lift_settings.h"
#include "web_assets.h"  // 生成物（scripts/embed_web.py が web/ から作る）

// 無線 AP の SSID とパスワードはコードに直書きしない（DetailedDesign-names.md §2）。
// secrets.h が無い状態でもビルドは通す（空の値で組む。WiFi に繋がないだけ）。
#if __has_include("secrets.h")
#include "secrets.h"
#else
#warning "include/secrets.h が無いので WiFi には繋がない。secrets.h.example をコピーして作ること"
#define WIFI_SSID ""
#define WIFI_PASSWORD ""
#endif

namespace {

// 1 フレームの上限。hold・hello・release は 200 バイトに収まる。
// これを超えたフレームは読まずに捨てる（判定に使わないので安全側）。
constexpr size_t kFrameMax = 1024;
// PUT /api/settings の本文の上限。設定の JSON は 100 バイト前後。
constexpr size_t kSettingsBodyMax = 2048;
// NVS（LIFT_NVS_NAMESPACE の下）の設定の鍵。保存する形は lift_settings の JSON。
constexpr char kSettingsNvsKey[] = "settings";

AsyncWebServer server(LIFT_HTTP_PORT);
// 口はコードの定数。設定で変えられるようにしない（DetailedDesign.md §3.1）。
// /ws/ui = 画面・/ws/module = 上部モジュール。受け口は 1 つの on_ws_event で
// どちらの口かを見分けて、接続の種類を LiftController へ渡す。
AsyncWebSocket ws_ui(LIFT_WS_UI_PATH);
AsyncWebSocket ws_module(LIFT_WS_MODULE_PATH);

LiftEsp32Hal hal;

// 上端の閾値は LIFT_TOP_MM（names.md §5.1。未設定の -1 の間は上端で止めない。
// 実機で上昇を試すときは特に注意）
LiftController controller(&hal, LIFT_TOP_MM);

// 昇降の速度の設定（protocol §3）。検証は lift_settings、保存は NVS。
// 画面のスライダーの範囲・初期値として出すだけで、昇降部側で丸めには使わない。
LiftSettings g_settings;
bool g_using_defaults = true;

int g_state_seq = 0;  // state 送出ごとの連番（DetailedDesign-protocol.md §2.2）
StopReason g_logged_reason = StopReason::NONE;
bool g_have_logged_reason = false;

// --- 2 つの口の接続番号の付け替え ---
// 2 つの AsyncWebSocket が振る id が口ごとに重なるかもしれないので、
// main.cpp 側で通し番号（ours）を振り直す。LiftController・LiftArbiter は
// id だけで接続を見分ける（種類は判定に使うが、表引きには使わない）ため。
struct ConnSlot {
  bool used = false;
  AsyncWebSocket* ws = nullptr;
  uint32_t ws_id = 0;
  int ours = 0;
};

constexpr int kConnSlots = 12;  // UI 上限 4＋上部モジュール＋余り（LiftController の 8 枠より広く）
ConnSlot g_conns[kConnSlots];
int g_next_conn_id = 1;

ConnSlot* find_conn(AsyncWebSocket* ws, uint32_t ws_id) {
  for (int i = 0; i < kConnSlots; ++i) {
    if (g_conns[i].used && g_conns[i].ws == ws && g_conns[i].ws_id == ws_id) {
      return &g_conns[i];
    }
  }
  return nullptr;
}

ConnSlot* alloc_conn(AsyncWebSocket* ws, uint32_t ws_id) {
  if (find_conn(ws, ws_id) != nullptr) {
    return find_conn(ws, ws_id);
  }
  for (int i = 0; i < kConnSlots; ++i) {
    if (!g_conns[i].used) {
      g_conns[i].used = true;
      g_conns[i].ws = ws;
      g_conns[i].ws_id = ws_id;
      g_conns[i].ours = g_next_conn_id++;
      return &g_conns[i];
    }
  }
  return nullptr;
}

void free_conn(AsyncWebSocket* ws, uint32_t ws_id) {
  ConnSlot* slot = find_conn(ws, ws_id);
  if (slot != nullptr) {
    slot->used = false;
  }
}

ConnId controller_conn(AsyncWebSocket* ws, int ours) {
  ConnId conn;
  conn.id = ours;
  conn.kind = (ws == &ws_ui) ? ConnKind::UI : ConnKind::MODULE;
  return conn;
}

// --- WS のタスクと loop() のタスクの受け渡し ---
// AsyncWebSocket の受信コールバックは AsyncTCP のタスクで走る（loop() とは別のタスク）。
// LiftController の中身を loop() だけが触るようにして、WS 側は受け渡しの列へ
// 書き込むだけにする。ロックは portMUX の臨界区間。
enum class PendingType : uint8_t {
  NONE = 0,
  HOLD,
  RELEASE,
  HELLO,
  CLOSE,
};

struct PendingEvent {
  PendingType type = PendingType::NONE;
  ConnId conn;
  HoldMsg hold;
  HelloMsg hello;
  int press = 0;
  uint32_t at_ms = 0;
};

constexpr int kPendingDepth = 16;  // 2 つの口の hold（10 Hz ずつ）が重なっても溢れない深さ
PendingEvent g_queue[kPendingDepth];
int g_q_head = 0;
int g_q_tail = 0;
int g_q_count = 0;
portMUX_TYPE g_cmd_mux = portMUX_INITIALIZER_UNLOCKED;

// イベントを「loop() が受け取る」ために積む。WS のタスクからしか呼ばない。
// 溢れたら古いものから捨てる（新しい命令が古い命令に勝つ。§3.3 と同じ向き）。
void post_event(const PendingEvent& event) {
  portENTER_CRITICAL(&g_cmd_mux);
  if (g_q_count >= kPendingDepth) {
    g_q_head = (g_q_head + 1) % kPendingDepth;
    g_q_count--;
  }
  g_queue[g_q_tail] = event;
  g_q_tail = (g_q_tail + 1) % kPendingDepth;
  g_q_count++;
  portEXIT_CRITICAL(&g_cmd_mux);
}

// 積んであるイベントを 1 つ取り出す。取れなければ false。loop() からしか呼ばない
bool take_pending_event(PendingEvent* out) {
  portENTER_CRITICAL(&g_cmd_mux);
  const bool have = g_q_count > 0;
  if (have) {
    *out = g_queue[g_q_head];
    g_q_head = (g_q_head + 1) % kPendingDepth;
    g_q_count--;
  }
  portEXIT_CRITICAL(&g_cmd_mux);
  return have;
}

// JSON の t が want かだけ見る。読めない hello を「hello らしい」と拾うため
// （protocol §2.1。読めない hello は距離計を持つものとして扱う）。
bool frame_type_is(const char* text, const char* want) {
  JsonDocument doc;
  if (deserializeJson(doc, text) != DeserializationError::Ok) {
    return false;
  }
  const char* t = doc["t"];
  return t != nullptr && std::strcmp(t, want) == 0;
}

// 1 フレームを読む。hello ならその口に応じて閉じるか loop() へ渡す。
// hold / release は loop() へ渡す。読めないものは捨てる
void handle_frame(AsyncWebSocket* ws, AsyncWebSocketClient* client, const uint8_t* data,
                  size_t len) {
  ConnSlot* slot = find_conn(ws, client->id());
  if (slot == nullptr) {
    return;
  }
  if (len > kFrameMax) {
    return;
  }
  // WS のフレームは NUL 終端されていない。lift_core の decode_* は C 文字列なので
  // 写して終端する（終端せずに渡すとバッファの外まで読む）。
  static char text[kFrameMax + 1];  // AsyncTCP の 1 タスクからしか呼ばない
  std::memcpy(text, data, len);
  text[len] = '\0';
  const ConnId conn = controller_conn(ws, slot->ours);
  const bool is_ui = (ws == &ws_ui);

  HoldMsg hold;
  if (decode_hold(text, &hold, millis())) {
    PendingEvent event;
    event.type = PendingType::HOLD;
    event.conn = conn;
    event.hold = hold;
    event.at_ms = hold.ceiling.received_at_ms;  // 受け取った時刻（ウォッチドッグはここから数える）
    post_event(event);
    return;
  }
  ReleaseMsg release;
  if (decode_release(text, &release)) {
    PendingEvent event;
    event.type = PendingType::RELEASE;
    event.conn = conn;
    event.press = release.press;
    post_event(event);
    return;
  }
  HelloMsg hello;
  if (decode_hello(text, &hello)) {
    if (is_ui) {
      // /ws/ui で hello を受けたら、その接続を閉じる（DetailedDesign.md §3.1）
      Serial.printf("[ws] #%u sent hello on /ws/ui. closing\n", client->id());
      client->close();
      return;
    }
    PendingEvent event;
    event.type = PendingType::HELLO;
    event.conn = conn;
    event.hello = hello;
    post_event(event);
    return;
  }
  if (frame_type_is(text, "hello")) {
    // 読めない hello は「ceiling_sensor: true」として扱う（protocol §2.1）。
    // HelloMsg の既定値がそのまま「距離計を持つ」なので、そのまま渡す。
    if (is_ui) {
      Serial.printf("[ws] #%u sent unreadable hello on /ws/ui. closing\n", client->id());
      client->close();
      return;
    }
    PendingEvent event;
    event.type = PendingType::HELLO;
    event.conn = conn;
    event.hello = HelloMsg();
    post_event(event);
  }
  // 知らない t・読めないものは捨てる
}

void on_ws_event(AsyncWebSocket* ws, AsyncWebSocketClient* client, AwsEventType type, void* arg,
                 uint8_t* data, size_t len) {
  switch (type) {
    case WS_EVT_CONNECT: {
      ConnSlot* slot = alloc_conn(ws, client->id());
      if (slot == nullptr) {
        client->close();
        break;
      }
      // LIFT_UI_CLIENTS_MAX を超えた画面は閉じる（names.md §5.1。
      // count() にはいま繋いだ接続も入っている）。
      if (ws == &ws_ui && ws_ui.count() > static_cast<size_t>(LIFT_UI_CLIENTS_MAX)) {
        Serial.println("[ws] 画面が多すぎるので閉じる");
        free_conn(ws, client->id());
        client->close();
        break;
      }
      Serial.printf("[ws] #%u が繋がりました（%s)\n", client->id(),
                    ws == &ws_ui ? "ui" : "module");
      break;
    }
    case WS_EVT_DISCONNECT: {
      Serial.printf("[ws] #%u が切れました。その場で停止する\n", client->id());
      // 正常な切断は待たずに止める（DetailedDesign-protocol.md §1）
      ConnSlot* slot = find_conn(ws, client->id());
      if (slot != nullptr) {
        PendingEvent event;
        event.type = PendingType::CLOSE;
        event.conn = controller_conn(ws, slot->ours);
        post_event(event);
        free_conn(ws, client->id());
      }
      break;
    }
    case WS_EVT_DATA: {
      // 分割されたフレームとバイナリは読まない（protocol §2 は 1 フレーム 1 メッセージ）
      AwsFrameInfo* info = static_cast<AwsFrameInfo*>(arg);
      if (info == nullptr || !info->final || info->index != 0 || info->len != len ||
          info->opcode != WS_TEXT) {
        return;
      }
      handle_frame(ws, client, data, len);
      break;
    }
    default:
      break;
  }
}

// --- 画面の配信（embed_web.py の生成物。gzip 済み）---

struct WebAsset {
  const char* path;
  const uint8_t* data;
  size_t len;
  const char* type;
};

// 新しい画面ファイルを足したら、scripts/embed_web.py の FILES にも足す
const WebAsset kWebAssets[] = {
    {"/", WEB_INDEX_HTML_GZ, WEB_INDEX_HTML_GZ_LEN, "text/html"},
    {"/index.html", WEB_INDEX_HTML_GZ, WEB_INDEX_HTML_GZ_LEN, "text/html"},
    {"/app.js", WEB_APP_JS_GZ, WEB_APP_JS_GZ_LEN, "application/javascript"},
    {"/style.css", WEB_STYLE_CSS_GZ, WEB_STYLE_CSS_GZ_LEN, "text/css"},
};

void serve_web(AsyncWebServerRequest* request, const char* path) {
  for (size_t i = 0; i < sizeof(kWebAssets) / sizeof(kWebAssets[0]); ++i) {
    if (std::strcmp(path, kWebAssets[i].path) == 0) {
      AsyncWebServerResponse* response =
          request->beginResponse(200, kWebAssets[i].type, kWebAssets[i].data, kWebAssets[i].len);
      response->addHeader("Content-Encoding", "gzip");
      request->send(response);
      return;
    }
  }
  request->send(404, "text/plain", "not found");
}

// --- 設定 API（protocol §3）。検証は lift_settings、保存は NVS ---

void load_settings() {
  g_settings = LiftSettings();  // 既定値（names.md §5.1）
  g_using_defaults = true;
  Preferences prefs;
  if (!prefs.begin(LIFT_NVS_NAMESPACE, true)) {
    return;
  }
  const String saved = prefs.getString(kSettingsNvsKey, "");
  prefs.end();
  if (saved.length() == 0) {
    return;
  }
  LiftSettings parsed;
  // 読めない・検証を通らないときは既定値で動き、using_defaults を立てる
  if (lift_settings_from_json(saved.c_str(), &parsed)) {
    g_settings = parsed;
    g_using_defaults = false;
  }
}

bool store_settings(const LiftSettings& settings) {
  char json[128];
  if (lift_settings_to_json(settings, json, sizeof(json)) == 0) {
    return false;
  }
  Preferences prefs;
  if (!prefs.begin(LIFT_NVS_NAMESPACE, false)) {
    return false;
  }
  const bool ok = prefs.putString(kSettingsNvsKey, json) > 0;
  prefs.end();
  return ok;
}

void send_settings(AsyncWebServerRequest* request) {
  char inner[128];
  lift_settings_to_json(g_settings, inner, sizeof(inner));
  char body[192];
  std::snprintf(body, sizeof(body), "{\"settings\":%s,\"using_defaults\":%s}", inner,
                g_using_defaults ? "true" : "false");
  request->send(200, "application/json", body);
}

void send_settings_error(AsyncWebServerRequest* request, int code, const char* message) {
  char body[256];
  std::snprintf(body, sizeof(body), "{\"errors\":[\"%s\"]}", message);
  request->send(code, "application/json", body);
}

// PUT の本文をまるごと置き換える。検証に通らなければ 400 を返し、保存しない。
void apply_settings_put(AsyncWebServerRequest* request, const char* body_text) {
  JsonDocument doc;
  if (deserializeJson(doc, body_text) != DeserializationError::Ok || !doc.is<JsonObject>()) {
    send_settings_error(request, 400, "JSON が読めません");
    return;
  }
  const JsonObject obj = doc.as<JsonObject>();
  if (!obj["lift_up"].is<JsonObject>() || !obj["lift_down"].is<JsonObject>()) {
    send_settings_error(request, 400, "lift_up と lift_down をまるごと送ってください");
    return;
  }
  LiftSettings parsed;
  if (!lift_settings_from_json(body_text, &parsed)) {
    send_settings_error(request, 400, "下限 ≦ 初期値 ≦ 上限・0〜100 の整数にしてください");
    return;
  }
  if (!store_settings(parsed)) {
    send_settings_error(request, 500, "保存できませんでした");
    return;
  }
  g_settings = parsed;
  g_using_defaults = false;
  char inner[128];
  lift_settings_to_json(g_settings, inner, sizeof(inner));
  char body[160];
  std::snprintf(body, sizeof(body), "{\"settings\":%s}", inner);
  request->send(200, "application/json", body);
}

// PUT の本文の受け口。本文が来たら on_settings_body が集めて apply_settings_put へ渡す
struct SettingsUpload {
  String text;
  bool responded = false;
};

void on_settings_body(AsyncWebServerRequest* request, uint8_t* data, size_t len, size_t index,
                      size_t total) {
  if (request->_tempObject == nullptr) {
    request->_tempObject = new SettingsUpload();
  }
  SettingsUpload* upload = static_cast<SettingsUpload*>(request->_tempObject);
  if (upload->responded) {
    return;
  }
  if (total > kSettingsBodyMax) {
    upload->responded = true;
    send_settings_error(request, 400, "設定が大きすぎます");
    delete upload;
    request->_tempObject = nullptr;
    return;
  }
  if (index == 0) {
    upload->text = "";
  }
  upload->text.concat(reinterpret_cast<const char*>(data), len);
  if (index + len >= total) {
    upload->responded = true;
    const String body = upload->text;
    delete upload;
    request->_tempObject = nullptr;
    apply_settings_put(request, body.c_str());
  }
}

// state を両方の口へ送る。LIFT_STATE_PERIOD_MS ごとに loop() から呼ぶ
void broadcast_state(const LiftState& state) {
  if (ws_ui.count() == 0 && ws_module.count() == 0) {
    return;
  }
  char text[LIFT_STATE_TEXT_MAX];
  const size_t len = state_encode(state, text, sizeof(text));
  if (len == 0) {
    Serial.println("[ws] state がバッファに収まりませんでした");
    return;
  }
  // 1 通の state を両方の口へ同じ内容で出す（protocol §2.2）
  if (ws_ui.count() > 0) {
    ws_ui.textAll(text, len);
  }
  if (ws_module.count() > 0) {
    ws_module.textAll(text, len);
  }
}

// WiFi は待たない。繋がる前も loop() は回り、指令が来なければ lift_decide が止める
void start_wifi() {
  // SSID が無くても WiFi.mode() は呼ぶ。TCP/IP スタックがここで立ち上がるので、
  // 呼ばないまま server.begin() すると tcpip_api_call の "Invalid mbox" で落ちる
  WiFi.mode(WIFI_STA);
  if (WIFI_SSID[0] == '\0') {
    Serial.println("[wifi] SSID が無いので繋がない（loop は回り続ける）");
    return;
  }
  WiFi.setSleep(false);  // 応答を遅くしない
  WiFi.setHostname(LIFT_MDNS_NAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[wifi] %s に繋ぎに行く（待つのは loop() で確認する）\n", WIFI_SSID);
}

void start_mdns() {
  if (!MDNS.begin(LIFT_MDNS_NAME)) {
    Serial.println("[mdns] 始められませんでした（IP アドレスで直接つないでください）");
    return;
  }
  MDNS.addService("http", "tcp", LIFT_HTTP_PORT);
  Serial.printf("[mdns] http://%s.local:%u/（画面）\n", LIFT_MDNS_NAME, LIFT_HTTP_PORT);
}

// 無線が落ちていたら張り直す。mDNS は繋がったときに 1 回だけ始める
void poll_wifi(uint32_t now) {
  if (WIFI_SSID[0] == '\0') {
    return;
  }
  static uint32_t last_ms = 0;
  static bool mdns_started = false;
  if (now - last_ms < LIFT_WIFI_POLL_MS) {
    return;
  }
  last_ms = now;
  if (WiFi.status() == WL_CONNECTED) {
    if (!mdns_started) {
      start_mdns();
      mdns_started = true;
      Serial.print("[wifi] 接続しました IP: ");
      Serial.println(WiFi.localIP());
    }
    return;
  }
  Serial.println("[wifi] 切れています。繋ぎ直す");
  WiFi.reconnect();
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);  // シリアル出力の 1 行目が消えないように

  hal.begin();  // モータは停止から始める
  // 指令を一度も受けていないので、この 1 回は CMD_TIMEOUT で止まる
  controller.step(millis());

  load_settings();  // NVS から昇降の速度の設定を読む（無ければ既定値）

  // TCP/IP スタックは WiFi.mode() で立ち上がる。server.begin() より先にしないと
  // tcpip_api_call の "Invalid mbox" で落ちて再起動を繰り返す
  start_wifi();

  ws_ui.onEvent(on_ws_event);
  ws_module.onEvent(on_ws_event);
  server.addHandler(&ws_ui);
  server.addHandler(&ws_module);

  server.on("/", HTTP_GET, [](AsyncWebServerRequest* request) { serve_web(request, "/"); });
  server.on("/index.html", HTTP_GET,
            [](AsyncWebServerRequest* request) { serve_web(request, "/index.html"); });
  server.on("/app.js", HTTP_GET,
            [](AsyncWebServerRequest* request) { serve_web(request, "/app.js"); });
  server.on("/style.css", HTTP_GET,
            [](AsyncWebServerRequest* request) { serve_web(request, "/style.css"); });
  server.on("/api/settings", HTTP_GET,
            [](AsyncWebServerRequest* request) { send_settings(request); });
  // 本文が無い PUT はここで 400。ある分は on_settings_body が集めてから答える
  server.on(
      "/api/settings", HTTP_PUT,
      [](AsyncWebServerRequest* request) {
        if (request->contentLength() == 0) {
          send_settings_error(request, 400, "JSON が読めません");
        }
      },
      NULL, on_settings_body);
  server.begin();

  Serial.println("[lift] 起動しました");
}

void loop() {
  const uint32_t now = millis();

  // 1) WS のタスクが積んだイベントを全部受ける
  PendingEvent event;
  while (take_pending_event(&event)) {
    switch (event.type) {
      case PendingType::HOLD:
        controller.on_hold(event.conn, event.hold, event.at_ms);
        break;
      case PendingType::RELEASE:
        controller.on_release(event.conn, event.press);
        break;
      case PendingType::HELLO:
        // /ws/ui の hello は frame 側で閉じているので、ここに来るのは module のみ
        controller.on_hello(event.conn, event.hello);
        break;
      case PendingType::CLOSE:
        controller.on_close(event.conn.id);
        break;
      case PendingType::NONE:
        break;
    }
  }
  controller.set_ui_clients(static_cast<int>(ws_ui.count()));

  // 2) 超音波の測定を出す。loop() は止めない
  hal.poll(now);

  // 3) 判定してモータへ出し、状態を更新する
  LiftState state = controller.step(now);

  // 4) 停止理由が変わったときだけシリアルへ出す（10 Hz で毎回出すと追えない）
  if (!g_have_logged_reason || state.reason != g_logged_reason) {
    g_logged_reason = state.reason;
    g_have_logged_reason = true;
    Serial.printf("[lift] reason=%d dir=%d duty=%d\n", static_cast<int>(state.reason),
                  static_cast<int>(state.dir), state.duty);
  }

  // 5) 状態を両方の口へ送る
  static uint32_t last_state_ms = 0;
  if (now - last_state_ms >= LIFT_STATE_PERIOD_MS) {
    last_state_ms = now;
    state.seq = g_state_seq;
    g_state_seq++;
    broadcast_state(state);
  }

  poll_wifi(now);
  ws_ui.cleanupClients();
  ws_module.cleanupClients();
}
