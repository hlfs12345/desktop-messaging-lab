import 'dart:async';

import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:edisk/processos/supervisor_backend.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';

class SupervisorSimulado implements SupervisorBackend {
  final controller = StreamController<SnapshotBackend>.broadcast(sync: true);
  int anexacoes = 0;
  int confirmacoes = 0;
  int encerramentos = 0;
  bool fechado = false;
  Future<void>? confirmacaoPendente;
  void Function()? aoEncerrar;
  @override
  Stream<SnapshotBackend> get eventos => controller.stream;
  @override
  Future<void> anexar() async {
    anexacoes++;
  }

  @override
  Future<void> confirmarPronto(String execucao) async {
    confirmacoes++;
    await confirmacaoPendente;
  }

  @override
  Future<void> confirmarEncerramento() async {
    encerramentos++;
    aoEncerrar?.call();
  }

  @override
  Future<void> close() async {
    fechado = true;
    await controller.close();
  }

  void emitir(
    int seq,
    String estado, {
    ContextoBackend? contexto,
    String codigo = '',
  }) => controller.add(
    SnapshotBackend(
      sequencia: seq,
      estado: estado,
      contexto: contexto,
      codigo: codigo,
    ),
  );
}

ContextoBackend contexto([String? id]) => ContextoBackend(
  execucao: id ?? '11111111111111111111111111111111',
  porta: 49152,
  token: tokenTeste,
);
Future<void> concluir() => Future<void>.delayed(Duration.zero);

void main() {
  test(
    'erro tardio na confirmação nativa não invalida uma nova geração',
    () async {
      final supervisor = SupervisorSimulado();
      final antiga = Completer<void>();
      supervisor.confirmacaoPendente = antiga.future;
      final controlador = ControladorConexao(
        supervisor: supervisor,
        criarCliente: (contexto) => ClienteHealth(
          endpoint: contexto.endpoint,
          token: contexto.token,
          transporte: TransporteSimulado((_) => resposta()),
        ),
      );
      addTearDown(controlador.dispose);
      supervisor.emitir(1, 'ready', contexto: contexto());
      await concluir();
      supervisor.confirmacaoPendente = null;
      supervisor.emitir(
        2,
        'ready',
        contexto: contexto('22222222222222222222222222222222'),
      );
      await concluir();
      antiga.completeError(StateError('confirmação antiga'));
      await concluir();
      expect(controlador.estado, EstadoConexao.disponivel);
      expect(controlador.podeVerificar, isTrue);
    },
  );
  test(
    'READY exige health; eventos antigos e duplicados não criam cliente',
    () async {
      final supervisor = SupervisorSimulado();
      final respostaPendente = Completer<http.StreamedResponse>();
      final transporte = TransporteSimulado((_) => respostaPendente.future);
      var criacoes = 0;
      final controlador = ControladorConexao(
        supervisor: supervisor,
        criarCliente: (contexto) {
          criacoes++;
          return ClienteHealth(
            endpoint: contexto.endpoint,
            token: contexto.token,
            transporte: transporte,
          );
        },
      );
      addTearDown(controlador.dispose);
      expect(controlador.estado, EstadoConexao.iniciandoBackend);
      expect(controlador.podeVerificar, isFalse);
      supervisor.emitir(1, 'preparing');
      expect(controlador.estado, EstadoConexao.preparandoPersistencia);
      supervisor.emitir(2, 'ready', contexto: contexto());
      expect(controlador.estado, EstadoConexao.inicializando);
      supervisor.emitir(1, 'preparing');
      supervisor.emitir(2, 'ready', contexto: contexto());
      supervisor.emitir(3, 'ready', contexto: contexto());
      expect(criacoes, 1);
      await controlador.verificar();
      expect(transporte.requisicoes, hasLength(1));
      respostaPendente.complete(resposta());
      await concluir();
      expect(controlador.estado, EstadoConexao.disponivel);
      expect(supervisor.confirmacoes, 1);
      expect(supervisor.anexacoes, 1);
    },
  );

  test(
    'morte cancela HTTP e não publica sucesso tardio nem revive contexto',
    () async {
      final supervisor = SupervisorSimulado();
      final pendente = Completer<http.StreamedResponse>();
      final transporte = TransporteSimulado((_) => pendente.future);
      final controlador = ControladorConexao(
        supervisor: supervisor,
        criarCliente: (contexto) => ClienteHealth(
          endpoint: contexto.endpoint,
          token: contexto.token,
          transporte: transporte,
        ),
      );
      addTearDown(controlador.dispose);
      supervisor.emitir(1, 'ready', contexto: contexto());
      supervisor.emitir(2, 'exited');
      expect(transporte.fechamentos, 1);
      expect(controlador.estado, EstadoConexao.backendEncerrado);
      expect(controlador.podeVerificar, isFalse);
      pendente.complete(resposta());
      await concluir();
      supervisor.emitir(3, 'ready', contexto: contexto());
      expect(controlador.estado, EstadoConexao.backendEncerrado);
      expect(controlador.ultimaVerificacao, isNull);
      expect(supervisor.confirmacoes, 0);
    },
  );

  test(
    'troca explícita de contexto descarta conclusão HTTP da geração anterior',
    () async {
      final supervisor = SupervisorSimulado();
      final antiga = Completer<http.StreamedResponse>();
      var criacoes = 0;
      final controlador = ControladorConexao(
        supervisor: supervisor,
        criarCliente: (contexto) => ClienteHealth(
          endpoint: contexto.endpoint,
          token: contexto.token,
          transporte: TransporteSimulado(
            (_) => ++criacoes == 1 ? antiga.future : resposta(codigo: 503),
          ),
        ),
      );
      addTearDown(controlador.dispose);
      supervisor.emitir(1, 'ready', contexto: contexto());
      supervisor.emitir(
        2,
        'ready',
        contexto: contexto('22222222222222222222222222222222'),
      );
      await concluir();
      antiga.complete(resposta());
      await concluir();
      expect(controlador.estado, EstadoConexao.indisponivel);
      expect(supervisor.confirmacoes, 0);
    },
  );

  test(
    'encerra HTTP antes de confirmar STOP e bloqueia consultas futuras',
    () async {
      final supervisor = SupervisorSimulado();
      final transporte = TransporteSimulado(
        (_) => Completer<http.StreamedResponse>().future,
      );
      final controlador = ControladorConexao(
        supervisor: supervisor,
        criarCliente: (contexto) => ClienteHealth(
          endpoint: contexto.endpoint,
          token: contexto.token,
          transporte: transporte,
        ),
      );
      addTearDown(controlador.dispose);
      supervisor.emitir(1, 'ready', contexto: contexto());
      supervisor.aoEncerrar = () => expect(transporte.fechamentos, 1);
      supervisor.emitir(2, 'stopping');
      expect(controlador.estado, EstadoConexao.encerrando);
      await controlador.verificar();
      expect(supervisor.encerramentos, 1);
      expect(transporte.requisicoes, hasLength(1));
    },
  );

  test(
    'falha do canal invalida contexto sem divulgar exceção ou reiniciar',
    () async {
      final supervisor = SupervisorSimulado();
      final controlador = ControladorConexao(
        supervisor: supervisor,
        criarCliente: (_) => throw StateError('não deve construir cliente'),
      );
      addTearDown(controlador.dispose);
      supervisor.controller.addError(StateError('caminho privado e segredo'));
      expect(controlador.estado, EstadoConexao.falhaInicializacao);
      expect(controlador.codigo, 'channel');
      await controlador.verificar();
      expect(supervisor.anexacoes, 1);
    },
  );

  test('contexto do canal valida endpoint, estado, sequência e segredo', () {
    final valido = {
      'sequence': 1,
      'state': 'ready',
      'code': '',
      'port': 80,
      'execution': '11111111111111111111111111111111',
      'token': tokenTeste,
    };
    expect(SnapshotBackend.deCanal(valido).contexto!.endpoint.port, 80);
    for (final valor in [
      null,
      {},
      {...valido, 'port': 0},
      {...valido, 'token': 'inválido'},
      {...valido, 'state': 'desconhecido'},
      {...valido, 'sequence': -1},
      {...valido, 'execution': 'id'},
    ]) {
      expect(() => SnapshotBackend.deCanal(valor), throwsFormatException);
    }
  });

  test('cliente exige segredo com tamanho e formato exatos', () {
    final transporte = TransporteSimulado((_) => resposta());
    expect(
      () => ClienteHealth(
        endpoint: Uri.parse('http://127.0.0.1:49152'),
        token: '',
        transporte: transporte,
      ),
      throwsArgumentError,
    );
    transporte.close();
  });
}
