#include "../backend_supervisor.h"
#include <iostream>
int wmain(int argc, wchar_t** argv) {
  if (argc < 3) return 2;
  BackendLaunch launch;
  std::vector<std::wstring> arguments;
  for (int i = 3; i < argc; ++i) arguments.emplace_back(argv[i]);
  bool valid = ResolveBackendLaunchFor(std::wstring(argv[1]) == L"source", argv[2], arguments, &launch);
  std::cout << (valid ? "ok" : launch.error) << "\n" << launch.profile << "\n";
  std::wcout << launch.executable << L"\n";
  // Apenas configuracao nao sensivel do filho; nunca iniciar o backend aqui.
  for (const wchar_t* p = launch.environment.data(); !launch.environment.empty() && *p; p += wcslen(p) + 1) {
    if (wcsncmp(p, L"EDISK_", 6) == 0 || wcsncmp(p, L"PYTHON", 6) == 0) std::wcout << p << L"\n";
  }
  return valid ? 0 : 1;
}
