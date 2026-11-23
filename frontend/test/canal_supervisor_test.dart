import 'dart:async';

import 'package:edisk/processos/supervisor_backend.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const canal = MethodChannel('edisk/backend');
  const codec = StandardMethodCodec();
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  Future<void> evento(String assinatura, int sequencia) async {
    final concluido = Completer<void>();
    messenger.handlePlatformMessage(
      'edisk/backend',
      codec.encodeMethodCall(
        MethodCall('state', {
          'subscription': assinatura,
          'snapshot': {'sequence': sequencia, 'state': 'preparing', 'code': ''},
        }),
      ),
      (_) => concluido.complete(),
    );
    await concluido.future;
    await Future<void>.delayed(Duration.zero);
  }

  test('nova anexaçao recebe snapshot e descarte antigo preserva a assinatura atual', () async {
    final assinaturas = <String>[];
    messenger.setMockMethodCallHandler(canal, (call) async {
      if (call.method == 'attach') {
        assinaturas.add(call.arguments as String);
        return {'sequence': 1, 'state': 'starting', 'code': ''};
      }
      return null;
    });
    addTearDown(() => messenger.setMockMethodCallHandler(canal, null));
    final antigo = SupervisorWindows();
    final atual = SupervisorWindows();
    final antigos = <int>[];
    final atuais = <int>[];
    final subscription1 = antigo.eventos.listen(
      (e) => antigos.add(e.sequencia),
    );
    final subscription2 = atual.eventos.listen((e) => atuais.add(e.sequencia));
    await antigo.anexar();
    await atual.anexar();
    expect(assinaturas, hasLength(2));
    expect(assinaturas[0], isNot(assinaturas[1]));
    await antigo.close();
    await evento(assinaturas[0], 2);
    await evento(assinaturas[1], 3);
    expect(antigos, [1]);
    expect(atuais, [1, 3]);
    await subscription1.cancel();
    await subscription2.cancel();
    await atual.close();
  });

  test('snapshot recebido após close não publica evento tardio', () async {
    final pendente = Completer<Object?>();
    messenger.setMockMethodCallHandler(
      canal,
      (call) async => call.method == 'attach' ? pendente.future : null,
    );
    addTearDown(() => messenger.setMockMethodCallHandler(canal, null));
    final supervisor = SupervisorWindows();
    final eventos = <SnapshotBackend>[];
    final subscription = supervisor.eventos.listen(eventos.add);
    final anexo = supervisor.anexar();
    await Future<void>.delayed(Duration.zero);
    await supervisor.close();
    pendente.complete({'sequence': 1, 'state': 'preparing', 'code': ''});
    await anexo;
    expect(eventos, isEmpty);
    await subscription.cancel();
  });
}
