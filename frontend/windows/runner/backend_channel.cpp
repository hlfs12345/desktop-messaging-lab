#include "backend_channel.h"

using flutter::EncodableValue;
using flutter::EncodableMap;

BackendChannel::BackendChannel(flutter::BinaryMessenger* messenger, BackendSupervisor* supervisor)
    : supervisor_(supervisor) {
  methods_ = std::make_unique<flutter::MethodChannel<EncodableValue>>(
      messenger, "edisk/backend", &flutter::StandardMethodCodec::GetInstance());
  methods_->SetMethodCallHandler([this](const auto& call, auto result) {
    if (call.method_name() == "attach") {
      auto argument = call.arguments() ? std::get_if<std::string>(call.arguments()) : nullptr;
      if (!argument || argument->size() != 32) { result->Error("subscription", "Assinatura inválida."); return; }
      subscription_ = *argument;
      result->Success(Snapshot());
    } else if (call.method_name() == "detach") {
      auto argument = call.arguments() ? std::get_if<std::string>(call.arguments()) : nullptr;
      if (argument && *argument == subscription_) subscription_.clear();
      result->Success();
    } else if (call.method_name() == "snapshot") result->Success(Snapshot());
    else if (call.method_name() == "confirmReady") {
      auto argument = call.arguments() ? std::get_if<std::string>(call.arguments()) : nullptr;
      if (!argument || argument->size() != 32) { result->Error("context", "Contexto inválido."); return; }
      supervisor_->ConfirmReady(*argument);
      result->Success();
    } else if (call.method_name() == "acknowledgeStop") {
      supervisor_->AcknowledgeStop();
      result->Success();
    } else result->NotImplemented();
  });
}

BackendChannel::~BackendChannel() {
  methods_->SetMethodCallHandler(nullptr);
}
EncodableValue BackendChannel::Snapshot() const {
  const auto snapshot = supervisor_->Snapshot();
  return EncodableValue(EncodableMap{
      {EncodableValue("sequence"), EncodableValue(snapshot.sequence)},
      {EncodableValue("state"), EncodableValue(snapshot.state)},
      {EncodableValue("code"), EncodableValue(snapshot.code)},
      {EncodableValue("execution"), EncodableValue(snapshot.execution)},
      {EncodableValue("port"), EncodableValue(static_cast<int32_t>(snapshot.port))},
      {EncodableValue("token"), EncodableValue(snapshot.token)},
  });
}
void BackendChannel::Publish() {
  if (!subscription_.empty()) {
    methods_->InvokeMethod("state", std::make_unique<EncodableValue>(EncodableMap{
      {EncodableValue("subscription"), EncodableValue(subscription_)},
      {EncodableValue("snapshot"), Snapshot()},
    }));
  }
}
