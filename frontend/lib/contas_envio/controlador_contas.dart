import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';

import '../autenticacao/cliente_autenticacao.dart';
import '../autenticacao/controlador_autenticacao.dart';

const provedoresConta = {
  'qr_candidato': 'WhatsApp QR (experimental)',
  'meta_cloud': 'WhatsApp Meta Cloud API',
  'telegram_bot_api': 'Telegram Bot API',
  'mock': 'Conta de simulação (WhatsApp)',
};
const estadosConta = {
  'ativa': 'Ativa',
  'desativada': 'Desativada',
  'removida': 'Removida',
};
const configuracoesConta = {
  'simulacao_sem_credenciais': 'Credenciais não aplicáveis',
  'pendente': 'Credencial pendente',
  'credencial_armazenada': 'Credencial armazenada localmente',
  'armazenamento_indisponivel':
      'Armazenamento indisponível; reconfigure a credencial',
  'aguarda_integracao': 'Integração experimental disponível',
};

const avisoLimpezaConta =
    'Há artefatos protegidos com limpeza local pendente. A limpeza será tentada novamente.';

String novaOperacaoConta() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}

/// Somente metadados de cadastro; credenciais nunca entram nesta classe.
class OperacaoConta {
  String? _assinatura, _id;
  Map<String, dynamic> corpo(Map<String, dynamic> campos) {
    final assinatura = jsonEncode(campos);
    if (_assinatura != assinatura) {
      _assinatura = assinatura;
      _id = novaOperacaoConta();
    }
    return {'operacao_id': _id, ...campos};
  }
}

class ControladorContas extends ChangeNotifier {
  ControladorContas(this.autenticacao);
  final ControladorAutenticacao autenticacao;
  bool ocupado = false, limpezaPendente = false, _descartado = false;
  String? mensagem;
  ConsultaNegocio? _consulta;

  Future<Map<String, dynamic>?> requisitar(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    Map<String, String>? parametros,
  }) async {
    if (_descartado || ocupado) return null;
    if (autenticacao.usuario?.perfil != 'administrador') {
      mensagem = 'Somente o administrador pode gerenciar contas.';
      notifyListeners();
      return null;
    }
    final consulta = ConsultaNegocio();
    _consulta = consulta;
    ocupado = true;
    mensagem = null;
    notifyListeners();
    try {
      final result = await autenticacao.negocio(
        metodo,
        caminho,
        corpo: corpo,
        parametros: parametros,
        consulta: consulta,
      );
      if (_descartado) return null;
      if (result.codigo >= 200 && result.codigo < 300) {
        if (result.dados?['limpeza_pendente'] is bool) {
          limpezaPendente = result.dados!['limpeza_pendente'] as bool;
        }
        return result.dados ?? <String, dynamic>{};
      }
      mensagem = switch (result.dados?['codigo']) {
        'banco_ocupado' => 'Banco ocupado por outra conexão. Finalize a consulta externa e atualize os dados antes de tentar novamente.',
        'acesso_negado' => 'Somente o administrador pode gerenciar contas.',
        'conflito' || 'duplicidade' => 'Os dados mudaram ou a operação divergiu. Atualize e revise novamente.',
        'segredo_indisponivel' => 'Armazenamento protegido indisponível. Atualize e reconfigure se necessário.',
        'credencial_nao_aplicavel' =>
          'Conta de simulação não admite credenciais.',
        'aguarda_integracao' => 'WhatsApp QR aguarda integração experimental.',
        'telegram_credencial_invalida' =>
          'Credencial Telegram inválida. Reconfigure a conta.',
        'telegram_sem_credencial' => 'Armazene a credencial da conta.',
        'telegram_conta_invalida' => 'Selecione uma conta Telegram ativa.',
        'telegram_timeout' =>
          'Telegram não respondeu no prazo. Tente novamente.',
        'telegram_comunicacao' || 'telegram_http' =>
          'Não foi possível comunicar com o Telegram. Tente novamente.',
        'telegram_resposta' => 'Resposta inesperada do Telegram.',
        'whatsapp_conta_invalida' => 'Selecione uma conta WhatsApp QR.',
        'whatsapp_conta_inativa' =>
          'Ative a conta WhatsApp antes de conectar.',
        'whatsapp_sessao_existente' =>
          'A conta já possui sessão. Use Reconectar.',
        'whatsapp_sem_sessao' =>
          'A conta ainda não possui sessão. Inicie o pareamento.',
        'whatsapp_timeout' =>
          'O componente local do WhatsApp não respondeu no prazo.',
        'whatsapp_indisponivel' =>
          'O componente local do WhatsApp não está disponível.',
        _ =>
          'Não foi possível concluir. Confira os campos e atualize a consulta.',
      };
    } on ErroAutenticacao catch (error) {
      if (!_descartado) {
        mensagem = error.codigo == 'tempo_esgotado'
            ? 'Resposta não recebida. Atualize antes de alterar os dados. Repetir os mesmos dados consulta a operação.'
            : 'Operação interrompida. Entre novamente se necessário.';
      }
    } finally {
      if (identical(_consulta, consulta)) _consulta = null;
      if (!_descartado) {
        ocupado = false;
        notifyListeners();
      }
    }
    return null;
  }

  Future<void> cancelar() async {
    final consulta = _consulta;
    if (consulta != null) {
      await autenticacao.cancelarNegocio(consulta: consulta);
    }
  }

  @override
  void dispose() {
    _descartado = true;
    cancelar().ignore();
    super.dispose();
  }
}
