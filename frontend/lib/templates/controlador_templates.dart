import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';

import '../autenticacao/cliente_autenticacao.dart';
import '../autenticacao/controlador_autenticacao.dart';

const categoriasTemplate = {
  'promocoes': 'Promoções',
  'atualizacoes_solicitadas': 'Atualizações solicitadas',
  'eventos_grupos': 'Eventos e grupos',
};
const plataformasTemplate = {'whatsapp': 'WhatsApp', 'telegram': 'Telegram'};
const avisoTemplate =
    'Prévia local: não comprova aprovação da Meta nem capacidade de envio.';

String novaOperacaoTemplate() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}

/// Reutiliza a sessão local; não recebe tokens nem consulta contatos.
class ControladorTemplates extends ChangeNotifier {
  ControladorTemplates(this.autenticacao);
  final ControladorAutenticacao autenticacao;
  bool ocupado = false;
  String? mensagem;
  bool _descartado = false;

  Future<Map<String, dynamic>?> requisitar(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    Map<String, String>? parametros,
  }) async {
    if (_descartado || ocupado) return null;
    ocupado = true;
    mensagem = null;
    notifyListeners();
    try {
      final result = await autenticacao.negocio(
        metodo,
        caminho,
        corpo: corpo,
        parametros: parametros,
      );
      if (_descartado) return null;
      if (result.codigo >= 200 && result.codigo < 300) {
        return result.dados ?? <String, dynamic>{};
      }
      mensagem = switch (result.dados?['codigo']) {
        'conflito' || 'duplicidade' => 'Os dados mudaram ou a operação divergiu. Atualize e revise novamente.',
        'arquivado' => 'Template arquivado não aceita novas versões.',
        'template_invalido' => 'Use texto, links e somente a variável {{nome}}, com as chaves completas.',
        'valor_ausente' => 'Informe um nome fictício para a prévia.',
        'corpo_excessivo' => 'O conteúdo excede o limite da requisição.',
        _ =>
          'Não foi possível concluir. Confira os campos e atualize a consulta.',
      };
    } on ErroAutenticacao catch (error) {
      if (!_descartado) {
        mensagem = error.codigo == 'tempo_esgotado'
            ? 'Resposta não recebida. Repita os mesmos dados para consultar a operação sem duplicá-la.'
            : 'Operação interrompida. Entre novamente se necessário.';
      }
    } finally {
      if (!_descartado) {
        ocupado = false;
        notifyListeners();
      }
    }
    return null;
  }

  @override
  void dispose() {
    _descartado = true;
    super.dispose();
  }
}

/// Um formulário mantém o UUID ao repetir os mesmos dados após falha/timeout.
class OperacaoTemplate {
  String? _assinatura;
  String? _id;
  Map<String, dynamic> corpo(Map<String, dynamic> campos) {
    final assinatura = jsonEncode(campos);
    if (_assinatura != assinatura) {
      _assinatura = assinatura;
      _id = novaOperacaoTemplate();
    }
    return {'operacao_id': _id, ...campos};
  }
}
