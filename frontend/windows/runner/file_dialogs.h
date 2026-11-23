#ifndef RUNNER_FILE_DIALOGS_H_
#define RUNNER_FILE_DIALOGS_H_
#include <flutter/method_channel.h>
#include <flutter/encodable_value.h>
#include <windows.h>
#include <memory>
struct FileDialogState {
  HWND active = nullptr;
  bool opening = false;
  bool close_requested = false;
};
bool DeferCloseForFileDialog(HWND owner, FileDialogState* state);
std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>>
CreateFileDialogs(flutter::BinaryMessenger* messenger, HWND owner, FileDialogState* state);
#endif
