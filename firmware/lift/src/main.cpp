// 実物の MD10C・スイッチ・超音波・WiFi・WS は WP-LIFT-02 で組む。
// 今は何も駆動しない（DetailedDesign-packets.md §1 の WP-LIFT-02）。
#include <Arduino.h>

#include "config.h"

// lift_core をここで include して LDF の依存に乗せる。こうしないと
// `pio run -e esp32dev` が lift_core をビルドしないので、
// 判定の層が ESP32 向けにも通ることを確かめられない（2026-09-28 実測）。
// ここで動かすのは WP-LIFT-02 から。
#include "cmd_codec.h"
#include "lift_controller.h"
#include "lift_decide.h"

void setup() {
}

void loop() {
  delay(1000);
}
