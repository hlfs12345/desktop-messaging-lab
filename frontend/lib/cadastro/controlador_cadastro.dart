import 'package:flutter/foundation.dart';

import '../autenticacao/cliente_autenticacao.dart';
import '../autenticacao/controlador_autenticacao.dart';

/// Orquestra chamadas tipadas pelo cliente autenticado; não recebe tokens.
class ControladorCadastro extends ChangeNotifier {
  ControladorCadastro(this.autenticacao);
  final ControladorAutenticacao autenticacao;
  bool ocupado = false;
  String? mensagem;
  bool _descartado = false;
  int _geracao = 0;

  Future<Map<String, dynamic>?> requisitar(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    Map<String, String>? parametros,
    List<int>? bytes,
  }) async {
    if (_descartado || ocupado) return null;
    final geracao = _geracao;
    ocupado = true;
    mensagem = null;
    notifyListeners();
    try {
      final result = await autenticacao.negocio(
        metodo,
        caminho,
        corpo: corpo,
        parametros: parametros,
        bytes: bytes,
      );
      if (_descartado || geracao != _geracao) return null;
      if (result.codigo >= 200 && result.codigo < 300) {
        return result.dados ?? <String, dynamic>{};
      }
      mensagem = switch (result.dados?['codigo']) {
        'banco_ocupado' => 'Banco ocupado por outra conexão. Finalize a consulta externa e atualize os dados antes de tentar novamente.',
        'duplicidade' => 'Este telefone ou nome de lista já está cadastrado, inclusive entre arquivados.',
        'conflito' || 'previa_desatualizada' => 'Os dados mudaram. Atualize a consulta ou valide o arquivo novamente.',
        'bloqueado' =>
          'Remova o bloqueio e registre uma nova autorização com evidência.',
        'evidencia_anterior' => 'A nova autorização deve ser posterior à revogação ou ao desbloqueio.',
        'pais_obrigatorio' || 'telefone_ambiguo' => 'Selecione o país e informe o número completo, incluindo DDD quando necessário.',
        'telefone_invalido' => 'Confira o telefone e o país. Use + e código do país para números internacionais.',
        'importacao_ativa' =>
          'Conclua ou cancele a importação em andamento no histórico.',
        'registro_arquivado' || 'arquivado' => 'O registro está arquivado.',
        'corpo_excessivo' => 'O arquivo excede o limite de 10 MiB.',
        _ =>
          'Não foi possível concluir. Confira os dados e atualize a consulta.',
      };
    } on ErroAutenticacao catch (error) {
      if (!_descartado) {
        mensagem = error.codigo == 'tempo_esgotado'
            ? 'A resposta não chegou no prazo. Consulte o resultado antes de tentar gravar novamente.'
            : 'A operação foi interrompida. Entre novamente se necessário.';
      }
    } finally {
      if (!_descartado && geracao == _geracao) {
        ocupado = false;
        notifyListeners();
      }
    }
    return null;
  }

  Future<List<int>?> relatorio(String id) async {
    if (_descartado || ocupado) return null;
    ocupado = true;
    notifyListeners();
    try {
      final result = await autenticacao.negocio(
        'GET',
        '/importacoes/$id/relatorio',
        arquivo: true,
      );
      if (!_descartado && result.codigo == 200) return result.arquivo;
    } on ErroAutenticacao {
      mensagem =
          'Não foi possível obter o relatório. Consulte o resultado novamente.';
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
    ++_geracao;
    super.dispose();
  }
}
