import 'package:flutter/foundation.dart';

import '../autenticacao/cliente_autenticacao.dart';
import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_campanhas.dart';

const avisoAutorizacao =
    'Esta autorização permite somente simulação. Nenhum envio externo ocorrerá. '
    'Iniciar o processamento exige uma revisão e confirmação separadas em Execuções mock.';

class ControladorAutorizacaoSimulacao extends ChangeNotifier {
  ControladorAutorizacaoSimulacao({
    required this.autenticacao,
    required this.campanhaId,
    this.preparacaoId,
    this.impressao,
    this.resumoConfiguracao,
    required this.fotografiaAutenticada,
  }) : _sessao = autenticacao.geracaoSessao;

  final ControladorAutenticacao autenticacao;
  final String campanhaId;
  final String? preparacaoId, impressao, resumoConfiguracao;
  final bool fotografiaAutenticada;
  bool get temFotografia =>
      fotografiaAutenticada &&
      preparacaoId != null &&
      impressao != null &&
      resumoConfiguracao != null;
  bool historicoCarregado = false;
  final int _sessao;
  bool _descartado = false, ocupado = false, resultadoDesconhecido = false;
  ConsultaNegocio? _consulta;
  ({
    String caminho,
    Map<String, dynamic> corpo,
    String preparacao,
    String? autorizacao,
    String estado,
  })?
  _pendente;
  Map<String, dynamic>? revisao, detalhe, recibo;
  List<Map<String, dynamic>> historico = [];
  int pagina = 1, total = 0;
  String? mensagem;
  String get root => '/campanhas/$campanhaId';
  bool get ativo =>
      !_descartado &&
      autenticacao.estado == EstadoAutenticacao.autenticado &&
      autenticacao.geracaoSessao == _sessao;
  bool get temPendente => _pendente != null;
  bool get podeAutorizar =>
      ativo &&
      temFotografia &&
      !ocupado &&
      !temPendente &&
      revisao?['autorizavel'] == true;
  bool get podeRevogar =>
      ativo && !ocupado && !temPendente && detalhe?['estado'] == 'vigente';

  Future<void> _operar(Future<void> Function() acao) async {
    if (!ativo || ocupado) return;
    ocupado = true;
    mensagem = null;
    notifyListeners();
    try {
      await acao();
    } on ErroAutenticacao catch (erro) {
      if (!ativo) return;
      if (temPendente) {
        resultadoDesconhecido = true;
        mensagem =
            'Resultado da decisão não confirmado. Consulte o estado ou repita '
            'a mesma operação. Cancelar a consulta não garante rollback.';
      } else {
        mensagem = erro.codigo == 'tempo_esgotado'
            ? 'Consulta sem resposta. Atualize para consultar novamente.'
            : 'Consulta interrompida. Entre novamente se necessário.';
      }
    } on FormatException {
      if (!ativo) return;
      revisao = null;
      detalhe = null;
      if (temPendente) resultadoDesconhecido = true;
      mensagem =
          'Resposta incompleta ou incompatível. Atualize a consulta. '
          'Uma decisão sem recibo válido deve ser consultada ou repetida identicamente.';
    } finally {
      if (ativo) {
        ocupado = false;
        notifyListeners();
      }
    }
  }

  Future<Map<String, dynamic>?> _pedir(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    Map<String, String>? parametros,
  }) async {
    if (!ativo) throw const ErroAutenticacao('cancelada');
    final consulta = ConsultaNegocio();
    _consulta = consulta;
    try {
      final resposta = await autenticacao.negocio(
        metodo,
        caminho,
        corpo: corpo,
        parametros: parametros,
        consulta: consulta,
      );
      if (!ativo) throw const ErroAutenticacao('cancelada');
      if (resposta.codigo >= 200 && resposta.codigo < 300) {
        final data = resposta.dados;
        if (data == null || data['processamento_disponivel'] != false) {
          throw const FormatException();
        }
        return data;
      }
      final status = resposta.codigo;
      mensagem = switch (status) {
        403 => 'Permissão insuficiente para esta operação.',
        409 => 'Os dados mudaram. Consulte o estado e faça uma nova revisão.',
        422 => 'Solicitação rejeitada. Atualize e confira os dados da decisão.',
        503 => 'Serviço indisponível. Preserve os dados e consulte novamente.',
        _ => 'Não foi possível confirmar a solicitação. Atualize a consulta.',
      };
      if (status == 409 || status == 422 || status == 503 || status == 403) {
        revisao = null;
        detalhe = null;
      }
      if (metodo == 'POST') {
        if (status >= 500) {
          resultadoDesconhecido = true;
          mensagem =
              '${mensagem!} O resultado da decisão deve ser consultado ou repetido.';
        } else {
          _pendente = null;
          resultadoDesconhecido = false;
        }
      }
      return null;
    } finally {
      if (identical(_consulta, consulta)) _consulta = null;
    }
  }

  bool _contagens(Map data) =>
      [
        'incluidos',
        'excluidos',
        'elegiveis_atuais',
        'impedidos_atuais',
      ].every((key) => data[key] is int && (data[key] as int) >= 0) &&
      data['elegiveis_atuais'] + data['impedidos_atuais'] ==
          data['incluidos'] &&
      data['impedimentos'] is List &&
      (data['impedimentos'] as List).every((value) => value is String);

  Future<void> revisar() => _operar(() async {
    if (temPendente || !temFotografia) return;
    revisao = null;
    final data = await _pedir(
      'GET',
      '$root/preparacoes/$preparacaoId/revisao-autorizacao',
    );
    if (data == null) return;
    final atual = data['avaliacao_atual'];
    if (data['id'] != campanhaId ||
        data['preparacao_id'] != preparacaoId ||
        data['impressao'] != impressao ||
        data['resumo_configuracao'] != resumoConfiguracao ||
        data['versao'] is! int ||
        data['versao'] < 1 ||
        data['autorizavel'] is! bool ||
        atual is! Map ||
        !_contagens(atual) ||
        atual['integridade'] != 'verificada' ||
        atual['condicoes_atuais_atendidas'] != data['autorizavel'] ||
        data['marcador_autorizacao'] is! String ||
        !RegExp(r'^autorizacao-v1\.[0-9a-f]{64}$')
            .hasMatch(data['marcador_autorizacao'])) {
      throw const FormatException();
    }
    revisao = Map.unmodifiable(data);
  });

  Future<void> carregar({int pagina = 1}) => _operar(() => _carregar(pagina));

  bool _itemValido(Map data) {
    final atual = data['validade_atual'];
    return data['id'] is String &&
        data['campanha_id'] == campanhaId &&
        data['preparacao_id'] is String &&
        data['versao'] is int &&
        data['versao'] >= 1 &&
        {'vigente', 'revogada'}.contains(data['estado']) &&
        data['autorizado_por_usuario_id'] is String &&
        data['autorizado_em'] is String &&
        atual is Map &&
        atual['avaliada'] is bool &&
        atual['condicoes_atuais_atendidas'] is bool &&
        atual['integridade'] is String &&
        atual['impedimentos'] is List &&
        (atual['impedimentos'] as List).every((value) => value is String) &&
        (atual['avaliada'] == false || _contagens(atual)) &&
        data['processamento_disponivel'] == false &&
        (data['estado'] != 'revogada' ||
            (data['revogado_em'] is String &&
                data['revogado_por_usuario_id'] is String &&
                data['motivo_codigo'] is String));
  }

  Future<void> _carregar(int novaPagina, {String? selecionar}) async {
    detalhe = null;
    historicoCarregado = false;
    historico = [];
    total = 0;
    final data = await _pedir(
      'GET',
      '$root/autorizacoes-simulacao',
      parametros: {'pagina': '$novaPagina', 'limite': '20'},
    );
    if (data == null) return;
    if (data['itens'] is! List ||
        data['total'] is! int ||
        data['total'] < 0 ||
        data['pagina'] != novaPagina ||
        data['limite'] != 20 ||
        !(data['itens'] as List).every(
          (item) => item is Map && _itemValido(item),
        )) {
      throw const FormatException();
    }
    historico = [
      for (final item in data['itens']) Map<String, dynamic>.from(item),
    ];
    pagina = novaPagina;
    total = data['total'];
    historicoCarregado = true;
    detalhe = null;
    final atual = historico.where(
      (item) => item['preparacao_id'] == preparacaoId,
    );
    final id =
        selecionar ??
        (atual.isNotEmpty
            ? atual.first['id']
            : (historico.isEmpty ? null : historico.first['id']));
    if (id != null) await _abrir(id);
  }

  Future<void> abrir(String id) => _operar(() => _abrir(id));

  Future<void> _abrir(String id) async {
    detalhe = null;
    final data = await _pedir('GET', '$root/autorizacoes-simulacao/$id');
    if (data == null) return;
    if (!_itemValido(data) || data['id'] != id) throw const FormatException();
    detalhe = Map.unmodifiable(data);
  }

  Future<void> autorizar(Map<String, dynamic> confirmada) async {
    if (!podeAutorizar || !identical(confirmada, revisao)) return;
    final corpo = OperacaoCampanha().corpo({
      'versao': confirmada['versao'],
      'impressao': confirmada['impressao'],
      'resumo_configuracao': confirmada['resumo_configuracao'],
      'marcador_autorizacao': confirmada['marcador_autorizacao'],
      'confirmo_simulacao': true,
    });
    await _decidir(
      '$root/preparacoes/$preparacaoId/autorizacoes-simulacao',
      corpo,
      preparacao: preparacaoId!,
      estado: 'vigente',
    );
  }

  Future<void> revogar(Map<String, dynamic> confirmada) async {
    if (!podeRevogar || !identical(confirmada, detalhe)) return;
    final id = confirmada['id'];
    await _decidir(
      '$root/autorizacoes-simulacao/$id/revogacao',
      OperacaoCampanha().corpo({
        'versao': confirmada['versao'],
        'motivo_codigo': 'revogacao_manual',
      }),
      preparacao: confirmada['preparacao_id'] as String,
      autorizacao: id as String,
      estado: 'revogada',
    );
  }

  Future<void> _decidir(
    String caminho,
    Map<String, dynamic> corpo, {
    required String preparacao,
    required String estado,
    String? autorizacao,
  }) => _operar(() async {
    _pendente = (
      caminho: caminho,
      corpo: Map.unmodifiable(corpo),
      preparacao: preparacao,
      autorizacao: autorizacao,
      estado: estado,
    );
    await _enviarPendente();
  });

  Future<void> repetir() => _operar(() async {
    if (temPendente) await _enviarPendente();
  });

  Future<void> _enviarPendente() async {
    final pedido = _pendente!;
    revisao = null;
    detalhe = null;
    final data = await _pedir('POST', pedido.caminho, corpo: pedido.corpo);
    if (data == null) return;
    if (data['id'] != campanhaId ||
        data['autorizacao_id'] is! String ||
        data['preparacao_id'] != pedido.preparacao ||
        (pedido.autorizacao != null &&
            data['autorizacao_id'] != pedido.autorizacao) ||
        data['estado_decisao'] != pedido.estado ||
        data['versao_autorizacao'] is! int ||
        data['versao'] is! int ||
        data['recibo_historico'] != true ||
        data['validade_atual'] != 'nao_avaliada' ||
        !{'vigente', 'revogada'}.contains(data['estado_decisao'])) {
      throw const FormatException();
    }
    recibo = Map.unmodifiable(data);
    _pendente = null;
    resultadoDesconhecido = false;
    revisao = null;
    detalhe = null;
    await _carregar(1, selecionar: data['autorizacao_id']);
  }

  Future<void> encerrar() {
    _descartado = true;
    revisao = null;
    detalhe = null;
    historico = [];
    final consulta = _consulta;
    return consulta == null
        ? Future.value()
        : autenticacao.cancelarNegocio(consulta: consulta);
  }

  @override
  void dispose() {
    encerrar().ignore();
    super.dispose();
  }
}
