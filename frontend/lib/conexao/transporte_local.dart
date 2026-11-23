import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

enum FalhaTransporte {
  conexao,
  tempoEsgotado,
  respostaInvalida,
  http,
  cancelada,
}

class ErroTransporte implements Exception {
  const ErroTransporte(this.falha);
  final FalhaTransporte falha;
}

/// Resultado privado do cliente HTTP; não deve ser enviado a logs.
class RespostaLocal {
  const RespostaLocal(this.codigo, this.dados, this.espera, {this.arquivo});
  final int codigo;
  final Map<String, dynamic>? dados;
  final int espera;
  final List<int>? arquivo;
}

/// Fronteira comum de loopback, autorização técnica, limites e cancelamento.
class TransporteLocal {
  TransporteLocal({
    required Uri endpoint,
    required String token,
    required this.transporte,
    this.prazo = const Duration(seconds: 8),
    this.limiteBytes = 8192,
  }) : _endpoint = endpoint,
       _token = token {
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(token)) {
      throw ArgumentError('Autorização local inválida.');
    }
    if (endpoint.scheme != 'http' ||
        endpoint.host != '127.0.0.1' ||
        endpoint.port < 1 ||
        endpoint.port > 65535 ||
        endpoint.userInfo.isNotEmpty ||
        (endpoint.path.isNotEmpty && endpoint.path != '/') ||
        endpoint.hasQuery ||
        endpoint.hasFragment) {
      throw ArgumentError('O serviço exige um endpoint HTTP em loopback.');
    }
    if (prazo <= Duration.zero || limiteBytes < 1) {
      throw ArgumentError('Prazo e limite de resposta devem ser positivos.');
    }
  }

  final Uri _endpoint;
  String? _token;
  final http.Client transporte;
  final Duration prazo;
  final int limiteBytes;
  _Consulta? _consulta;
  bool _fechado = false;

  Future<RespostaLocal> solicitar(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    String? sessao,
    bool lerErros = true,
    Map<String, String>? parametros,
    List<int>? bytes,
    bool arquivo = false,
    int? limiteResposta,
  }) async {
    if (_fechado) throw const ErroTransporte(FalhaTransporte.cancelada);
    if (_consulta != null) {
      throw StateError('Já existe uma requisição em andamento.');
    }
    if (!caminho.startsWith('/') ||
        caminho.startsWith('//') ||
        caminho.contains('?') ||
        caminho.contains('#')) {
      throw ArgumentError('Caminho local inválido.');
    }
    final consulta = _Consulta();
    _consulta = consulta;
    final timer = Timer(
      prazo,
      () => consulta.interromper(FalhaTransporte.tempoEsgotado),
    );
    try {
      return await Future.any([
        _enviar(
          consulta,
          metodo,
          caminho,
          corpo,
          sessao,
          lerErros,
          parametros,
          bytes,
          arquivo,
          limiteResposta ?? limiteBytes,
        ),
        consulta.interrupcao.future,
      ]);
    } finally {
      timer.cancel();
      consulta.abortar();
      if (identical(_consulta, consulta)) _consulta = null;
    }
  }

  Future<RespostaLocal> _enviar(
    _Consulta consulta,
    String metodo,
    String caminho,
    Map<String, dynamic>? corpo,
    String? sessao,
    bool lerErros,
    Map<String, String>? parametros,
    List<int>? upload,
    bool arquivo,
    int limiteResposta,
  ) async {
    try {
      final request =
          http.AbortableRequest(
              metodo,
              _endpoint.resolve(caminho).replace(queryParameters: parametros),
              abortTrigger: consulta.aborto.future,
            )
            ..followRedirects = false
            ..headers['Accept'] = 'application/json'
            ..headers['Authorization'] = 'Bearer $_token';
      if (sessao != null) request.headers['X-eDisk-Session'] = sessao;
      if (corpo != null) {
        request.headers['Content-Type'] = 'application/json; charset=utf-8';
        request.body = jsonEncode(corpo);
      }
      if (upload != null) {
        request.headers['Content-Type'] = 'application/octet-stream';
        request.bodyBytes = upload;
      }
      final response = await transporte.send(request);
      final codigo = response.statusCode;
      final espera = int.tryParse(response.headers['retry-after'] ?? '') ?? 0;
      if (codigo == 204 ||
          (codigo != 200 && !lerErros) ||
          (codigo >= 300 && codigo < 400)) {
        response.stream.listen(null, onError: (Object _) {}).cancel().ignore();
        return RespostaLocal(codigo, null, espera);
      }
      if ((response.contentLength ?? 0) > limiteResposta) {
        response.stream.listen(null, onError: (Object _) {}).cancel().ignore();
        throw const ErroTransporte(FalhaTransporte.respostaInvalida);
      }
      final bytes = <int>[];
      await for (final chunk in response.stream) {
        if (chunk.length > limiteResposta - bytes.length) {
          throw const ErroTransporte(FalhaTransporte.respostaInvalida);
        }
        bytes.addAll(chunk);
      }
      if (arquivo &&
          codigo == 200 &&
          response.headers['content-type']?.split(';').first.trim() ==
              'text/csv') {
        return RespostaLocal(codigo, null, espera, arquivo: bytes);
      }
      if (response.headers['content-type']
              ?.split(';')
              .first
              .trim()
              .toLowerCase() !=
          'application/json') {
        throw const ErroTransporte(FalhaTransporte.respostaInvalida);
      }
      final dados = jsonDecode(utf8.decode(bytes));
      if (dados is! Map<String, dynamic>) {
        throw const ErroTransporte(FalhaTransporte.respostaInvalida);
      }
      return RespostaLocal(codigo, dados, espera);
    } on ErroTransporte {
      rethrow;
    } on http.RequestAbortedException {
      throw const ErroTransporte(FalhaTransporte.cancelada);
    } on SocketException {
      throw const ErroTransporte(FalhaTransporte.conexao);
    } on FormatException {
      throw const ErroTransporte(FalhaTransporte.respostaInvalida);
    } on Exception {
      throw const ErroTransporte(FalhaTransporte.http);
    }
  }

  void cancelar() => _consulta?.interromper(FalhaTransporte.cancelada);
  void close() {
    if (_fechado) return;
    _fechado = true;
    _token = null;
    cancelar();
    transporte.close();
  }
}

class _Consulta {
  final aborto = Completer<void>();
  final interrupcao = Completer<RespostaLocal>();
  void abortar() {
    if (!aborto.isCompleted) aborto.complete();
  }

  void interromper(FalhaTransporte falha) {
    if (!interrupcao.isCompleted) {
      interrupcao.completeError(ErroTransporte(falha));
    }
    abortar();
  }
}
