import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import '../templates/texto_unicode.dart';
import 'controlador_campanhas.dart';
import 'preparacao_campanha.dart';

const motivosCampanha = {
  'bloqueio_global': 'Bloqueio global do contato',
  'bloqueio_canal': 'Bloqueio neste canal',
  'categoria_revogada': 'Autorização revogada nesta categoria',
  'identidade_nao_cadastrada': 'Identidade não cadastrada',
  'contato_arquivado': 'Contato arquivado',
  'telefone_arquivado': 'Telefone arquivado',
  'sem_autorizacao': 'Sem autorização registrada',
  'inscricao_telegram_ausente': 'Sem inscrição neste bot',
  'inscricao_telegram_revogada': 'Inscrição Telegram revogada',
  'consentimento_telegram_ausente':
      'Sem autorização Telegram para este escopo e categoria',
  'contato_inativo': 'Contato arquivado',
  'conta_inativa': 'Conta Telegram inativa',
  'conta_telegram_incompativel': 'Conta incompatível com Telegram',
  'bot_telegram_nao_verificado': 'Bot Telegram ainda não verificado',
};

class RevisaoCampanha extends StatefulWidget {
  const RevisaoCampanha({
    super.key,
    required this.autenticacao,
    required this.id,
  });
  final ControladorAutenticacao autenticacao;
  final String id;
  @override
  State<RevisaoCampanha> createState() => _RevisaoCampanhaState();
}

class _RevisaoCampanhaState extends State<RevisaoCampanha> {
  late final c = ControladorCampanhas(widget.autenticacao);
  final nome = TextEditingController();
  final form = GlobalKey<FormState>();
  final operacoes = <String, OperacaoCampanha>{};
  final operacaoElegiveis = OperacaoCampanha();
  Map<String, dynamic> dados = {'itens': [], 'total': 0};
  String? previa;
  int pagina = 1;
  bool selecionadas = false, atualizada = false;

  bool get telegramAtual =>
      (dados['referencias'] as Map?)?['conta']?['provedor'] ==
      'telegram_bot_api';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void dispose() {
    nome.dispose();
    operacoes.clear();
    previa = null;
    dados = {};
    c.dispose();
    super.dispose();
  }

  Future<void> carregar() async {
    setState(() {
      atualizada = false;
      previa = null;
    });
    final result = await c.requisitar(
      'GET',
      '/campanhas/${widget.id}/revisao',
      parametros: {
        'pagina': '$pagina',
        'somente_selecionadas': '$selecionadas',
      },
    );
    if (mounted && result != null) {
      setState(() {
        dados = result;
        atualizada = true;
      });
    }
  }

  Future<void> selecionar(Map row, bool valor) async {
    final telegram = row['canal'] == 'telegram';
    final campo = telegram ? 'identidade_telegram_id' : 'identidade_contato_id';
    final id = row[campo] as String;
    final operacao = operacoes.putIfAbsent(id, OperacaoCampanha.new);
    setState(() => previa = null);
    final result = await c.requisitar(
      'PUT',
      '/campanhas/${widget.id}/selecoes',
      corpo: operacao.corpo({
        'versao': dados['versao'],
        campo: id,
        'selecionada': valor,
      }),
    );
    if (!mounted) return;
    if (result != null) {
      await carregar();
    } else {
      // Exigir consulta nova antes de alterar a seleção novamente.
      setState(() => atualizada = false);
    }
  }

  Future<void> selecionarElegiveis() async {
    setState(() => previa = null);
    final result = await c.requisitar(
      'PUT',
      '/campanhas/${widget.id}/selecoes-elegiveis',
      corpo: operacaoElegiveis.corpo({'versao': dados['versao']}),
    );
    if (!mounted) return;
    if (result != null) {
      await carregar();
      if (mounted) {
        setState(
          () => c.mensagem =
              '${result['selecionados']} destinatario(s) elegivel(is) selecionado(s).',
        );
      }
    } else {
      setState(() => atualizada = false);
    }
  }

  Widget candidato(Map row) {
    final telegram = row['canal'] == 'telegram';
    final selecionada = row['selecionada'] as bool;
    final consentimento = row['consentimento'] as Map;
    final motivos =
        (row['motivos'] as List?) ??
        (consentimento['motivos'] as List?) ??
        const [];
    final destino = telegram ? row['destino_mascarado'] : row['telefone'];
    final telegramInelegivel =
        telegram && consentimento['elegivel_consentimento'] != true;
    return Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          CheckboxListTile(
            title: Text('${row['nome'] ?? 'Contato sem nome'} · $destino'),
            subtitle: Text(
              telegram
                  ? 'Identidade Telegram vinculada à conta desta campanha.'
                  : 'Contato com ${row['identidades_do_contato'] ?? 1} identidade(s) cadastrada(s). Cada identidade tem seleção e consentimento próprios.',
            ),
            value: selecionada,
            onChanged:
                c.ocupado ||
                    !atualizada ||
                    (row['candidata_atual'] != true && !selecionada) ||
                    (telegramInelegivel && !selecionada)
                ? null
                : (v) => selecionar(row, v!),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  row['pertence_listas'] == true
                      ? 'Pertence às listas atuais.'
                      : 'Fora das listas atuais. Desmarque ou revise as listas.',
                ),
                if (row['candidata_atual'] != true)
                  const Text(
                    'Não é candidata atual: confira plataforma e listas ativas.',
                  ),
                if (telegramInelegivel)
                  const Text(
                    'Não selecionável: consentimento, bloqueio ou revogação impede o envio.',
                  ),
                Text('Consentimento atual: ${consentimento['estado']}.'),
                for (final motivo in motivos)
                  Text(motivosCampanha[motivo] ?? '$motivo'),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Future<void> gerarPrevia() async {
    if (!form.currentState!.validate()) return;
    setState(() => previa = null);
    final result = await c.requisitar(
      'POST',
      '/campanhas/${widget.id}/previa',
      corpo: {
        'versao': dados['versao'],
        if (nome.text.trim().isNotEmpty) 'nome': nome.text.trim(),
      },
    );
    if (!mounted) return;
    if (result != null) {
      setState(() => previa = result['conteudo_renderizado'] as String);
    } else if (c.codigoHttp == 409 || c.codigoErro == 'referencia_invalida') {
      setState(() => atualizada = false);
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: const Text('Revisar destinatários'),
        actions: [
          IconButton(
            tooltip: 'Atualizar revisão',
            onPressed: c.ocupado ? null : carregar,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(avisoCampanha),
          Text(
            telegramAtual
                ? 'Candidatos usam identidades Telegram recebidas pelo bot da conta e associadas às listas da campanha.'
                : 'Candidatos usam identidades já cadastradas no canal da conta. Telefones sem identidade não são incluídos.',
          ),
          const Text(
            'Consentimento e bloqueios são recalculados a cada consulta. Seleção não autoriza envio.',
          ),
          Text(
            telegramAtual
                ? 'Conta/bot, inscrição, consentimento, bloqueios e revogações são avaliados novamente antes da preparação.'
                : 'Não avaliados: frequência, conexão, capacidades do provedor e existência do canal.',
          ),
          if (c.ocupado) const LinearProgressIndicator(),
          if (c.mensagem != null) Text(c.mensagem!),
          FilledButton.tonal(
            key: const Key('selecionar-todos-elegiveis'),
            onPressed: c.ocupado || !atualizada ? null : selecionarElegiveis,
            child: const Text('Todos os contatos elegiveis'),
          ),
          if (!atualizada)
            const Text('Consulta desatualizada. Atualize a revisão.'),
          if (((dados['referencias'] as Map?)?['problemas'] as List? ?? [])
              .isNotEmpty)
            Text(
              'Referências precisam de revisão: ${(dados['referencias'] as Map)['problemas']}',
            ),
          SwitchListTile(
            title: const Text('Somente identidades selecionadas'),
            value: selecionadas,
            onChanged: c.ocupado
                ? null
                : (v) {
                    setState(() {
                      selecionadas = v;
                      pagina = 1;
                    });
                    carregar();
                  },
          ),
          Text('${dados['total']} identidades · Página $pagina'),
          if (telegramAtual)
            const Text('Selecione um ou mais destinatários Telegram.'),
          for (final row in dados['itens'] as List) candidato(row as Map),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              TextButton(
                onPressed: c.ocupado || pagina == 1
                    ? null
                    : () {
                        setState(() => pagina--);
                        carregar();
                      },
                child: const Text('Anterior'),
              ),
              TextButton(
                onPressed: c.ocupado || pagina * 20 >= (dados['total'] as int)
                    ? null
                    : () {
                        setState(() => pagina++);
                        carregar();
                      },
                child: const Text('Próxima'),
              ),
            ],
          ),
          const Divider(),
          const Text(
            'Prévia textual transitória com nome fictício. O conteúdo não fica congelado.',
          ),
          Form(
            key: form,
            child: TextFormField(
              controller: nome,
              enabled: !c.ocupado,
              decoration: const InputDecoration(
                labelText: 'Nome fictício para prévia',
              ),
              validator: (v) => validarLimiteTemplate(v, 120),
              onChanged: (_) => setState(() => previa = null),
            ),
          ),
          OutlinedButton(
            onPressed: c.ocupado || !atualizada ? null : gerarPrevia,
            child: const Text('Consultar prévia'),
          ),
          if (previa != null) Text(previa!),
          FilledButton(
            key: const Key('continuar-resumo-final'),
            onPressed: c.ocupado || !atualizada
                ? null
                : () => Navigator.of(context).pushReplacement<void, void>(
                    MaterialPageRoute(
                      builder: (_) => PreparacaoCampanha(
                        autenticacao: widget.autenticacao,
                        id: widget.id,
                        fluxoSimplificado: true,
                      ),
                    ),
                  ),
            child: const Text('Continuar para o resumo final'),
          ),
        ],
      ),
    ),
  );
}
