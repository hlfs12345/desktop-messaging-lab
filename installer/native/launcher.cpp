// Coordenador da instancia instalada. Nunca libera a manutencao sem testemunhos de termino.
#include "installation.h"
#include <bcrypt.h>
#include <fstream>
#include <array>
#include <map>
#include <vector>

namespace fs = std::filesystem;
namespace {
// Contrato publico: 0 sucesso; 1 instalacao; 2 coordenacao inicial;
// 3 termino/resultado nao confirmado; 4 inicio do runner; 5 runner retornou erro.
enum ExitCode { kSuccess = 0, kInstallation = 1, kCoordination = 2,
                kUnconfirmed = 3, kStartup = 4, kRunner = 5 };
int Failure(ExitCode code, const wchar_t* text, DWORD detail = 0,
            const wchar_t* label = nullptr) {
  std::wstring message = text;
  if (label) message += L"\n\n" + std::wstring(label) + L": " + std::to_wstring(detail) + L".";
  MessageBoxW(nullptr, message.c_str(), L"Messaging Lab", MB_OK | MB_ICONERROR);
  return code;
}
std::wstring Id() {
  unsigned char bytes[16]{};
  if (BCryptGenRandom(nullptr, bytes, 16, BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) return {};
  std::wstring id;
  for (auto byte : bytes) { id += L"0123456789abcdef"[byte >> 4]; id += L"0123456789abcdef"[byte & 15]; }
  return id;
}
bool Retire(const fs::path& claim) {
  FILETIME created{}, exited{}, kernel{}, user{};
  if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) return false;
  std::string payload = "EDISK-CONFIRMED-3\r\n" + std::to_string(GetCurrentProcessId()) + "\r\n" +
    std::to_string(created.dwLowDateTime) + "\r\n" + std::to_string(created.dwHighDateTime) + "\r\n";
  BCRYPT_ALG_HANDLE algorithm = nullptr;
  std::array<unsigned char, 32> digest{};
  if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0) return false;
  auto status = BCryptHash(algorithm, nullptr, 0, reinterpret_cast<PUCHAR>(payload.data()),
                          static_cast<ULONG>(payload.size()), digest.data(), static_cast<ULONG>(digest.size()));
  BCryptCloseAlgorithmProvider(algorithm, 0);
  if (status < 0) return false;
  std::string checksum;
  for (auto byte : digest) { checksum += "0123456789abcdef"[byte >> 4]; checksum += "0123456789abcdef"[byte & 15]; }
  payload += checksum + "\r\n";
  auto next = claim;
  next += L".next";
  HANDLE file = CreateFileW(next.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) return false;
  DWORD written = 0;
  bool ok = WriteFile(file, payload.data(), static_cast<DWORD>(payload.size()), &written, nullptr) &&
            written == payload.size() && FlushFileBuffers(file);
  CloseHandle(file);
  return ok && MoveFileExW(next.c_str(), claim.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}

bool Confirm(HANDLE job, const std::map<DWORD,HANDLE>& observed, bool lost) {
  JOBOBJECT_BASIC_ACCOUNTING_INFORMATION info{};
  if (lost || !QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &info, sizeof(info), nullptr) ||
      info.ActiveProcesses != 0 || info.TotalProcesses != observed.size()) return false;
  for (auto& item : observed) if (WaitForSingleObject(item.second, 0) != WAIT_OBJECT_0) return false;
  return true;
}
}
int WINAPI wWinMain(HINSTANCE, HINSTANCE, wchar_t*, int) {
  auto root = edisk_install::OwnDirectory();
  edisk_install::Guard guard;
  edisk_install::Active active;
  if (!guard.Open(root) || !edisk_install::ReadActive(root, &active) || !edisk_install::Verify(root, active)) {
    return Failure(kInstallation, L"Instala\u00e7\u00e3o incompleta ou em manuten\u00e7\u00e3o. Execute novamente o instalador.");
  }
  const auto id = Id();
  const auto runtime = root / L"runtime";
  std::error_code ec;
  fs::create_directory(runtime, ec);
  if (id.empty() || !edisk_install::SafeTree(runtime))
    return Failure(kCoordination, L"N\u00e3o foi poss\u00edvel preparar a abertura segura do Messaging Lab. Tente novamente ou contate o suporte.");
  const auto claim = runtime / (id + L".claim");
  HANDLE marker = CreateFileW(claim.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (marker == INVALID_HANDLE_VALUE)
    return Failure(kCoordination, L"N\u00e3o foi poss\u00edvel preparar a abertura segura do Messaging Lab. Tente novamente ou contate o suporte.");
  DWORD written = 0;
  const char content[] = "EDISK-UNCONFIRMED-3\r\n";
  bool persisted = WriteFile(marker, content, sizeof(content)-1, &written, nullptr) && written == sizeof(content)-1 && FlushFileBuffers(marker);
  CloseHandle(marker);
  if (!persisted) // O marcador permanece inclusive em falhas.
    return Failure(kCoordination, L"N\u00e3o foi poss\u00edvel registrar a abertura segura do Messaging Lab. Contate o suporte.");
  const auto name = L"Local\\eDisk-" + id;
  HANDLE retained = CreateEventW(nullptr, TRUE, FALSE, (name + L"-retained").c_str());
  HANDLE inner_job = nullptr;
  HANDLE job = CreateJobObjectW(nullptr, name.c_str());
  bool new_job = job && GetLastError() != ERROR_ALREADY_EXISTS;
  HANDLE port = CreateIoCompletionPort(INVALID_HANDLE_VALUE, nullptr, 0, 1);
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
  limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  JOBOBJECT_ASSOCIATE_COMPLETION_PORT association{nullptr, port};
  bool ready = retained && new_job && port &&
    SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits)) &&
    SetInformationJobObject(job, JobObjectAssociateCompletionPortInformation, &association, sizeof(association));
  SIZE_T size = 0;
  InitializeProcThreadAttributeList(nullptr, 1, 0, &size);
  std::vector<unsigned char> buffer(size);
  auto attributes = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(buffer.data());
  bool initialized = InitializeProcThreadAttributeList(attributes, 1, 0, &size) != FALSE;
  ready = ready && initialized && UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST, &job, sizeof(job), nullptr, nullptr);
  if (ready) ready = SetEnvironmentVariableW(L"EDISK_INSTALL_JOB", id.c_str()) != FALSE;
  auto directory = root / L"releases" / active.release;
  auto executable = directory / L"edisk.exe";
  std::wstring command = L"\"" + executable.wstring() + L"\"";
  STARTUPINFOEXW startup{};
  startup.StartupInfo.cb = sizeof(startup);
  startup.lpAttributeList = attributes;
  PROCESS_INFORMATION process{};
  bool created = false, started = false;
  DWORD startup_error = ERROR_SUCCESS;
  if (ready) {
    created = CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, FALSE,
      EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED, nullptr, directory.c_str(), &startup.StartupInfo, &process) != FALSE;
    if (!created) startup_error = GetLastError(); // Antes de qualquer limpeza ou outra API.
  }
  if (initialized) DeleteProcThreadAttributeList(attributes);
  std::map<DWORD,HANDLE> observed;
  bool lost = false, stopped = !created;
  // O runner nasce suspenso para que seu handle exista antes de qualquer descendente.
  if (created) {
    started = ResumeThread(process.hThread) != static_cast<DWORD>(-1);
    if (!started) {
      startup_error = GetLastError();
      lost = true;
      TerminateJobObject(job, 1);
    }
    CloseHandle(process.hThread);
    observed.emplace(process.dwProcessId, process.hProcess);
  }
  ULONGLONG deadline = 0;
  bool confirmed = false;
  while (ready) {
    if (!inner_job) {
      inner_job = OpenJobObjectW(JOB_OBJECT_QUERY, FALSE, (name + L"-backend").c_str());
      if (inner_job) SetEvent(retained);
    }
    DWORD message = 0; ULONG_PTR key = 0; OVERLAPPED* pid = nullptr;
    if (GetQueuedCompletionStatus(port, &message, &key, &pid, 20) && message == JOB_OBJECT_MSG_NEW_PROCESS) {
      DWORD value = static_cast<DWORD>(reinterpret_cast<ULONG_PTR>(pid));
      if (!observed.count(value)) {
        HANDLE handle = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, value);
        BOOL member = FALSE;
        if (!handle || !IsProcessInJob(handle, job, &member) || !member) {
          if (handle) CloseHandle(handle);
          lost = true;
        } else observed.emplace(value, handle);
      }
    }
    if (!stopped && WaitForSingleObject(process.hProcess, 0) == WAIT_OBJECT_0) {
#ifdef EDISK_COORD_TEST
      // Somente o binario de ensaio: conserva backend vivo ate o teste disputar manutencao.
      if (!fs::exists(root / L"test-retire-mode")) {
        auto pause = runtime / L"test-pause";
        std::ofstream(pause).put('1');
        while (!fs::exists(runtime / L"test-release")) Sleep(10);
      }
#endif
      stopped = true;
    }
    if (stopped) {
      if (!deadline) { TerminateJobObject(job, 1); deadline = GetTickCount64() + 15000; }
      if (Confirm(job, observed, lost)) { confirmed = true; break; }
      if (GetTickCount64() >= deadline) break; // Timeout nunca concede manutencao.
    }
  }
  DWORD runner_exit = ERROR_SUCCESS, result_error = ERROR_SUCCESS;
  bool result_known = false;
  if (confirmed && started) {
    result_known = GetExitCodeProcess(process.hProcess, &runner_exit) != FALSE;
    if (!result_known) result_error = GetLastError();
  }
  for (auto& item : observed) CloseHandle(item.second);
  if (inner_job) CloseHandle(inner_job);
  if (retained) CloseHandle(retained);
  if (job) CloseHandle(job);
  if (port) CloseHandle(port);
  if (confirmed) confirmed = Retire(claim);
#ifdef EDISK_COORD_TEST
  if (confirmed && fs::exists(root / L"test-retire-mode")) {
    guard.Close();
    std::ofstream(root / L"test-retire-pause").put('1');
    while (!fs::exists(root / L"test-retire-release")) Sleep(10);
  }
#endif
  if (!ready)
    return Failure(kCoordination, L"N\u00e3o foi poss\u00edvel preparar a abertura segura do Messaging Lab. Tente novamente ou contate o suporte.");
  if (!created || !started)
    return Failure(kStartup, L"N\u00e3o foi poss\u00edvel iniciar o Messaging Lab. Tente novamente. Se o problema persistir, contate o suporte.",
                   startup_error, L"C\u00f3digo do Windows");
  if (!confirmed)
    return Failure(kUnconfirmed, L"N\u00e3o foi poss\u00edvel confirmar o encerramento seguro do Messaging Lab. Contate o suporte antes de atualizar ou desinstalar.");
  if (!result_known)
    return Failure(kUnconfirmed, L"N\u00e3o foi poss\u00edvel consultar o resultado da execu\u00e7\u00e3o do Messaging Lab. Contate o suporte.",
                   result_error, L"C\u00f3digo do Windows");
  if (runner_exit != 0)
    return Failure(kRunner, L"O Messaging Lab encerrou com erro. Tente novamente. Se o problema persistir, contate o suporte.",
                   runner_exit, L"C\u00f3digo do aplicativo");
  return kSuccess;
}
