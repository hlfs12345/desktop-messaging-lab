#ifndef RUNNER_BACKEND_SUPERVISOR_H_
#define RUNNER_BACKEND_SUPERVISOR_H_

#include <windows.h>
#include <array>
#include <atomic>
#include <chrono>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

struct BackendLaunch {
  std::string error;
  std::string profile;
  std::wstring executable;
  std::vector<std::wstring> arguments;
  std::wstring directory;
  // Bloco Unicode com NUL duplo; vazio herda o ambiente (harness apenas).
  std::vector<wchar_t> environment;
};

struct BackendSnapshot {
  int64_t sequence = 0;
  std::string state = "starting";
  std::string code;
  std::string execution;
  std::string token;  // Somente em ready; jamais serializar em logs.
  DWORD launcher_pid = 0;
  DWORD backend_pid = 0;
  uint16_t port = 0;
  bool forced = false;
  DWORD active_processes = 0;
};

class BackendSupervisor {
 public:
  using Clock = std::chrono::steady_clock;
  explicit BackendSupervisor(std::function<void()> notify);
  ~BackendSupervisor();
  BackendSupervisor(const BackendSupervisor&) = delete;
  BackendSupervisor& operator=(const BackendSupervisor&) = delete;
  void Start(BackendLaunch launch);
  void RequestStop();
  void CloseAdmission();
  void AcknowledgeStop();
  void ConfirmReady(const std::string& execution);
  BackendSnapshot Snapshot() const;
  void Join();

 private:
  void Run(BackendLaunch launch);
  void State(const std::string& state, const std::string& code = "");
  std::function<void()> notify_;
  mutable std::mutex mutex_;
  BackendSnapshot snapshot_;
  std::array<uint8_t, 16> execution_{};
  std::array<uint8_t, 32> secret_{};
  std::thread worker_;
  std::atomic<bool> stop_{false};
  std::atomic<bool> acknowledged_{false};
  std::atomic<bool> confirmed_{false};
  std::atomic<bool> started_{false};
  std::atomic<bool> admission_closed_{false};
  HANDLE admission_event_ = nullptr;
};

bool ResolveBackendLaunch(BackendLaunch* launch);
bool ResolveBackendLaunchFor(bool source, const std::wstring& executable_dir,
                             const std::vector<std::wstring>& arguments, BackendLaunch* launch);
std::wstring ExecutableDirectory();
std::wstring QuoteWindowsArgument(const std::wstring& value);
#endif
