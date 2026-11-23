import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import 'arquivos_locais.dart';
import 'controlador_cadastro.dart';
import 'pagina_importacao.dart';
import 'pagina_consentimentos.dart';

Future<T?> dialogoComCampos<T>({
  required BuildContext context,
  required List<TextEditingController> campos,
  required WidgetBuilder builder,
}) async {
  final route = DialogRoute<T>(context: context, builder: builder);
  final result = await Navigator.of(context).push(route);
  route.completed.then((_) {
    for (final campo in campos) {
      campo.dispose();
    }
  }).ignore();
  return result;
}

class PaginaCadastro extends StatefulWidget {
  const PaginaCadastro({super.key, required this.autenticacao, this.arquivos});
  final ControladorAutenticacao autenticacao;
  final ArquivosLocais? arquivos;
  @override
  State<PaginaCadastro> createState() => _PaginaCadastroState();
}

class _PaginaCadastroState extends State<PaginaCadastro> {
  late final c = ControladorCadastro(widget.autenticacao);
  late final arquivos = widget.arquivos ?? ArquivosLocais();
  final busca = TextEditingController();
  int aba = 0;
  int pagina = 1;
  bool arquivados = false;
  String? listaId;
  Map<String, String> filtroConsentimento = {};
  Map<String, dynamic> dados = {'itens': [], 'total': 0};
  String get recurso => aba == 0
      ? 'contatos'
      : aba == 1
      ? 'listas'
      : 'importacoes';

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
    final result = await c.requisitar(
      'GET',
      '/$recurso',
      parametros: {
        'pagina': '$pagina',
        'limite': '50',
        if (aba != 2) 'q': busca.text,
        if (aba != 2) 'ativo': '${!arquivados}',
        if (aba == 0) ...filtroConsentimento,
        if (aba == 0 && listaId != null) 'lista_id': listaId!,
      },
    );
    if (mounted && result != null) setState(() => dados = result);
  }

  Future<void> filtrarConsentimento() async {
    final scopes = await c.requisitar('GET', '/contatos/escopos-comunicacao');
    if (!mounted || scopes == null) return;
    final itens = scopes['itens'] as List? ?? [];
    if (itens.isEmpty) {
      aviso(
        'Defina o escopo no detalhe de um contato antes de filtrar consentimentos.',
      );
      return;
    }
    String? categoria, estado;
    final aplicar = await showDialog<bool>(
      context: context,
      useRootNavigator: false,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, atualizar) => AlertDialog(
          title: const Text('Filtrar consentimento'),
          content: SizedBox(
            width: 420,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text("Escopo: ${itens.first['nome']}"),
                DropdownButtonFormField<String>(
                  decoration: const InputDecoration(labelText: 'Categoria'),
                  items: categoriasConsentimento.entries
                      .map(
                        (e) => DropdownMenuItem(
                          value: e.key,
                          child: Text(e.value),
                        ),
                      )
                      .toList(),
                  onChanged: (value) => atualizar(() => categoria = value),
                ),
                DropdownButtonFormField<String>(
                  decoration: const InputDecoration(
                    labelText: 'Estado efetivo',
                  ),
                  items: ['desconhecido', 'autorizado', 'bloqueado']
                      .map((e) => DropdownMenuItem(value: e, child: Text(e)))
                      .toList(),
                  onChanged: (value) => atualizar(() => estado = value),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancelar'),
            ),
            FilledButton(
              onPressed: categoria == null || estado == null
                  ? null
                  : () => Navigator.pop(ctx, true),
              child: const Text('Aplicar filtro'),
            ),
          ],
        ),
      ),
    );
    if (aplicar == true && mounted) {
      setState(() {
        filtroConsentimento = {
          'escopo_id': itens.first['id'] as String,
          'categoria': categoria!,
          'consentimento': estado!,
        };
        pagina = 1;
      });
      await carregar();
    }
  }

  void aviso(String texto) {
    if (mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(texto)));
    }
  }

  Future<void> formulario({
    Map<String, dynamic>? item,
    bool lista = false,
  }) async {
    final nome = TextEditingController(text: item?['nome'] as String? ?? '');
    final notas = TextEditingController(
      text: item?[lista ? 'descricao' : 'observacoes'] as String? ?? '',
    );
    final telefone = TextEditingController();
    final pais = TextEditingController(text: 'BR');
    final form = GlobalKey<FormState>();
    final result = await dialogoComCampos<Map<String, dynamic>>(
      context: context,
      campos: [nome, notas, telefone, pais],
      builder: (ctx) => AlertDialog(
        title: Text(
          item == null ? (lista ? 'Nova lista' : 'Novo contato') : 'Editar',
        ),
        content: SizedBox(
          width: 480,
          child: SingleChildScrollView(
            child: Form(
              key: form,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextFormField(
                    controller: nome,
                    autofocus: true,
                    maxLength: 120,
                    decoration: InputDecoration(
                      labelText: lista ? 'Nome da lista' : 'Nome (opcional)',
                    ),
                    validator: (v) => lista && (v ?? '').trim().isEmpty
                        ? 'Informe o nome.'
                        : null,
                  ),
                  if (!lista && item == null) ...[
                    TextFormField(
                      controller: telefone,
                      maxLength: 80,
                      decoration: const InputDecoration(
                        labelText: 'Telefone',
                        hintText: '+55 11 91234-5678',
                      ),
                      validator: (v) => (v ?? '').trim().isEmpty
                          ? 'Informe o telefone.'
                          : null,
                    ),
                    TextFormField(
                      controller: pais,
                      maxLength: 2,
                      textCapitalization: TextCapitalization.characters,
                      decoration: const InputDecoration(
                        labelText:
                            'País para número nacional (ex.: BR, GB, US)',
                      ),
                    ),
                    const Text(
                      'Confira o país sugerido. Um telefone não comprova conta em mensageiro ou autorização para mensagens.',
                    ),
                  ],
                  TextFormField(
                    controller: notas,
                    maxLength: 2000,
                    maxLines: 3,
                    decoration: InputDecoration(
                      labelText: lista ? 'Descrição' : 'Observações',
                    ),
                    onFieldSubmitted: (_) {
                      if (form.currentState!.validate()) {
                        Navigator.pop(ctx, <String, dynamic>{});
                      }
                    },
                  ),
                ],
              ),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () {
              if (form.currentState!.validate()) {
                Navigator.pop(ctx, <String, dynamic>{});
              }
            },
            child: const Text('Salvar'),
          ),
        ],
      ),
    );
    if (result != null && mounted) {
      final body = <String, dynamic>{
        'nome': nome.text.trim().isEmpty ? null : nome.text,
        lista ? 'descricao' : 'observacoes': notas.text.trim().isEmpty
            ? null
            : notas.text,
        if (item != null) 'versao': item['versao'],
        if (!lista && item == null) 'telefone': telefone.text,
        if (!lista && item == null)
          'pais': pais.text.trim().isEmpty ? null : pais.text.toUpperCase(),
      };
      final saved = await c.requisitar(
        item == null ? 'POST' : 'PATCH',
        '/${lista ? 'listas' : 'contatos'}${item == null ? '' : '/${item['id']}'}',
        corpo: body,
      );
      if (saved != null) await carregar();
    }
  }

  Future<void> arquivar(Map<String, dynamic> item, {bool lista = false}) async {
    final ok = await showDialog<bool>(
      context: context,
      useRootNavigator: false,
      builder: (ctx) => AlertDialog(
        title: const Text('Arquivar registro?'),
        content: const Text(
          'O registro e seu histórico serão preservados. A importação não o reativará.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Arquivar'),
          ),
        ],
      ),
    );
    if (ok == true) {
      final result = await c.requisitar(
        'PATCH',
        '/${lista ? 'listas' : 'contatos'}/${item['id']}',
        corpo: {'versao': item['versao'], 'ativo': false},
      );
      if (result != null) await carregar();
    }
  }

  Future<void> detalhe(String id) async {
    final item = await c.requisitar('GET', '/contatos/$id');
    if (!mounted || item == null) return;
    final action = await showDialog<String>(
      context: context,
      useRootNavigator: false,
      builder: (ctx) => AlertDialog(
        title: Text(item['nome'] as String? ?? 'Contato sem nome'),
        content: SizedBox(
          width: 520,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(item['observacoes'] as String? ?? 'Sem observações'),
                const SizedBox(height: 12),
                for (final raw in item['telefones'] as List)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(raw['telefone_e164'] as String),
                    subtitle: Text(
                      raw['ativo'] == true ? 'Ativo' : 'Arquivado',
                    ),
                    trailing: raw['ativo'] == true
                        ? IconButton(
                            tooltip: 'Arquivar telefone',
                            icon: const Icon(Icons.archive_outlined),
                            onPressed: () => Navigator.pop(
                              ctx,
                              'telefone:${raw['id']}:${raw['versao']}',
                            ),
                          )
                        : null,
                  ),
                for (final destino
                    in (item['destinos_telegram'] as List? ?? const []))
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Telegram'),
                    subtitle: Text(
                      'Destino $destino · identidade recebida pelo bot',
                    ),
                  ),
                Text(
                  '${(item['listas'] as List).length} lista(s) associada(s)',
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Fechar'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, 'listas'),
            child: const Text('Organizar listas'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, 'consentimentos'),
            child: const Text('Consentimento'),
          ),
          if (item['ativo'] == true)
            TextButton(
              onPressed: () => Navigator.pop(ctx, 'adicionar'),
              child: const Text('Adicionar telefone'),
            ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, 'editar'),
            child: const Text('Editar'),
          ),
        ],
      ),
    );
    if (!mounted) return;
    if (action == 'consentimentos') {
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => PaginaConsentimentos(controlador: c, contato: item),
        ),
      );
      if (mounted) await carregar();
    }
    if (action == 'editar') await formulario(item: item);
    if (action == 'adicionar') await adicionarTelefone(id);
    if (action == 'listas') await organizar(item);
    if (!mounted) return;
    if (action?.startsWith('telefone:') == true) {
      final parts = action!.split(':');
      final ok = await showDialog<bool>(
        context: context,
        useRootNavigator: false,
        builder: (ctx) => AlertDialog(
          title: const Text('Arquivar telefone?'),
          content: const Text('O número continuará reservado a este contato.'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancelar'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Arquivar'),
            ),
          ],
        ),
      );
      if (ok == true) {
        final result = await c.requisitar(
          'PATCH',
          '/contatos/$id/telefones/${parts[1]}',
          corpo: {'versao': int.parse(parts[2]), 'ativo': false},
        );
        if (result != null) await carregar();
      }
    }
  }

  Future<void> adicionarTelefone(String id) async {
    final phone = TextEditingController();
    final pais = TextEditingController(text: 'BR');
    final ok = await dialogoComCampos<bool>(
      context: context,
      campos: [phone, pais],
      builder: (ctx) => AlertDialog(
        title: const Text('Adicionar telefone'),
        content: SizedBox(
          width: 420,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: phone,
                autofocus: true,
                maxLength: 80,
                decoration: const InputDecoration(labelText: 'Telefone'),
              ),
              TextField(
                controller: pais,
                maxLength: 2,
                decoration: const InputDecoration(
                  labelText: 'País de referência',
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Adicionar'),
          ),
        ],
      ),
    );
    if (ok == true) {
      final result = await c.requisitar(
        'POST',
        '/contatos/$id/telefones',
        corpo: {
          'telefone': phone.text,
          'pais': pais.text.isEmpty ? null : pais.text.toUpperCase(),
        },
      );
      if (result != null) await carregar();
    }
  }

  Future<void> organizar(Map<String, dynamic> item) async {
    int page = 1;
    while (mounted) {
      final groups = await c.requisitar(
        'GET',
        '/listas',
        parametros: {'pagina': '$page', 'limite': '50'},
      );
      if (!mounted || groups == null) return;
      final selected = Set<String>.from(item['listas'] as List);
      final changes = <String, bool>{};
      final result = await showDialog<String>(
        context: context,
        useRootNavigator: false,
        builder: (ctx) => StatefulBuilder(
          builder: (ctx, set) => AlertDialog(
            title: const Text('Listas do contato'),
            content: SizedBox(
              width: 460,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    for (final raw in groups['itens'] as List)
                      CheckboxListTile(
                        title: Text(raw['nome'] as String),
                        value:
                            changes[raw['id']] ?? selected.contains(raw['id']),
                        onChanged: (value) =>
                            set(() => changes[raw['id'] as String] = value!),
                      ),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('Cancelar'),
              ),
              if (page > 1)
                TextButton(
                  onPressed: () => Navigator.pop(ctx, 'anterior'),
                  child: const Text('Anterior'),
                ),
              if (page * 50 < (groups['total'] as int))
                TextButton(
                  onPressed: () => Navigator.pop(ctx, 'proxima'),
                  child: const Text('Próxima'),
                ),
              FilledButton(
                onPressed: () => Navigator.pop(ctx, 'salvar'),
                child: const Text('Salvar esta página'),
              ),
            ],
          ),
        ),
      );
      if (result == 'salvar') {
        for (final entry in changes.entries) {
          final result = await c.requisitar(
            entry.value ? 'PUT' : 'DELETE',
            '/listas/${entry.key}/contatos/${item['id']}',
          );
          if (result == null) return;
        }
        await carregar();
        return;
      }
      if (result == 'anterior') {
        page--;
      } else if (result == 'proxima') {
        page++;
      } else {
        return;
      }
    }
  }

  Future<void> autorizarLista(Map<String, dynamic> lista) async {
    final scopes = await c.requisitar('GET', '/contatos/escopos-comunicacao');
    if (!mounted || scopes == null) return;
    final itens = scopes['itens'] as List? ?? const [];
    if (itens.isEmpty) {
      setState(() => c.mensagem = 'Defina o escopo de comunicação nas configurações.');
      return;
    }
    final metodo = TextEditingController(text: 'Autorização recebida fora do Messaging Lab');
    final evidencia = TextEditingController();
    final data = TextEditingController(text: DateTime.now().toUtc().toIso8601String());
    var categoria = 'promocoes';
    final ok = await dialogoComCampos<bool>(
      context: context,
      campos: [metodo, evidencia, data],
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, set) => AlertDialog(
          title: Text('Registrar autorização para ${lista['nome']}'),
          content: SizedBox(
            width: 520,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DropdownButtonFormField<String>(
                  initialValue: categoria,
                  decoration: const InputDecoration(
                    labelText: 'Tipo de comunicação',
                  ),
                  items: categoriasConsentimento.entries
                      .map(
                        (e) => DropdownMenuItem(
                          value: e.key,
                          child: Text(e.value),
                        ),
                      )
                      .toList(),
                  onChanged: (value) => set(() => categoria = value!),
                ),
                TextField(
                  controller: metodo,
                  maxLength: 120,
                  onChanged: (_) => set(() {}),
                  decoration: const InputDecoration(
                    labelText: 'Como a autorização foi recebida',
                  ),
                ),
                TextField(
                  controller: evidencia,
                  maxLength: 500,
                  onChanged: (_) => set(() {}),
                  decoration: const InputDecoration(
                    labelText: 'Referência da autorização',
                  ),
                ),
                TextField(
                  controller: data,
                  decoration: const InputDecoration(
                    labelText: 'Data e hora com fuso',
                  ),
                ),
                const Text('Contatos bloqueados continuarão bloqueados.'),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancelar'),
            ),
            FilledButton(
              onPressed: metodo.text.trim().isEmpty || evidencia.text.trim().isEmpty
                  ? null
                  : () => Navigator.pop(ctx, true),
              child: const Text('Registrar'),
            ),
          ],
        ),
      ),
    );
    if (ok != true || !mounted) return;
    final result = await c.requisitar(
      'POST',
      '/contatos/listas/${lista['id']}/autorizacoes-whatsapp',
      corpo: {
        'operacao_id': operacaoConsentimento(),
        'escopo_id': itens.single['id'],
        'categoria': categoria,
        'ocorrido_em': data.text.trim(),
        'metodo_captura': metodo.text.trim(),
        'evidencia_referencia': evidencia.text.trim(),
      },
    );
    if (!mounted || result == null) return;
    setState(() {
      c.mensagem = '${result['autorizados']} autorizada(s); '
          '${result['ja_autorizados']} já autorizada(s); '
          '${result['bloqueados']} bloqueada(s).';
    });
  }

  Future<void> importar([String? id]) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) =>
            PaginaImportacao(controlador: c, arquivos: arquivos, id: id),
      ),
    );
    await carregar();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: const Text('Contatos e listas')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          children: [
            Wrap(
              spacing: 12,
              children: [
                for (final (i, label) in [
                  'Contatos',
                  'Listas',
                  'Importações',
                ].indexed)
                  ChoiceChip(
                    label: Text(label),
                    selected: aba == i,
                    onSelected: c.ocupado
                        ? null
                        : (_) {
                            setState(() {
                              aba = i;
                              pagina = 1;
                              listaId = null;
                              busca.clear();
                            });
                            carregar();
                          },
                  ),
              ],
            ),
            const SizedBox(height: 12),
            Card.outlined(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Wrap(
                  spacing: 12,
                  runSpacing: 12,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    if (aba != 2)
                      SizedBox(
                        width: 300,
                        child: TextField(
                          controller: busca,
                          maxLength: 120,
                          decoration: InputDecoration(
                            labelText: aba == 0
                                ? 'Buscar nome ou telefone'
                                : 'Buscar lista',
                            counterText: '',
                          ),
                          onSubmitted: (_) {
                            pagina = 1;
                            carregar();
                          },
                        ),
                      ),
                    if (aba != 2)
                      FilterChip(
                        label: const Text('Arquivados'),
                        selected: arquivados,
                        onSelected: c.ocupado
                            ? null
                            : (v) {
                                setState(() {
                                  arquivados = v;
                                  pagina = 1;
                                });
                                carregar();
                              },
                      ),
                    if (aba == 0)
                      OutlinedButton(
                        onPressed: c.ocupado ? null : filtrarConsentimento,
                        child: const Text('Filtrar consentimento'),
                      ),
                    if (aba == 0 && filtroConsentimento.isNotEmpty)
                      InputChip(
                        label: Text(
                          "Consentimento: ${filtroConsentimento['consentimento']}",
                        ),
                        onDeleted: c.ocupado
                            ? null
                            : () {
                                setState(() {
                                  filtroConsentimento = {};
                                  pagina = 1;
                                });
                                carregar();
                              },
                      ),
                    if (listaId != null)
                      InputChip(
                        label: const Text('Filtrado por lista'),
                        onDeleted: () {
                          setState(() => listaId = null);
                          carregar();
                        },
                      ),
                    OutlinedButton(
                      onPressed: c.ocupado
                          ? null
                          : () {
                              pagina = 1;
                              carregar();
                            },
                      child: const Text('Buscar / atualizar'),
                    ),
                    if (aba != 2)
                      FilledButton(
                        onPressed: c.ocupado
                            ? null
                            : () => formulario(lista: aba == 1),
                        child: Text(aba == 0 ? 'Novo contato' : 'Nova lista'),
                      ),
                    FilledButton.tonal(
                      onPressed: c.ocupado ? null : () => importar(),
                      child: const Text('Importar arquivo'),
                    ),
                  ],
                ),
              ),
            ),
            if (c.ocupado)
              const LinearProgressIndicator(
                semanticsLabel: 'Consultando dados',
              ),
            if (c.mensagem != null)
              Padding(
                padding: const EdgeInsets.all(12),
                child: Semantics(liveRegion: true, child: Text(c.mensagem!)),
              ),
            const SizedBox(height: 12),
            Expanded(
              child: Card.outlined(
                clipBehavior: Clip.antiAlias,
                child: (dados['itens'] as List).isEmpty
                    ? const Center(child: Text('Nenhum registro encontrado.'))
                    : ListView.builder(
                        padding: const EdgeInsets.all(8),
                        itemCount: (dados['itens'] as List).length,
                        itemBuilder: (context, index) {
                          final item = Map<String, dynamic>.from(
                            (dados['itens'] as List)[index] as Map,
                          );
                          return ListTile(
                            title: Text(
                              (item[aba == 2 ? 'nome_arquivo' : 'nome']
                                      as String?) ??
                                  'Contato sem nome',
                            ),
                            subtitle: Text(
                              aba == 0
                                  ? [
                                      ...(item['telefones'] as List).map(
                                        (p) =>
                                            '${p['telefone_e164']}${p['ativo'] == true ? '' : ' (arquivado)'}',
                                      ),
                                      ...(item['destinos_telegram'] as List? ??
                                              const [])
                                          .map((d) => 'Telegram $d'),
                                    ].join(' · ')
                                  : aba == 1
                                  ? (item['descricao'] as String? ?? '')
                                  : '${item['status']} · ${item['criado_em']}',
                            ),
                            onTap: c.ocupado
                                ? null
                                : () {
                                    if (aba == 0) {
                                      detalhe(item['id'] as String);
                                    } else if (aba == 1) {
                                      setState(() {
                                        listaId = item['id'] as String;
                                        aba = 0;
                                        pagina = 1;
                                        busca.clear();
                                        arquivados = false;
                                      });
                                      carregar();
                                    } else {
                                      importar(item['id'] as String);
                                    }
                                  },
                            trailing: aba == 2
                                ? null
                                : Wrap(
                                    children: [
                                      if (aba == 1)
                                        IconButton(
                                          tooltip: 'Registrar autorizações WhatsApp',
                                          onPressed: c.ocupado
                                              ? null
                                              : () => autorizarLista(item),
                                          icon: const Icon(Icons.verified_user_outlined),
                                        ),
                                      if (aba == 1)
                                        IconButton(
                                          tooltip: 'Editar lista',
                                          onPressed: c.ocupado
                                              ? null
                                              : () => formulario(
                                                  item: item,
                                                  lista: true,
                                                ),
                                          icon: const Icon(Icons.edit_outlined),
                                        ),
                                      if (item['ativo'] == true)
                                        IconButton(
                                          tooltip: 'Arquivar',
                                          onPressed: c.ocupado
                                              ? null
                                              : () => arquivar(
                                                  item,
                                                  lista: aba == 1,
                                                ),
                                          icon: const Icon(
                                            Icons.archive_outlined,
                                          ),
                                        ),
                                    ],
                                  ),
                          );
                        },
                      ),
              ),
            ),
            Wrap(
              spacing: 16,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text('${dados['total']} registro(s) — página $pagina'),
                TextButton(
                  onPressed: c.ocupado || pagina <= 1
                      ? null
                      : () {
                          pagina--;
                          carregar();
                        },
                  child: const Text('Anterior'),
                ),
                TextButton(
                  onPressed: c.ocupado || pagina * 50 >= (dados['total'] as int)
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
