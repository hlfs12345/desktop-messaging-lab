import 'dart:convert';

import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import '../simulacao.dart';
import 'controlador_autorizacao_simulacao.dart';

class AutorizacaoSimulacao extends StatefulWidget {
  const AutorizacaoSimulacao({
    super.key,
    required this.autenticacao,
    required this.campanha,
    this.fotografia,
    this.aoMudar,
  });
  final ControladorAutenticacao autenticacao;
  final Map<String, dynamic> campanha;
  final Map<String, dynamic>? fotografia;
  final Future<void> Function()? aoMudar;
  @override
  State<AutorizacaoSimulacao> createState() => _AutorizacaoSimulacaoState();
}

class _AutorizacaoSimulacaoState extends State<AutorizacaoSimulacao> {
  late ControladorAutorizacaoSimulacao c;
  late String _assinatura;
  late int _sessao;
  late bool _ativa;
  int _geracao = 0;
  bool _confirmando = false;
  Future<void> _fimAnterior = Future.value();
  DialogRoute<bool>? _dialogo;
  NavigatorState? _navegador;

  Map? get cfg =>
      widget.fotografia?['referencias_congeladas']?['configuracao_simulacao']
          as Map?;
  bool get revisavel =>
      widget.campanha['status'] == 'preparada' &&
      widget.fotografia?['confirmado_em'] is String &&
      widget.fotografia?['invalidado_em'] == null &&
      widget.fotografia?['integridade'] == 'verificada' &&
      widget.fotografia?['bloqueada'] != true &&
      cfg?['modo'] == 'simulacao' &&
      cfg?['provedor'] == 'mock' &&
      cfg?['plataforma'] == 'whatsapp';
  String get assinatura => jsonEncode([
    widget.campanha['id'],
    widget.campanha['conta_envio_id'],
    widget.campanha['versao'],
    widget.campanha['status'],
    widget.fotografia?['preparacao_id'],
    widget.fotografia?['impressao'],
    widget.fotografia?['confirmado_em'],
    widget.fotografia?['invalidado_em'],
    widget.fotografia?['integridade'],
    cfg,
  ]);
  ControladorAutorizacaoSimulacao novo() => ControladorAutorizacaoSimulacao(
    autenticacao: widget.autenticacao,
    campanhaId: widget.campanha['id'] as String,
    preparacaoId: widget.fotografia?['preparacao_id'] as String?,
    impressao: widget.fotografia?['impressao'] as String?,
    resumoConfiguracao: cfg?['resumo_configuracao'] as String?,
    fotografiaAutenticada: revisavel,
  );
  bool valida(int geracao, ControladorAutorizacaoSimulacao origem) =>
      mounted && geracao == _geracao && identical(c, origem) && origem.ativo;

  @override
  void initState() {
    super.initState();
    _assinatura = assinatura;
    _sessao = widget.autenticacao.geracaoSessao;
    _ativa = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    c = novo();
    widget.autenticacao.addListener(sessao);
    agendarConsulta();
  }

  void agendarConsulta() {
    final geracao = _geracao, origem = c;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await _fimAnterior;
      if (valida(geracao, origem)) await origem.carregar();
    });
  }

  void fecharDialogo() {
    final route = _dialogo, nav = _navegador;
    _dialogo = null;
    _navegador = null;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (nav != null && nav.mounted && route != null && route.isActive) {
        nav.removeRoute(route);
      }
    });
  }

  void reiniciar() {
    _geracao++;
    fecharDialogo();
    final anterior = c;
    _fimAnterior = Future.wait([_fimAnterior, anterior.encerrar()])
        .then((_) {});
    anterior.dispose();
    _assinatura = assinatura;
    _sessao = widget.autenticacao.geracaoSessao;
    _ativa = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    _confirmando = false;
    c = novo();
    agendarConsulta();
  }

  void sessao() {
    final ativa = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    if (!mounted ||
        (_sessao == widget.autenticacao.geracaoSessao && ativa == _ativa)) {
      return;
    }
    setState(reiniciar);
  }

  @override
  void didUpdateWidget(AutorizacaoSimulacao oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.autenticacao != widget.autenticacao ||
        _assinatura != assinatura) {
      oldWidget.autenticacao.removeListener(sessao);
      widget.autenticacao.addListener(sessao);
      reiniciar();
    }
  }

  @override
  void dispose() {
    _geracao++;
    fecharDialogo();
    widget.autenticacao.removeListener(sessao);
    c.dispose();
    super.dispose();
  }

  Future<bool> confirmar(bool revogar) async {
    var marcada = false;
    final origem = c, geracao = _geracao;
    setState(() => _confirmando = true);
    final nav = Navigator.of(context);
    final route = DialogRoute<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, atualizar) => AlertDialog(
          title: Text(
            revogar ? 'Confirmar revogação' : 'Confirmar autorização',
          ),
          content: SizedBox(
            width: 520,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  revogar
                      ? 'Revogar preserva o histórico e não modifica a fotografia.'
                      : avisoAutorizacao,
                ),
                CheckboxListTile(
                  key: const ValueKey('confirmacao-autorizacao'),
                  value: marcada,
                  title: Text(
                    revogar
                        ? 'Confirmo a revogação'
                        : 'Confirmo a decisão de simulação',
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
              onPressed: !marcada
                  ? null
                  : () {
                      if (!valida(geracao, origem) ||
                          !ctx.mounted ||
                          ModalRoute.of(ctx)?.isCurrent != true) {
                        return;
                      }
                      Navigator.of(ctx).pop(true);
                    },
              child: Text(
                revogar ? 'Confirmar revogação' : 'Confirmar autorização',
              ),
            ),
          ],
        ),
      ),
    );
    _dialogo = route;
    _navegador = nav;
    final resultado = await nav.push(route);
    if (identical(_dialogo, route)) {
      _dialogo = null;
      _navegador = null;
    }
    if (!valida(geracao, origem)) return false;
    setState(() => _confirmando = false);
    return resultado == true;
  }

  Future<void> decidir(bool revogar) async {
    if (_confirmando ||
        c.ocupado ||
        (revogar ? !c.podeRevogar : !c.podeAutorizar)) {
      return;
    }
    final origem = c, geracao = _geracao;
    final dados = revogar ? c.detalhe! : c.revisao!;
    if (!await confirmar(revogar) || !valida(geracao, origem)) return;
    final anterior = c.recibo;
    if (revogar) {
      await origem.revogar(dados);
    } else {
      await origem.autorizar(dados);
    }
    if (valida(geracao, origem) && !identical(anterior, origem.recibo)) {
      await widget.aoMudar?.call();
    }
  }

  Future<void> repetir() async {
    final origem = c, geracao = _geracao, anterior = c.recibo;
    await origem.repetir();
    if (valida(geracao, origem) && !identical(anterior, origem.recibo)) {
      await widget.aoMudar?.call();
    }
  }

  String motivo(String code) => switch (code) {
    'bloqueio_global' => 'Bloqueio global',
    'bloqueio_canal' => 'Descadastramento ou bloqueio do canal',
    'categoria_revogada' => 'Consentimento da categoria revogado',
    'sem_autorizacao' => 'Sem consentimento',
    'sem_destinatarios_elegiveis' => 'Nenhum incluído atualmente elegível',
    'conta_inativa' => 'Conta inativa',
    'autorizacao_revogada' => 'Autorização revogada',
    'preparacao_invalidada' => 'Preparação invalidada',
    'referencias_campanha_alteradas' => 'Referências da campanha alteradas',
    'integridade_comprometida' => 'Integridade comprometida',
    'conteudo_indisponivel' => 'Chave ou fotografia indisponível',
    'revogacao_manual' => 'Revogação manual',
    'retorno_rascunho' => 'Retorno ao rascunho',
    _ =>
      RegExp(r'^[a-z_]{1,64}$').hasMatch(code)
          ? code
          : 'Motivo não reconhecido',
  };

  List<Widget> avaliacao(Map data) => [
    if (data['incluidos'] is int)
      Text('Incluídos originais: ${data['incluidos']}'),
    if (data['elegiveis_atuais'] is int)
      Text('Atualmente elegíveis: ${data['elegiveis_atuais']}'),
    if (data['impedidos_atuais'] is int)
      Text('Atualmente impedidos: ${data['impedidos_atuais']}'),
    for (final code in data['impedimentos'] as List)
      Text('Impedimento: ${motivo(code as String)}'),
  ];

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) {
      if (!c.ativo) return const Text('Sessão encerrada. Entre novamente.');
      final bloqueada = c.ocupado || _confirmando;
      final atual = c.detalhe?['validade_atual'] as Map?;
      final cenario = cfg?['cenario'];
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Divider(),
          Text(
            'Autorização de simulação',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const Text(avisoAutorizacao),
          Text('Campanha: ${widget.campanha['nome'] ?? c.campanhaId}'),
          if (widget.fotografia == null)
            const Text(
              'Fotografia não aberta ou indisponível. Histórico e revogação independem dela.',
            ),
          if (widget.fotografia != null) Text('Preparação: ${c.preparacaoId}'),
          if (cfg != null) ...[
            Text('Conta mock congelada (ID): ${cfg?['conta_envio_id']}'),
            Text('Cenário congelado: ${cenariosSimulacao[cenario] ?? cenario}'),
          ],
          if (c.impressao != null)
            Text('Impressão da fotografia: ${c.impressao}'),
          if (c.ocupado) const LinearProgressIndicator(),
          if (c.mensagem != null) Text(c.mensagem!),
          if (c.resultadoDesconhecido) ...[
            const Text(
              'Resultado da decisão não confirmado. Consultar não desfaz a operação.',
            ),
            OutlinedButton(
              onPressed: bloqueada ? null : repetir,
              child: const Text('Repetir a mesma operação'),
            ),
          ],
          if (!revisavel)
            const Text(
              'A autorização exige preparação simulada vigente e confirmada.',
            ),
          if (revisavel)
            OutlinedButton(
              onPressed: bloqueada || c.temPendente ? null : c.revisar,
              child: const Text('Revisar autorização'),
            ),
          if (c.revisao != null) ...[
            ...avaliacao(c.revisao!['avaliacao_atual'] as Map),
            FilledButton(
              onPressed: bloqueada || !c.podeAutorizar
                  ? null
                  : () => decidir(false),
              child: const Text('Autorizar simulação'),
            ),
          ],
          if (c.recibo != null)
            Text(
              'Recibo da operação (histórico): ${c.recibo!['estado_decisao']}. Não comprova validade atual.',
            ),
          const Divider(),
          const Text('Histórico de autorizações'),
          if (c.historicoCarregado && c.total == 0)
            const Text('Nenhuma autorização registrada nesta campanha.'),
          if (!c.historicoCarregado && !c.ocupado)
            const Text('Histórico de autorizações não carregado.'),
          TextButton(
            onPressed: bloqueada ? null : () => c.carregar(pagina: c.pagina),
            child: const Text('Consultar autorizações'),
          ),
          for (final item in c.historico)
            TextButton(
              onPressed: bloqueada ? null : () => c.abrir(item['id'] as String),
              child: Text('Decisão ${item['id']} — ${item['estado']}'),
            ),
          Row(
            children: [
              TextButton(
                onPressed: bloqueada || c.pagina <= 1
                    ? null
                    : () => c.carregar(pagina: c.pagina - 1),
                child: const Text('Autorizações anteriores'),
              ),
              Text('Página ${c.pagina}'),
              TextButton(
                onPressed: bloqueada || c.pagina * 20 >= c.total
                    ? null
                    : () => c.carregar(pagina: c.pagina + 1),
                child: const Text('Próximas autorizações'),
              ),
            ],
          ),
          if (c.detalhe != null && atual != null) ...[
            Text('Decisão histórica: ${c.detalhe!['estado']}'),
            Text('Preparação da decisão: ${c.detalhe!['preparacao_id']}'),
            Text('Autorizada em: ${c.detalhe!['autorizado_em']}'),
            Text('Autor (ID): ${c.detalhe!['autorizado_por_usuario_id']}'),
            if (c.detalhe!['estado'] == 'revogada') ...[
              const Text('Autorização revogada'),
              Text('Revogada em: ${c.detalhe!['revogado_em']}'),
              Text('Revogador (ID): ${c.detalhe!['revogado_por_usuario_id']}'),
              Text(
                'Motivo da revogação: ${motivo(c.detalhe!['motivo_codigo'] as String)}',
              ),
            ],
            Text(
              atual['avaliada'] != true
                  ? 'Validade atual não verificada.'
                  : atual['condicoes_atuais_atendidas'] == true
                  ? 'Condições avaliadas atendidas neste momento. Processamento indisponível.'
                  : 'Autorização atualmente impedida.',
            ),
            ...avaliacao(atual),
            OutlinedButton(
              onPressed: bloqueada || !c.podeRevogar
                  ? null
                  : () => decidir(true),
              child: const Text('Revogar autorização'),
            ),
          ],
          const Text(
            'Processamento indisponível. Não há início de campanha nesta versão.',
          ),
        ],
      );
    },
  );
}
