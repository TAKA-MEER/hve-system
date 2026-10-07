// UART の行の読み取り（M）・組み立て（C・B）（DetailedDesign-protocol.md §5）。
// 読めない行は捨てる（false・0 を返し、呼び出し側は生存確認を延ばさない）。
// Arduino.h は include しない（DetailedDesign-names.md §1）。
#pragma once

#include <cstddef>
#include <cstdint>

// UART の速さ [bps]（DetailedDesign-names.md §5.4）。UnitV2 の io_baud と揃える
constexpr int IO_BAUD = 115200;
// 1 行の長さの上限（DetailedDesign-protocol.md §5）。`M 65535 -1800 -680` が 20 文字
constexpr unsigned int IO_LINE_MAX = 32;

// UnitV2 からの指令（DetailedDesign-protocol.md §5 の M 行）。
// seq はログ用で判定には使わない。範囲外の値もここでは受け付け、丸めは IoController の役
struct IoCmd {
  unsigned int seq = 0;   // 0〜65535 で一周する連番
  int pitch_ddeg = 0;     // ピッチの目標角 [0.1°]
  int32_t yaw_hsps = 0;   // ヨーの半ステップの速さ [1 秒あたり]（符号が向き。0 で止める）
};

// M 行を読む。読めたら true。読めなければ out を 0 にして false を返す
bool parse_m_line(const char* line, IoCmd* out);

// C 行（天井の測定 1 回）を作る。書き込んだ文字数（終端なし）を返す。
// バッファが足りない・IO_LINE_MAX を超える・fw が読めないときは 0
unsigned int format_c_line(uint32_t uno_ms, int st, int cm, char* out, unsigned int out_size);
unsigned int format_b_line(uint32_t uno_ms, const char* fw, char* out, unsigned int out_size);
