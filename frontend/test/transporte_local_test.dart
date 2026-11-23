import 'dart:io';

import 'package:edisk/main.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('composição ignora proxy e libera o cliente nativo', () {
    final nativo = _HttpSimulado();
    HttpOverrides.runZoned(() {
      final transporte = criarTransporteLocal();
      expect(nativo.proxy!(Uri.parse('http://127.0.0.1:49152')), 'DIRECT');
      transporte.close();
      expect(nativo.fechado, isTrue);
      expect(nativo.forcado, isTrue);
    }, createHttpClient: (_) => nativo);
  });
}

class _HttpSimulado implements HttpClient {
  String Function(Uri)? proxy;
  bool fechado = false;
  bool forcado = false;

  @override
  set findProxy(String Function(Uri)? valor) => proxy = valor;

  @override
  void close({bool force = false}) {
    fechado = true;
    forcado = force;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}
