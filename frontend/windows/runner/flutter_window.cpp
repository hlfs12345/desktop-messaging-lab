#include "flutter_window.h"

#include <optional>

#include "flutter/generated_plugin_registrant.h"

namespace { constexpr UINT kBackendChanged = WM_APP + 41; }

FlutterWindow::FlutterWindow(const flutter::DartProject& project)
    : project_(project) {}

FlutterWindow::~FlutterWindow() {}

bool FlutterWindow::OnCreate() {
  if (!Win32Window::OnCreate()) {
    return false;
  }

  RECT frame = GetClientArea();

  // The size here must match the window dimensions to avoid unnecessary surface
  // creation / destruction in the startup path.
  flutter_controller_ = std::make_unique<flutter::FlutterViewController>(
      frame.right - frame.left, frame.bottom - frame.top, project_);
  // Ensure that basic setup of the controller was successful.
  if (!flutter_controller_->engine() || !flutter_controller_->view()) {
    return false;
  }
  RegisterPlugins(flutter_controller_->engine());
  file_dialogs_ = CreateFileDialogs(flutter_controller_->engine()->messenger(), GetHandle(), &file_dialog_state_);
  const HWND target = GetHandle();
  supervisor_ = std::make_unique<BackendSupervisor>([target] {
    PostMessageW(target, kBackendChanged, 0, 0);
  });
  backend_channel_ = std::make_unique<BackendChannel>(flutter_controller_->engine()->messenger(), supervisor_.get());
  BackendLaunch launch;
  if (!ResolveBackendLaunch(&launch)) launch.executable.clear();
  std::wstring profile(launch.profile.begin(), launch.profile.end());
  SetWindowTextW(GetHandle(), (L"Messaging Lab - DEMONSTRACAO EXPERIMENTAL [" + profile + L"]").c_str());
  supervisor_->Start(std::move(launch));
  SetChildContent(flutter_controller_->view()->GetNativeWindow());

  flutter_controller_->engine()->SetNextFrameCallback([&]() {
    this->Show();
  });

  // Flutter can complete the first frame before the "show window" callback is
  // registered. The following call ensures a frame is pending to ensure the
  // window is shown. It is a no-op if the first frame hasn't completed yet.
  flutter_controller_->ForceRedraw();

  return true;
}

void FlutterWindow::OnDestroy() {
  file_dialogs_.reset();
  backend_channel_.reset();
  supervisor_.reset();  // Contenção também cobre destruição fora de WM_CLOSE.
  if (flutter_controller_) {
    flutter_controller_ = nullptr;
  }

  Win32Window::OnDestroy();
}

LRESULT
FlutterWindow::MessageHandler(HWND hwnd, UINT const message,
                              WPARAM const wparam,
                              LPARAM const lparam) noexcept {
  if (message == WM_CLOSE && supervisor_) supervisor_->CloseAdmission();
  if (message == WM_CLOSE && DeferCloseForFileDialog(hwnd, &file_dialog_state_)) return 0;
  if (message == kBackendChanged && supervisor_) {
    backend_channel_->Publish();
    const auto state = supervisor_->Snapshot().state;
    if (closing_ && (state == "stopped" || state == "failed" || state == "exited")) {
      DestroyWindow(hwnd);
    }
    return 0;
  }
  if (message == WM_CLOSE && supervisor_) {
    const auto state = supervisor_->Snapshot().state;
    if (state != "stopped" && state != "failed" && state != "exited") {
      if (!closing_) {
        closing_ = true;
        EnableWindow(hwnd, FALSE);
        supervisor_->RequestStop();
      }
      return 0;
    }
  }
  if (message == WM_ENDSESSION && wparam && supervisor_) {
    supervisor_->RequestStop();
    supervisor_->AcknowledgeStop();
  }
  // Give Flutter, including plugins, an opportunity to handle window messages.
  if (flutter_controller_) {
    std::optional<LRESULT> result =
        flutter_controller_->HandleTopLevelWindowProc(hwnd, message, wparam,
                                                      lparam);
    if (result) {
      return *result;
    }
  }

  switch (message) {
    case WM_FONTCHANGE:
      flutter_controller_->engine()->ReloadSystemFonts();
      break;
  }

  return Win32Window::MessageHandler(hwnd, message, wparam, lparam);
}
