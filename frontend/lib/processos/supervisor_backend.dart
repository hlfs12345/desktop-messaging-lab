import 'dart:async';
import 'dart:math';

import 'package:flutter/services.dart';

/// Segredo somente em memória; não incluir este objeto em logs/diagnósticos.
class ContextoBackend {
  ContextoBackend({
    required this.execucao,
    required int porta,
    required this.token,
  }) : endpoint = Uri(scheme: 'http', host: '127.0.0.1', port: porta) {
    if (!RegExp(r'^[0-9a-f]{32}$').hasMatch(execucao) ||
        !RegExp(r'^[0-9a-f]{64}$').hasMatch(token) ||
        porta < 1 ||
        porta > 65535) {
      throw const FormatException('Contexto local inválido.');
    }
  }
  final String execucao;
  final Uri endpoint;
  final String token;
}

class SnapshotBackend {
  SnapshotBackend({
    required this.sequencia,
    required this.estado,
    this.contexto,
    this.codigo = '',
  });

  factory SnapshotBackend.deCanal(Object? raw) {
    if (raw is! Map ||
        raw['sequence'] is! int ||
        raw['state'] is! String ||
        raw['code'] is! String ||
        (raw['sequence'] as int) < 0) {
      throw const FormatException('Estado local inválido.');
    }
    final estado = raw['state'] as String;
    if (!{
      'starting',
      'preparing',
      'ready',
      'failed',
      'exited',
      'stopping',
      'stopped',
    }.contains(estado)) {
      throw const FormatException('Estado local desconhecido.');
    }
    ContextoBackend? contexto;
    if (estado == 'ready') {
      if (raw['port'] is! int ||
          raw['execution'] is! String ||
          raw['token'] is! String) {
        throw const FormatException('Contexto local ausente.');
      }
      contexto = ContextoBackend(
        execucao: raw['execution'] as String,
        porta: raw['port'] as int,
        token: raw['token'] as String,
      );
    }
    return SnapshotBackend(
      sequencia: raw['sequence'] as int,
      estado: estado,
      contexto: contexto,
      codigo: raw['code'] as String,
    );
  }

  final int sequencia;
  final String estado;
  final ContextoBackend? contexto;
  final String codigo;
}

abstract interface class SupervisorBackend {
  Stream<SnapshotBackend> get eventos;
  Future<void> anexar();
  Future<void> confirmarPronto(String execucao);
  Future<void> confirmarEncerramento();
  Future<void> close();
}

class SupervisorWindows implements SupervisorBackend {
  static const _metodos = MethodChannel('edisk/backend');
  static SupervisorWindows? _ativo;
  final _controller = StreamController<SnapshotBackend>.broadcast();
  final String _assinatura = List.generate(
    16,
    (_) => Random.secure().nextInt(256).toRadixString(16).padLeft(2, '0'),
  ).join();
  bool _anexado = false;
  bool _fechado = false;

  @override
  Stream<SnapshotBackend> get eventos => _controller.stream;

  void _receber(Object? raw) {
    if (_fechado) return;
    try {
      _controller.add(SnapshotBackend.deCanal(raw));
    } on Object {
      _controller.addError(StateError('Falha no estado do serviço local.'));
    }
  }

  @override
  Future<void> anexar() async {
    if (_fechado || _anexado) return;
    _anexado = true;
    _ativo = this;
    _metodos.setMethodCallHandler((call) async {
      if (_fechado || call.method != 'state') return;
      final raw = call.arguments;
      if (raw is Map && raw['subscription'] == _assinatura) {
        _receber(raw['snapshot']);
      }
    });
    // O handler já está instalado quando o runner registra a assinatura e lê
    // seu snapshot na thread da janela. A sequência ordena respostas/eventos.
    _receber(await _metodos.invokeMethod<Object?>('attach', _assinatura));
  }

  @override
  Future<void> confirmarPronto(String execucao) =>
      _metodos.invokeMethod<void>('confirmReady', execucao);

  @override
  Future<void> confirmarEncerramento() =>
      _metodos.invokeMethod<void>('acknowledgeStop');

  @override
  Future<void> close() async {
    if (_fechado) return;
    _fechado = true;
    try {
      await _metodos.invokeMethod<void>('detach', _assinatura);
    } on Object {
      // O runner pode já estar fechando; nunca criar um processo para recuperar.
    }
    if (identical(_ativo, this)) {
      _metodos.setMethodCallHandler(null);
      _ativo = null;
    }
    await _controller.close();
  }
}
