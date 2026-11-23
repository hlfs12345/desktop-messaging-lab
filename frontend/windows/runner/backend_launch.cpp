#include "backend_supervisor.h"
#include <shellapi.h>
#include <filesystem>
#include <algorithm>

namespace {
std::wstring Environment(const wchar_t* key) {
  DWORD size = GetEnvironmentVariableW(key, nullptr, 0);
  if (!size) return L"";
  std::wstring result(size, L'\0');
  DWORD count = GetEnvironmentVariableW(key, result.data(), size);
  if (!count || count >= size) return L"";
  result.resize(count);
  return result;
}
bool LocalAbsolute(const std::filesystem::path& path) {
  return path.is_absolute() && path.wstring().rfind(L"\\\\", 0) != 0;
}
std::vector<wchar_t> ChildEnvironment(const std::wstring& environment,
                                      const std::wstring& test_root, bool source) {
  std::vector<wchar_t> result;
  std::vector<std::wstring> entries;
  wchar_t* block = GetEnvironmentStringsW();
  if (!block) return result;
  for (const wchar_t* item = block; *item; item += wcslen(item) + 1) {
    if (_wcsnicmp(item, L"EDISK_", 6) == 0 ||
        (!source && (_wcsnicmp(item, L"PYTHON", 6) == 0 ||
                     _wcsnicmp(item, L"_PYI_", 5) == 0 ||
                     _wcsnicmp(item, L"PYINSTALLER_", 12) == 0 ||
                     _wcsnicmp(item, L"VIRTUAL_ENV=", 12) == 0 ||
                     _wcsnicmp(item, L"UV_", 3) == 0))) continue;
    entries.emplace_back(item);
  }
  FreeEnvironmentStringsW(block);
  entries.push_back(L"EDISK_ENV=" + environment);
  auto coordination = Environment(L"EDISK_INSTALL_JOB");
  if (!coordination.empty()) entries.push_back(L"EDISK_INSTALL_JOB=" + coordination);
  if (environment == L"test") entries.push_back(L"EDISK_TEST_ROOT=" + test_root);
  std::sort(entries.begin(), entries.end(), [](const auto& left, const auto& right) {
    return _wcsicmp(left.c_str(), right.c_str()) < 0;
  });
  for (const auto& entry : entries) {
    result.insert(result.end(), entry.begin(), entry.end());
    result.push_back(L'\0');
  }
  result.push_back(L'\0');
  return result;
}
}  // namespace

std::wstring ExecutableDirectory() {
  std::wstring path(32768, L'\0');
  DWORD size = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
  if (!size || size >= path.size()) return L"";
  path.resize(size);
  return std::filesystem::path(path).parent_path().wstring();
}

bool ResolveBackendLaunchFor(bool source, const std::wstring& executable_dir,
                             const std::vector<std::wstring>& arguments, BackendLaunch* launch) {
  namespace fs = std::filesystem;
  *launch = BackendLaunch{};
  launch->error = "configuration";
  std::wstring environment = source ? Environment(L"EDISK_ENV") : L"test";
  if (environment.empty()) environment = L"development";
  std::wstring test_root = source ? Environment(L"EDISK_TEST_ROOT") : L"";
#if defined(EDISK_DISTRIBUTION)
  if (source || !arguments.empty()) return false;
  environment = L"production";
  bool production = true, test = false;
#else
  bool production = false, test = false;
#endif
  const bool installed = _wcsicmp(fs::path(executable_dir).parent_path().filename().c_str(), L"releases") == 0;
  for (const auto& arg : arguments) {
    if (arg == L"--edisk-producao") {
      return false; // Perfil tecnico nunca seleciona producao, inclusive fora de releases.
    } else if (arg.rfind(L"--edisk-test-root=", 0) == 0) {
      if (test) return false;
      test = true;
      test_root = arg.substr(18);
    } else if (arg.rfind(L"--edisk-", 0) == 0) return false;
  }
  if (production && test) return false;
  if (production) environment = L"production";
  else if (test) environment = L"test";
  if (source && environment != L"development" && environment != L"test") return false;
  if (!source && !production && !test) {
    auto local = Environment(L"LOCALAPPDATA");
    if (local.empty()) return false;
    auto technical_root = fs::path(local) / (installed ? L"eDisk-validacao-instalador" : L"eDisk-validacao-tecnica");
    if (installed) technical_root /= fs::path(executable_dir).parent_path().parent_path().filename();
    test_root = technical_root.wstring();
  }
  if (environment == L"test" && !LocalAbsolute(fs::path(test_root))) return false;
  if (source) {
    fs::path root(Environment(L"EDISK_DEV_ROOT"));
    if (!LocalAbsolute(root)) return false;
    launch->directory = (root / L"backend").wstring();
    launch->executable = (root / L"backend/.venv/Scripts/python.exe").wstring();
    launch->arguments = {L"-u", L"-B", L"-m", L"app"};
  } else {
    if (!LocalAbsolute(fs::path(executable_dir))) return false;
    launch->directory = (fs::path(executable_dir) / L"backend").wstring();
    launch->executable = (fs::path(launch->directory) / L"edisk-backend.exe").wstring();
  }
  launch->profile = std::string(source ? "source/" : "bundled/") +
                    (environment == L"test" ? "test" : environment == L"production" ? "production" : "development");
  launch->environment = ChildEnvironment(environment, test_root, source);
  if (launch->environment.empty()) return false;
  std::error_code error;
  if (!fs::is_regular_file(launch->executable, error) || error) {
    launch->error = "backend_missing";
    return false;
  }
  launch->error.clear();
  return true;
}
bool ResolveBackendLaunch(BackendLaunch* launch) {
  int argc = 0;
  wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
  if (!argv) return false;
  std::vector<std::wstring> arguments;
  for (int i = 1; i < argc; ++i) arguments.emplace_back(argv[i]);
  LocalFree(argv);
#if defined(_DEBUG) || defined(EDISK_SOURCE_BACKEND)
  constexpr bool source = true;
#else
  constexpr bool source = false;
#endif
  return ResolveBackendLaunchFor(source, ExecutableDirectory(), arguments, launch);
}
