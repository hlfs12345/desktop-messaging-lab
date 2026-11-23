import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

const tokenTeste =
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

/// Transporte exclusivamente de testes, sem sockets ou banco de dados.
class TransporteSimulado extends http.BaseClient {
  TransporteSimulado(this.responder);

  final FutureOr<http.StreamedResponse> Function(http.BaseRequest) responder;
  final requisicoes = <http.BaseRequest>[];
  int fechamentos = 0;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    requisicoes.add(request);
    return responder(request);
  }

  @override
  void close() => fechamentos++;
}

http.StreamedResponse resposta({
  int codigo = 200,
  String corpo = '{"status":"ok"}',
  String tipo = 'application/json',
}) => http.StreamedResponse(
  Stream.value(utf8.encode(corpo)),
  codigo,
  headers: {'content-type': tipo},
);
