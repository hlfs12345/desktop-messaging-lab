import 'dart:async';

import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';

void main() {
  test('configuração ausente fica em erro e não permite consulta', () async {
    final controlador = ControladorConexao(cliente: null);
    addTearDown(controlador.dispose);
    await controlador.verificar();
    expect(controlador.estado, EstadoConexao.erro);
    expect(controlador.podeVerificar, isFalse);
    expect(controlador.ultimaVerificacao, isNull);
  });

  test(
    'consulta inicial, bloqueio simultâneo, transições e nova tentativa',
    () async {
      final primeira = Completer<http.StreamedResponse>();
      final segunda = Completer<http.StreamedResponse>();
      // A fila é controlada sem rede.
      var chamadas = 0;
      final fila = TransporteSimulado(
        (_) => ++chamadas == 1 ? primeira.future : segunda.future,
      );
      var agora = DateTime.utc(2026, 10, 29, 12);
      final controlador = ControladorConexao(
        cliente: ClienteHealth(
          token: tokenTeste,
          endpoint: Uri.parse('http://127.0.0.1:49152'),
          transporte: fila,
        ),
        agora: () => agora,
      );
      addTearDown(controlador.dispose);
      final estados = <EstadoConexao>[];
      controlador.addListener(() => estados.add(controlador.estado));
      expect(controlador.estado, EstadoConexao.inicializando);
      expect(controlador.emAndamento, isTrue);
      expect(controlador.ultimaVerificacao, isNull);
      await controlador.verificar();
      expect(chamadas, 1);
      primeira.complete(resposta(codigo: 503));
      await Future<void>.delayed(Duration.zero);
      expect(controlador.estado, EstadoConexao.indisponivel);
      expect(controlador.ultimaVerificacao, agora);
      agora = agora.add(const Duration(minutes: 1));
      final repeticao = controlador.verificar();
      expect(controlador.estado, EstadoConexao.inicializando);
      expect(controlador.ultimaVerificacao, DateTime.utc(2026, 10, 29, 12));
      segunda.complete(resposta());
      await repeticao;
      expect(controlador.estado, EstadoConexao.disponivel);
      expect(controlador.ultimaVerificacao, agora);
      expect(estados, [
        EstadoConexao.indisponivel,
        EstadoConexao.inicializando,
        EstadoConexao.disponivel,
      ]);
      expect(chamadas, 2);
    },
  );

  test('resposta inválida produz estado de erro', () async {
    final controlador = ControladorConexao(
      cliente: ClienteHealth(
        token: tokenTeste,
        endpoint: Uri.parse('http://127.0.0.1:49152'),
        transporte: TransporteSimulado((_) => resposta(corpo: '{}')),
      ),
    );
    addTearDown(controlador.dispose);
    await Future<void>.delayed(Duration.zero);
    expect(controlador.estado, EstadoConexao.erro);
    expect(controlador.resultado, ResultadoHealth.respostaInvalida);
  });

  test('descarte fecha recursos e ignora sucesso tardio', () async {
    final pendente = Completer<http.StreamedResponse>();
    final transporte = TransporteSimulado((_) => pendente.future);
    final controlador = ControladorConexao(
      cliente: ClienteHealth(
        token: tokenTeste,
        endpoint: Uri.parse('http://127.0.0.1:49152'),
        transporte: transporte,
      ),
    );
    var notificacoes = 0;
    controlador.addListener(() => notificacoes++);
    controlador.dispose();
    pendente.complete(resposta());
    await Future<void>.delayed(Duration.zero);
    await controlador.verificar();
    expect(notificacoes, 0);
    expect(controlador.ultimaVerificacao, isNull);
    expect(controlador.podeVerificar, isFalse);
    expect(transporte.fechamentos, 1);
  });
}
