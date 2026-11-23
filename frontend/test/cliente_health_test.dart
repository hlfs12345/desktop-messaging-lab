import 'dart:async';
import 'dart:io';

import 'package:edisk/conexao/cliente_health.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';

void main() {
  late TransporteSimulado transporte;
  late ClienteHealth cliente;

  void configurar(
    FutureOr<http.StreamedResponse> Function(http.BaseRequest) responder, {
    Duration prazo = const Duration(seconds: 8),
    int limite = 8192,
  }) {
    transporte = TransporteSimulado(responder);
    cliente = ClienteHealth(
      token: tokenTeste,
      endpoint: Uri.parse('http://127.0.0.1:49152'),
      transporte: transporte,
      prazo: prazo,
      limiteBytes: limite,
    );
    addTearDown(cliente.close);
  }

  test('envia somente GET /health com JSON e sem redirecionamento', () async {
    configurar((request) {
      expect(request.method, 'GET');
      expect(request.url.toString(), 'http://127.0.0.1:49152/health');
      expect(request.headers['Accept'], 'application/json');
      expect(request.headers['Authorization'], 'Bearer $tokenTeste');
      expect(request.followRedirects, isFalse);
      expect(request, isA<http.AbortableRequest>());
      return resposta(tipo: 'application/json; charset=utf-8');
    });
    expect(await cliente.verificar(), ResultadoHealth.disponivel);
    expect(await cliente.verificar(), ResultadoHealth.disponivel);
    expect(transporte.requisicoes, hasLength(2));
  });

  test('porta 80 continua válida após normalização do Uri', () async {
    final transporte = TransporteSimulado((_) => resposta());
    final cliente = ClienteHealth(
      token: tokenTeste,
      endpoint: Uri.parse('http://127.0.0.1:80'),
      transporte: transporte,
    );
    addTearDown(cliente.close);
    expect(await cliente.verificar(), ResultadoHealth.disponivel);
    expect(transporte.requisicoes.single.url.port, 80);
  });

  test('HTTP 503 indica indisponibilidade sem depender do corpo', () async {
    configurar((_) => resposta(codigo: 503, corpo: 'detalhe privado'));
    expect(await cliente.verificar(), ResultadoHealth.indisponivel);
    expect(transporte.requisicoes, hasLength(1));
  });

  test('503 descarta corpo excessivo sem alterar indisponibilidade', () async {
    var cancelado = false;
    final corpo = StreamController<List<int>>(
      onCancel: () {
        cancelado = true;
      },
    );
    configurar(
      (_) => http.StreamedResponse(corpo.stream, 503, contentLength: 1000000),
    );
    expect(await cliente.verificar(), ResultadoHealth.indisponivel);
    expect(cancelado, isTrue);
    await corpo.close();
  });

  test('conexão recusada é normalizada sem expor a exceção', () async {
    configurar((_) => throw const SocketException('segredo e caminho privado'));
    expect(await cliente.verificar(), ResultadoHealth.falhaConexao);
  });

  for (final codigo in [201, 204, 301, 302, 307, 308, 400, 401, 404, 500]) {
    test('código $codigo não é sucesso nem provoca nova requisição', () async {
      configurar((_) => resposta(codigo: codigo));
      expect(await cliente.verificar(), ResultadoHealth.httpInesperado);
      expect(transporte.requisicoes, hasLength(1));
      expect(transporte.requisicoes.single.followRedirects, isFalse);
    });
  }

  for (final corpo in [
    'não é JSON',
    'null',
    '[]',
    '"ok"',
    '{}',
    '{"status":true}',
    '{"status":"indisponivel"}',
    '{"status":"OK"}',
  ]) {
    test('recusa JSON inválido ou contrato incompatível: $corpo', () async {
      configurar((_) => resposta(corpo: corpo));
      expect(await cliente.verificar(), ResultadoHealth.respostaInvalida);
    });
  }

  test('recusa Content-Type incompatível', () async {
    configurar((_) => resposta(tipo: 'text/html'));
    expect(await cliente.verificar(), ResultadoHealth.respostaInvalida);
  });

  test('recusa UTF-8 inválido', () async {
    configurar(
      (_) => http.StreamedResponse(
        Stream.value([0xff]),
        200,
        headers: {'content-type': 'application/json'},
      ),
    );
    expect(await cliente.verificar(), ResultadoHealth.respostaInvalida);
  });

  test(
    'timeout antes dos cabeçalhos aborta efetivamente o transporte',
    () async {
      final abortado = Completer<void>();
      configurar((request) async {
        await (request as http.AbortableRequest).abortTrigger;
        abortado.complete();
        throw http.RequestAbortedException(request.url);
      }, prazo: const Duration(milliseconds: 20));
      expect(await cliente.verificar(), ResultadoHealth.tempoEsgotado);
      await abortado.future;
      expect(transporte.requisicoes, hasLength(1));
    },
  );

  test('timeout inclui o corpo e cancela a assinatura', () async {
    var assinaturaCancelada = false;
    final corpo = StreamController<List<int>>(
      onCancel: () {
        assinaturaCancelada = true;
      },
    );
    configurar((request) {
      (request as http.AbortableRequest).abortTrigger!.then((_) {
        if (!corpo.isClosed) {
          corpo.addError(http.RequestAbortedException(request.url));
          unawaited(corpo.close());
        }
      });
      return http.StreamedResponse(
        corpo.stream,
        200,
        headers: {'content-type': 'application/json'},
      );
    }, prazo: const Duration(milliseconds: 20));
    expect(await cliente.verificar(), ResultadoHealth.tempoEsgotado);
    await Future<void>.delayed(Duration.zero);
    expect(assinaturaCancelada, isTrue);
  });

  test('limite considera bytes acumulados e cancela o corpo', () async {
    var cancelado = false;
    final corpo = StreamController<List<int>>(
      onListen: () {},
      onCancel: () {
        cancelado = true;
      },
    );
    configurar((_) {
      corpo.add([1, 2]);
      corpo.add([3, 4]);
      return http.StreamedResponse(corpo.stream, 200);
    }, limite: 3);
    expect(await cliente.verificar(), ResultadoHealth.respostaInvalida);
    expect(cancelado, isTrue);
    await corpo.close();
  });

  test(
    'limite também recusa Content-Length excessivo e libera stream',
    () async {
      var cancelado = false;
      final corpo = StreamController<List<int>>(
        onCancel: () {
          cancelado = true;
        },
      );
      configurar(
        (_) => http.StreamedResponse(corpo.stream, 200, contentLength: 8193),
      );
      expect(await cliente.verificar(), ResultadoHealth.respostaInvalida);
      expect(cancelado, isTrue);
      await corpo.close();
    },
  );

  test('aceita corpo com tamanho exatamente igual ao limite', () async {
    configurar((_) => resposta(), limite: 15);
    expect(await cliente.verificar(), ResultadoHealth.disponivel);
  });

  test('close durante requisição aborta, conclui e fecha uma vez', () async {
    final iniciou = Completer<void>();
    final abortado = Completer<void>();
    configurar((request) async {
      iniciou.complete();
      await (request as http.AbortableRequest).abortTrigger;
      abortado.complete();
      throw http.RequestAbortedException(request.url);
    });
    final consulta = cliente.verificar();
    await iniciou.future;
    cliente.close();
    cliente.close();
    expect(await consulta, ResultadoHealth.cancelada);
    await abortado.future;
    expect(transporte.fechamentos, 1);
    expect(await cliente.verificar(), ResultadoHealth.cancelada);
    expect(transporte.requisicoes, hasLength(1));
  });

  test('close durante corpo cancela operação e libera assinatura', () async {
    final iniciou = Completer<void>();
    final cancelou = Completer<void>();
    final corpo = StreamController<List<int>>(
      onListen: () => iniciou.complete(),
      onCancel: () => cancelou.complete(),
    );
    configurar((request) {
      (request as http.AbortableRequest).abortTrigger!.then((_) {
        corpo.addError(http.RequestAbortedException(request.url));
        unawaited(corpo.close());
      });
      return http.StreamedResponse(corpo.stream, 200);
    });
    final consulta = cliente.verificar();
    await iniciou.future;
    cliente.close();
    expect(await consulta, ResultadoHealth.cancelada);
    await cancelou.future;
    expect(transporte.fechamentos, 1);
  });

  test(
    'erro tardio de transporte após timeout não fica sem tratamento',
    () async {
      final pendente = Completer<http.StreamedResponse>();
      configurar(
        (_) => pendente.future,
        prazo: const Duration(milliseconds: 20),
      );
      expect(await cliente.verificar(), ResultadoHealth.tempoEsgotado);
      pendente.completeError(http.ClientException('conteúdo privado'));
      await Future<void>.delayed(Duration.zero);
    },
  );

  test('falha de protocolo é normalizada', () async {
    configurar((_) => throw http.ClientException('detalhe privado'));
    expect(await cliente.verificar(), ResultadoHealth.httpInesperado);
  });

  test('não aceita consultas simultâneas no cliente', () async {
    final pendente = Completer<http.StreamedResponse>();
    configurar((_) => pendente.future);
    final primeira = cliente.verificar();
    await expectLater(cliente.verificar(), throwsStateError);
    pendente.complete(resposta());
    expect(await primeira, ResultadoHealth.disponivel);
  });

  test('recusa endpoint fora do contrato local', () {
    for (final valor in [
      'http://localhost:80',
      'http://192.168.1.2:80',
      'https://127.0.0.1:80',
      'http://127.0.0.1:0',
      'http://127.0.0.1:70000',
      'http://127.0.0.1:80/privado',
      'http://usuario@127.0.0.1:80',
      'http://127.0.0.1:80?a=b',
      'http://127.0.0.1:80#fragmento',
    ]) {
      final transporte = TransporteSimulado((_) => resposta());
      expect(
        () => ClienteHealth(
          token: tokenTeste,
          endpoint: Uri.parse(valor),
          transporte: transporte,
        ),
        throwsArgumentError,
      );
      transporte.close();
    }
  });
}
