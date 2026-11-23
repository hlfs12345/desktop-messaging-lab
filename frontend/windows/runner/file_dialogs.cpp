#include "file_dialogs.h"
#include <flutter/standard_method_codec.h>
#include <commdlg.h>
#include <string>
#include <vector>

namespace {
UINT_PTR CALLBACK FileDialogHook(HWND child, UINT message, WPARAM, LPARAM param) {
  if (message == WM_INITDIALOG) {
    auto* options = reinterpret_cast<OPENFILENAMEW*>(param);
    auto* state = reinterpret_cast<FileDialogState*>(options->lCustData);
    SetWindowLongPtrW(child, DWLP_USER, reinterpret_cast<LONG_PTR>(state));
    state->active = GetParent(child);
    if (state->close_requested) PostMessageW(state->active, WM_CLOSE, 0, 0);
  } else if (message == WM_DESTROY) {
    auto* state = reinterpret_cast<FileDialogState*>(GetWindowLongPtrW(child, DWLP_USER));
    if (state) state->active = nullptr;
  }
  return 0;
}
}

bool DeferCloseForFileDialog(HWND owner, FileDialogState* state) {
  if (!state->opening) return false;
  state->close_requested = true;
  if (state->active && state->active != owner) PostMessageW(state->active, WM_CLOSE, 0, 0);
  return true;
}

std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>>
CreateFileDialogs(flutter::BinaryMessenger* messenger, HWND owner, FileDialogState* state) {
  auto channel = std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
      messenger, "edisk/file_dialogs", &flutter::StandardMethodCodec::GetInstance());
  channel->SetMethodCallHandler([owner, state](const auto& call, auto result) {
    const bool save = call.method_name() == "salvar";
    if (!save && call.method_name() != "abrir") {
      result->NotImplemented(); return;
    }
    if (state->opening) { result->Error("dialogo", "Um diálogo já está aberto."); return; }
    std::vector<wchar_t> buffer(32768, L'\0');
    if (save) wcscpy_s(buffer.data(), buffer.size(), L"erros-importacao.csv");
    OPENFILENAMEW dialog{};
    dialog.lStructSize = sizeof(dialog);
    dialog.hwndOwner = owner;
    dialog.lpstrFile = buffer.data();
    dialog.nMaxFile = static_cast<DWORD>(buffer.size());
    dialog.lpstrFilter = save ? L"Relatorio CSV\0*.csv\0\0" :
        L"Planilhas Excel e CSV\0*.xlsx;*.csv\0\0";
    dialog.lpstrDefExt = save ? L"csv" : nullptr;
    dialog.lpfnHook = FileDialogHook;
    dialog.lCustData = reinterpret_cast<LPARAM>(state);
    dialog.Flags = OFN_EXPLORER | OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST | OFN_ENABLEHOOK |
        (save ? OFN_OVERWRITEPROMPT : OFN_FILEMUSTEXIST);
    state->opening = true;
    const BOOL ok = save ? GetSaveFileNameW(&dialog) : GetOpenFileNameW(&dialog);
    state->opening = false;
    if (state->close_requested) {
      state->close_requested = false;
      result->Success();
      PostMessageW(owner, WM_CLOSE, 0, 0);
      return;
    }
    if (!ok) {
      if (CommDlgExtendedError()) result->Error("dialogo", "Não foi possível selecionar o arquivo.");
      else result->Success();
      return;
    }
    const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
        buffer.data(), -1, nullptr, 0, nullptr, nullptr);
    if (size <= 0) { result->Error("dialogo", "Caminho inválido."); return; }
    std::string path(static_cast<size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, buffer.data(), -1,
        path.data(), size, nullptr, nullptr);
    path.resize(static_cast<size_t>(size - 1));
    result->Success(flutter::EncodableValue(path));
  });
  return channel;
}
