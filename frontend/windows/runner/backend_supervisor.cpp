#include "backend_supervisor.h"
#include "backend_protocol.h"

#include <bcrypt.h>
#include <algorithm>
#include <memory>
#include <filesystem>
#include <utility>

namespace {
class Handle {
 public:
  Handle() = default;
  ~Handle() { Reset(); }
  Handle(const Handle&) = delete;
  Handle& operator=(const Handle&) = delete;
  HANDLE Get() const { return value_; }
  HANDLE* Put() { Reset(); return &value_; }
  void Reset() { if (value_ && value_ != INVALID_HANDLE_VALUE) CloseHandle(value_); value_ = nullptr; }
 private:
  HANDLE value_ = nullptr;
};
std::string Hex(const uint8_t* data, size_t size) {
  const char* digits = "0123456789abcdef";
  std::string result;
  result.reserve(size * 2);
  for (size_t i = 0; i < size; ++i) {
    result.push_back(digits[data[i] >> 4]);
    result.push_back(digits[data[i] & 15]);
  }
  return result;
}
bool Exited(HANDLE process) { return WaitForSingleObject(process, 0) == WAIT_OBJECT_0; }
bool ActiveCount(HANDLE job, DWORD* count) {
  JOBOBJECT_BASIC_ACCOUNTING_INFORMATION info{};
  if (!QueryInformationJobObject(job, JobObjectBasicAccountingInformation,
                                 &info, sizeof(info), nullptr)) return false;
  *count = info.ActiveProcesses;
  return true;
}
bool WriteControl(HANDLE pipe, uint8_t kind, const uint8_t* payload) {
  auto frame = backend_protocol::Frame(kind, payload);
  DWORD written = 0;
  // INIT, LIFETIME, QUIESCE e STOP somam 144 bytes em pipe de 4096.
  // Mesmo um filho que nunca leia não pode esgotar a capacidade do pipe.
  bool result = WriteFile(pipe, frame.data(), static_cast<DWORD>(frame.size()),
                          &written, nullptr) && written == frame.size();
  SecureZeroMemory(frame.data(), frame.size());
  return result;
}
}  // namespace

std::wstring QuoteWindowsArgument(const std::wstring& value) {
  std::wstring result = L"\"";
  size_t slashes = 0;
  for (wchar_t c : value) {
    if (c == L'\\') { ++slashes; continue; }
    result.append(slashes * (c == L'\"' ? 2 : 1), L'\\');
    slashes = 0;
    if (c == L'\"') result.push_back(L'\\');
    result.push_back(c);
  }
  result.append(slashes * 2, L'\\');
  result.push_back(L'\"');
  return result;
}

BackendSupervisor::BackendSupervisor(std::function<void()> notify) : notify_(std::move(notify)) {}
BackendSupervisor::~BackendSupervisor() {
  RequestStop();
  AcknowledgeStop();
  Join();
  if (admission_event_) CloseHandle(admission_event_);
  SecureZeroMemory(secret_.data(), secret_.size());
}
void BackendSupervisor::Start(BackendLaunch launch) {
  if (started_.exchange(true)) return;
  if (launch.executable.empty() || launch.directory.empty()) { State("failed", launch.error.empty() ? "configuration" : launch.error); return; }
  worker_ = std::thread(&BackendSupervisor::Run, this, std::move(launch));
}
void BackendSupervisor::Join() { if (worker_.joinable()) worker_.join(); }
void BackendSupervisor::CloseAdmission() {
  admission_closed_.store(true);
  std::lock_guard<std::mutex> lock(mutex_);
  if (admission_event_) SetEvent(admission_event_);
}
void BackendSupervisor::RequestStop() { CloseAdmission(); stop_.store(true); }
void BackendSupervisor::AcknowledgeStop() { acknowledged_.store(true); }
void BackendSupervisor::ConfirmReady(const std::string& execution) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (snapshot_.state == "ready" && execution == snapshot_.execution) confirmed_.store(true);
}
BackendSnapshot BackendSupervisor::Snapshot() const {
  std::lock_guard<std::mutex> lock(mutex_);
  auto result = snapshot_;
  if (result.state == "ready") result.token = Hex(secret_.data(), secret_.size());
  return result;
}
void BackendSupervisor::State(const std::string& state, const std::string& code) {
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot_.state = state;
    snapshot_.code = code;
    ++snapshot_.sequence;
    if (state != "ready") snapshot_.port = 0;
  }
  notify_();
}

void BackendSupervisor::Run(BackendLaunch launch) {
  Handle job, input_read, input_write, output_read, output_write, log_read, log_write;
  Handle process, primary_thread, backend_process;
  std::array<uint8_t, 16> capability{};
  if (BCryptGenRandom(nullptr, secret_.data(), static_cast<ULONG>(secret_.size()),
                     BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0 ||
      BCryptGenRandom(nullptr, execution_.data(), static_cast<ULONG>(execution_.size()),
                     BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0 ||
      BCryptGenRandom(nullptr, capability.data(), static_cast<ULONG>(capability.size()),
                     BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) {
    State("failed", "random"); return;
  }
  const auto event_id = Hex(execution_.data(), execution_.size()) + "-" + Hex(capability.data(), capability.size());
  const std::wstring event_name = L"Local\\eDisk-close-" + std::wstring(event_id.begin(), event_id.end());
  bool event_ok = false;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    admission_event_ = CreateEventW(nullptr, TRUE, admission_closed_.load(), event_name.c_str());
    event_ok = admission_event_ && GetLastError() != ERROR_ALREADY_EXISTS;
  }
  if (!event_ok) { State("failed", "containment"); return; }
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot_.execution = Hex(execution_.data(), execution_.size());
  }
  State("starting");
  std::wstring coordination;
  wchar_t installed_id[33]{}, image[32768]{};
  GetModuleFileNameW(nullptr, image, 32768);
  if (_wcsicmp(std::filesystem::path(image).parent_path().parent_path().filename().c_str(), L"releases") == 0 &&
      GetEnvironmentVariableW(L"EDISK_INSTALL_JOB", installed_id, 33) == 32) {
    for (size_t i = 0; i < 32; ++i) if (!wcschr(L"0123456789abcdef", installed_id[i])) {
      State("failed", "containment"); return;
    }
    coordination = L"Local\\eDisk-" + std::wstring(installed_id);
  }
  const auto job_name = coordination + L"-backend";
  *job.Put() = CreateJobObjectW(nullptr, coordination.empty() ? nullptr : job_name.c_str());
  if (!coordination.empty() && GetLastError() == ERROR_ALREADY_EXISTS) {
    State("failed", "containment"); return;
  }
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
  limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  if (!job.Get() || !SetInformationJobObject(job.Get(), JobObjectExtendedLimitInformation,
                                           &limits, sizeof(limits))) {
    State("failed", "containment"); return;
  }
  if (!coordination.empty()) {
    Handle retained;
    *retained.Put() = OpenEventW(SYNCHRONIZE, FALSE, (coordination + L"-retained").c_str());
    if (!retained.Get() || WaitForSingleObject(retained.Get(), 5000) != WAIT_OBJECT_0) {
      State("failed", "containment"); return;
    }
  }
  SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
  if (!CreatePipe(input_read.Put(), input_write.Put(), &attributes, 4096) ||
      !CreatePipe(output_read.Put(), output_write.Put(), &attributes, 4096) ||
      !CreatePipe(log_read.Put(), log_write.Put(), &attributes, 4096) ||
      !SetHandleInformation(input_write.Get(), HANDLE_FLAG_INHERIT, 0) ||
      !SetHandleInformation(output_read.Get(), HANDLE_FLAG_INHERIT, 0) ||
      !SetHandleInformation(log_read.Get(), HANDLE_FLAG_INHERIT, 0)) {
    State("failed", "pipe"); return;
  }
  SIZE_T bytes = 0;
  InitializeProcThreadAttributeList(nullptr, 2, 0, &bytes);
  std::vector<uint8_t> storage(bytes);
  auto list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(storage.data());
  if (!InitializeProcThreadAttributeList(list, 2, 0, &bytes)) {
    State("failed", "containment"); return;
  }
  HANDLE job_handle = job.Get();
  HANDLE inherited[] = {input_read.Get(), output_write.Get(), log_write.Get()};
  bool configured = UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
      &job_handle, sizeof(job_handle), nullptr, nullptr) &&
      UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
      inherited, sizeof(inherited), nullptr, nullptr);
  STARTUPINFOEXW startup{};
  startup.StartupInfo.cb = sizeof(startup);
  startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
  startup.StartupInfo.hStdInput = input_read.Get();
  startup.StartupInfo.hStdOutput = output_write.Get();
  startup.StartupInfo.hStdError = log_write.Get();
  startup.lpAttributeList = list;
  std::wstring command = QuoteWindowsArgument(launch.executable);
  for (const auto& argument : launch.arguments) command += L" " + QuoteWindowsArgument(argument);
  PROCESS_INFORMATION created{};
  const auto started = Clock::now();
  bool launched = configured && CreateProcessW(launch.executable.c_str(), command.data(),
      nullptr, nullptr, TRUE, EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
      launch.environment.empty() ? nullptr : launch.environment.data(), launch.directory.c_str(),
      &startup.StartupInfo, &created);
  DeleteProcThreadAttributeList(list);
  if (!launched) { State("failed", configured ? "launch" : "containment"); return; }
  *process.Put() = created.hProcess;
  *primary_thread.Put() = created.hThread;
  primary_thread.Reset();
  input_read.Reset(); output_write.Reset(); log_write.Reset();
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot_.launcher_pid = created.dwProcessId;
  }
  BOOL associated = FALSE;
  std::string failure;
  if (!IsProcessInJob(process.Get(), job.Get(), &associated) || !associated) failure = "containment";
  std::array<uint8_t, 48> init{};
  std::copy(execution_.begin(), execution_.end(), init.begin());
  std::copy(secret_.begin(), secret_.end(), init.begin() + 16);
  if (failure.empty() && !WriteControl(input_write.Get(), backend_protocol::kInit, init.data())) failure = "pipe";
  SecureZeroMemory(init.data(), init.size());
  std::array<uint8_t, 32> lifetime{};
  std::copy(execution_.begin(), execution_.end(), lifetime.begin());
  std::copy(capability.begin(), capability.end(), lifetime.begin() + 16);
  if (failure.empty() && !WriteControl(input_write.Get(), backend_protocol::kLifetime, lifetime.data())) failure = "pipe";
  SecureZeroMemory(capability.data(), capability.size());
  SecureZeroMemory(lifetime.data(), lifetime.size());

  int phase = 0;  // HELLO -> PREPARING -> READY -> STOPPED.
  bool stop_sent = false, stopped_frame = false, forced = false, unexpected_exit = false, quiesce_sent = false;
  auto stopping_at = Clock::time_point::max();
  auto death_seen = Clock::time_point::max();
  std::vector<uint8_t> buffer;
  buffer.reserve(backend_protocol::kHeader + backend_protocol::kLimit);
  while (failure.empty()) {
    const auto now = Clock::now();
    if (admission_closed_.load() && !quiesce_sent) {
      quiesce_sent = true;
      if (!WriteControl(input_write.Get(), backend_protocol::kQuiesce, execution_.data())) {
        failure = "pipe"; break;
      }
    }
    if (stop_.load() && stopping_at == Clock::time_point::max()) {
      stopping_at = now;
      State("stopping");
    }
    if (stopping_at != Clock::time_point::max() && !stop_sent &&
        (acknowledged_.load() || now - stopping_at >= std::chrono::milliseconds(250))) {
      stop_sent = true;
      if (!WriteControl(input_write.Get(), backend_protocol::kStop, execution_.data())) {
        failure = "pipe"; break;
      }
    }
    // Ler apenas bytes já disponíveis: nenhum ReadFile pode esperar por mais dados.
    for (int reads = 0; reads < 32 && failure.empty(); ++reads) {
      DWORD available = 0;
      if (!PeekNamedPipe(output_read.Get(), nullptr, 0, nullptr, &available, nullptr)) {
        if (!(stopped_frame && buffer.empty())) {
          unexpected_exit = phase >= 3 && !stop_sent;
          failure = unexpected_exit ? "backend_exited" : "protocol";
        }
        break;
      }
      if (!available) break;
      uint8_t chunk[520];
      DWORD read = 0;
      DWORD wanted = std::min<DWORD>(available, static_cast<DWORD>(sizeof(chunk) - buffer.size()));
      if (!ReadFile(output_read.Get(), chunk, wanted, &read, nullptr) || !read) {
        failure = "pipe"; break;
      }
      buffer.insert(buffer.end(), chunk, chunk + read);
      while (buffer.size() >= backend_protocol::kHeader && failure.empty()) {
        if (!backend_protocol::HeaderValid(buffer.data())) { failure = "protocol"; break; }
        const size_t total = backend_protocol::kHeader + backend_protocol::U16(buffer.data() + 6);
        if (buffer.size() < total) break;
        const uint8_t kind = buffer[5];
        const uint8_t* payload = buffer.data() + backend_protocol::kHeader;
        if (!std::equal(execution_.begin(), execution_.end(), payload)) {
          failure = "identity"; break;
        }
        if (kind == backend_protocol::kHello && phase == 0) {
          DWORD pid = backend_protocol::U32(payload + 16);
          *backend_process.Put() = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
          if (!backend_process.Get() || !IsProcessInJob(backend_process.Get(), job.Get(), &associated) || !associated) {
            failure = "identity"; break;
          }
          { std::lock_guard<std::mutex> lock(mutex_); snapshot_.backend_pid = pid; }
          phase = 1;
        } else if (kind == backend_protocol::kPreparing && phase == 1) {
          phase = 2;
          if (!stop_.load()) State("preparing");
        } else if (kind == backend_protocol::kReady && phase == 2 && !stopped_frame) {
          uint16_t port = backend_protocol::U16(payload + 16);
          if (!port) { failure = "protocol"; break; }
          phase = 3;
          if (!stop_.load()) {
            { std::lock_guard<std::mutex> lock(mutex_); snapshot_.port = port; }
            State("ready");
          }
        } else if (kind == backend_protocol::kStopped && phase >= 1 && stop_sent && !stopped_frame) {
          stopped_frame = true;
        } else if (kind == backend_protocol::kError && phase >= 1 && !stopped_frame) {
          uint16_t code = backend_protocol::U16(payload + 16);
          static const char* codes[] = {"protocol", "protocol", "configuration", "instance_in_use", "persistence", "startup"};
          failure = code >= 1 && code <= 5 ? codes[code] : "protocol";
        } else {
          failure = "protocol";
        }
        buffer.erase(buffer.begin(), buffer.begin() + total);
      }
      if (buffer.size() > backend_protocol::kHeader + backend_protocol::kLimit) failure = "protocol";
    }
    // stderr não é um segundo canal de controle. Drenar e descartar, sem ecoar PII.
    DWORD available = 0, read = 0;
    uint8_t diagnostics[4096];
    if (PeekNamedPipe(log_read.Get(), nullptr, 0, nullptr, &available, nullptr) && available) {
      ReadFile(log_read.Get(), diagnostics, std::min<DWORD>(available, sizeof(diagnostics)), &read, nullptr);
    }
    if (!failure.empty()) break;
    if (Exited(process.Get()) || (backend_process.Get() && Exited(backend_process.Get()))) {
      if (death_seen == Clock::time_point::max()) death_seen = now;
      // STOPPED pode ser escrito entre PeekNamedPipe e a consulta do handle.
      if (!stopped_frame && now - death_seen < std::chrono::milliseconds(50)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        continue;
      }
      if (!stop_sent || !stopped_frame || !buffer.empty()) {
        failure = phase >= 3 ? "backend_exited" : "startup";
        unexpected_exit = phase >= 3;
      } else {
        DWORD status = 1;
        if (!GetExitCodeProcess(process.Get(), &status) || (status != 0 && status != STILL_ACTIVE)) failure = "shutdown";
      }
      break;
    }
    if (stopping_at != Clock::time_point::max()) {
      if (now - stopping_at >= std::chrono::seconds(10)) { forced = true; break; }
    } else if (phase == 0 && now - started >= std::chrono::seconds(5)) {
      failure = "handshake_timeout";
    } else if (!confirmed_.load() && now - started >= std::chrono::seconds(60)) {
      failure = "startup_timeout";
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  DWORD active = 0;
  bool counted = ActiveCount(job.Get(), &active);
  if (!failure.empty() || forced) {
    forced = true;
    TerminateJobObject(job.Get(), 1);
  }
  auto deadline = Clock::now() + std::chrono::seconds(5);
  while ((!counted || active) && Clock::now() < deadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    counted = ActiveCount(job.Get(), &active);
  }
  if (!counted || active) {
    TerminateJobObject(job.Get(), 1);
    failure = "containment_timeout";
  }
  if (failure.empty() && !forced && counted && !active) {
    DWORD launcher_status = 1, backend_status = 1;
    if (!GetExitCodeProcess(process.Get(), &launcher_status) || launcher_status != 0 ||
        !backend_process.Get() || !GetExitCodeProcess(backend_process.Get(), &backend_status) || backend_status != 0) {
      failure = "shutdown";
    }
  }
  {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot_.forced = forced;
    snapshot_.active_processes = active;
    // Estado terminal e invalidação do segredo são atômicos para Snapshot().
    snapshot_.state = failure.empty() ? "stopped" : (unexpected_exit ? "exited" : "failed");
    snapshot_.code = failure.empty() ? (forced ? "forced" : "") : failure;
    snapshot_.port = 0;
    ++snapshot_.sequence;
    SecureZeroMemory(secret_.data(), secret_.size());
  }
  notify_();
}
