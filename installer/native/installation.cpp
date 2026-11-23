#include "installation.h"
#include "variant.h"
#include <bcrypt.h>
#include <algorithm>
#include <array>
#include <fstream>
#include <map>
#include <set>
#include <vector>

namespace edisk_install {
namespace fs = std::filesystem;
namespace {
bool Regular(const fs::path& p) {
  DWORD attr = GetFileAttributesW(p.c_str());
  return attr != INVALID_FILE_ATTRIBUTES && !(attr & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY));
}
bool Lines(const fs::path& p, std::vector<std::string>* out, size_t limit) {
  if (!Regular(p)) return false;
  std::error_code ec;
  if (fs::file_size(p, ec) > limit || ec) return false;
  std::ifstream input(p, std::ios::binary);
  std::string line;
  while (std::getline(input, line)) {
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty() || line.find('\0') != std::string::npos) return false;
    out->push_back(line);
  }
  return input.eof() && !out->empty();
}
bool Hex(const std::string& text) {
  return text.size() == 64 && text.find_first_not_of("0123456789abcdef") == std::string::npos;
}
std::string Hash(const fs::path& path) {
  if (!Regular(path)) return {};
  BCRYPT_ALG_HANDLE algorithm = nullptr;
  BCRYPT_HASH_HANDLE hash = nullptr;
  std::string result;
  if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return {};
  if (BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0) {
    std::ifstream stream(path, std::ios::binary);
    std::array<unsigned char, 65536> buffer{};
    bool ok = stream.is_open();
    while (ok && stream) {
      stream.read(reinterpret_cast<char*>(buffer.data()), buffer.size());
      auto count = stream.gcount();
      if (count > 0 && BCryptHashData(hash, buffer.data(), static_cast<ULONG>(count), 0) < 0) ok = false;
    }
    std::array<unsigned char, 32> digest{};
    if (ok && stream.eof() && BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) >= 0) {
      for (auto c : digest) { result += "0123456789abcdef"[c >> 4]; result += "0123456789abcdef"[c & 15]; }
    }
    BCryptDestroyHash(hash);
  }
  BCryptCloseAlgorithmProvider(algorithm, 0);
  return result;
}
bool Relative(const std::string& value) {
  // O bundle atual usa somente nomes ASCII; Unicode e permitido na raiz instalada.
  if (value.empty() || value.size() > 240 || value.front() == '/' || value.back() == '/') return false;
  if (value.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-+") != std::string::npos) return false;
  fs::path path(value);
  for (auto& part : path) {
    auto name = part.string();
    if (name == "." || name == ".." || name.back() == '.') return false;
    auto base = name.substr(0, name.find('.'));
    std::transform(base.begin(), base.end(), base.begin(), [](unsigned char c) { return static_cast<char>(toupper(c)); });
    if (base == "CON" || base == "PRN" || base == "AUX" || base == "NUL" ||
        (base.size() == 4 && (base.substr(0, 3) == "COM" || base.substr(0, 3) == "LPT") && base[3] >= '0' && base[3] <= '9')) return false;
  }
  return value.find("//") == std::string::npos;
}
}
Guard::~Guard() { Close(); }
void Guard::Close() { if (handle_ != INVALID_HANDLE_VALUE) CloseHandle(handle_); handle_ = INVALID_HANDLE_VALUE; }
bool Guard::Open(const fs::path& root) {
  if (handle_ != INVALID_HANDLE_VALUE || !SafeTree(root) || !Regular(root / L"maintenance.lock")) return false;
  handle_ = CreateFileW((root / L"maintenance.lock").c_str(), GENERIC_READ, FILE_SHARE_READ,
                        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (handle_ == INVALID_HANDLE_VALUE) return false;
  // pending e criado sob exclusividade. Falhas de instalacao permanecem fechadas.
  return GetFileAttributesW((root / L"pending.txt").c_str()) == INVALID_FILE_ATTRIBUTES && GetLastError() == ERROR_FILE_NOT_FOUND;
}
bool SafeTree(const fs::path& root) {
  if (!root.is_absolute() || root.wstring().rfind(L"\\\\", 0) == 0) return false;
  fs::path current;
  for (auto& part : root) {
    current /= part;
    if (current == root.root_name()) continue;
    DWORD attr = GetFileAttributesW(current.c_str());
    if (attr == INVALID_FILE_ATTRIBUTES || !(attr & FILE_ATTRIBUTE_DIRECTORY) || (attr & FILE_ATTRIBUTE_REPARSE_POINT)) return false;
  }
  return true;
}
bool ReadActive(const fs::path& root, Active* active) {
  std::vector<std::string> id, lines;
  if (!Lines(root / L"installation.id", &id, 256) || id.size() != 2 || id[0] != "EDISK-INSTALL-3" ||
      (id[1] != "tecnico" && id[1] != "candidato")) return false;
  if (!Lines(root / L"active.txt", &lines, 512) || lines.size() != 5 || lines[0] != "EDISK-ACTIVE-1" || lines[1] != id[1] || !Hex(lines[4])) return false;
  if (lines[2].empty() || lines[2].find_first_not_of("0123456789.") != std::string::npos ||
      lines[3].empty() || lines[3].find_first_not_of("0123456789abcdef.-") != std::string::npos || lines[3] == "." || lines[3] == "..") return false;
  *active = {lines[1], lines[2], lines[3], lines[4]};
  return true;
}
bool Verify(const fs::path& root, const Active& active) {
  fs::path payload = root / L"releases" / active.release;
  if (!SafeTree(payload) || Hash(payload / L"inventory.txt") != active.inventory_hash) return false;
  std::vector<std::string> inventory;
  if (!Lines(payload / L"inventory.txt", &inventory, 1024 * 1024)) return false;
  std::map<std::string, std::string> expected;
  std::set<std::string> folded;
  for (auto& line : inventory) {
    if (line.size() < 67 || line.substr(64, 2) != "  ") return false;
    auto relative = line.substr(66), digest = line.substr(0, 64), lower = relative;
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) { return static_cast<char>(tolower(c)); });
    if (!Hex(digest) || !Relative(relative) || lower == "inventory.txt" || !folded.insert(lower).second) return false;
    expected.emplace(relative, digest);
  }
  if (!expected.count("edisk.exe") || !expected.count("backend/edisk-backend.exe") || !expected.count("build-info.json")) return false;
  std::error_code ec;
  size_t count = 0;
  fs::recursive_directory_iterator it(payload, fs::directory_options::none, ec), end;
  while (!ec && it != end) {
    auto path = it->path();
    auto attr = GetFileAttributesW(path.c_str());
    if (attr == INVALID_FILE_ATTRIBUTES || (attr & FILE_ATTRIBUTE_REPARSE_POINT)) return false;
    if (!(attr & FILE_ATTRIBUTE_DIRECTORY)) {
      auto relative = path.lexically_relative(payload).generic_string();
      if (relative != "inventory.txt") {
        auto found = expected.find(relative);
        if (found == expected.end() || Hash(path) != found->second) return false;
        ++count;
      }
    }
    it.increment(ec);
  }

  std::vector<std::string> approved;
  if (!Lines(payload / L"approved-payload.txt", &approved, 2048) || approved.size() != 7 ||
      approved[0] != "EDISK-PAYLOAD-3" || approved[1] != active.channel ||
      approved[2] != (active.channel == "tecnico" ? "bundled/test" : "bundled/production") ||
      approved[3] != active.version || approved[4] != active.release ||
      Hash(payload / L"manifest-sha256.json") != approved[5] ||
      Hash(payload / L"build-info.json") != approved[6] ||
      !Variant((payload / L"edisk.exe").wstring(), active.channel, active.version)) return false;
  return !ec && count == expected.size();
}

bool Coordinated(const fs::path& root) {
  wchar_t id[33]{};
  if (GetEnvironmentVariableW(L"EDISK_INSTALL_JOB", id, 33) != 32) return false;
  for (size_t i = 0; i < 32; ++i) if (!wcschr(L"0123456789abcdef", id[i])) return false;
  if (!SafeTree(root / L"runtime") || !Regular(root / L"runtime" / (std::wstring(id) + L".claim"))) return false;
  HANDLE job = OpenJobObjectW(JOB_OBJECT_QUERY, FALSE, (L"Local\\eDisk-" + std::wstring(id)).c_str());
  if (!job) return false;
  BOOL member = FALSE;
  bool valid = IsProcessInJob(GetCurrentProcess(), job, &member) && member;
  CloseHandle(job);
  return valid;
}
bool Runner(Guard* guard, const fs::path& directory, const std::string& channel, const std::string& version) {
  fs::path root = directory.parent_path().parent_path();
  if (!guard->Open(root) || !Coordinated(root)) return false;
  Active active;
  return ReadActive(root, &active) && active.channel == channel && active.version == version &&
         directory == root / L"releases" / active.release && Verify(root, active);
}
fs::path OwnDirectory() {
  std::wstring value(32768, L'\0');
  auto count = GetModuleFileNameW(nullptr, value.data(), static_cast<DWORD>(value.size()));
  if (!count || count >= value.size()) return {};
  value.resize(count);
  return fs::path(value).parent_path();
}
}
