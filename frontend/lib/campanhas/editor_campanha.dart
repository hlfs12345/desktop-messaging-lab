import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import '../templates/controlador_templates.dart' show categoriasTemplate;
import '../templates/texto_unicode.dart';
import 'controlador_campanhas.dart';
import 'revisao_campanha.dart';
import 'preparacao_campanha.dart';
import '../simulacao.dart';

String rotuloReferencia(Map<String, dynamic> item) {
  final partes = [
    item['nome'] ?? '',
    if (item['plataforma'] != null) item['plataforma'],
    if (item['provedor'] != null)
      item['provedor'] == 'mock'
          ? 'Conta de simulação — sem envio externo'
          : item['provedor'],
    if (item['numero_versao'] != null) 'versão ${item['numero_versao']}',
    if (item['categoria'] != null) categoriasTemplate[item['categoria']],
  ];
  return partes.join(' · ');
}

class EditorCampanha extends StatefulWidget {
  const EditorCampanha({super.key, required this.autenticacao, this.id});
  final ControladorAutenticacao autenticacao;
  final String? id;
  @override
  State<EditorCampanha> createState() => _EditorCampanhaState();
}

class _EditorCampanhaState extends State<EditorCampanha> {
  late final c = ControladorCampanhas(widget.autenticacao);
  final nome = TextEditingController();
  final form = GlobalKey<FormState>();
  final operacao = OperacaoCampanha();
  Map<String, dynamic>? conta, escopo, template;
  List<Map<String, dynamic>> listas = [];
  int versao = 1;
  bool carregado = false, alterado = false;
  String? avisoLocal;
  List<dynamic> problemas = [];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void dispose() {
    nome.dispose();
    c.dispose();
    super.dispose();
  }

  Future<void> carregar() async {
    if (widget.id == null) {
      await preencherOpcoesUnicas();
      if (mounted) setState(() => carregado = true);
      return;
    }
    final result = await c.requisitar('GET', '/campanhas/${widget.id}');
    if (!mounted || result == null) return;
    if (result['status'] == 'preparada') {
      await Navigator.of(context).pushReplacement<void, void>(
        MaterialPageRoute(
          builder: (_) => PreparacaoCampanha(
            autenticacao: widget.autenticacao,
            id: widget.id!,
          ),
        ),
      );
      return;
    }
    final refs = result['referencias'] as Map;
    setState(() {
      nome.text = result['nome'] as String;
      conta = Map<String, dynamic>.from(refs['conta'] as Map);
      escopo = Map<String, dynamic>.from(refs['escopo'] as Map);
      template = Map<String, dynamic>.from(refs['template'] as Map);
      listas = (refs['listas'] as List)
          .map((i) => Map<String, dynamic>.from(i as Map))
          .toList();
      problemas = refs['problemas'] as List;
      versao = result['versao'] as int;
      carregado = true;
      alterado = false;
      avisoLocal = null;
    });
  }

  Future<void> preencherOpcoesUnicas() async {
    for (final tipo in ['contas', 'escopos', 'versoes']) {
      final result = await c.requisitar(
        'GET',
        '/campanhas/opcoes',
        parametros: {'tipo': tipo, 'pagina': '1', 'limite': '2'},
      );
      if (!mounted || result == null) return;
      final itens = result['itens'] as List;
      if (result['total'] == 1 && itens.length == 1) {
        final item = Map<String, dynamic>.from(itens.single as Map);
        switch (tipo) {
          case 'contas':
            conta = item;
          case 'escopos':
            escopo = item;
          case 'versoes':
            template = item;
        }
      }
    }
  }

  Future<void> escolher(String tipo) async {
    final result = await Navigator.of(context).push<Map<String, dynamic>>(
      MaterialPageRoute(
        builder: (_) =>
            EscolhaReferencia(autenticacao: widget.autenticacao, tipo: tipo),
      ),
    );
    if (!mounted || result == null) return;
    setState(() {
      alterado = true;
      switch (tipo) {
        case 'contas':
          conta = result;
        case 'escopos':
          escopo = result;
        case 'versoes':
          template = result;
        case 'listas':
          if (!listas.any((i) => i['id'] == result['id'])) listas.add(result);
      }
    });
  }

  Future<void> salvar() async {
    if (!form.currentState!.validate()) return;
    if (escopo == null) {
      setState(
        () => avisoLocal = 'Configure o escopo em Configurações avançadas antes de criar campanhas.',
      );
      return;
    }
    if (conta == null || template == null) {
      setState(() => avisoLocal = 'Escolha conta e versão do template.');
      return;
    }
    final result = await c.requisitar(
      widget.id == null ? 'POST' : 'PATCH',
      widget.id == null ? '/campanhas' : '/campanhas/${widget.id}',
      corpo: operacao.corpo({
        'nome': nome.text.trim(),
        'conta_envio_id': conta!['id'],
        'escopo_id': escopo!['id'],
        'template_versao_id': template!['id'],
        'categoria': template!['categoria'],
        'listas': listas.map((i) => i['id']).toList(),
        if (widget.id != null) 'versao': versao,
      }),
    );
    if (mounted && result != null) {
      final id = result['id'] as String;
      await Navigator.of(context).pushReplacement<void, void>(
        MaterialPageRoute(
          builder: (_) =>
              RevisaoCampanha(autenticacao: widget.autenticacao, id: id),
        ),
      );
    }
  }

  Widget referencia(
    String titulo,
    Map<String, dynamic>? item,
    String tipo,
  ) => ListTile(
    title: Text(titulo),
    subtitle: Text(
      item == null
          ? 'Escolha explicitamente'
          : '${rotuloReferencia(item)}${item['provedor'] == 'mock' ? '\n$avisoSimulacao' : ''}',
    ),
    trailing: OutlinedButton(
      onPressed: c.ocupado ? null : () => escolher(tipo),
      child: const Text('Escolher'),
    ),
  );

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: Text(widget.id == null ? 'Novo rascunho' : 'Editar rascunho'),
      ),
      body: !carregado
          ? Center(
              child: c.ocupado
                  ? const CircularProgressIndicator()
                  : Text(c.mensagem ?? 'Rascunho indisponível.'),
            )
          : Form(
              key: form,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  const Text(avisoCampanha),
                  if (widget.id != null)
                    OutlinedButton(
                      onPressed: c.ocupado || alterado
                          ? null
                          : () async {
                              await Navigator.of(context).push<void>(
                                MaterialPageRoute(
                                  builder: (_) => PreparacaoCampanha(
                                    autenticacao: widget.autenticacao,
                                    id: widget.id!,
                                  ),
                                ),
                              );
                              if (context.mounted) {
                                Navigator.of(context).pop();
                              }
                            },
                      child: const Text('Preparacao e historico'),
                    ),
                  if (c.ocupado) const LinearProgressIndicator(),
                  if (c.mensagem != null) Text(c.mensagem!),
                  if (avisoLocal != null) Text(avisoLocal!),
                  if (problemas.isNotEmpty)
                    Text(
                      'Referências precisam de revisão: ${problemas.join(', ')}.',
                    ),
                  TextFormField(
                    controller: nome,
                    enabled: !c.ocupado,
                    decoration: const InputDecoration(
                      labelText: 'Nome da campanha',
                    ),
                    validator: (v) => v == null || v.trim().isEmpty
                        ? 'Informe o nome.'
                        : validarLimiteTemplate(v, 120),
                    onChanged: (_) => setState(() => alterado = true),
                  ),
                  referencia('Conta', conta, 'contas'),
                  if (escopo == null)
                    const Card(
                      child: ListTile(
                        leading: Icon(Icons.settings_outlined),
                        title: Text('Escopo de comunicação não configurado'),
                        subtitle: Text(
                          'Abra Configurações avançadas e defina o escopo local antes de criar campanhas.',
                        ),
                      ),
                    ),
                  referencia('Versão do template', template, 'versoes'),
                  const Text(
                    'A categoria acompanha a versão escolhida. Consentimentos continuam vinculados ao escopo local.',
                  ),
                  const SizedBox(height: 12),
                  const Text('Listas de candidatos'),
                  for (final lista in listas)
                    ListTile(
                      title: Text(lista['nome'] as String),
                      subtitle: lista['ativo'] == false
                          ? const Text('Lista arquivada')
                          : null,
                      trailing: IconButton(
                        tooltip: 'Remover lista ${lista['nome']}',
                        onPressed: c.ocupado
                            ? null
                            : () => setState(() {
                                listas.removeWhere(
                                  (i) => i['id'] == lista['id'],
                                );
                                alterado = true;
                              }),
                        icon: const Icon(Icons.close),
                      ),
                    ),
                  OutlinedButton(
                    onPressed: c.ocupado ? null : () => escolher('listas'),
                    child: const Text('Associar lista'),
                  ),
                  const Text(
                    'Nenhuma identidade é selecionada automaticamente. As seleções existentes são preservadas para revisão.',
                  ),
                  FilledButton(
                    onPressed: c.ocupado || escopo == null ? null : salvar,
                    child: const Text('Salvar rascunho'),
                  ),
                  if (widget.id != null) ...[
                    OutlinedButton(
                      onPressed: c.ocupado ? null : carregar,
                      child: const Text('Recarregar dados salvos'),
                    ),
                    OutlinedButton(
                      onPressed: c.ocupado || alterado
                          ? null
                          : () async {
                              await Navigator.of(context).push<void>(
                                MaterialPageRoute(
                                  builder: (_) => RevisaoCampanha(
                                    autenticacao: widget.autenticacao,
                                    id: widget.id!,
                                  ),
                                ),
                              );
                              if (mounted) await carregar();
                            },
                      child: const Text('Revisar destinatários'),
                    ),
                    if (alterado)
                      const Text(
                        'Salve as alterações antes de revisar destinatários.',
                      ),
                  ],
                ],
              ),
            ),
    ),
  );
}

/// Escolha paginada: não carrega todos os catálogos nem acessa credenciais.
class EscolhaReferencia extends StatefulWidget {
  const EscolhaReferencia({
    super.key,
    required this.autenticacao,
    required this.tipo,
  });
  final ControladorAutenticacao autenticacao;
  final String tipo;
  @override
  State<EscolhaReferencia> createState() => _EscolhaReferenciaState();
}

class _EscolhaReferenciaState extends State<EscolhaReferencia> {
  late final c = ControladorCampanhas(widget.autenticacao);
  final busca = TextEditingController();
  Map<String, dynamic> dados = {'itens': [], 'total': 0};
  int pagina = 1;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void dispose() {
    busca.dispose();
    c.dispose();
    super.dispose();
  }

  Future<void> carregar() async {
    if (validarLimiteTemplate(busca.text, 120) != null) {
      setState(() => c.mensagem = 'Use até 120 pontos de código Unicode.');
      return;
    }
    final result = await c.requisitar(
      'GET',
      '/campanhas/opcoes',
      parametros: {'tipo': widget.tipo, 'pagina': '$pagina', 'q': busca.text},
    );
    if (mounted && result != null) setState(() => dados = result);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: Text('Escolher ${widget.tipo}')),
      body: Column(
        children: [
          TextField(
            controller: busca,
            decoration: const InputDecoration(labelText: 'Buscar referência'),
            maxLength: 120,
            onSubmitted: (_) {
              setState(() => pagina = 1);
              carregar();
            },
          ),
          TextButton(
            onPressed: c.ocupado
                ? null
                : () {
                    setState(() => pagina = 1);
                    carregar();
                  },
            child: const Text('Buscar'),
          ),
          if (c.ocupado) const LinearProgressIndicator(),
          if (c.mensagem != null) Text(c.mensagem!),
          Expanded(
            child: ListView(
              children: [
                if ((dados['itens'] as List).isEmpty && !c.ocupado)
                  const ListTile(
                    title: Text('Nenhuma referência ativa disponível.'),
                  ),
                for (final row in dados['itens'] as List)
                  ListTile(
                    title: Text(
                      rotuloReferencia(Map<String, dynamic>.from(row as Map)),
                    ),
                    onTap: c.ocupado
                        ? null
                        : () =>
                              Navigator.of(context)
                                  .pop(Map<String, dynamic>.from(row)),
                  ),
              ],
            ),
          ),
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
              Text('Página $pagina'),
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
        ],
      ),
    ),
  );
}
