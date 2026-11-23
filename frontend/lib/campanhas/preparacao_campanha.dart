import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_campanhas.dart';
import 'controlador_execucoes.dart';
import 'autorizacao_simulacao.dart';
import 'pagina_execucoes.dart';
import '../simulacao.dart';

const avisoPreparacao = 'Preparacao nao autoriza envio real.';

class PreparacaoCampanha extends StatefulWidget {
  const PreparacaoCampanha({
    super.key,
    required this.autenticacao,
    required this.id,
    this.fluxoSimplificado = false,
  });
  final ControladorAutenticacao autenticacao;
  final String id;
  final bool fluxoSimplificado;
  @override
  State<PreparacaoCampanha> createState() => _PreparacaoCampanhaState();
}

class _PreparacaoCampanhaState extends State<PreparacaoCampanha> {
  late var c = ControladorCampanhas(widget.autenticacao);
  var operacao = OperacaoCampanha();
  String? cenario;
  int _geracao = 0;
  Future<void> _fimAnterior = Future.value();
  late bool _sessaoAtiva;
  bool get mockAtual =>
      campanha?['referencias']?['conta']?['provedor'] == 'mock';
  bool get telegramAtual =>
      campanha?['referencias']?['conta']?['provedor'] == 'telegram_bot_api';
  bool get whatsappAtual =>
      campanha?['referencias']?['conta']?['provedor'] == 'qr_candidato';
  bool get envioTelegramUnitario =>
      telegramAtual &&
      foto?['total'] == 1 &&
      foto?['incluidos'] == 1 &&
      foto?['excluidos'] == 0;
  bool get envioWhatsappUnitario =>
      whatsappAtual &&
      foto?['total'] == 1 &&
      foto?['incluidos'] == 1 &&
      foto?['excluidos'] == 0;
  bool valida(int geracao) =>
      mounted &&
      geracao == _geracao &&
      widget.autenticacao.estado == EstadoAutenticacao.autenticado;
  Map<String, dynamic>? campanha, revisao, foto, revisaoEnvio, resultadoEnvio;
  bool confirmaEnvioReal = false;
  var operacaoEnvio = OperacaoCampanha();
  List<dynamic> historico = [];
  bool integridadeComprometida = false;
  int pagina = 1, paginaHistorico = 1, totalHistorico = 0;
  String get root => '/campanhas/${widget.id}';
  @override
  void initState() {
    super.initState();
    _sessaoAtiva = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    widget.autenticacao.addListener(sessao);
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  void limpar() {
    _geracao++;
    if (c.ocupado) _fimAnterior = c.cancelar();
    c.dispose();
    c = ControladorCampanhas(widget.autenticacao);
    campanha = null;
    revisao = null;
    foto = null;
    revisaoEnvio = null;
    resultadoEnvio = null;
    confirmaEnvioReal = false;
    operacaoEnvio = OperacaoCampanha();
    historico = [];
    cenario = null;
    pagina = 1;
    paginaHistorico = 1;
    totalHistorico = 0;
    integridadeComprometida = false;
    operacao = OperacaoCampanha();
  }

  void sessao() {
    final ativa = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    if (!mounted || ativa == _sessaoAtiva) return;
    _sessaoAtiva = ativa;
    setState(limpar);
    if (ativa) WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void didUpdateWidget(PreparacaoCampanha oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.id != widget.id ||
        oldWidget.autenticacao != widget.autenticacao) {
      oldWidget.autenticacao.removeListener(sessao);
      widget.autenticacao.addListener(sessao);
      _sessaoAtiva =
          widget.autenticacao.estado == EstadoAutenticacao.autenticado;
      limpar();
      WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
    }
  }

  @override
  void dispose() {
    _geracao++;
    widget.autenticacao.removeListener(sessao);
    revisao = null;
    foto = null;
    revisaoEnvio = null;
    resultadoEnvio = null;
    historico = [];
    c.dispose();
    super.dispose();
  }

  Future<void> carregar() async {
    if (widget.autenticacao.estado != EstadoAutenticacao.autenticado) return;
    final geracao = ++_geracao;
    await _fimAnterior;
    if (!valida(geracao)) return;
    setState(() {
      revisao = null;
      foto = null;
      revisaoEnvio = null;
      resultadoEnvio = null;
      confirmaEnvioReal = false;
    });
    final item = await c.requisitar('GET', root);
    if (!valida(geracao) || item == null) return;
    setState(() {
      if (campanha?['conta_envio_id'] != item['conta_envio_id']) cenario = null;
      campanha = item;
    });
    final history = await c.requisitar(
      'GET',
      '$root/preparacoes',
      parametros: {'pagina': '$paginaHistorico'},
    );
    if (!valida(geracao) || history == null) return;
    setState(() {
      historico = history['itens'] as List;
      totalHistorico = history['total'] as int;
      revisao = null;
      foto = null;
      integridadeComprometida = false;
      pagina = 1;
    });
    if (widget.fluxoSimplificado &&
        campanha?['status'] == 'rascunho' &&
        (telegramAtual || whatsappAtual)) {
      await revisar();
      if (revisao != null && revisao!['incluidos'] > 0) await preparar();
    } else if (historico.isNotEmpty) {
      await abrir(historico.first['preparacao_id'] as String);
    }
  }

  Future<void> atualizarCampanha() async {
    final geracao = _geracao;
    final item = await c.requisitar('GET', root);
    if (valida(geracao) && item != null) setState(() => campanha = item);
  }

  Future<void> revisar() async {
    if (mockAtual && cenario == null) return;
    final geracao = _geracao;
    final escolhido = cenario;
    final result = await c.requisitar(
      'GET',
      '$root/revisao-preparacao',
      parametros: {
        'pagina': '$pagina',
        if (mockAtual) 'cenario': cenario!,
        if (revisao != null) 'marcador': revisao!['marcador'] as String,
      },
    );
    if (!valida(geracao) || escolhido != cenario) return;
    setState(() {
      revisao = result;
      if (mockAtual &&
          result != null &&
          result['configuracao_simulacao'] is! Map) {
        revisao = null;
        c.mensagem = 'A configuração de simulação não foi retornada. Atualize e revise novamente.';
      }
      foto = null;
    });
  }

  Future<void> abrir(String id) async {
    final geracao = _geracao;
    final result = await c.requisitar(
      'GET',
      '$root/preparacoes/$id',
      parametros: {'pagina': '$pagina'},
    );
    if (!valida(geracao)) return;
    if (result != null) {
      setState(() {
        foto = result;
        revisao = null;
        revisaoEnvio = null;
        resultadoEnvio = null;
        confirmaEnvioReal = false;
        integridadeComprometida = false;
      });
    } else if (mounted && c.codigoErro == 'integridade_comprometida') {
      bloquear(id);
    } else if (mounted && c.codigoErro == 'conteudo_indisponivel') {
      setState(() {
        foto = null;
        revisao = null;
      });
    }
  }

  void bloquear(String id) {
    setState(() {
      foto = null;
      revisao = null;
      integridadeComprometida = true;
      historico = [
        for (final item in historico)
          if (item['preparacao_id'] == id)
            {
              'preparacao_id': id,
              'integridade': 'comprometida',
              'bloqueada': true,
            }
          else
            item,
      ];
    });
  }

  Future<void> preparar() async {
    if (revisao == null) return;
    final geracao = _geracao;
    final result = await c.requisitar(
      'POST',
      '$root/preparacoes',
      corpo: operacao.corpo({
        'versao': revisao!['versao'],
        'marcador': revisao!['marcador'],
        if (mockAtual)
          'configuracao_simulacao': revisao!['configuracao_simulacao'],
      }),
    );
    if (!valida(geracao)) return;
    if (result != null) {
      await carregar();
    } else if (c.codigoHttp == 409) {
      setState(() => revisao = null);
    }
  }

  Future<bool> transicao(bool confirmar) async {
    if (foto == null || campanha == null) return false;
    final geracao = _geracao;
    if (!confirmar) cenario = null;
    final id = foto!['preparacao_id'];
    final result = await c.requisitar(
      'POST',
      confirmar
          ? '$root/preparacoes/$id/confirmacao'
          : '$root/retorno-rascunho',
      corpo: operacao.corpo({
        'versao': campanha!['versao'],
        'preparacao_id': id,
        if (confirmar) 'impressao': foto!['impressao'],
      }),
    );
    if (!valida(geracao)) return false;
    if (result != null) {
      await carregar();
      return true;
    } else if (c.codigoHttp == 409 ||
        c.codigoErro == 'integridade_comprometida') {
      if (c.codigoErro == 'integridade_comprometida') {
        bloquear(id as String);
      } else {
        setState(() {
          foto = null;
          revisao = null;
          integridadeComprometida = false;
        });
      }
    }
    return false;
  }

  Future<void> confirmarEIniciar() async {
    if (foto == null || foto!['confirmado_em'] != null) return;
    var marcada = false;
    final confirmou = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, atualizar) => AlertDialog(
          title: const Text('Confirmar e iniciar campanha'),
          content: SizedBox(
            width: 560,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Conta: ${campanha?['referencias']?['conta']?['nome']}'),
                Text('Categoria: ${campanha?['categoria']}'),
                Text(
                  'Template: ${campanha?['referencias']?['template']?['nome']}',
                ),
                Text(
                  'Elegiveis: ${foto!['incluidos']} | Excluidos: ${foto!['excluidos']}',
                ),
                Text('Preparacao exata: ${foto!['impressao']}'),
                const Text(
                  'O conteudo final cifrado pode ser conferido em Ver texto. Todas as validacoes serao repetidas antes de cada tentativa.',
                ),
                CheckboxListTile(
                  key: const Key('confirmar-fluxo-simplificado'),
                  value: marcada,
                  title: const Text(
                    'Confirmo esta preparacao exata e autorizo o inicio do envio.',
                  ),
                  onChanged: (value) =>
                      atualizar(() => marcada = value == true),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('Cancelar'),
            ),
            FilledButton(
              onPressed: marcada ? () => Navigator.of(ctx).pop(true) : null,
              child: const Text('Confirmar e iniciar'),
            ),
          ],
        ),
      ),
    );
    if (!mounted || confirmou != true) return;
    final preparacaoId = foto!['preparacao_id'] as String;
    if (!await transicao(true) || !mounted) return;
    if (envioTelegramUnitario) {
      await revisarEnvioTelegram();
      if (!mounted || revisaoEnvio == null) return;
      setState(() => confirmaEnvioReal = true);
      await enviarTelegram();
      return;
    }
    if (envioWhatsappUnitario) {
      await revisarEnvioWhatsapp();
      if (!mounted || revisaoEnvio == null) return;
      setState(() => confirmaEnvioReal = true);
      await enviarWhatsapp();
      return;
    }
    final execucoes = ControladorExecucoes(
      autenticacao: widget.autenticacao,
      campanhaId: widget.id,
      preparacaoId: preparacaoId,
    );
    await execucoes.revisar();
    final revisaoAtual = execucoes.revisao;
    if (revisaoAtual != null && revisaoAtual.permitida) {
      await execucoes.confirmarRevisao(revisaoAtual);
    }
    final iniciou = execucoes.recibo != null && !execucoes.temPendente;
    final mensagem = execucoes.mensagem;
    execucoes.dispose();
    if (!mounted) return;
    if (!iniciou) {
      setState(() => c.mensagem = mensagem ?? 'Revise as condicoes atuais.');
      return;
    }
    await Navigator.of(context).pushReplacement<void, void>(
      MaterialPageRoute(
        builder: (_) => PaginaExecucoes(
          autenticacao: widget.autenticacao,
          campanhaId: widget.id,
          preparacaoId: preparacaoId,
        ),
      ),
    );
  }

  Future<void> texto(String identity) async {
    final id = foto!['preparacao_id'] as String;
    final conteudo = GlobalKey<_ConteudoFotografiaState>();
    await showDialog<void>(
      context: context,
      useRootNavigator: false,
      builder: (_) => PopScope<void>(
        onPopInvokedWithResult: (saiu, _) {
          if (saiu) conteudo.currentState?.encerrar().ignore();
        },
        child: AlertDialog(
          title: const Text('Conteudo congelado'),
          content: SizedBox(
            width: 600,
            child: ConteudoFotografia(
              key: conteudo,
              autenticacao: widget.autenticacao,
              caminho: '$root/preparacoes/$id/itens/$identity/conteudo',
              aoComprometer: () {
                if (mounted) bloquear(id);
              },
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Fechar'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> revisarEnvioTelegram() async {
    if (foto == null || foto!['confirmado_em'] == null) return;
    final geracao = _geracao;
    final id = foto!['preparacao_id'] as String;
    final result = await c.requisitar(
      'GET',
      '$root/preparacoes/$id/revisao-envio-telegram',
    );
    if (!valida(geracao)) return;
    setState(() {
      revisaoEnvio = result;
      resultadoEnvio = null;
      confirmaEnvioReal = false;
      operacaoEnvio = OperacaoCampanha();
    });
  }

  Future<void> enviarTelegram() async {
    if (revisaoEnvio == null || !confirmaEnvioReal) return;
    final geracao = _geracao;
    final id = revisaoEnvio!['preparacao_id'] as String;
    final result = await c.requisitar(
      'POST',
      '$root/preparacoes/$id/envio-telegram',
      corpo: operacaoEnvio.corpo({
        'versao': revisaoEnvio!['versao'],
        'impressao': revisaoEnvio!['impressao'],
        'marcador_envio': revisaoEnvio!['marcador_envio'],
        'confirmo_envio_real': true,
      }),
    );
    if (!valida(geracao)) return;
    if (result != null) {
      setState(() {
        resultadoEnvio = result;
        revisaoEnvio = null;
        confirmaEnvioReal = false;
      });
    }
  }

  Future<void> revisarEnvioWhatsapp() async {
    if (foto == null || foto!['confirmado_em'] == null) return;
    final geracao = _geracao;
    final id = foto!['preparacao_id'] as String;
    final result = await c.requisitar(
      'GET',
      '$root/preparacoes/$id/revisao-envio-whatsapp',
    );
    if (!valida(geracao)) return;
    setState(() {
      revisaoEnvio = result;
      resultadoEnvio = null;
      confirmaEnvioReal = false;
      operacaoEnvio = OperacaoCampanha();
    });
  }

  Future<void> enviarWhatsapp() async {
    if (revisaoEnvio == null || !confirmaEnvioReal) return;
    final geracao = _geracao;
    final id = revisaoEnvio!['preparacao_id'] as String;
    final result = await c.requisitar(
      'POST',
      '$root/preparacoes/$id/envio-whatsapp',
      corpo: operacaoEnvio.corpo({
        'versao': revisaoEnvio!['versao'],
        'impressao': revisaoEnvio!['impressao'],
        'marcador_envio': revisaoEnvio!['marcador_envio'],
        'confirmo_envio_real': true,
      }),
    );
    if (!valida(geracao)) return;
    if (result != null) {
      setState(() {
        resultadoEnvio = result;
        revisaoEnvio = null;
        confirmaEnvioReal = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) {
      final dados = revisao ?? foto;
      final vigente =
          foto != null &&
          !integridadeComprometida &&
          foto!['bloqueada'] != true &&
          foto!['invalidado_em'] == null &&
          campanha?['status'] == 'preparada';
      final configuracao =
          revisao?['configuracao_simulacao'] ??
          foto?['referencias_congeladas']?['configuracao_simulacao'];
      final String? pidExecucao =
          vigente &&
              foto?['confirmado_em'] != null &&
              ((configuracao is Map && configuracao['provedor'] == 'mock') ||
                  (telegramAtual && !envioTelegramUnitario))
          ? foto!['preparacao_id'] as String
          : null;
      if (!_sessaoAtiva) {
        return const Scaffold(
          body: Center(child: Text('Sessão encerrada. Entre novamente.')),
        );
      }
      return Scaffold(
        appBar: AppBar(
          title: const Text('Preparacao e historico'),
          actions: [
            IconButton(
              tooltip: mockAtual ? 'Execuções mock' : 'Execuções da campanha',
              icon: const Icon(Icons.playlist_play),
              onPressed: campanha == null || (!mockAtual && !telegramAtual)
                  ? null
                  : () => Navigator.of(context).push<void>(
                      MaterialPageRoute(
                        builder: (_) => PaginaExecucoes(
                          autenticacao: widget.autenticacao,
                          campanhaId: widget.id,
                          preparacaoId: pidExecucao,
                        ),
                      ),
                    ),
            ),
            IconButton(
              onPressed: c.ocupado ? null : carregar,
              icon: const Icon(Icons.refresh),
              tooltip: 'Atualizar preparacao',
            ),
          ],
        ),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(avisoPreparacao),
            const Text(
              'Consentimento atual nao e autorizacao permanente. Frequencia, conexao e capacidades nao foram verificadas.',
            ),
            if (c.ocupado) const LinearProgressIndicator(),
            if (c.mensagem != null) Text(c.mensagem!),
            if (integridadeComprometida)
              const Text('Fotografia bloqueada por integridade comprometida.'),
            if (mockAtual && campanha?['status'] == 'rascunho') ...[
              const Text('Conta de simulação'),
              const Text(avisoSimulacao),
              Text(
                'Conta: ${campanha?['referencias']?['conta']?['nome'] ?? campanha?['conta_envio_id']}',
              ),
              DropdownButtonFormField<String>(
                key: ValueKey('cenario-$_geracao'),
                initialValue: cenario,
                decoration: const InputDecoration(
                  labelText: 'Cenário de simulação',
                ),
                hint: const Text('Escolha explicitamente'),
                isExpanded: true,
                items: cenariosSimulacao.entries
                    .map(
                      (e) =>
                          DropdownMenuItem(value: e.key, child: Text(e.value)),
                    )
                    .toList(),
                onChanged: c.ocupado
                    ? null
                    : (valor) => setState(() {
                        cenario = valor;
                        revisao = null;
                        foto = null;
                        pagina = 1;
                        operacao = OperacaoCampanha();
                      }),
              ),
            ],
            if (campanha?['status'] == 'rascunho')
              OutlinedButton(
                onPressed: c.ocupado || (mockAtual && cenario == null)
                    ? null
                    : () {
                        pagina = 1;
                        revisao = null;
                        revisar();
                      },
                child: const Text('Revisar para preparar'),
              ),
            if (revisao != null)
              FilledButton(
                onPressed: c.ocupado || revisao!['incluidos'] == 0
                    ? null
                    : preparar,
                child: const Text('Preparar fotografia imutavel'),
              ),
            if (configuracao is Map)
              resumoConfiguracao(configuracao, congelada: foto != null),
            if (dados != null) ...[
              if (telegramAtual && revisaoEnvio == null) ...[
                Text(
                  'Conta/bot: ${campanha?['referencias']?['conta']?['nome'] ?? dados['referencias_congeladas']?['conta_envio_id']}',
                ),
                Text(
                  'Categoria: ${campanha?['categoria'] ?? dados['referencias_congeladas']?['categoria']}',
                ),
                Text('Destinatarios revisados: ${dados['total']}'),
              ],
              Text(
                'Incluidos: ${dados['incluidos']} | Excluidos: ${dados['excluidos']}',
              ),
              if (foto?['referencias_congeladas'] != null)
                Text(
                  'Referencias congeladas: conta ${foto!['referencias_congeladas']['conta_envio_id']}; template ${foto!['referencias_congeladas']['template_versao_id']}',
                ),
              if (foto != null)
                Text(
                  foto!['invalidado_em'] != null
                      ? 'Fotografia invalidada'
                      : foto!['confirmado_em'] != null
                      ? (telegramAtual || whatsappAtual
                            ? 'Revisao confirmada. Revise antes de enviar.'
                            : 'Revisao confirmada. Envio indisponivel.')
                      : 'Aguardando confirmacao da fotografia',
                ),
              if (foto?['revisao_desatualizada'] == true)
                const Text(
                  'Dados alterados: retorne ao rascunho e prepare novamente.',
                ),
              for (final row in dados['itens'] as List)
                ListTile(
                  title: Text(
                    '${row['nome_atual'] ?? row['identidade_contato_id']} ${row['destino_mascarado'] ?? ''}',
                  ),
                  subtitle: Text(
                    '${row['incluido'] == true ? 'Incluido' : 'Excluido'}: ${(row['motivos'] as List).join(', ')}${row['impedimentos_atuais'] == null ? '' : ' | Impedimentos atuais: ${(row['impedimentos_atuais'] as List).join(', ')}'}',
                  ),
                  trailing: foto != null && row['incluido'] == true
                      ? TextButton(
                          onPressed: c.ocupado
                              ? null
                              : () => texto(
                                  (row['identidade_telegram_id'] ??
                                          row['identidade_contato_id'])
                                      as String,
                                ),
                          child: const Text('Ver texto'),
                        )
                      : null,
                ),
              Row(
                children: [
                  TextButton(
                    onPressed: c.ocupado || pagina <= 1
                        ? null
                        : () {
                            pagina--;
                            if (revisao != null) {
                              revisar();
                            } else {
                              abrir(foto!['preparacao_id'] as String);
                            }
                          },
                    child: const Text('Anterior'),
                  ),
                  Text('Pagina $pagina'),
                  TextButton(
                    onPressed:
                        c.ocupado || pagina * 20 >= (dados['total'] as int)
                        ? null
                        : () {
                            pagina++;
                            if (revisao != null) {
                              revisar();
                            } else {
                              abrir(foto!['preparacao_id'] as String);
                            }
                          },
                    child: const Text('Proxima'),
                  ),
                ],
              ),
            ],
            if (campanha?['id'] is String && mockAtual)
              AutorizacaoSimulacao(
                key: ValueKey('autorizacao-${widget.id}'),
                autenticacao: widget.autenticacao,
                campanha: campanha!,
                fotografia: foto,
                aoMudar: atualizarCampanha,
              ),
            if (vigente) ...[
              if (widget.fluxoSimplificado &&
                  (telegramAtual || envioWhatsappUnitario) &&
                  foto!['confirmado_em'] == null &&
                  foto!['revisao_desatualizada'] != true)
                FilledButton.icon(
                  key: const Key('confirmar-iniciar-campanha'),
                  onPressed: c.ocupado ? null : confirmarEIniciar,
                  icon: const Icon(Icons.send),
                  label: const Text('Confirmar e iniciar campanha'),
                ),
              FilledButton(
                onPressed:
                    c.ocupado ||
                        (widget.fluxoSimplificado &&
                            (telegramAtual || whatsappAtual)) ||
                        foto!['confirmado_em'] != null ||
                        foto!['revisao_desatualizada'] == true
                    ? null
                    : () => transicao(true),
                child: const Text('Confirmar preparação'),
              ),
              OutlinedButton(
                onPressed: c.ocupado ? null : () => transicao(false),
                child: const Text('Rejeitar e retornar ao rascunho'),
              ),
            ],
            if (vigente &&
                envioTelegramUnitario &&
                foto!['confirmado_em'] != null &&
                revisaoEnvio == null &&
                resultadoEnvio == null)
              FilledButton(
                key: const Key('revisar-envio-telegram'),
                onPressed: c.ocupado ? null : revisarEnvioTelegram,
                child: const Text('Revisar envio real Telegram'),
              ),
            if (vigente &&
                envioWhatsappUnitario &&
                !widget.fluxoSimplificado &&
                foto!['confirmado_em'] != null &&
                revisaoEnvio == null &&
                resultadoEnvio == null)
              FilledButton(
                key: const Key('revisar-envio-whatsapp'),
                onPressed: c.ocupado ? null : revisarEnvioWhatsapp,
                child: const Text('Revisar envio real WhatsApp'),
              ),
            if (vigente &&
                telegramAtual &&
                !envioTelegramUnitario &&
                foto!['confirmado_em'] != null)
              FilledButton(
                key: const Key('revisar-lote-telegram'),
                onPressed: c.ocupado
                    ? null
                    : () => Navigator.of(context).push<void>(
                        MaterialPageRoute(
                          builder: (_) => PaginaExecucoes(
                            autenticacao: widget.autenticacao,
                            campanhaId: widget.id,
                            preparacaoId: pidExecucao,
                          ),
                        ),
                      ),
                child: const Text('Revisar e autorizar lote Telegram'),
              ),
            if (revisaoEnvio != null) ...[
              Text(
                revisaoEnvio!['canal'] == 'whatsapp'
                    ? 'Revisao do envio real WhatsApp'
                    : 'Revisao do envio real Telegram',
              ),
              if (revisaoEnvio!['canal'] == 'whatsapp')
                Text('Conta: ${revisaoEnvio!['conta']['nome']}')
              else
                Text(
                  'Conta/bot: ${revisaoEnvio!['conta']['nome']} (${revisaoEnvio!['conta']['bot_id']})',
                ),
              Text(
                'Destinatario: ${revisaoEnvio!['destinatario']['nome']} ${revisaoEnvio!['destinatario']['destino_mascarado']}',
              ),
              Text('Categoria: ${revisaoEnvio!['categoria']}'),
              const Text('Conteudo final:'),
              SelectableText(revisaoEnvio!['conteudo'] as String),
              CheckboxListTile(
                key: const Key('confirmar-envio-real'),
                value: confirmaEnvioReal,
                title: const Text(
                  'Confirmo o envio real desta mensagem a este destinatario.',
                ),
                onChanged: c.ocupado
                    ? null
                    : (value) =>
                          setState(() => confirmaEnvioReal = value == true),
              ),
              FilledButton(
                key: Key(
                  revisaoEnvio!['canal'] == 'whatsapp'
                      ? 'enviar-whatsapp-real'
                      : 'enviar-telegram-real',
                ),
                onPressed: c.ocupado || !confirmaEnvioReal
                    ? null
                    : revisaoEnvio!['canal'] == 'whatsapp'
                    ? enviarWhatsapp
                    : enviarTelegram,
                child: const Text('Enviar mensagem real'),
              ),
            ],
            if (resultadoEnvio != null)
              Text(
                'Resultado: ${resultadoEnvio!['estado']} (${resultadoEnvio!['codigo_resultado']})',
              ),
            Row(
              children: [
                TextButton(
                  onPressed: c.ocupado || paginaHistorico <= 1
                      ? null
                      : () {
                          paginaHistorico--;
                          carregar();
                        },
                  child: const Text('Historico anterior'),
                ),
                TextButton(
                  onPressed: c.ocupado || paginaHistorico * 20 >= totalHistorico
                      ? null
                      : () {
                          paginaHistorico++;
                          carregar();
                        },
                  child: const Text('Proximo historico'),
                ),
              ],
            ),
            for (final p in historico)
              TextButton(
                onPressed: c.ocupado
                    ? null
                    : () {
                        pagina = 1;
                        abrir(p['preparacao_id'] as String);
                      },
                child: Text(
                  p['integridade'] == 'comprometida'
                      ? 'Fotografia ${p['preparacao_id']} - integridade comprometida; bloqueada'
                      : p['configuracao_simulacao'] is Map
                      ? 'Preparação simulada ${p['preparacao_id']}'
                      : 'Fotografia ${p['preparacao_id']}',
                ),
              ),
          ],
        ),
      );
    },
  );
}

Widget resumoConfiguracao(Map cfg, {required bool congelada}) => Column(
  crossAxisAlignment: CrossAxisAlignment.start,
  children: [
    Text(
      congelada ? 'Preparação simulada' : 'Configuração de simulação revisada',
    ),
    const Text(avisoSimulacao),
    Text(
      'Modo: ${cfg['modo']} • Provedor: ${cfg['provedor']} • Plataforma: ${cfg['plataforma']}',
    ),
    Text('Conta: ${cfg['conta_envio_id']}'),
    Text(
      'Cenário de simulação: ${cenariosSimulacao[cfg['cenario']] ?? cfg['cenario']}',
    ),
    ExpansionTile(
      title: const Text('Detalhes da configuração'),
      children: [
        Text('Identificador: ${cfg['configuracao_id']}'),
        Text('Versão da configuração: ${cfg['versao_configuracao']}'),
        Text('Contrato mock: ${cfg['versao_contrato_mock']}'),
        Text('Política: ${cfg['politica_cenarios']}'),
        Text('Capacidades declaradas: ${cfg['capacidades']}'),
        SelectableText('Resumo: ${cfg['resumo_configuracao']}'),
      ],
    ),
  ],
);

class ConteudoFotografia extends StatefulWidget {
  const ConteudoFotografia({
    super.key,
    required this.autenticacao,
    required this.caminho,
    this.aoComprometer,
  });
  final ControladorAutenticacao autenticacao;
  final String caminho;
  final VoidCallback? aoComprometer;
  @override
  State<ConteudoFotografia> createState() => _ConteudoFotografiaState();
}

class _ConteudoFotografiaState extends State<ConteudoFotografia> {
  late var c = ControladorCampanhas(widget.autenticacao);
  String? texto;
  int _geracao = 0;
  Future<void> _fimAnterior = Future.value();
  late bool _sessaoAtiva;
  Future<void> encerrar() {
    _geracao++;
    texto = null;
    return c.cancelar();
  }

  void refazer() {
    _geracao++;
    texto = null;
    if (c.ocupado) _fimAnterior = c.cancelar();
    c.dispose();
    c = ControladorCampanhas(widget.autenticacao);
  }

  void sessao() {
    final ativa = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    if (!mounted || ativa == _sessaoAtiva) return;
    _sessaoAtiva = ativa;
    setState(refazer);
    if (ativa) WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void didUpdateWidget(ConteudoFotografia oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.caminho != widget.caminho ||
        oldWidget.autenticacao != widget.autenticacao) {
      oldWidget.autenticacao.removeListener(sessao);
      widget.autenticacao.addListener(sessao);
      _sessaoAtiva =
          widget.autenticacao.estado == EstadoAutenticacao.autenticado;
      refazer();
      WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
    }
  }

  @override
  void initState() {
    super.initState();
    _sessaoAtiva = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    widget.autenticacao.addListener(sessao);
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  Future<void> carregar() async {
    if (!_sessaoAtiva) return;
    final geracao = _geracao;
    await _fimAnterior;
    if (!mounted || geracao != _geracao || !_sessaoAtiva) return;
    final r = await c.requisitar('GET', widget.caminho);
    if (!mounted || geracao != _geracao || !_sessaoAtiva) return;
    if (r != null) {
      setState(() => texto = r['conteudo_renderizado'] as String);
    } else if (mounted && c.codigoErro == 'integridade_comprometida') {
      widget.aoComprometer?.call();
    }
  }

  @override
  void dispose() {
    _geracao++;
    widget.autenticacao.removeListener(sessao);
    texto = null;
    c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (_, _) => SingleChildScrollView(
      child: Text(
        !_sessaoAtiva
            ? 'Conteúdo indisponível: sessão encerrada.'
            : texto ?? c.mensagem ?? 'Consultando conteudo...',
      ),
    ),
  );
}
