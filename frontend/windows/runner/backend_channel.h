#ifndef RUNNER_BACKEND_CHANNEL_H_
#define RUNNER_BACKEND_CHANNEL_H_
#include "backend_supervisor.h"
#include <flutter/method_channel.h>
#include <flutter/standard_method_codec.h>
#include <memory>

class BackendChannel {
 public:
  BackendChannel(flutter::BinaryMessenger* messenger, BackendSupervisor* supervisor);
  ~BackendChannel();
  void Publish();  // Somente na thread da janela/plataforma.
 private:
  flutter::EncodableValue Snapshot() const;
  BackendSupervisor* supervisor_;
  std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>> methods_;
  std::string subscription_;
};
#endif
