#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <windows.h>
#include <filesystem>

#include "flutter_window.h"
#include "utils.h"
#include "installation.h"
#include "runner_policy.h"

int APIENTRY wWinMain(_In_ HINSTANCE instance, _In_opt_ HINSTANCE prev,
                      _In_ wchar_t *command_line, _In_ int show_command) {
  // Antes do Flutter/backend: trava compartilhada, perfil e inventario da versao ativa.
  auto ediskDirectory = edisk_install::OwnDirectory();
  edisk_install::Guard ediskGuard;
  bool installed = _wcsicmp(ediskDirectory.parent_path().filename().c_str(), L"releases") == 0;
#if defined(EDISK_DISTRIBUTION)
  installed = true;
#endif
  if (installed && !edisk_install::Runner(&ediskGuard, ediskDirectory,
      EDISK_CHANNEL, EDISK_NATIVE_VERSION)) {
    MessageBoxW(nullptr, L"A instalacao do Messaging Lab esta em manutencao, incompleta ou esta versao foi substituida. Execute novamente o instalador para reparar.", L"Messaging Lab", MB_OK | MB_ICONERROR);
    return EXIT_FAILURE;
  }
  // Attach to console when present (e.g., 'flutter run') or create a
  // new console when running with a debugger.
  if (!::AttachConsole(ATTACH_PARENT_PROCESS) && ::IsDebuggerPresent()) {
    CreateAndAttachConsole();
  }

  // Initialize COM, so that it is available for use in the library and/or
  // plugins.
  ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  flutter::DartProject project((std::filesystem::path(ExecutableDirectory()) / L"data").wstring());

  std::vector<std::string> command_line_arguments =
      GetCommandLineArguments();

  project.set_dart_entrypoint_arguments(std::move(command_line_arguments));

  FlutterWindow window(project);
  Win32Window::Point origin(10, 10);
  Win32Window::Size size(1280, 720);
  if (!window.Create(L"Messaging Lab", origin, size)) {
    return EXIT_FAILURE;
  }
  window.SetQuitOnClose(true);

  ::MSG msg;
  while (::GetMessage(&msg, nullptr, 0, 0)) {
    ::TranslateMessage(&msg);
    ::DispatchMessage(&msg);
  }

  ::CoUninitialize();
  return EXIT_SUCCESS;
}
