import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../autenticacao/cliente_autenticacao.dart';
import '../autenticacao/controlador_autenticacao.dart';
import '../templates/controlador_templates.dart' show novaOperacaoTemplate;

const avisoCampanha =
    'Revisão preliminar - esta campanha não está pronta para envio.';

class OperacaoCampanha {
  String? _assinatura, _id;
  Map<String, dynamic> corpo(Map<String, dynamic> campos) {
    final assinatura = jsonEncode(campos);
    if (_assinatura != assinatura) {
      _assinatura = assinatura;
      _id = novaOperacaoTemplate();
    }
    return {'operacao_id': _id, ...campos};
  }
}

class ControladorCampanhas extends ChangeNotifier {
  ControladorCampanhas(this.autenticacao);
  final ControladorAutenticacao autenticacao;
  bool ocupado = false, _descartado = false;
  String? mensagem;
  ConsultaNegocio? _consulta;
  int? codigoHttp;
  String? codigoErro;

  Future<Map<String, dynamic>?> requisitar(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    Map<String, String>? parametros,
  }) async {
    if (_descartado || ocupado) return null;
    final consulta = ConsultaNegocio();
    _consulta = consulta;
    ocupado = true;
    mensagem = null;
    codigoHttp = null;
    codigoErro = null;
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
      codigoHttp = result.codigo;
      codigoErro = result.dados?['codigo'] as String?;
      if (result.codigo >= 200 && result.codigo < 300) {
        return result.dados ?? <String, dynamic>{};
      }
      mensagem = switch (result.dados?['codigo']) {
        'configuracao_obrigatoria' => 'Escolha explicitamente um cenário e revise a configuração de simulação.',
        'configuracao_incompativel' => 'A configuração não corresponde à conta. Atualize e revise novamente.',
        'integridade_comprometida' => 'Integridade comprometida: nao foi possivel autenticar a fotografia. Leitura e confirmacao bloqueadas. Preserve os dados.',
        'chave_nao_conferida' => 'O administrador precisa inicializar e conferir o codigo de recuperacao em Protecao das campanhas.',
        'conteudo_indisponivel' => 'Chave indisponivel ou fotografia danificada. Solicite recuperacao ao administrador; preserve os dados.',
        'volume_excessivo' => 'A campanha excede um limite: 10.000 identidades, 64 KiB por texto ou 64 MiB por preparacao.',
        'sem_destinatarios' => 'Nenhum destinatario pode ser incluido. Confira os motivos de exclusao.',
        'sem_conteudo' =>
          'Esta identidade foi excluida e nao possui texto congelado.',
        'conflito' || 'duplicidade' => 'Os dados mudaram ou a operação divergiu. Atualize e revise novamente.',
        'referencia_invalida' => 'Escolha referências ativas e compatíveis. Confira conta, escopo, template e listas.',
        'fora_das_listas' =>
          'A identidade saiu dos candidatos atuais. Atualize a revisão.',
        'valor_ausente' => 'Informe um nome fictício para a prévia.',
        _ =>
          'Não foi possível concluir. Confira os campos e atualize a consulta.',
      };
    } on ErroAutenticacao catch (error) {
      if (!_descartado) {
        mensagem = error.codigo == 'tempo_esgotado'
            ? 'Resposta não recebida. Repita os mesmos dados para consultar a operação.'
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
