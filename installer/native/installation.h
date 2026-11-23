#pragma once
#include <windows.h>
#include <filesystem>
#include <string>

namespace edisk_install {
struct Active {
  std::string channel, version, release, inventory_hash;
};
class Guard {
 public:
  ~Guard();
  Guard() = default;
  Guard(const Guard&) = delete;
  Guard& operator=(const Guard&) = delete;
  void Close();
  bool Open(const std::filesystem::path& root);
 private:
  HANDLE handle_ = INVALID_HANDLE_VALUE;
};
bool Coordinated(const std::filesystem::path& root);
bool SafeTree(const std::filesystem::path& root);
bool ReadActive(const std::filesystem::path& root, Active* active);
bool Verify(const std::filesystem::path& root, const Active& active);
bool Runner(Guard* guard, const std::filesystem::path& executable_dir,
            const std::string& channel, const std::string& version);
std::filesystem::path OwnDirectory();
}
