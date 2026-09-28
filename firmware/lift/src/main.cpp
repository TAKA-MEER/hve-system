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
AsyncWebSocket ws(LIFT_WS_PATH);

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
// 1 か所へ書き込むだけにする。ロックは portMUX の臨界区間。コピーするのは LiftCmd
// （3 つの値）だけなので、臨界区間は数 us で終わる。
portMUX_TYPE g_cmd_mux = portMUX_INITIALIZER_UNLOCKED;
LiftCmd g_pending_cmd;        // 次の loop() が on_command に渡す指令
uint32_t g_pending_at_ms = 0; // **受け取った時刻**（ウォッチドッグはここから数える）
bool g_pending_valid = false;

// 指令を「次回の loop() が受け取る」ために積む。WS のタスクからしか呼ばない
void post_command(const LiftCmd& cmd, uint32_t at_ms) {
  portENTER_CRITICAL(&g_cmd_mux);
  g_pending_cmd = cmd;
  g_pending_at_ms = at_ms;
  g_pending_valid = true;
  portEXIT_CRITICAL(&g_cmd_mux);
}

// 積んである指令を 1 つ取り出す。取れなければ false
bool take_pending_command(LiftCmd* out, uint32_t* at_ms) {
  portENTER_CRITICAL(&g_cmd_mux);
  const bool have = g_pending_valid;
  if (have) {
    *out = g_pending_cmd;
    *at_ms = g_pending_at_ms;
    g_pending_valid = false;
  }
  portEXIT_CRITICAL(&g_cmd_mux);
  return have;
}

// カメラ部（または tools/lift_probe.py）からの cmd。壊れていても cmd_decode が
// 「止まる」側にして返すので、ここではその結果をそのまま積む
void handle_cmd_frame(const char* text, size_t len) {
  LiftCmd cmd;
  cmd_decode(text, &cmd);
  // 受け取った時刻で受け渡す。loop() に戻るまでの遅れでウォッチドッグを伸ばさない
  post_command(cmd, millis());
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
      LiftCmd stop;
      stop.dir = LiftDir::STOP;
      stop.duty = 0;
      stop.ceil_ok = false;
      post_command(stop, millis());
      break;
    }
    case WS_EVT_DATA: {
      // 分割されたフレームとバイナリは読まない（protocol §2 は 1 フレーム 1 メッセージ）
      AwsFrameInfo* info = static_cast<AwsFrameInfo*>(arg);
      if (info == nullptr || !info->final || info->index != 0 || info->len != len ||
          info->opcode != WS_TEXT) {
        return;
      }
      handle_cmd_frame(reinterpret_cast<const char*>(data), len);
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
  if (WIFI_SSID[0] == '\0') {
    Serial.println("[wifi] SSID が無いので繋がない（loop は回り続ける）");
    return;
  }
  WiFi.mode(WIFI_STA);
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
  Serial.printf("[mdns] http://%s.local:%u%s\n", LIFT_MDNS_NAME, LIFT_HTTP_PORT, LIFT_WS_PATH);
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

  ws.onEvent(on_ws_event);
  server.addHandler(&ws);
  server.begin();  // :80 は WS だけ。静的な画面は持たない（protocol §1）

  start_wifi();
  Serial.println("[lift] 起動しました");
}

void loop() {
  const uint32_t now = millis();

  // 1) WS のタスクが積んだ指令を受ける。取ってから 1 周期ぶん step する
  LiftCmd cmd;
  uint32_t at_ms = 0;
  if (take_pending_command(&cmd, &at_ms)) {
    controller.on_command(cmd, at_ms);
  }

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
