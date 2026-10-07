// UART の行（DetailedDesign-protocol.md §5）の読み取りと組み立て。
// 形は `M <seq> <pitch_ddeg> <yaw_hsps>`・`C <uno_ms> <st> <cm>`・`B <uno_ms> <fw>`。
// 区切りは空白 1 つ。読めない行は捨てる（false・0。生存確認を延ばさない）。
#include "io_codec.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

// `p` が 10 進の整数（`[+-]?[0-9]+`）か。読めた桁数の先を end に入れる
bool take_int(const char* p, long* value, const char** end) {
  if (p == nullptr || *p == '\0') {
    return false;
  }
  const char* q = p;
  if (*q == '+' || *q == '-') {
    ++q;
  }
  if (*q < '0' || *q > '9') {
    return false;
  }
  char* stop = nullptr;
  const long parsed = std::strtol(p, &stop, 10);
  if (stop == p) {
    return false;
  }
  *value = parsed;
  *end = stop;
  return true;
}

// 32 bit に収まるか（AVR の long は 32 bit、ホストの long は 64 bit のため）
bool fits_int32(long value) { return value >= -2147483647L - 1 && value <= 2147483647L; }

}  // namespace

bool parse_m_line(const char* line, IoCmd* out) {
  IoCmd rejected;  // 最後まで読めなかったときは 0 にして返す
  if (out == nullptr) {
    return false;
  }
  *out = rejected;
  if (line == nullptr) {
    return false;
  }
  if (std::strlen(line) > IO_LINE_MAX) {
    return false;
  }
  // 頭文字・区切りはきっちり見る（空白 1 つ。改行が混ざった行も捨てる）
  if (line[0] != 'M' || line[1] != ' ') {
    return false;
  }
  const char* p = line + 2;
  long seq = 0;
  const char* end = nullptr;
  if (!take_int(p, &seq, &end) || *p == '-' || seq < 0 || seq > 65535 || *end != ' ') {
    return false;
  }
  p = end + 1;
  long pitch = 0;
  if (!take_int(p, &pitch, &end) || !fits_int32(pitch) || *end != ' ') {
    return false;
  }
  p = end + 1;
  long yaw = 0;
  if (!take_int(p, &yaw, &end) || !fits_int32(yaw) || *end != '\0') {
    return false;
  }
  out->seq = static_cast<unsigned int>(seq);
  out->pitch_ddeg = static_cast<int>(pitch);
  out->yaw_hsps = static_cast<int32_t>(yaw);
  return true;
}

unsigned int format_c_line(uint32_t uno_ms, int st, int cm, char* out, unsigned int out_size) {
  if (out == nullptr || out_size == 0) {
    return 0;
  }
  // uint32_t は AVR では unsigned long、ホストでは unsigned int なので long に広げて %lu
  const int written =
      std::snprintf(out, out_size, "C %lu %d %d", static_cast<unsigned long>(uno_ms), st, cm);
  if (written <= 0 || static_cast<unsigned int>(written) >= out_size ||
      static_cast<unsigned int>(written) > IO_LINE_MAX) {
    if (out_size > 0) {
      out[0] = '\0';
    }
    return 0;
  }
  return static_cast<unsigned int>(written);
}

unsigned int format_b_line(uint32_t uno_ms, const char* fw, char* out, unsigned int out_size) {
  if (out == nullptr || out_size == 0 || fw == nullptr || *fw == '\0') {
    return 0;
  }
  for (const char* p = fw; *p != '\0'; ++p) {
    if (*p == ' ' || *p == '\r' || *p == '\n') {
      return 0;  // 空白が混ざると UnitV2 側で 4 項目に見える
    }
  }
  const int written =
      std::snprintf(out, out_size, "B %lu %s", static_cast<unsigned long>(uno_ms), fw);
  if (written <= 0 || static_cast<unsigned int>(written) >= out_size ||
      static_cast<unsigned int>(written) > IO_LINE_MAX) {
    if (out_size > 0) {
      out[0] = '\0';
    }
    return 0;
  }
  return static_cast<unsigned int>(written);
}
