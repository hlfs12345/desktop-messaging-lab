import 'dart:async';

import 'package:flutter/foundation.dart';

import '../autenticacao/cliente_autenticacao.dart';
import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_campanhas.dart';
import 'execucao_mock.dart';

class _Pendente {
  _Pendente(
    this.caminho,
    Map<String, dynamic> corpo,
    this.execucao,
    this.preparacao,
  ) : corpo = Map.unmodifiable(corpo);
  final String caminho, preparacao;
  final String? execucao;
  final Map<String, dynamic> corpo;
}

class _Rejeicao implements Exception {
  _Rejeicao(this.status);
  final int status;
}

class ControladorExecucoes extends ChangeNotifier {
  ControladorExecucoes({
    required this.autenticacao,
    required this.campanhaId,
    this.preparacaoId,
    this.intervalo = const Duration(seconds: 5),
  }) : _sessao = autenticacao.geracaoSessao {
    autenticacao.addListener(_sessaoMudou);
  }
  final ControladorAutenticacao autenticacao;
  final String campanhaId;
  final String? preparacaoId;
  final Duration intervalo;
  final int _sessao;
  bool _fechado = false, _visivel = true;
  bool ocupado = false, confirmando = false, historicoIndisponivel = false;
  bool resultadoDesconhecido = false;
  Timer? _timer;
  ConsultaNegocio? _consulta;
  _Pendente? _pendente;
  String? mensagem, nomeConta;
  PaginaMock<ExecucaoMock>? execucoes;
  PaginaMock<ItemMock>? itens;
  PaginaMock<TentativaMock>? tentativas;
  ExecucaoMock? execucao;
  ItemMock? item;
  RevisaoExecucao? revisao;
  ReciboExecucao? recibo;
  bool get ativo =>
      !_fechado &&
      autenticacao.estado == EstadoAutenticacao.autenticado &&
      autenticacao.geracaoSessao == _sessao;
  bool get temPendente => _pendente != null;
  String? get operacaoPendente => _pendente?.corpo['operacao_id'] as String?;
  String get root => '/campanhas/$campanhaId';
  bool get podeDecidir => ativo && !ocupado && !confirmando && !temPendente;

  void _sessaoMudou() {
    if (ativo || _fechado) return;
    _timer?.cancel();
    final consulta = _consulta;
    if (consulta != null) {
      autenticacao.cancelarNegocio(consulta: consulta).ignore();
    }
    _limpar();
    ocupado = false;
    mensagem = 'Sessão encerrada. Entre novamente para consultar o histórico.';
    notifyListeners();
  }

  void _limpar() {
    revisao = null;
    recibo = null;
    execucoes = null;
    execucao = null;
    itens = null;
    item = null;
    tentativas = null;
    _pendente = null;
    resultadoDesconhecido = false;
    nomeConta = null;
  }

  Future<Map<String, dynamic>> _pedir(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    int? pagina,
  }) async {
    if (!ativo) throw const ErroAutenticacao('cancelada');
    final consulta = ConsultaNegocio();
    _consulta = consulta;
    try {
      final response = await autenticacao.negocio(
        metodo,
        caminho,
        corpo: corpo,
        parametros: pagina == null
            ? null
            : {'pagina': '$pagina', 'limite': '20'},
        consulta: consulta,
      );
      if (!ativo) throw const ErroAutenticacao('cancelada');
      if (response.codigo < 200 || response.codigo >= 300) {
        throw _Rejeicao(response.codigo);
      }
      if (response.dados == null) throw const FormatException();
      return response.dados!;
    } finally {
      if (identical(_consulta, consulta)) _consulta = null;
    }
  }

  Future<void> _operar(
    Future<void> Function() acao, {
    bool comando = false,
  }) async {
    if (!ativo || ocupado) return;
    _timer?.cancel();
    ocupado = true;
    mensagem = null;
    notifyListeners();
    var falhou = false;
    try {
      await acao();
    } on _Rejeicao catch (e) {
      falhou = true;
      if (!ativo) return;
      revisao = null;
      mensagem = switch (e.status) {
        403 => 'Você não tem permissão para esta ação.',
        404 => 'Registro não encontrado. Atualize o histórico.',
        409 => 'Os dados mudaram. Faça uma nova revisão antes de confirmar.',
        422 => 'Solicitação rejeitada. Revise os dados novamente.',
        503 => 'Serviço local indisponível. Consulte novamente quando estiver disponível.',
        _ => 'Não foi possível confirmar a resposta do serviço local.',
      };
      if (comando && e.status < 500) _pendente = null;
      if (comando && temPendente) await _incerta();
      if (!comando) historicoIndisponivel = true;
    } on ErroAutenticacao {
      falhou = true;
      if (!ativo) return;
      revisao = null;
      mensagem =
          'O serviço local não respondeu. Atualize para consultar novamente.';
      if (comando && temPendente) await _incerta();
      if (!comando) historicoIndisponivel = true;
    } on FormatException {
      falhou = true;
      if (!ativo) return;
      revisao = null;
      mensagem = 'Resposta incompatível. Atualize a consulta; nenhum sucesso foi confirmado.';
      if (comando && temPendente) await _incerta();
      if (!comando) historicoIndisponivel = true;
    } finally {
      if (ativo) {
        ocupado = false;
        notifyListeners();
        if (!falhou) _agendar();
      }
    }
  }

  Future<void> _incerta() async {
    resultadoDesconhecido = true;
    mensagem =
        'Confirmação pendente: a operação pode ter sido gravada. '
        'Consulte o estado ou repita a mesma operação. Nenhuma nova operação será criada.';
    try {
      await _listar(execucoes?.pagina ?? 1);
      if (execucao != null) await _detalhe(execucao!.id);
    } on Object {
      // Consultar nao confirma nem desfaz a operacao sem recibo.
    }
  }

  void _agendar() {
    _timer?.cancel();
    if (!ativo || !_visivel || confirmando || execucao?.acompanha != true) {
      return;
    }
    _timer = Timer(intervalo, () {
      if (ativo && !ocupado && !confirmando && _visivel) atualizar();
    });
  }

  void visibilidade(bool visivel) {
    _visivel = visivel;
    if (visivel) {
      _agendar();
    } else {
      _timer?.cancel();
    }
  }

  void confirmarDialogo(bool valor) {
    confirmando = valor;
    if (valor) {
      _timer?.cancel();
    } else {
      _agendar();
    }
    if (ativo) notifyListeners();
  }

  Future<void> carregar({int pagina = 1}) => _operar(() async {
    revisao = null;
    final camp = await _pedir('GET', root);
    if (camp['id'] != campanhaId) throw const FormatException();
    final referencias = camp['referencias'];
    final conta = referencias is Map ? referencias['conta'] : null;
    nomeConta = conta is Map && conta['nome'] is String
        ? conta['nome'] as String
        : null;
    await _listar(pagina);
  });

  Future<void> _listar(int pagina) async {
    final data = await _pedir('GET', '$root/execucoes', pagina: pagina);
    execucoes = PaginaMock(data, pagina, (d) => ExecucaoMock(d, campanhaId));
    historicoIndisponivel = false;
    if (execucao == null && _pendente != null) {
      for (final e in execucoes!.itens) {
        if (e.preparacao == _pendente!.preparacao) {
          execucao = e;
          break;
        }
      }
    }
  }

  Future<void> _detalhe(String id) async {
    final data = await _pedir('GET', '$root/execucoes/$id');
    final e = ExecucaoMock(data, campanhaId);
    if (e.id != id) throw const FormatException();
    execucao = e;
    historicoIndisponivel = false;
  }

  Future<void> abrirExecucao(String id) => _operar(() async {
    revisao = null;
    item = null;
    itens = null;
    tentativas = null;
    await _detalhe(id);
    await _itens(1);
  });

  Future<void> _itens(int pagina) async {
    final e = execucao!;
    itens = PaginaMock(
      await _pedir('GET', '$root/execucoes/${e.id}/itens', pagina: pagina),
      pagina,
      (d) => ItemMock(d, e.id),
    );
  }

  Future<void> carregarItens(int pagina) => _operar(() => _itens(pagina));

  Future<void> _tentativas(ItemMock alvo, int pagina) async {
    final e = execucao!;
    final data = await _pedir(
      'GET',
      '$root/execucoes/${e.id}/itens/${alvo.id}/tentativas',
      pagina: pagina,
    );
    tentativas = PaginaMock(
      data,
      pagina,
      (d) => TentativaMock(d, e.id, alvo.id),
    );
    item = alvo;
  }

  Future<void> abrirItem(ItemMock alvo, {int pagina = 1}) =>
      _operar(() => _tentativas(alvo, pagina));

  void voltar() {
    if (ocupado || confirmando) return;
    revisao = null;
    if (item != null) {
      item = null;
      tentativas = null;
    } else {
      execucao = null;
      itens = null;
    }
    _agendar();
    notifyListeners();
  }

  Future<void> atualizar() => _operar(() async {
    revisao = null;
    if (execucao == null) {
      await _listar(execucoes?.pagina ?? 1);
    } else {
      await _detalhe(execucao!.id);
      if (item == null) {
        await _itens(itens?.pagina ?? 1);
      } else {
        await _tentativas(item!, tentativas?.pagina ?? 1);
      }
    }
  });

  Future<void> revisar({bool retomar = false}) => _operar(() async {
    if (temPendente) return;
    revisao = null;
    final alvo = retomar ? execucao?.id : preparacaoId;
    if (alvo == null) return;
    final data = await _pedir(
      'GET',
      retomar
          ? '$root/execucoes/$alvo/revisao-retomada'
          : '$root/preparacoes/$alvo/revisao-inicio',
    );
    revisao = RevisaoExecucao(data, campanhaId, retomada: retomar, alvo: alvo);
  });

  Future<void> confirmarRevisao(RevisaoExecucao origem) async {
    if (!podeDecidir || !identical(revisao, origem) || !origem.permitida) {
      return;
    }
    final eid = origem.retomada ? execucao?.id : null;
    final caminho = origem.retomada
        ? '$root/execucoes/$eid/retomada'
        : '$root/preparacoes/${origem.preparacao}/execucoes';
    await _decidir(
      caminho,
      OperacaoCampanha().corpo(origem.corpo),
      eid,
      origem.preparacao,
    );
  }

  Future<void> pausar(ExecucaoMock origem) async {
    if (!podeDecidir || !identical(origem, execucao) || !origem.acompanha) {
      return;
    }
    await _decidir(
      '$root/execucoes/${origem.id}/pausa',
      OperacaoCampanha().corpo({'versao': origem.versao}),
      origem.id,
      origem.preparacao,
    );
  }

  Future<void> _decidir(
    String caminho,
    Map<String, dynamic> corpo,
    String? eid,
    String pid,
  ) => _operar(() async {
    _pendente = _Pendente(caminho, corpo, eid, pid);
    await _enviar();
  }, comando: true);

  Future<void> repetir() => _operar(() async {
    if (temPendente) await _enviar();
  }, comando: true);

  Future<void> _enviar() async {
    final p = _pendente!;
    revisao = null;
    final data = await _pedir('POST', p.caminho, corpo: p.corpo);
    final r = ReciboExecucao(data, campanhaId, p.corpo, execucao: p.execucao);
    if (r.estado.preparacao != p.preparacao) throw const FormatException();
    recibo = r;
    execucao = r.estado;
    _pendente = null;
    resultadoDesconhecido = false;
    historicoIndisponivel = false;
    mensagem = r.falhaAtivacao
        ? 'Execução gravada, mas o processamento não foi ativado. Consulte o estado e revise a retomada.'
        : r.replay
        ? 'Recibo histórico confirmado. Repetir não inicia nem retoma processamento.'
        : 'Operação confirmada. Acompanhe o estado registrado abaixo.';
    // Uma falha posterior de leitura nao torna incerta uma operacao ja confirmada.
  }

  Future<void> encerrar() async {
    if (_fechado) return;
    _fechado = true;
    _timer?.cancel();
    autenticacao.removeListener(_sessaoMudou);
    _limpar();
    final consulta = _consulta;
    if (consulta != null) {
      await autenticacao.cancelarNegocio(consulta: consulta);
    }
  }

  @override
  void dispose() {
    encerrar().ignore();
    super.dispose();
  }
}
