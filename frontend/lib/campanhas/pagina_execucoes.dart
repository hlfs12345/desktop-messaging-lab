import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_execucoes.dart';
import 'execucao_mock.dart';

class PaginaExecucoes extends StatefulWidget {
  const PaginaExecucoes({
    super.key,
    required this.autenticacao,
    required this.campanhaId,
    this.preparacaoId,
  });
  final ControladorAutenticacao autenticacao;
  final String campanhaId;
  final String? preparacaoId;
  @override
  State<PaginaExecucoes> createState() => _PaginaExecucoesState();
}

class _PaginaExecucoesState extends State<PaginaExecucoes>
    with WidgetsBindingObserver {
  late ControladorExecucoes c;
  DialogRoute<bool>? _dialogo;
  NavigatorState? _nav;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _criar();
  }

  void _criar() {
    c = ControladorExecucoes(
      autenticacao: widget.autenticacao,
      campanhaId: widget.campanhaId,
      preparacaoId: widget.preparacaoId,
    );
    c.addListener(_contexto);
    final origem = c;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && identical(c, origem) && c.ativo) c.carregar();
    });
  }

  void _fecharDialogo() {
    final route = _dialogo, nav = _nav;
    _dialogo = null;
    _nav = null;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (route != null && route.isActive && nav != null && nav.mounted) {
        nav.removeRoute(route);
      }
    });
  }

  void _contexto() {
    if (!c.ativo) _fecharDialogo();
  }

  @override
  void didUpdateWidget(PaginaExecucoes oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.autenticacao != widget.autenticacao ||
        oldWidget.campanhaId != widget.campanhaId ||
        oldWidget.preparacaoId != widget.preparacaoId) {
      _fecharDialogo();
      c.removeListener(_contexto);
      c.dispose();
      _criar();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    c.visibilidade(state == AppLifecycleState.resumed);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _fecharDialogo();
    c.removeListener(_contexto);
    c.dispose();
    super.dispose();
  }

  Future<bool> _confirmar(String titulo, String explicacao) async {
    final origem = c;
    var marcada = false;
    c.confirmarDialogo(true);
    final nav = Navigator.of(context);
    final route = DialogRoute<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, atualizar) => AlertDialog(
          title: Text(titulo),
          content: SizedBox(
            width: 520,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(explicacao),
                CheckboxListTile(
                  key: const ValueKey('confirmacao-execucao'),
                  value: marcada,
                  title: const Text('Li e confirmo esta ação'),
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
              key: const ValueKey('confirmar-execucao'),
              onPressed: marcada
                  ? () {
                      if (mounted &&
                          identical(c, origem) &&
                          origem.ativo &&
                          ModalRoute.of(ctx)?.isCurrent == true) {
                        Navigator.of(ctx).pop(true);
                      }
                    }
                  : null,
              child: const Text('Confirmar'),
            ),
          ],
        ),
      ),
    );
    _dialogo = route;
    _nav = nav;
    final resposta = await nav.push(route);
    if (identical(_dialogo, route)) {
      _dialogo = null;
      _nav = null;
    }
    if (!mounted || !identical(c, origem) || !origem.ativo) return false;
    c.confirmarDialogo(false);
    return resposta == true;
  }

  Future<void> _decidir(RevisaoExecucao revisao) async {
    final origem = c;
    final telegram = revisao.modo == 'telegram';
    final aviso = telegram ? avisoExecucaoTelegram : avisoExecucao;
    final confirmou = await _confirmar(
      revisao.retomada ? 'Confirmar retomada' : 'Confirmar início',
      '$aviso\n${revisao.retomada ? 'Somente pendentes elegíveis serão processados. Resultados desconhecidos não serão repetidos.' : telegram ? 'Esta autorização vale somente para o lote, bot, categoria, conteúdo, quantidade e configuração revisados.' : 'Esta ação inicia o processamento simulado da preparação revisada.'}',
    );
    if (confirmou &&
        mounted &&
        identical(c, origem) &&
        identical(c.revisao, revisao)) {
      await c.confirmarRevisao(revisao);
    }
  }

  Future<void> _pausar() async {
    final origem = c, e = c.execucao!;
    final confirmou = await _confirmar(
      'Confirmar pausa',
      'Novos despachos serão suspensos. Uma tentativa já admitida pode terminar. A pausa não é cancelamento instantâneo.',
    );
    if (confirmou && mounted && identical(c, origem)) await c.pausar(e);
  }

  Widget _paginacao(int pagina, int total, void Function(int) carregar) => Wrap(
    crossAxisAlignment: WrapCrossAlignment.center,
    spacing: 12,
    children: [
      TextButton(
        onPressed: !c.ocupado && pagina > 1 ? () => carregar(pagina - 1) : null,
        child: const Text('Anterior'),
      ),
      Text('Página $pagina · $total registros'),
      TextButton(
        onPressed: !c.ocupado && pagina * 20 < total
            ? () => carregar(pagina + 1)
            : null,
        child: const Text('Próxima'),
      ),
    ],
  );

  Widget _revisao(RevisaoExecucao r) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            r.retomada ? 'Revisão de retomada' : 'Revisão de início',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          Text(
            r.modo == 'telegram'
                ? 'Configuração: Telegram Bot API · sequencial · intervalo mínimo de 5 segundos'
                : 'Configuração: mock · WhatsApp',
          ),
          if (r.modo == 'mock')
            Text('Cenário: ${descricaoEstado(r.cenario)}'),
          if (r.modo == 'telegram') ...[
            Text(
              'Bot: ${r.bot ?? 'não verificado'} · Categoria: ${r.categoria}',
            ),
            Text(
              'Fotografia do conteúdo: ${r.impressao.length <= 12 ? r.impressao : '${r.impressao.substring(0, 12)}…'}',
            ),
          ],
          Text(
            'Incluídos: ${r.incluidos} · ${r.retomada ? 'Pendentes elegíveis agora' : 'Elegíveis agora'}: ${r.elegiveis} · Ignorados atuais: ${r.ignorados}',
          ),
          Text('Excluídos da preparação: ${r.excluidos}'),
          if (r.retomada)
            Text(
              'Pendentes: ${r.pendentes}. Desconhecidos não serão repetidos.',
            ),
          Text(r.modo == 'telegram' ? avisoExecucaoTelegram : avisoExecucao),
          if (r.impedimentos.isNotEmpty)
            Text('Bloqueios: ${r.impedimentos.join(', ')}'),
          if (r.impedimentoRetomada != null)
            Text('Retomada bloqueada: ${r.impedimentoRetomada}'),
          if (!r.permitida)
            const Text(
              'Condições atuais não permitem esta ação. Confira autorização, conta e elegibilidade.',
            ),
          FilledButton(
            onPressed: c.podeDecidir && r.permitida ? () => _decidir(r) : null,
            child: Text(r.retomada ? 'Confirmar retomada' : 'Confirmar início'),
          ),
        ],
      ),
    ),
  );

  List<Widget> _dashboard(ExecucaoMock e) => [
    Text(
      descricaoExecucao(e),
      style: Theme.of(context).textTheme.headlineSmall,
    ),
    Text(
      e.workerAtivo
          ? (e.admitida
                ? (e.telegram
                      ? 'Processamento Telegram sequencial ativo nesta abertura.'
                      : 'Processamento simulado ativo nesta abertura.')
                : 'Finalizando uma etapa em andamento.')
          : 'Processamento inativo nesta consulta. Uma execução registrada não significa que as tentativas começaram.',
    ),
    if (e.estado == 'pausando')
      const Text('Aguardando finalizar tentativa já admitida.'),
    if (e.estado == 'pausada')
      const Text(
        'Retomada exige nova revisão e confirmação. Reabrir ou entrar não retoma.',
      ),
    if (!e.recuperacaoDisponivel)
      const Text(
        'Recuperação indisponível: processamento bloqueado; histórico preservado.',
      ),
    if (e.encerrando)
      const Text('Aplicativo encerrando. Novos despachos bloqueados.'),
    Text(
      'Criada: ${formatarHorario(e.criado)} · Atualizada: ${formatarHorario(e.atualizado)}',
    ),
    Wrap(
      spacing: 12,
      runSpacing: 8,
      children: [
        Chip(label: Text('Total: ${e.total}')),
        for (final estado in estadosItens)
          Chip(
            label: Text('${descricaoEstado(estado)}: ${e.contagens[estado]}'),
          ),
      ],
    ),
    if ((e.contagens['desconhecida'] ?? 0) > 0)
      const Text(
        'Há resultados desconhecidos: ausência de confirmação não comprova falha ou não envio. Eles não serão repetidos automaticamente.',
      ),
    Wrap(
      spacing: 12,
      children: [
        OutlinedButton(
          onPressed: c.podeDecidir && e.acompanha ? _pausar : null,
          child: const Text('Pausar'),
        ),
        OutlinedButton(
          onPressed: c.podeDecidir && e.estado == 'pausada' && e.disponivel
              ? () => c.revisar(retomar: true)
              : null,
          child: const Text('Revisar retomada'),
        ),
      ],
    ),
  ];

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: const Text('Execuções de campanha'),
        actions: [
          IconButton(
            tooltip: 'Atualizar execução',
            onPressed: c.ativo && !c.ocupado && !c.confirmando
                ? c.atualizar
                : null,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: !c.ativo
          ? const Center(child: Text('Sessão encerrada. Entre novamente.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  c.execucao?.telegram == true || c.revisao?.modo == 'telegram'
                      ? avisoExecucaoTelegram
                      : avisoExecucao,
                ),
                if (c.nomeConta != null)
                  Text('Conta atual da campanha: ${c.nomeConta}'),
                if (c.ocupado) const LinearProgressIndicator(),
                if (c.mensagem != null)
                  Text(c.mensagem!, key: const ValueKey('mensagem-execucao')),
                if (c.historicoIndisponivel)
                  const Text(
                    'Histórico indisponível nesta consulta. Os dados anteriores podem estar desatualizados.',
                  ),
                if (c.temPendente) ...[
                  const Text(
                    'Operação com confirmação pendente. Uma consulta não confirma o recibo.',
                  ),
                  Wrap(
                    spacing: 12,
                    children: [
                      OutlinedButton(
                        onPressed: c.ocupado ? null : c.atualizar,
                        child: const Text('Consultar estado'),
                      ),
                      OutlinedButton(
                        onPressed: c.ocupado ? null : c.repetir,
                        child: const Text('Repetir a mesma operação'),
                      ),
                    ],
                  ),
                ],
                if (c.recibo != null)
                  Text(
                    c.recibo!.replay
                        ? 'Recibo histórico recebido.'
                        : 'Recibo da operação recebido.',
                  ),
                if (c.execucao == null) ...[
                  if (widget.preparacaoId != null)
                    OutlinedButton(
                      onPressed: c.podeDecidir ? () => c.revisar() : null,
                      child: const Text(
                        'Revisar início da preparação confirmada',
                      ),
                    ),
                  if (widget.preparacaoId == null)
                    const Text(
                      'Para iniciar, selecione uma preparação confirmada em Preparação e histórico.',
                    ),
                  if (c.revisao != null) _revisao(c.revisao!),
                  const Text('Histórico de execuções'),
                  if (!c.historicoIndisponivel &&
                      c.execucoes?.itens.isEmpty == true)
                    const Text('Sem execução registrada.'),
                  for (final e in c.execucoes?.itens ?? <ExecucaoMock>[])
                    ListTile(
                      title: Text(descricaoExecucao(e)),
                      subtitle: Text(
                        '${formatarHorario(e.criado)} · ${e.total} itens',
                      ),
                      onTap: c.ocupado ? null : () => c.abrirExecucao(e.id),
                    ),
                  if (c.execucoes != null)
                    _paginacao(
                      c.execucoes!.pagina,
                      c.execucoes!.total,
                      (p) => c.carregar(pagina: p),
                    ),
                ] else ...[
                  TextButton(
                    onPressed: c.ocupado ? null : c.voltar,
                    child: Text(
                      c.item == null
                          ? 'Voltar às execuções'
                          : 'Voltar aos itens',
                    ),
                  ),
                  ..._dashboard(c.execucao!),
                  if (c.revisao != null) _revisao(c.revisao!),
                  if (c.item == null) ...[
                    const Text('Itens da execução'),
                    if (c.itens == null)
                      TextButton(
                        onPressed: c.ocupado ? null : () => c.carregarItens(1),
                        child: const Text('Consultar itens'),
                      ),
                    for (final i in c.itens?.itens ?? <ItemMock>[])
                      ListTile(
                        title: Text(
                          'Item ${i.ordem} · ${descricaoEstado(i.estado)}',
                        ),
                        subtitle: Text(
                          '${formatarHorario(i.atualizado)}${i.motivo == null ? '' : ' · Bloqueio: ${i.motivo}'}',
                        ),
                        trailing: const Icon(Icons.history),
                        onTap: c.ocupado ? null : () => c.abrirItem(i),
                      ),
                    if (c.itens != null)
                      _paginacao(
                        c.itens!.pagina,
                        c.itens!.total,
                        c.carregarItens,
                      ),
                  ] else ...[
                    Text('Tentativas do item ${c.item!.ordem}'),
                    if (c.tentativas?.itens.isEmpty == true)
                      const Text('Nenhuma tentativa registrada.'),
                    for (final t in c.tentativas?.itens ?? <TentativaMock>[])
                      ListTile(
                        title: Text(
                          'Tentativa ${t.numero} · ${descricaoEstado(t.estado)}',
                        ),
                        subtitle: Text(
                          'Reserva: ${formatarHorario(t.reservada)}\nInvocação: ${formatarHorario(t.invocacao)} · Resultado: ${formatarHorario(t.finalizada)}${t.messageId == null ? '' : ' · message_id: ${t.messageId}'}${t.retryAfter == null ? '' : ' · retry_after: ${t.retryAfter}s'}',
                        ),
                      ),
                    if (c.tentativas != null)
                      _paginacao(
                        c.tentativas!.pagina,
                        c.tentativas!.total,
                        (p) => c.abrirItem(c.item!, pagina: p),
                      ),
                  ],
                ],
              ],
            ),
    ),
  );
}
