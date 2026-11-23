#ifndef RUNNER_BACKEND_PROTOCOL_H_
#define RUNNER_BACKEND_PROTOCOL_H_

#include <array>
#include <cstdint>
#include <vector>

// Contrato compartilhado com app/runtime/control_protocol.py. Little endian.
namespace backend_protocol {
constexpr size_t kHeader = 8;
constexpr size_t kLimit = 512;
enum Kind : uint8_t { kInit = 1, kHello, kPreparing, kReady, kError, kStop, kStopped, kLifetime, kQuiesce };
inline size_t Size(uint8_t kind) {
  switch (kind) {
    case kInit: return 48;
    case kHello: return 20;
    case kPreparing: case kStop: case kStopped: case kQuiesce: return 16;
    case kLifetime: return 32;
    case kReady: case kError: return 18;
    default: return 0;
  }
}
inline uint16_t U16(const uint8_t* data) {
  return static_cast<uint16_t>(data[0] | (data[1] << 8));
}
inline uint32_t U32(const uint8_t* data) {
  return static_cast<uint32_t>(data[0]) | (static_cast<uint32_t>(data[1]) << 8) |
         (static_cast<uint32_t>(data[2]) << 16) | (static_cast<uint32_t>(data[3]) << 24);
}
inline bool HeaderValid(const uint8_t* data) {
  return data[0] == 'E' && data[1] == 'D' && data[2] == '5' && data[3] == 'P' &&
         data[4] == 1 && Size(data[5]) != 0 && U16(data + 6) <= kLimit &&
         U16(data + 6) == Size(data[5]);
}
inline std::vector<uint8_t> Frame(uint8_t kind, const uint8_t* payload) {
  const size_t size = Size(kind);
  std::vector<uint8_t> frame{'E', 'D', '5', 'P', 1, kind,
      static_cast<uint8_t>(size & 255), static_cast<uint8_t>(size >> 8)};
  frame.insert(frame.end(), payload, payload + size);
  return frame;
}
}  // namespace backend_protocol
#endif
