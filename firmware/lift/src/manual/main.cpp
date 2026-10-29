// 組み立て用ファーム（env:esp32dev_manual）。昇降機のメカを組み立てるあいだ、
// <on>-off-<on> のモーメンタリスイッチでモータを回すだけ。無線・画面・高さは使わない。
// 判定は lib/manual_switch。ここは読んで渡し、結果をそのままモータへ出す。
//
// WAIVER(demo): W-2 下端・ウォッチドッグ・連続駆動の上限・上端のどれも効かない組み立て用ファーム
// （docs/plan/EXCEPTION-LEDGER.md）。止めるのはスイッチを離す・電源を切る（th-system の非常停止）だけ。
// 組み立てが終わったら本番のファーム（env:esp32dev）を書き戻す。
#include <Arduino.h>

#include "../config.h"
#include "hal_core.h"
#include "manual_switch.h"

namespace {

ManualSwitch g_switch;
ManualDir g_applied = ManualDir::STOP;

const char* dir_name(ManualDir dir) {
  switch (dir) {
    case ManualDir::UP:
      return "UP";
    case ManualDir::DOWN:
      return "DOWN";
    default:
      return "STOP";
  }
}

void motor_apply(ManualDir dir) {
  if (dir == ManualDir::STOP) {
    ledcWrite(PWM_CH, 0);
    return;
  }
  const bool up = (dir == ManualDir::UP);
  digitalWrite(MOTOR_DIR_PIN, (up == MOTOR_DIR_UP_LEVEL) ? HIGH : LOW);
  ledcWrite(PWM_CH, motor_duty_to_pwm(MANUAL_DUTY_PCT, PWM_RESOLUTION));
}

}  // namespace

void setup() {
  Serial.begin(115200);

  // 起動直後は必ず停止（本番の hal_esp32 の begin() と同じ）
  pinMode(MOTOR_DIR_PIN, OUTPUT);
  digitalWrite(MOTOR_DIR_PIN, LOW);
  ledcSetup(PWM_CH, PWM_FREQ_HZ, PWM_RESOLUTION);
  ledcAttachPin(MOTOR_PWM_PIN, PWM_CH);
  ledcWrite(PWM_CH, 0);

  // スイッチの共通端子は GND。入っている側が LOW
  pinMode(LIFT_SW_UP_PIN, INPUT_PULLUP);
  pinMode(LIFT_SW_DOWN_PIN, INPUT_PULLUP);

  g_switch = ManualSwitch(millis());
  Serial.printf("[manual] 組み立て用ファーム（安全装置なし・W-2）。デューティ %d%%\n",
                MANUAL_DUTY_PCT);
}

void loop() {
  const ManualDir dir =
      g_switch.update(digitalRead(LIFT_SW_UP_PIN), digitalRead(LIFT_SW_DOWN_PIN), millis());
  if (dir != g_applied) {
    motor_apply(dir);
    g_applied = dir;
    Serial.printf("[manual] %s\n", dir_name(dir));
  }
  delay(1);
}
