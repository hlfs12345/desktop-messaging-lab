import 'package:http/http.dart' as http;

import 'transporte_local.dart';

enum ResultadoHealth {
  disponivel,
  indisponivel,
  falhaConexao,
  tempoEsgotado,
  respostaInvalida,
  httpInesperado,
  cancelada,
}

/// Mantém o contrato da Entrega 5 sobre o transporte local compartilhado.
class ClienteHealth {
  ClienteHealth({
    required Uri endpoint,
    required String token,
    required this.transporte,
    this.prazo = const Duration(seconds: 8),
    this.limiteBytes = 8192,
  }) : _local = TransporteLocal(
         endpoint: endpoint,
         token: token,
         transporte: transporte,
         prazo: prazo,
         limiteBytes: limiteBytes,
       );

  final http.Client transporte;
  final Duration prazo;
  final int limiteBytes;
  final TransporteLocal _local;

  Future<ResultadoHealth> verificar() async {
    try {
      final resposta = await _local.solicitar(
        'GET',
        '/health',
        lerErros: false,
      );
      if (resposta.codigo == 503) return ResultadoHealth.indisponivel;
      if (resposta.codigo != 200) return ResultadoHealth.httpInesperado;
      return resposta.dados?['status'] == 'ok'
          ? ResultadoHealth.disponivel
          : ResultadoHealth.respostaInvalida;
    } on ErroTransporte catch (erro) {
      return switch (erro.falha) {
        FalhaTransporte.conexao => ResultadoHealth.falhaConexao,
        FalhaTransporte.tempoEsgotado => ResultadoHealth.tempoEsgotado,
        FalhaTransporte.respostaInvalida => ResultadoHealth.respostaInvalida,
        FalhaTransporte.http => ResultadoHealth.httpInesperado,
        FalhaTransporte.cancelada => ResultadoHealth.cancelada,
      };
    }
  }

  void close() => _local.close();
}
