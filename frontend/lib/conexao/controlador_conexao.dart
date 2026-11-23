import 'dart:async';

import 'package:flutter/foundation.dart';

import 'cliente_health.dart';
import '../processos/supervisor_backend.dart';

enum EstadoConexao {
  iniciandoBackend,
  preparandoPersistencia,
  inicializando,
  disponivel,
  indisponivel,
  erro,
  falhaInicializacao,
  backendEncerrado,
  encerrando,
}

/// Pode ser usado por outras telas. Não conhece widgets nem dart-define.
/// Assume a propriedade do cliente e inicia uma única consulta na criação.
class ControladorConexao extends ChangeNotifier {
  ControladorConexao({
    ClienteHealth? cliente,
    SupervisorBackend? supervisor,
    ClienteHealth Function(ContextoBackend)? criarCliente,
    DateTime Function()? agora,
    this.aoConectar,
    this.aoInvalidar,
    this.aoDisponivel,
  }) : _cliente = cliente,
       _supervisor = supervisor,
       _criarCliente = criarCliente,
       _agora = agora ?? DateTime.now {
    if (supervisor != null) {
      if (cliente != null || criarCliente == null) {
        throw ArgumentError('Composição do serviço local inválida.');
      }
      _estado = EstadoConexao.iniciandoBackend;
      _assinatura = supervisor.eventos.listen(
        _receberEstado,
        onError: (Object _) => _falhaSupervisor(),
      );
      unawaited(_anexar());
    } else if (cliente == null) {
      _estado = EstadoConexao.erro;
    } else {
      unawaited(verificar());
    }
  }

  ClienteHealth? _cliente;
  final SupervisorBackend? _supervisor;
  final ClienteHealth Function(ContextoBackend)? _criarCliente;
  final void Function(ContextoBackend)? aoConectar;
  final void Function()? aoInvalidar;
  final void Function()? aoDisponivel;
  StreamSubscription<SnapshotBackend>? _assinatura;
  int _sequencia = -1;
  int _geracao = 0;
  String? _execucao;
  String _codigo = '';
  final DateTime Function() _agora;
  EstadoConexao _estado = EstadoConexao.inicializando;
  ResultadoHealth? _resultado;
  DateTime? _ultimaVerificacao;
  bool _emAndamento = false;
  bool _descartado = false;
  bool _terminado = false;

  EstadoConexao get estado => _estado;
  ResultadoHealth? get resultado => _resultado;
  DateTime? get ultimaVerificacao => _ultimaVerificacao;
  bool get emAndamento => _emAndamento;
  String get codigo => _codigo;
  bool get preparando =>
      _estado == EstadoConexao.iniciandoBackend ||
      _estado == EstadoConexao.preparandoPersistencia;
  bool get podeVerificar => !_descartado && !_emAndamento && _cliente != null;

  Future<void> _anexar() async {
    try {
      await _supervisor!.anexar();
    } on Object {
      _falhaSupervisor();
    }
  }

  void _invalidar() {
    ++_geracao;
    _cliente?.close();
    // A composição cancela autenticação antes de qualquer acknowledgeStop.
    aoInvalidar?.call();
    _cliente = null;
    _execucao = null;
    _emAndamento = false;
    _resultado = null;
    _ultimaVerificacao = null;
  }

  void _falhaSupervisor() {
    if (_descartado || _terminado) return;
    _invalidar();
    _terminado = true;
    _estado = EstadoConexao.falhaInicializacao;
    _codigo = 'channel';
    notifyListeners();
  }

  void _receberEstado(SnapshotBackend snapshot) {
    if (_descartado || _terminado || snapshot.sequencia <= _sequencia) return;
    _sequencia = snapshot.sequencia;
    _codigo = snapshot.codigo;
    if (snapshot.estado == 'ready') {
      final contexto = snapshot.contexto;
      if (contexto == null) {
        _falhaSupervisor();
        return;
      }
      if (_execucao == contexto.execucao && _cliente != null) return;
      _invalidar();
      try {
        _cliente = _criarCliente!(contexto);
        _execucao = contexto.execucao;
        aoConectar?.call(contexto);
        unawaited(verificar());
      } on Object {
        _falhaSupervisor();
      }
      return;
    }
    _invalidar();
    _terminado = {
      'failed',
      'exited',
      'stopping',
      'stopped',
    }.contains(snapshot.estado);
    _estado = switch (snapshot.estado) {
      'starting' => EstadoConexao.iniciandoBackend,
      'preparing' => EstadoConexao.preparandoPersistencia,
      'exited' => EstadoConexao.backendEncerrado,
      'stopping' || 'stopped' => EstadoConexao.encerrando,
      _ => EstadoConexao.falhaInicializacao,
    };
    notifyListeners();
    if (snapshot.estado == 'stopping') {
      unawaited(_supervisor!.confirmarEncerramento().catchError((Object _) {}));
    }
  }

  Future<void> verificar() async {
    if (!podeVerificar) return;
    final geracao = _geracao;
    _emAndamento = true;
    _estado = EstadoConexao.inicializando;
    notifyListeners();
    final resultado = await _cliente!.verificar();
    if (_descartado || geracao != _geracao) return;
    if (resultado == ResultadoHealth.disponivel && _supervisor != null) {
      try {
        await _supervisor.confirmarPronto(_execucao!);
      } on Object {
        if (!_descartado && geracao == _geracao) _falhaSupervisor();
        return;
      }
      if (_descartado || geracao != _geracao) return;
    }
    _emAndamento = false;
    _resultado = resultado;
    _ultimaVerificacao = _agora().toUtc();
    _estado = switch (resultado) {
      ResultadoHealth.disponivel => EstadoConexao.disponivel,
      ResultadoHealth.indisponivel ||
      ResultadoHealth.falhaConexao ||
      ResultadoHealth.tempoEsgotado => EstadoConexao.indisponivel,
      _ => EstadoConexao.erro,
    };
    if (_estado == EstadoConexao.disponivel) aoDisponivel?.call();
    notifyListeners();
  }

  @override
  void dispose() {
    if (_descartado) return;
    _descartado = true;
    _invalidar();
    unawaited(_assinatura?.cancel());
    unawaited(_supervisor?.close().catchError((Object _) {}));
    super.dispose();
  }
}
