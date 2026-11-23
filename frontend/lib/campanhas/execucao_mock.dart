const avisoExecucao =
    'Simulação mock: nenhum envio real. Aceitação pelo mock não comprova entrega.';
const avisoExecucaoTelegram =
    'Envio real Telegram: resultados desconhecidos não serão reenviados automaticamente.';

const descricoesExecucao = {
  'executando': 'Execução registrada',
  'pausando': 'Pausa solicitada',
  'pausada': 'Pausada',
  'concluida': 'Concluída',
  'concluida_com_falhas': 'Concluída com falhas ou incertezas',
  'interrompida': 'Interrompida',
  'pendente': 'Pendente',
  'reservado': 'Reservado',
  'reservada': 'Reservada',
  'processando': 'Em processamento',
  'iniciada': 'Invocação registrada',
  'aceita_provedor': 'Aceito pelo provedor',
  'falhou': 'Falhou',
  'desconhecida': 'Resultado desconhecido',
  'ignorado': 'Ignorado por impedimento',
  'interrompido': 'Interrompido',
  'interrupcao': 'Interrupção simulada',
  'excecao': 'Exceção simulada',
};
const estadosExecucao = {
  'executando',
  'pausando',
  'pausada',
  'concluida',
  'concluida_com_falhas',
  'interrompida',
};
const estadosItens = {
  'pendente',
  'reservado',
  'processando',
  'aceita_provedor',
  'falhou',
  'desconhecida',
  'ignorado',
  'interrompido',
};
String descricaoEstado(String estado) =>
    descricoesExecucao[estado] ?? 'Estado indisponível';

String descricaoExecucao(ExecucaoMock e) =>
    e.estado == 'executando' && e.workerAtivo && e.admitida
    ? (e.telegram ? 'Executando lote Telegram' : 'Executando simulação')
    : descricaoEstado(e.estado);

String textoCampo(Map d, String k) {
  if (d[k] is! String || (d[k] as String).isEmpty) {
    throw const FormatException();
  }
  return d[k] as String;
}

int inteiroCampo(Map d, String k, {int minimo = 0}) {
  if (d[k] is! int || d[k] < minimo) throw const FormatException();
  return d[k] as int;
}

bool booleanoCampo(Map d, String k) {
  if (d[k] is! bool) throw const FormatException();
  return d[k] as bool;
}

DateTime dataCampo(Map d, String k) {
  final value = textoCampo(d, k);
  if (!value.endsWith('Z')) throw const FormatException();
  return DateTime.parse(value).toUtc();
}

String formatarHorario(DateTime? data) {
  if (data == null) return '—';
  final local = data.toLocal();
  String dois(int n) => '$n'.padLeft(2, '0');
  return '${dois(local.day)}/${dois(local.month)}/${local.year} '
      '${dois(local.hour)}:${dois(local.minute)}:${dois(local.second)}';
}

class ExecucaoMock {
  ExecucaoMock(Map d, String campanha) {
    id = textoCampo(d, 'id');
    preparacao = textoCampo(d, 'preparacao_id');
    autorizacao = textoCampo(d, 'autorizacao_id');
    modo = d['modo'] is String
        ? textoCampo(d, 'modo')
        : (d['simulacao'] == true ? 'mock' : 'telegram');
    estado = textoCampo(d, 'estado');
    versao = inteiroCampo(d, 'versao', minimo: 1);
    geracao = inteiroCampo(d, 'geracao', minimo: 1);
    criado = dataCampo(d, 'criado_em');
    atualizado = dataCampo(d, 'atualizado_em');
    final op = d['operacional'], counts = d['contagens'];
    if (d['campanha_id'] != campanha ||
        !estadosExecucao.contains(estado) ||
        !{'mock', 'telegram'}.contains(modo) ||
        d['simulacao'] != (modo == 'mock') ||
        d['validade_atual'] != 'nao_avaliada' ||
        op is! Map ||
        counts is! Map) {
      throw const FormatException();
    }
    admitida = booleanoCampo(op, 'admitida');
    workerAtivo = booleanoCampo(op, 'worker_ativo');
    disponivel = booleanoCampo(op, 'processamento_disponivel');
    recuperacaoDisponivel = booleanoCampo(op, 'recuperacao_disponivel');
    encerrando = booleanoCampo(op, 'encerrando');
    contagens = Map.unmodifiable({
      for (final key in estadosItens) key: inteiroCampo(counts, key),
    });
  }
  late final String id, preparacao, autorizacao, estado, modo;
  late final int versao, geracao;
  late final DateTime criado, atualizado;
  late final bool admitida,
      workerAtivo,
      disponivel,
      recuperacaoDisponivel,
      encerrando;
  late final Map<String, int> contagens;
  int get total => contagens.values.fold(0, (a, b) => a + b);
  bool get telegram => modo == 'telegram';
  bool get acompanha => estado == 'executando' || estado == 'pausando';
}

class ItemMock {
  ItemMock(Map d, String execucao) {
    id = textoCampo(d, 'id');
    estado = textoCampo(d, 'estado');
    ordem = inteiroCampo(d, 'ordem', minimo: 1);
    atualizado = dataCampo(d, 'atualizado_em');
    motivo = d['motivo_codigo'] is String
        ? d['motivo_codigo'] as String
        : null;
    if (d['execucao_id'] != execucao || !estadosItens.contains(estado)) {
      throw const FormatException();
    }
  }
  late final String id, estado;
  late final String? motivo;
  late final int ordem;
  late final DateTime atualizado;
}

class TentativaMock {
  TentativaMock(Map d, String execucao, String item) {
    id = textoCampo(d, 'id');
    estado = textoCampo(d, 'estado');
    numero = inteiroCampo(d, 'numero', minimo: 1);
    reservada = dataCampo(d, 'reservada_em');
    invocacao = d['invocacao_em'] == null ? null : dataCampo(d, 'invocacao_em');
    finalizada = d['finalizada_em'] == null
        ? null
        : dataCampo(d, 'finalizada_em');
    messageId = d['message_id'] is int ? d['message_id'] as int : null;
    retryAfter = d['retry_after'] is int ? d['retry_after'] as int : null;
    if (d['execucao_id'] != execucao ||
        d['item_id'] != item ||
        !{
          'reservada',
          'iniciada',
          'interrompida',
          'aceita_provedor',
          'falhou',
          'desconhecida',
        }.contains(estado)) {
      throw const FormatException();
    }
  }
  late final String id, estado;
  late final int numero;
  late final int? messageId, retryAfter;
  late final DateTime reservada;
  late final DateTime? invocacao, finalizada;
}

class PaginaMock<T> {
  PaginaMock(Map d, int esperada, T Function(Map) converter) {
    total = inteiroCampo(d, 'total');
    pagina = inteiroCampo(d, 'pagina', minimo: 1);
    if (pagina != esperada ||
        d['limite'] != 20 ||
        d['simulacao'] is! bool ||
        d['itens'] is! List ||
        (d['itens'] as List).length > 20) {
      throw const FormatException();
    }
    itens = List.unmodifiable(
      (d['itens'] as List).map((item) {
        if (item is! Map) throw const FormatException();
        return converter(item);
      }),
    );
  }
  late final List<T> itens;
  late final int total, pagina;
  bool get proxima => pagina * 20 < total;
}

class RevisaoExecucao {
  RevisaoExecucao(
    Map d,
    String campanha, {
    required this.retomada,
    required String alvo,
  }) {
    modo = d['modo'] is String
        ? textoCampo(d, 'modo')
        : (d['simulacao'] == true ? 'mock' : 'telegram');
    final campos = retomada
        ? [
            'versao',
            'preparacao_id',
            'autorizacao_id',
            'versao_autorizacao',
            'geracao',
            'impressao',
            'resumo_configuracao',
            'marcador_retomada',
          ]
        : modo == 'mock'
        ? [
            'versao',
            'autorizacao_id',
            'versao_autorizacao',
            'impressao',
            'resumo_configuracao',
            'marcador_inicio',
          ]
        : [
            'versao',
            'impressao',
            'resumo_configuracao',
            'marcador_inicio',
            'modo',
          ];
    preparacao = textoCampo(d, 'preparacao_id');
    impressao = textoCampo(d, 'impressao');
    if (d[retomada ? 'campanha_id' : 'id'] != campanha ||
        (retomada ? d['execucao_id'] != alvo : preparacao != alvo) ||
        !{'mock', 'telegram'}.contains(modo) ||
        d['simulacao'] != (modo == 'mock')) {
      throw const FormatException();
    }
    for (final k in campos) {
      if (k.startsWith('versao') || k == 'geracao') {
        inteiroCampo(d, k, minimo: 1);
      } else {
        textoCampo(d, k);
      }
    }
    final tipo = retomada ? 'ativar' : 'inicio';
    if (!RegExp('^$tipo-v1\\.[0-9a-f]{64}\$')
        .hasMatch(d[retomada ? 'marcador_retomada' : 'marcador_inicio'])) {
      throw const FormatException();
    }
    final atual = d['avaliacao_atual'];
    final cfg = d['configuracao_execucao'] ?? d['configuracao_simulacao'];
    if (atual is! Map ||
        cfg is! Map ||
        cfg['provedor'] != (modo == 'mock' ? 'mock' : 'telegram_bot_api') ||
        cfg['modo'] != (modo == 'mock' ? 'simulacao' : 'envio_real') ||
        cfg['plataforma'] != (modo == 'mock' ? 'whatsapp' : 'telegram')) {
      throw const FormatException();
    }
    permitida =
        booleanoCampo(d, retomada ? 'retomavel' : 'iniciavel') &&
        (retomada || booleanoCampo(d, 'processamento_disponivel'));
    incluidos = inteiroCampo(atual, 'incluidos');
    excluidos = inteiroCampo(atual, 'excluidos');
    elegiveis = retomada
        ? inteiroCampo(d, 'elegiveis_pendentes')
        : inteiroCampo(atual, 'elegiveis_atuais');
    ignorados = inteiroCampo(atual, 'impedidos_atuais');
    pendentes = retomada ? inteiroCampo(d, 'pendentes') : incluidos;
    impedimentos = List<String>.unmodifiable(
      ((d['impedimentos_execucao'] as List?) ?? const [])
          .whereType<String>(),
    );
    impedimentoRetomada = d['impedimento_codigo'] is String
        ? d['impedimento_codigo'] as String
        : null;
    categoria = modo == 'telegram' ? textoCampo(d, 'categoria') : null;
    bot = modo == 'telegram' && d['bot_externo_id'] is String
        ? textoCampo(d, 'bot_externo_id')
        : null;
    cenario = modo == 'mock' ? textoCampo(cfg, 'cenario') : 'telegram_real';
    if ((modo == 'mock' && !{
          'aceita_provedor',
          'falhou',
          'desconhecida',
          'interrupcao',
          'excecao',
        }.contains(cenario)) ||
        (modo == 'mock' &&
            cfg['resumo_configuracao'] != d['resumo_configuracao'])) {
      throw const FormatException();
    }
    corpo = Map.unmodifiable({
      for (final k in campos) k: d[k],
      'modo': modo,
      if (retomada) 'confirmo_retomada': true else 'confirmo_inicio': true,
    });
  }
  final bool retomada;
  late final String preparacao, cenario, modo, impressao;
  late final String? impedimentoRetomada, categoria, bot;
  late final bool permitida;
  late final int incluidos, excluidos, elegiveis, ignorados, pendentes;
  late final Map<String, dynamic> corpo;
  late final List<String> impedimentos;
}

class ReciboExecucao {
  ReciboExecucao(Map d, String campanha, Map pedido, {String? execucao}) {
    final r = d['recibo'], a = d['ativacao'], atual = d['atual'];
    final modo = atual is Map && atual['modo'] is String
        ? atual['modo']
        : (d['simulacao'] == true ? 'mock' : 'telegram');
    if (r is! Map ||
        a is! Map ||
        atual is! Map ||
        d['simulacao'] != (modo == 'mock') ||
        r['id'] != campanha ||
        r['operacao_id'] != pedido['operacao_id'] ||
        r['recibo_historico'] != true ||
        r['validade_atual'] != 'nao_avaliada' ||
        r['processamento_disponivel'] != false ||
        (execucao != null && r['execucao_id'] != execucao)) {
      throw const FormatException();
    }
    estado = ExecucaoMock(atual, campanha);
    if (r['execucao_id'] != estado.id ||
        r['preparacao_id'] != estado.preparacao ||
        r['autorizacao_id'] != estado.autorizacao ||
        (pedido['autorizacao_id'] != null &&
            pedido['autorizacao_id'] != estado.autorizacao) ||
        (pedido['preparacao_id'] != null &&
            pedido['preparacao_id'] != estado.preparacao)) {
      throw const FormatException();
    }
    inteiroCampo(r, 'versao_execucao', minimo: 1);
    replay = booleanoCampo(d, 'replay');
    admitida = booleanoCampo(a, 'admissao_confirmada');
    falhaAtivacao = a['erro_codigo'] != null;
  }
  late final ExecucaoMock estado;
  late final bool replay, admitida, falhaAtivacao;
}
