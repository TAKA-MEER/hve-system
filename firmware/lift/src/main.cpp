// 昇降部ファームの組み立て（WP-LIFT-02）。DetailedDesign.md §4.1 の
// 「main.cpp は組み立てて呼ぶだけ」に従い、判定も安全の判断もここに書かない。
// ここで持つのは 4 つ:
//   WiFi へ繋ぐ・mDNS で名を晒す・WS サーバを出す・受け渡しの排他
// 判定そのものは lib/lift_core（LiftController / lift_decide）の中。
#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include <ESPmDNS.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/portmacro.h>

#include "cmd_codec.h"
#include "config.h"
#include "hal_esp32.h"
#include "lift_arbiter.h"
#include "lift_controller.h"
#include "lift_decide.h"

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

AsyncWebServer server(LIFT_HTTP_PORT);
// WP-LIFT-03 の時点では画面の口だけ出す。上部モジュールの口（/ws/module）は
// WP-LIFT-04 で足す（DetailedDesign-protocol.md §1）
AsyncWebSocket ws(LIFT_WS_UI_PATH);

LiftEsp32Hal hal;

// 上端の閾値は LIFT_TOP_MM（names.md §5。WP-MEAS-01 で決めるまで未設定なので、
// **未設定の間は上端で止めない**。実機で上昇を試すときは特に注意）
LiftController controller(&hal, LIFT_TOP_MM);

int g_state_seq = 0;        // state 送出ごとの連番（DetailedDesign-protocol.md §2.3）
StopReason g_logged_reason = StopReason::NONE;
bool g_have_logged_reason = false;

// --- WS のタスクと loop() のタスクの受け渡し ---
// AsyncWebSocket の受信コールバックは AsyncTCP のタスクで走る（loop() とは別のタスク）。
// LiftController の中身を loop() だけが触るようにして、WS 側は受け渡し用のスロット
// 1 か所へ書き込むだけにする。ロックは portMUX の臨界区間。
// （WP-LIFT-03 時点の最小の書き換え。2 つの口への分割は WP-LIFT-04）
enum class PendingType : uint8_t {
  NONE = 0,
  HOLD,
  RELEASE,
  CLOSE,
};

struct PendingEvent {
  PendingType type = PendingType::NONE;
  int conn_id = 0;
  HoldMsg hold;
  int press = 0;
  uint32_t at_ms = 0;
};

portMUX_TYPE g_cmd_mux = portMUX_INITIALIZER_UNLOCKED;
PendingEvent g_pending;  // 次の loop() が取り出すイベント（1 件ぶん）

// イベントを「次回の loop() が受け取る」ために積む。WS のタスクからしか呼ばない
void post_event(const PendingEvent& event) {
  portENTER_CRITICAL(&g_cmd_mux);
  g_pending = event;
  portEXIT_CRITICAL(&g_cmd_mux);
}

// 積んであるイベントを 1 つ取り出す。取れなければ false
bool take_pending_event(PendingEvent* out) {
  portENTER_CRITICAL(&g_cmd_mux);
  const bool have = g_pending.type != PendingType::NONE;
  if (have) {
    *out = g_pending;
    g_pending.type = PendingType::NONE;
  }
  portEXIT_CRITICAL(&g_cmd_mux);
  return have;
}

ConnId ui_conn(int id) {
  ConnId conn;
  conn.id = id;
  conn.kind = ConnKind::UI;
  return conn;
}

// 画面からの 1 フレーム。hello ならその接続を閉じる（DetailedDesign.md §3.1）。
// hold / release は loop() へ渡す。読めないものは捨てる
void handle_ui_frame(AsyncWebSocketClient* client, const char* text) {
  const int id = static_cast<int>(client->id());
  HoldMsg hold;
  if (decode_hold(text, &hold, millis())) {
    PendingEvent event;
    event.type = PendingType::HOLD;
    event.conn_id = id;
    event.hold = hold;
    event.at_ms = hold.ceiling.received_at_ms;  // 受け取った時刻（ウォッチドッグはここから数える）
    post_event(event);
    return;
  }
  ReleaseMsg release;
  if (decode_release(text, &release)) {
    PendingEvent event;
    event.type = PendingType::RELEASE;
    event.conn_id = id;
    event.press = release.press;
    post_event(event);
    return;
  }
  HelloMsg hello;
  if (decode_hello(text, &hello)) {
    // /ws/ui で hello を受けたら、その接続を閉じる
    Serial.printf("[ws] #%u sent hello on /ws/ui. closing\n", client->id());
    client->close();
  }
}

void on_ws_event(AsyncWebSocket* /*server*/, AsyncWebSocketClient* client, AwsEventType type,
                 void* arg, uint8_t* data, size_t len) {
  switch (type) {
    case WS_EVT_CONNECT: {
      Serial.printf("[ws] #%u が繋がりました\n", client->id());
      break;
    }
    case WS_EVT_DISCONNECT: {
      Serial.printf("[ws] #%u が切れました。その場で停止する\n", client->id());
      // 正常な切断は待たずに止める（DetailedDesign-protocol.md §1）
      PendingEvent event;
      event.type = PendingType::CLOSE;
      event.conn_id = static_cast<int>(client->id());
      post_event(event);
      break;
    }
    case WS_EVT_DATA: {
      // 分割されたフレームとバイナリは読まない（protocol §2 は 1 フレーム 1 メッセージ）
      AwsFrameInfo* info = static_cast<AwsFrameInfo*>(arg);
      if (info == nullptr || !info->final || info->index != 0 || info->len != len ||
          info->opcode != WS_TEXT) {
        return;
      }
      handle_ui_frame(client, reinterpret_cast<const char*>(data));
      break;
    }
    default:
      break;
  }
}

// state を全クライアントへ送る。LIFT_STATE_PERIOD_MS ごとに loop() から呼ぶ
void broadcast_state(const LiftState& state) {
  if (ws.count() == 0) {
    return;
  }
  char text[LIFT_STATE_TEXT_MAX];
  const size_t len = state_encode(state, text, sizeof(text));
  if (len == 0) {
    Serial.println("[ws] state がバッファに収まりませんでした");
    return;
  }
  ws.textAll(text, len);
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
  Serial.printf("[mdns] http://%s.local:%u%s\n", LIFT_MDNS_NAME, LIFT_HTTP_PORT, LIFT_WS_UI_PATH);
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

  // TCP/IP スタックは WiFi.mode() で立ち上がる。server.begin() より先にしないと
  // tcpip_api_call の "Invalid mbox" で落ちて再起動を繰り返す
  start_wifi();

  ws.onEvent(on_ws_event);
  server.addHandler(&ws);
  server.begin();  // :80 は WS だけ。静的な画面は持たない（protocol §1）

  Serial.println("[lift] 起動しました");
}

void loop() {
  const uint32_t now = millis();

  // 1) WS のタスクが積んだイベントを受ける
  PendingEvent event;
  if (take_pending_event(&event)) {
    switch (event.type) {
      case PendingType::HOLD:
        controller.on_hold(ui_conn(event.conn_id), event.hold, event.at_ms);
        break;
      case PendingType::RELEASE:
        controller.on_release(ui_conn(event.conn_id), event.press);
        break;
      case PendingType::CLOSE:
        controller.on_close(event.conn_id);
        break;
      case PendingType::NONE:
        break;
    }
  }
  controller.set_ui_clients(static_cast<int>(ws.count()));

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

  // 5) 状態を全クライアントへ送る
  static uint32_t last_state_ms = 0;
  if (now - last_state_ms >= LIFT_STATE_PERIOD_MS) {
    last_state_ms = now;
    state.seq = g_state_seq;
    g_state_seq++;
    broadcast_state(state);
  }

  poll_wifi(now);
  ws.cleanupClients();
}
