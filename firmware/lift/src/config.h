// 実物側（WP-LIFT-02）の定数の置き場。値は DetailedDesign-names.md §5、
// ピンは DetailedDesign-hardware.md §1。
// 判定に使う LIFT_CMD_TIMEOUT_MS / HEIGHT_STALE_MS / LIFT_TOP_MM /
// LIFT_MAX_RUN_MS / LIFT_DUTY_ABS_MAX_PCT は Arduino.h を include しない
// lib/lift_core/lift_decide.h にある。ここには実物側で使うものだけを書く。
#pragma once

#include <cstddef>
#include <cstdint>

// --- ピン（DetailedDesign-hardware.md §1。ストラッピングの 0・2・5・12・15 は避ける）---

// MD10C（DIR + PWM の 2 線）。先行試作と同じ
constexpr uint8_t MOTOR_DIR_PIN = 14;
constexpr uint8_t MOTOR_PWM_PIN = 32;
// DIR = HIGH で上昇するか。**実機配線で確認するまで仮**（false にすると逆向きになる）
constexpr bool MOTOR_DIR_UP_LEVEL = true;

// 下端リミットスイッチ。**仮**。INPUT_PULLUP + 常時閉（NC）配線
constexpr uint8_t BOTTOM_PIN = 27;

// HC-SR04。**仮**。TRIG は 3.3 V の出力でそのまま動く。
// ECHO は 5 V が返るので、分圧（例 1 kΩ / 2 kΩ）で 3.3 V に落としてから入れる
constexpr uint8_t SONAR_TRIG_PIN = 25;
constexpr uint8_t SONAR_ECHO_PIN = 26;

// --- PWM（先行試作と同じ。LEDC ch0 / 5 kHz / 8 bit）---

constexpr uint8_t PWM_CH = 0;
constexpr uint32_t PWM_FREQ_HZ = 5000;
constexpr uint8_t PWM_RESOLUTION = 8;

// --- HC-SR04 の測定周期（names.md §5。**仮**。1 回の測定に 60 ms 以上空ける）---

constexpr uint32_t SONAR_PERIOD_MS = 100;

// --- 状態の出し方（DetailedDesign-protocol.md §1・§2.3）---

constexpr uint32_t LIFT_STATE_PERIOD_MS = 100;  // **仮**
constexpr uint16_t LIFT_HTTP_PORT = 80;
constexpr const char* LIFT_WS_PATH = "/ws";
constexpr const char* LIFT_MDNS_NAME = "hve-lift";  // names.md §2

// state_encode のバッファ。**仮**（state の JSON は 200 バイト未満）
constexpr size_t LIFT_STATE_TEXT_MAX = 256;

// WiFi の状態を見る周期。**仮**（無線が落ちていたら張り直すだけなので短くなくてよい）
constexpr uint32_t LIFT_WIFI_POLL_MS = 5000;
