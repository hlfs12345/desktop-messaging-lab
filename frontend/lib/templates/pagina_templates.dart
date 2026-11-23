import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'texto_unicode.dart';

import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_templates.dart';
import 'editor_template.dart';

class PaginaTemplates extends StatefulWidget {
  const PaginaTemplates({super.key, required this.autenticacao});
  final ControladorAutenticacao autenticacao;
  @override
  State<PaginaTemplates> createState() => _PaginaTemplatesState();
}

class _PaginaTemplatesState extends State<PaginaTemplates> {
  late final c = ControladorTemplates(widget.autenticacao);
  final busca = TextEditingController();
  final formBusca = GlobalKey<FormState>();
  String? categoria, plataforma;
  bool arquivados = false;
  int pagina = 1;
  Map<String, dynamic> dados = {'itens': [], 'total': 0};

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
    if (formBusca.currentState?.validate() == false) return;
    final result = await c.requisitar(
      'GET',
      '/templates',
      parametros: {
        'q': busca.text,
        'pagina': '$pagina',
        'limite': '20',
        'ativo': '${!arquivados}',
        'categoria': ?categoria,
        'plataforma': ?plataforma,
      },
    );
    if (mounted && result != null) setState(() => dados = result);
  }

  Future<void> criar() async {
    final result = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) =>
            EditorTemplate(controlador: c, modo: ModoEditorTemplate.criar),
      ),
    );
    if (mounted && result == true) await carregar();
  }

  Future<void> abrir(Map<String, dynamic> item) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) =>
            DetalheTemplate(controlador: c, id: item['id'] as String),
      ),
    );
    if (mounted) await carregar();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: const Text('Templates')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            Card.outlined(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Wrap(
                  spacing: 12,
                  runSpacing: 12,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    SizedBox(
                      width: 230,
                      child: Form(
                        key: formBusca,
                        child: TextFormField(
                          controller: busca,
                          maxLength: 120,
                          maxLengthEnforcement: MaxLengthEnforcement.none,
                          buildCounter: contadorTemplate(busca, 120),
                          validator: (v) => validarLimiteTemplate(v, 120),
                          decoration: const InputDecoration(
                            labelText: 'Buscar template',
                          ),
                          onFieldSubmitted: c.ocupado
                              ? null
                              : (_) {
                                  pagina = 1;
                                  carregar();
                                },
                        ),
                      ),
                    ),
                    SizedBox(
                      width: 240,
                      child: DropdownButtonFormField<String>(
                        isExpanded: true,
                        decoration: const InputDecoration(
                          labelText: 'Filtrar categoria',
                        ),
                        items: [
                          const DropdownMenuItem(
                            value: '',
                            child: Text('Todas as categorias'),
                          ),
                          ...categoriasTemplate.entries.map(
                            (e) => DropdownMenuItem(
                              value: e.key,
                              child: Text(e.value),
                            ),
                          ),
                        ],
                        onChanged: c.ocupado
                            ? null
                            : (v) => categoria = v == '' ? null : v,
                      ),
                    ),
                    SizedBox(
                      width: 190,
                      child: DropdownButtonFormField<String>(
                        isExpanded: true,
                        decoration: const InputDecoration(
                          labelText: 'Filtrar plataforma',
                        ),
                        items: [
                          const DropdownMenuItem(
                            value: '',
                            child: Text('Todas as plataformas'),
                          ),
                          ...plataformasTemplate.entries.map(
                            (e) => DropdownMenuItem(
                              value: e.key,
                              child: Text(e.value),
                            ),
                          ),
                        ],
                        onChanged: c.ocupado
                            ? null
                            : (v) => plataforma = v == '' ? null : v,
                      ),
                    ),
                    FilterChip(
                      label: const Text('Arquivados'),
                      selected: arquivados,
                      onSelected: c.ocupado
                          ? null
                          : (v) => setState(() => arquivados = v),
                    ),
                    OutlinedButton(
                      onPressed: c.ocupado
                          ? null
                          : () {
                              pagina = 1;
                              carregar();
                            },
                      child: const Text('Pesquisar'),
                    ),
                    FilledButton(
                      onPressed: c.ocupado ? null : criar,
                      child: const Text('Novo template'),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            if (c.ocupado) const LinearProgressIndicator(),
            if (c.mensagem != null) Text(c.mensagem!),
            Expanded(
              child: Card.outlined(
                child: ListView(
                  padding: const EdgeInsets.all(8),
                  children: [
                    if ((dados['itens'] as List).isEmpty)
                      const Padding(
                        padding: EdgeInsets.all(20),
                        child: Text('Nenhum template encontrado.'),
                      ),
                    for (final item in dados['itens'] as List)
                      ListTile(
                        leading: const Icon(Icons.article_outlined),
                        title: Text(item['nome'] as String),
                        subtitle: Text(
                          categoriasTemplate[item['categoria']] ?? '',
                        ),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: c.ocupado
                            ? null
                            : () =>
                                  abrir(Map<String, dynamic>.from(item as Map)),
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 12,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                TextButton(
                  onPressed: c.ocupado || pagina == 1
                      ? null
                      : () {
                          pagina--;
                          carregar();
                        },
                  child: const Text('Anterior'),
                ),
                Text('Página $pagina • ${dados['total']} templates'),
                TextButton(
                  onPressed: c.ocupado || pagina * 20 >= (dados['total'] as int)
                      ? null
                      : () {
                          pagina++;
                          carregar();
                        },
                  child: const Text('Próxima'),
                ),
              ],
            ),
          ],
        ),
      ),
    ),
  );
}

class DetalheTemplate extends StatefulWidget {
  const DetalheTemplate({
    super.key,
    required this.controlador,
    required this.id,
  });
  final ControladorTemplates controlador;
  final String id;
  @override
  State<DetalheTemplate> createState() => _DetalheTemplateState();
}

class _DetalheTemplateState extends State<DetalheTemplate> {
  ControladorTemplates get c => widget.controlador;
  Map<String, dynamic>? item;
  Map<String, dynamic> historico = {'itens': [], 'total': 0};
  int pagina = 1;
  String? plataforma;
  final arquivamento = OperacaoTemplate();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  Future<void> carregar() async {
    final result = await c.requisitar('GET', '/templates/${widget.id}');
    if (!mounted || result == null) return;
    setState(() => item = result);
    final versions = await c.requisitar(
      'GET',
      '/templates/${widget.id}/versoes',
      parametros: {
        'pagina': '$pagina',
        'limite': '10',
        'plataforma': ?plataforma,
      },
    );
    if (mounted && versions != null) setState(() => historico = versions);
  }

  Future<void> editar(ModoEditorTemplate modo) async {
    final result = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) =>
            EditorTemplate(controlador: c, modo: modo, template: item),
      ),
    );
    if (mounted && result == true) {
      pagina = 1;
      await carregar();
    }
  }

  Future<void> arquivar() async {
    final confirmed = await showDialog<bool>(
      context: context,
      useRootNavigator: false,
      builder: (ctx) => AlertDialog(
        title: const Text('Arquivar template?'),
        content: const Text(
          'As versões serão preservadas. Novas versões ficarão bloqueadas.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Confirmar arquivamento'),
          ),
        ],
      ),
    );
    if (!mounted || confirmed != true) return;
    final result = await c.requisitar(
      'PATCH',
      '/templates/${widget.id}',
      corpo: arquivamento.corpo({'versao': item!['versao'], 'ativo': false}),
    );
    if (mounted && result != null) await carregar();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: Text(item?['nome'] as String? ?? 'Template')),
      body: item == null
          ? Center(
              child: c.ocupado
                  ? const CircularProgressIndicator()
                  : Text(c.mensagem ?? 'Template indisponível.'),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Categoria fixa: ${categoriasTemplate[item!['categoria']]}',
                ),
                if (item!['descricao'] != null)
                  Text(item!['descricao'] as String),
                Text(item!['ativo'] == true ? 'Ativo' : 'Arquivado'),
                const Text(avisoTemplate),
                Wrap(
                  spacing: 12,
                  children: [
                    OutlinedButton(
                      onPressed: c.ocupado
                          ? null
                          : () => editar(ModoEditorTemplate.metadados),
                      child: const Text('Editar cadastro'),
                    ),
                    FilledButton(
                      onPressed: c.ocupado || item!['ativo'] != true
                          ? null
                          : () => editar(ModoEditorTemplate.versionar),
                      child: const Text('Nova versão'),
                    ),
                    OutlinedButton(
                      onPressed: c.ocupado || item!['ativo'] != true
                          ? null
                          : arquivar,
                      child: const Text('Arquivar'),
                    ),
                    TextButton(
                      onPressed: c.ocupado ? null : carregar,
                      child: const Text('Atualizar'),
                    ),
                  ],
                ),
                if (c.mensagem != null) Text(c.mensagem!),
                const SizedBox(height: 12),
                SizedBox(
                  width: 220,
                  child: DropdownButtonFormField<String>(
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Plataforma do histórico',
                    ),
                    items: [
                      const DropdownMenuItem(
                        value: '',
                        child: Text('Todas as plataformas'),
                      ),
                      ...plataformasTemplate.entries.map(
                        (e) => DropdownMenuItem(
                          value: e.key,
                          child: Text(e.value),
                        ),
                      ),
                    ],
                    onChanged: c.ocupado
                        ? null
                        : (v) {
                            plataforma = v == '' ? null : v;
                            pagina = 1;
                            carregar();
                          },
                  ),
                ),
                for (final version in historico['itens'] as List)
                  ExpansionTile(
                    key: ValueKey(version['id']),
                    title: Text(
                      '${plataformasTemplate[version['plataforma']]} • versão ${version['numero_versao']}',
                    ),
                    subtitle: Text(
                      'Salva em ${version['criado_em']} • somente leitura',
                    ),
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(12),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            SelectableText(version['conteudo'] as String),
                            PreviaTemplate(
                              controlador: c,
                              plataforma: version['plataforma'] as String,
                              conteudo: version['conteudo'] as String,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                Wrap(
                  spacing: 12,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    TextButton(
                      onPressed: c.ocupado || pagina == 1
                          ? null
                          : () {
                              pagina--;
                              carregar();
                            },
                      child: const Text('Anterior'),
                    ),
                    Text('Página $pagina • ${historico['total']} versões'),
                    TextButton(
                      onPressed:
                          c.ocupado ||
                              pagina * 10 >= (historico['total'] as int)
                          ? null
                          : () {
                              pagina++;
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
