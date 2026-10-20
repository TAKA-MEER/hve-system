// Arduino UNO の実物（DetailedDesign.md §4.5・DetailedDesign-protocol.md §5）。
// Serial（D0/D1・IO_BAUD）で M 行を受けて IoController へ、SRF02 の測定ごとに C 行、
// 起動時に B 行を出す。組み立てて呼ぶだけで、判定（途絶・丸め）は IoController に任せる。
// Serial にデバッグの出力を出さない（UnitV2 へ流れる）。
#include <Arduino.h>
#include <avr/wdt.h>

#include "config.h"
#include "hal_uno.h"
#include "io_codec.h"
#include "io_controller.h"
#include "io_core_version.h"

namespace {

// B 行の fw（DetailedDesign-names.md §2。hve_cam_io の名と io_core の版を一緒に出す）
constexpr char kFwName[] = "hve_cam_io-";

HalUno g_hal;
IoController g_controller(&g_hal);

char g_line[IO_LINE_MAX + 1];
unsigned int g_len = 0;
bool g_discard = false;  // 長すぎる行・壊れた行の残りを \n まで捨てている間

void send_text(const char* s, unsigned int n) {
  Serial.write(reinterpret_cast<const uint8_t*>(s), n);
  Serial.write('\n');
}

void send_boot(uint32_t now_ms) {
  char fw[IO_LINE_MAX + 1];
  fw[0] = '\0';
  unsigned int pos = 0;
  for (const char* p = kFwName; *p != '\0' && pos + 1 < sizeof(fw); ++p) {
    fw[pos++] = *p;
  }
  for (const char* p = io_core_version(); *p != '\0' && pos + 1 < sizeof(fw); ++p) {
    fw[pos++] = *p;
  }
  fw[pos] = '\0';
  char buf[IO_LINE_MAX + 1];
  const unsigned int n = format_b_line(now_ms, fw, buf, sizeof(buf));
  if (n > 0) {
    send_text(buf, n);
  }
}

// 溜まった 1 行を渡す。読めない行は捨てる（生存確認を延ばさない）
void feed_line(char* line, uint32_t now_ms) {
  // 末尾の \r（\r\n で送る相手のため）を 1 つだけ落とす。途中にある \r は壊れた行
  unsigned int len = 0;
  while (line[len] != '\0') {
    ++len;
  }
  if (len > 0 && line[len - 1] == '\r') {
    line[len - 1] = '\0';
    --len;
  }
  for (unsigned int i = 0; i < len; ++i) {
    if (line[i] == '\r') {
      return;
    }
  }
  IoCmd cmd;
  if (parse_m_line(line, &cmd)) {
    g_controller.on_command(cmd, now_ms);
  }
}

void poll_serial(uint32_t now_ms) {
  while (Serial.available() > 0) {
    const int c = Serial.read();
    if (c < 0) {
      break;
    }
    if (c == '\n') {
      if (!g_discard) {
        g_line[g_len] = '\0';
        feed_line(g_line, now_ms);
      }
      g_len = 0;
      g_discard = false;
      continue;
    }
    if (g_discard) {
      continue;
    }
    if (g_len < IO_LINE_MAX) {
      g_line[g_len++] = static_cast<char>(c);
    } else {
      // IO_LINE_MAX を超えた行は読めない行。残りを \n まで捨てる
      g_discard = true;
    }
  }
}

}  // namespace

void setup() {
  Serial.begin(IO_BAUD);
  g_hal.begin();
  send_boot(millis());
  wdt_enable(IO_WDT_TIMEOUT);
}

void loop() {
  const uint32_t now_ms = millis();
  poll_serial(now_ms);
  g_controller.tick(now_ms);
  int st = 1;
  int cm = 0;
  if (g_hal.srf02_poll(now_ms, &st, &cm)) {
    char buf[IO_LINE_MAX + 1];
    const unsigned int n = format_c_line(now_ms, st, cm, buf, sizeof(buf));
    if (n > 0) {
      send_text(buf, n);
    }
  }
  wdt_reset();
}
