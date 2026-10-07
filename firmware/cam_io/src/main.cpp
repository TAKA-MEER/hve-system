// Arduino UNO のファームの骨格（DetailedDesign.md §4.5）。何も駆動しない。
// io_core のヘッダを include だけする（使うのは WP-IO-02）。
// uno のビルドで io_core の .cpp までコンパイルされるように縛るための結線で、
// avr-gcc（C++ 標準ライブラリなし・int が 16 bit）で読めることの検査を兼ねる。
#include <Arduino.h>

#include "io_codec.h"
#include "io_controller.h"
#include "step_rate.h"

void setup() {
}

void loop() {
}
