// Harness independente do Flutter: o mesmo supervisor e processos Windows reais.
#include "../backend_supervisor.h"
#include <iostream>

int wmain(int argc, wchar_t** argv) {
  if (argc < 4) return 2;
  // Job externo para exercitar hierarquias reais usadas por ferramentas Windows.
  HANDLE outer = nullptr;
  const bool nested = std::wstring(argv[1]) == L"nested" || std::wstring(argv[1]) == L"restricted";
  if (nested) {
    outer = CreateJobObjectW(nullptr, nullptr);
    if (!outer || !AssignProcessToJobObject(outer, GetCurrentProcess())) return 3;
    if (std::wstring(argv[1]) == L"restricted") {
      JOBOBJECT_BASIC_UI_RESTRICTIONS restrictions{JOB_OBJECT_UILIMIT_DESKTOP};
      if (!SetInformationJobObject(outer, JobObjectBasicUIRestrictions, &restrictions,
                                   sizeof(restrictions))) return 4;
      JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
      limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
      limits.BasicLimitInformation.ActiveProcessLimit = 1;
      if (!SetInformationJobObject(outer, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) return 5;
    }
  }
  BackendLaunch launch;
  launch.executable = argv[2];
  launch.directory = argv[3];
  for (int i = 4; i < argc; ++i) launch.arguments.push_back(argv[i]);
  BackendSupervisor supervisor([] {});
  supervisor.Start(std::move(launch));
  int64_t previous = -1;
  std::string command;
  for (;;) {
    auto snapshot = supervisor.Snapshot();
    if (snapshot.sequence != previous) {
      previous = snapshot.sequence;
      // Nunca imprimir token ou payload. STDOUT é pipe privado do ensaio.
      std::cout << snapshot.state << ' ' << snapshot.sequence << ' '
                << snapshot.launcher_pid << ' ' << snapshot.backend_pid << ' '
                << snapshot.port << ' ' << (snapshot.code.empty() ? "none" : snapshot.code)
                << ' ' << snapshot.active_processes << ' ' << snapshot.forced << std::endl;
      if (snapshot.state == "ready" && std::wstring(argv[1]) != L"unconfirmed") {
        supervisor.ConfirmReady(snapshot.execution);
      }
    }
    if (snapshot.state == "failed" || snapshot.state == "exited" || snapshot.state == "stopped") break;
    DWORD available = 0;
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) {
      supervisor.RequestStop(); supervisor.AcknowledgeStop();
    } else if (available) {
      char data = 0; DWORD read = 0;
      if (ReadFile(input, &data, 1, &read, nullptr) && read) {
        if (data == '\n') {
          if (command == "stop") { supervisor.RequestStop(); supervisor.AcknowledgeStop(); }
          if (command == "quiesce") { supervisor.CloseAdmission(); }
          command.clear();
        } else if (data != '\r' && command.size() < 32) command.push_back(data);
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  supervisor.Join();
  const auto terminal = supervisor.Snapshot();
  supervisor.Start(BackendLaunch{});  // Reuso acidental também não reinicia.
  if (supervisor.Snapshot().sequence != terminal.sequence) return 6;
  if (outer) CloseHandle(outer);
  return 0;
}
