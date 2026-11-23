import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import 'arquivos_locais.dart';
import 'controlador_cadastro.dart';

class PaginaImportacao extends StatefulWidget {
  const PaginaImportacao({
    super.key,
    required this.controlador,
    required this.arquivos,
    this.id,
  });
  final ControladorCadastro controlador;
  final ArquivosLocais arquivos;
  final String? id;
  @override
  State<PaginaImportacao> createState() => _PaginaImportacaoState();
}

class _PaginaImportacaoState extends State<PaginaImportacao> {
  ControladorCadastro get c => widget.controlador;
  String? id;
  Map<String, dynamic>? estado;
  List<dynamic> abas = [];
  String? aba;
  List<dynamic> amostra = [];
  List<dynamic> resultados = [];
  List<dynamic> listas = [];
  final escolhidas = <String>{};
  int paginaListas = 1;
  int totalListas = 0;
  int pagina = 1;
  int totalLinhas = 0;
  String filtro = '';
  final cabecalho = TextEditingController(text: '1');
  final pais = TextEditingController(text: 'BR');
  bool temCabecalho = true;
  String codificacao = 'utf-8-sig';
  String delimitador = ';';
  String politica = 'preservar';
  final campos = <String>{};
  int? colunaNome = 0;
  int colunaTelefone = 1;
  int? colunaNotas;
  int? colunaPais;
  Timer? timer;
  bool dialogo = false;
  String? aviso;
  bool configuracaoRestaurada = false;

  String assinatura(Map<String, dynamic> value) {
    final copy = Map<String, dynamic>.from(value);
    for (final key in ['listas', 'campos_atualizar']) {
      copy[key] = List<String>.from(copy[key] as List? ?? [])..sort();
    }
    final keys = copy.keys.toList()..sort();
    return jsonEncode({for (final key in keys) key: copy[key]});
  }

  bool get previaAtual =>
      estado?['configuracao'] is Map &&
      assinatura(config()) ==
          assinatura(Map<String, dynamic>.from(estado!['configuracao'] as Map));

  void configuracaoAlterada() {
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    id = widget.id;
    pais.addListener(configuracaoAlterada);
    cabecalho.addListener(configuracaoAlterada);
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (id != null) await consultar();
      await carregarListas();
    });
  }

  @override
  void dispose() {
    timer?.cancel();
    cabecalho.dispose();
    pais.dispose();
    super.dispose();
  }

  Map<String, dynamic> config() => {
    'aba': aba,
    'cabecalho': temCabecalho ? int.tryParse(cabecalho.text) : null,
    'codificacao': codificacao,
    'delimitador': delimitador,
    'coluna_nome': colunaNome,
    'coluna_telefone': colunaTelefone,
    'coluna_observacoes': colunaNotas,
    'coluna_pais': colunaPais,
    'pais': pais.text.trim().isEmpty ? null : pais.text.trim().toUpperCase(),
    'politica': politica,
    'campos_atualizar': campos.toList(),
    'listas': escolhidas.toList(),
  };

  Future<void> carregarListas() async {
    final result = await c.requisitar(
      'GET',
      '/listas',
      parametros: {'pagina': '$paginaListas', 'limite': '50'},
    );
    if (mounted && result != null) {
      setState(() {
        listas.addAll(result['itens'] as List);
        totalListas = result['total'] as int;
      });
    }
  }

  Future<void> criarListaDaImportacao() async {
    final nome = TextEditingController();
    final chave = GlobalKey<FormState>();
    final confirmado = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Nova lista para esta importacao'),
        content: Form(
          key: chave,
          child: TextFormField(
            controller: nome,
            autofocus: true,
            maxLength: 120,
            decoration: const InputDecoration(labelText: 'Nome da lista'),
            validator: (value) => value == null || value.trim().isEmpty
                ? 'Informe o nome da lista.'
                : null,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () {
              if (chave.currentState!.validate()) {
                Navigator.of(ctx).pop(true);
              }
            },
            child: const Text('Criar e selecionar'),
          ),
        ],
      ),
    );
    final valor = nome.text.trim();
    nome.dispose();
    if (!mounted || confirmado != true) return;
    final result = await c.requisitar(
      'POST',
      '/listas',
      corpo: {
        'nome': valor,
        'descricao': 'Contatos incluidos pela importacao.',
      },
    );
    if (!mounted || result == null) return;
    setState(() {
      listas.insert(0, result);
      totalListas++;
      escolhidas.add(result['id'] as String);
    });
  }

  Future<void> selecionar() async {
    setState(() {
      dialogo = true;
      aviso = null;
    });
    try {
      final file = await widget.arquivos.abrir();
      if (!mounted || file == null) return;
      final created = await c.requisitar(
        'POST',
        '/importacoes',
        corpo: {'nome_arquivo': file.nome},
      );
      if (!mounted || created == null) return;
      id = created['id'] as String;
      configuracaoRestaurada = false;
      final uploaded = await c.requisitar(
        'PUT',
        '/importacoes/$id/arquivo',
        bytes: file.bytes,
      );
      if (!mounted) return;
      if (uploaded != null) {
        abas = uploaded['abas'] as List;
        aba = abas.isEmpty ? null : abas.first as String;
      }
      await consultar();
    } catch (_) {
      if (mounted) {
        setState(
          () => aviso = 'Não foi possível ler o arquivo. Confira o formato e o limite de 10 MiB.',
        );
      }
    } finally {
      if (mounted) setState(() => dialogo = false);
    }
  }

  Future<void> consultar() async {
    timer?.cancel();
    if (id == null) return;
    final result = await c.requisitar('GET', '/importacoes/$id');
    if (!mounted || result == null) return;
    setState(() {
      estado = result;
      if (result['arquivo'] is Map && abas.isEmpty) {
        abas = (result['arquivo'] as Map)['abas'] as List;
        aba = abas.isEmpty ? null : abas.first as String;
      }
      if (!configuracaoRestaurada &&
          result['configuracao'] is Map &&
          (result['configuracao'] as Map).isNotEmpty) {
        final conf = result['configuracao'] as Map;
        aba = conf['aba'] as String?;
        temCabecalho = conf['cabecalho'] != null;
        cabecalho.text = '${conf['cabecalho'] ?? 1}';
        pais.text = conf['pais'] as String? ?? '';
        delimitador = conf['delimitador'] as String;
        codificacao = conf['codificacao'] as String;
        colunaNome = conf['coluna_nome'] as int?;
        colunaTelefone = conf['coluna_telefone'] as int;
        colunaNotas = conf['coluna_observacoes'] as int?;
        colunaPais = conf['coluna_pais'] as int?;
        politica = conf['politica'] as String;
        campos
          ..clear()
          ..addAll(List<String>.from(conf['campos_atualizar'] as List));
        escolhidas
          ..clear()
          ..addAll(List<String>.from(conf['listas'] as List));
        configuracaoRestaurada = true;
      }
    });
    if (result['status'] == 'validando') {
      timer = Timer(const Duration(milliseconds: 700), () {
        if (mounted) consultar();
      });
    } else if ({'pronta', 'confirmada'}.contains(result['status'])) {
      await carregarLinhas();
    }
  }

  Future<void> carregarLinhas() async {
    final result = await c.requisitar(
      'GET',
      '/importacoes/$id/linhas',
      parametros: {
        'pagina': '$pagina',
        'limite': '50',
        if (filtro.isNotEmpty) 'resultado': filtro,
      },
    );
    if (mounted && result != null) {
      setState(() {
        resultados = result['itens'] as List;
        totalLinhas = result['total'] as int;
      });
    }
  }

  Future<void> verAmostra() async {
    final result = await c.requisitar(
      'POST',
      '/importacoes/$id/amostra',
      corpo: config(),
    );
    if (mounted && result != null) {
      setState(() => amostra = result['itens'] as List);
    }
  }

  Future<void> validar() async {
    aviso = null;
    if (temCabecalho && (int.tryParse(cabecalho.text) ?? 0) < 1) {
      setState(() => aviso = 'Informe a linha de cabeçalho.');
      return;
    }
    final result = await c.requisitar(
      'POST',
      '/importacoes/$id/validacao',
      corpo: config(),
    );
    if (result != null) {
      pagina = 1;
      await consultar();
    }
  }

  Future<void> confirmar() async {
    if (!previaAtual) {
      setState(
        () => aviso = 'As opções mudaram. Valide o arquivo inteiro novamente.',
      );
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      useRootNavigator: false,
      builder: (ctx) => AlertDialog(
        title: const Text('Confirmar importação?'),
        content: const Text(
          'Somente linhas válidas serão gravadas. Linhas com erro ou conflito serão ignoradas. Campos vazios não apagam dados. Esta ação não concede autorização para mensagens.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Voltar à revisão'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Confirmar alterações'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    await c.requisitar('POST', '/importacoes/$id/confirmacao');
    final erro = c.mensagem;
    // Mesmo após timeout, consultar antes de oferecer qualquer nova gravação.
    if (mounted) await consultar();
    if (mounted && erro != null && estado?['status'] != 'confirmada') {
      setState(() => aviso = erro);
    }
  }

  Future<void> exportar() async {
    final bytes = await c.relatorio(id!);
    if (!mounted || bytes == null) return;
    setState(() => dialogo = true);
    try {
      final saved = await widget.arquivos.salvar(bytes);
      if (mounted) {
        setState(
          () => aviso = saved ? 'Relatório salvo.' : 'Exportação cancelada.',
        );
      }
    } catch (_) {
      if (mounted) {
        setState(() => aviso = 'Não foi possível salvar o relatório.');
      }
    } finally {
      if (mounted) setState(() => dialogo = false);
    }
  }

  Widget coluna(
    String label,
    int? value,
    void Function(int?) apply, {
    bool obrigatoria = false,
  }) {
    final maxCol = amostra.fold<int>(
      (value ?? 1) + 1,
      (v, row) => (row['celulas'] as List).length > v
          ? (row['celulas'] as List).length
          : v,
    );
    final first = amostra.isEmpty
        ? <dynamic>[]
        : amostra.first['celulas'] as List;
    return SizedBox(
      width: 260,
      child: DropdownButtonFormField<int>(
        initialValue: value ?? -1,
        key: ValueKey('$label:$value:$maxCol'),
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        items: [
          if (!obrigatoria)
            const DropdownMenuItem(value: -1, child: Text('Não importar')),
          for (var i = 0; i < maxCol; i++)
            DropdownMenuItem(
              value: i,
              child: Text(
                '${i + 1}${i < first.length ? ' — ${first[i]}' : ''}',
                overflow: TextOverflow.ellipsis,
              ),
            ),
        ],
        onChanged: c.ocupado
            ? null
            : (v) => setState(() => apply(v == -1 ? null : v)),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) {
      final status = estado?['status'] as String?;
      final editavel = {'carregada', 'pronta'}.contains(status);
      final busy = c.ocupado || dialogo;
      return Scaffold(
        appBar: AppBar(title: const Text('Importar contatos')),
        body: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1100),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '1. Selecionar arquivo',
                    style: TextStyle(fontSize: 22),
                  ),
                  const Text(
                    'Excel .xlsx ou CSV, até 10 MiB e 20.000 registros. O arquivo é validado inteiro antes de gravar. Telefones devem ser texto para preservar zeros e prefixos.',
                  ),
                  if (id == null)
                    FilledButton(
                      onPressed: busy ? null : selecionar,
                      child: const Text('Selecionar arquivo'),
                    ),
                  if (estado != null)
                    Text('${estado!['nome_arquivo']} — ${_status(status)}'),
                  if (busy || status == 'validando')
                    const LinearProgressIndicator(
                      semanticsLabel: 'Processando importação',
                    ),
                  if (c.mensagem != null)
                    Semantics(liveRegion: true, child: Text(c.mensagem!)),
                  if (aviso != null)
                    Semantics(liveRegion: true, child: Text(aviso!)),
                  if (editavel) ...[
                    const SizedBox(height: 24),
                    const Text(
                      '2. Configurar leitura e conferir amostra',
                      style: TextStyle(fontSize: 22),
                    ),
                    Wrap(
                      spacing: 16,
                      runSpacing: 16,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        if (abas.isNotEmpty)
                          SizedBox(
                            width: 260,
                            child: DropdownButtonFormField<String>(
                              isExpanded: true,
                              initialValue: aba,
                              decoration: const InputDecoration(
                                labelText: 'Aba da planilha',
                              ),
                              items: [
                                for (final a in abas)
                                  DropdownMenuItem(
                                    value: a as String,
                                    child: Text(a),
                                  ),
                              ],
                              onChanged: busy
                                  ? null
                                  : (v) => setState(() {
                                      aba = v;
                                      amostra = [];
                                    }),
                            ),
                          ),
                        if (abas.isEmpty)
                          SizedBox(
                            width: 220,
                            child: DropdownButtonFormField<String>(
                              isExpanded: true,
                              initialValue: delimitador,
                              decoration: const InputDecoration(
                                labelText: 'Separador CSV',
                              ),
                              items: const [
                                DropdownMenuItem(
                                  value: ';',
                                  child: Text('Ponto e vírgula'),
                                ),
                                DropdownMenuItem(
                                  value: ',',
                                  child: Text('Vírgula'),
                                ),
                                DropdownMenuItem(
                                  value: '\t',
                                  child: Text('Tabulação'),
                                ),
                              ],
                              onChanged: busy
                                  ? null
                                  : (v) => setState(() => delimitador = v!),
                            ),
                          ),
                        if (abas.isEmpty)
                          SizedBox(
                            width: 220,
                            child: DropdownButtonFormField<String>(
                              isExpanded: true,
                              initialValue: codificacao,
                              decoration: const InputDecoration(
                                labelText: 'Codificação',
                              ),
                              items: const [
                                DropdownMenuItem(
                                  value: 'utf-8-sig',
                                  child: Text('UTF-8'),
                                ),
                                DropdownMenuItem(
                                  value: 'cp1252',
                                  child: Text('Windows-1252'),
                                ),
                              ],
                              onChanged: busy
                                  ? null
                                  : (v) => setState(() => codificacao = v!),
                            ),
                          ),
                        FilterChip(
                          label: const Text('Arquivo tem cabeçalho'),
                          selected: temCabecalho,
                          onSelected: busy
                              ? null
                              : (v) => setState(() => temCabecalho = v),
                        ),
                        if (temCabecalho)
                          SizedBox(
                            width: 170,
                            child: TextField(
                              controller: cabecalho,
                              keyboardType: TextInputType.number,
                              decoration: const InputDecoration(
                                labelText: 'Linha do cabeçalho',
                              ),
                            ),
                          ),
                        OutlinedButton(
                          onPressed: busy ? null : verAmostra,
                          child: const Text('Ver primeiras linhas'),
                        ),
                      ],
                    ),
                    if (amostra.isNotEmpty)
                      SingleChildScrollView(
                        scrollDirection: Axis.horizontal,
                        child: DataTable(
                          columns: [
                            const DataColumn(label: Text('Linha')),
                            for (
                              var i = 0;
                              i <
                                  amostra.fold<int>(
                                    0,
                                    (v, row) =>
                                        (row['celulas'] as List).length > v
                                        ? (row['celulas'] as List).length
                                        : v,
                                  );
                              i++
                            )
                              DataColumn(label: Text('Coluna ${i + 1}')),
                          ],
                          rows: [
                            for (final row in amostra)
                              DataRow(
                                cells: [
                                  DataCell(Text('${row['linha']}')),
                                  for (
                                    var i = 0;
                                    i <
                                        amostra.fold<int>(
                                          0,
                                          (v, r) =>
                                              (r['celulas'] as List).length > v
                                              ? (r['celulas'] as List).length
                                              : v,
                                        );
                                    i++
                                  )
                                    DataCell(
                                      Text(
                                        i < (row['celulas'] as List).length
                                            ? '${row['celulas'][i]}'
                                            : '',
                                      ),
                                    ),
                                ],
                              ),
                          ],
                        ),
                      ),
                    const SizedBox(height: 24),
                    const Text(
                      '3. Mapear colunas e país',
                      style: TextStyle(fontSize: 22),
                    ),
                    const Text(
                      'As colunas são numeradas a partir de 1. Selecione o país para números nacionais; números com + usam o código internacional informado.',
                    ),
                    Wrap(
                      spacing: 16,
                      runSpacing: 16,
                      children: [
                        coluna(
                          'Telefone obrigatório',
                          colunaTelefone,
                          (v) => colunaTelefone = v!,
                          obrigatoria: true,
                        ),
                        coluna(
                          'Nome opcional',
                          colunaNome,
                          (v) => colunaNome = v,
                        ),
                        coluna(
                          'Observações',
                          colunaNotas,
                          (v) => colunaNotas = v,
                        ),
                        coluna(
                          'País por linha (opcional)',
                          colunaPais,
                          (v) => colunaPais = v,
                        ),
                        SizedBox(
                          width: 260,
                          child: TextField(
                            controller: pais,
                            maxLength: 2,
                            decoration: const InputDecoration(
                              labelText:
                                  'País de referência (confira BR sugerido)',
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 24),
                    const Text(
                      '4. Atualização e listas',
                      style: TextStyle(fontSize: 22),
                    ),
                    SizedBox(
                      width: 350,
                      child: DropdownButtonFormField<String>(
                        isExpanded: true,
                        initialValue: politica,
                        decoration: const InputDecoration(
                          labelText: 'Contatos existentes',
                        ),
                        items: const [
                          DropdownMenuItem(
                            value: 'preservar',
                            child: Text('Preservar dados existentes'),
                          ),
                          DropdownMenuItem(
                            value: 'preencher',
                            child: Text('Preencher campos vazios'),
                          ),
                          DropdownMenuItem(
                            value: 'substituir',
                            child: Text('Substituir campos selecionados'),
                          ),
                        ],
                        onChanged: busy
                            ? null
                            : (v) => setState(() => politica = v!),
                      ),
                    ),
                    if (politica != 'preservar')
                      Wrap(
                        children: [
                          for (final field in ['nome', 'observacoes'])
                            FilterChip(
                              label: Text(
                                field == 'nome'
                                    ? 'Atualizar nome'
                                    : 'Atualizar observações',
                              ),
                              selected: campos.contains(field),
                              onSelected: busy
                                  ? null
                                  : (v) => setState(() {
                                      if (v) {
                                        campos.add(field);
                                      } else {
                                        campos.remove(field);
                                      }
                                    }),
                            ),
                        ],
                      ),
                    const Text(
                      'Campos vazios nunca apagam valores. Listas anteriores e registros arquivados são preservados.',
                    ),
                    Wrap(
                      spacing: 8,
                      children: [
                        for (final raw in listas)
                          FilterChip(
                            label: Text(raw['nome'] as String),
                            selected: escolhidas.contains(raw['id']),
                            onSelected: busy
                                ? null
                                : (v) => setState(() {
                                    if (v && escolhidas.length < 20) {
                                      escolhidas.add(raw['id'] as String);
                                    } else {
                                      escolhidas.remove(raw['id']);
                                    }
                                  }),
                          ),
                      ],
                    ),
                    OutlinedButton.icon(
                      onPressed: busy ? null : criarListaDaImportacao,
                      icon: const Icon(Icons.playlist_add),
                      label: const Text(
                        'Criar lista com todos os contatos desta importacao',
                      ),
                    ),
                    if (listas.length < totalListas)
                      TextButton(
                        onPressed: busy
                            ? null
                            : () {
                                paginaListas++;
                                carregarListas();
                              },
                        child: const Text('Carregar mais listas'),
                      ),
                    FilledButton(
                      onPressed: busy ? null : validar,
                      child: const Text('Validar arquivo inteiro'),
                    ),
                  ],
                  if (estado?['contagens'] is Map &&
                      (estado!['contagens'] as Map).isNotEmpty) ...[
                    const SizedBox(height: 24),
                    const Text(
                      '5. Revisão e resultado',
                      style: TextStyle(fontSize: 22),
                    ),
                    Wrap(
                      spacing: 16,
                      runSpacing: 8,
                      children: [
                        for (final entry
                            in (estado!['contagens'] as Map).entries)
                          Text(
                            '${_rotulo(entry.key as String)}: ${entry.value}',
                          ),
                      ],
                    ),
                  ],
                  if ({'pronta', 'confirmada'}.contains(status)) ...[
                    if (status == 'pronta' && !previaAtual)
                      const Text(
                        'As opções mudaram. Valide o arquivo inteiro novamente antes de confirmar.',
                      ),
                    const Text(
                      'Erros e conflitos não serão gravados. Confira avisos de células numéricas e corrija o arquivo de origem quando necessário.',
                    ),
                    SizedBox(
                      width: 280,
                      child: DropdownButtonFormField<String>(
                        isExpanded: true,
                        initialValue: filtro,
                        decoration: const InputDecoration(
                          labelText: 'Filtrar linhas',
                        ),
                        items: [
                          for (final f in [
                            '',
                            'invalido',
                            'duplicado',
                            'ignorado',
                            'inserido',
                            'atualizado',
                            'sem_alteracao',
                          ])
                            DropdownMenuItem(
                              value: f,
                              child: Text(f.isEmpty ? 'Todas' : _rotulo(f)),
                            ),
                        ],
                        onChanged: busy
                            ? null
                            : (v) {
                                setState(() {
                                  filtro = v!;
                                  pagina = 1;
                                });
                                carregarLinhas();
                              },
                      ),
                    ),
                    for (final raw in resultados)
                      ListTile(
                        title: Text(
                          'Linha ${raw['numero_linha']}: ${_rotulo(raw['resultado'] as String)}',
                        ),
                        subtitle: Text(
                          '${raw['telefone_e164'] ?? ''} ${_motivo(raw['motivo'] as String)}',
                        ),
                      ),
                    Wrap(
                      spacing: 16,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Text('$totalLinhas linha(s) — página $pagina'),
                        TextButton(
                          onPressed: busy || pagina <= 1
                              ? null
                              : () {
                                  pagina--;
                                  carregarLinhas();
                                },
                          child: const Text('Anterior'),
                        ),
                        TextButton(
                          onPressed: busy || pagina * 50 >= totalLinhas
                              ? null
                              : () {
                                  pagina++;
                                  carregarLinhas();
                                },
                          child: const Text('Próxima'),
                        ),
                      ],
                    ),
                    Wrap(
                      spacing: 16,
                      children: [
                        if (status == 'pronta')
                          FilledButton(
                            onPressed: busy || !previaAtual ? null : confirmar,
                            child: const Text('Confirmar alterações'),
                          ),
                        OutlinedButton(
                          onPressed: busy ? null : exportar,
                          child: const Text('Salvar relatório CSV'),
                        ),
                      ],
                    ),
                  ],
                  if (id != null)
                    Wrap(
                      spacing: 16,
                      children: [
                        TextButton(
                          onPressed: busy ? null : consultar,
                          child: const Text('Consultar resultado'),
                        ),
                        if (!{
                          'confirmada',
                          'cancelada',
                          'falhou',
                          'interrompida',
                          'expirada',
                        }.contains(status))
                          TextButton(
                            onPressed: busy
                                ? null
                                : () async {
                                    await c.requisitar(
                                      'POST',
                                      '/importacoes/$id/cancelamento',
                                    );
                                    await consultar();
                                  },
                            child: const Text('Cancelar importação'),
                          ),
                      ],
                    ),
                  if ({
                    'falhou',
                    'cancelada',
                    'interrompida',
                    'expirada',
                  }.contains(status))
                    TextButton(
                      onPressed: busy
                          ? null
                          : () => setState(() {
                              id = null;
                              estado = null;
                              resultados = [];
                              amostra = [];
                              abas = [];
                            }),
                      child: const Text('Selecionar arquivo novamente'),
                    ),
                ],
              ),
            ),
          ),
        ),
      );
    },
  );

  String _status(String? s) => switch (s) {
    'criada' => 'aguardando arquivo',
    'carregada' => 'configure e valide o arquivo',
    'validando' => 'validando todas as linhas',
    'pronta' => 'prévia pronta, aguardando confirmação',
    'confirmada' => 'importação concluída',
    'interrompida' => 'interrompida; selecione e valide novamente',
    'expirada' => 'prévia expirada',
    'cancelada' => 'cancelada',
    'falhou' => 'arquivo recusado; confira formato e limites',
    _ => '',
  };
  String _rotulo(String s) => switch (s) {
    'inserido' => 'Novos',
    'atualizado' => 'Atualizados',
    'sem_alteracao' => 'Sem alteração',
    'duplicado' => 'Repetidos',
    'invalido' => 'Inválidos / conflitos',
    'ignorado' => 'Ignorados',
    'total_linhas' => 'Total de linhas',
    'associacoes_adicionadas' => 'Associações adicionadas',
    'erro' => 'Falha',
    _ => s,
  };
  String _motivo(String s) => switch (s) {
    'duplicidade_conflitante' || 'atualizacao_conflitante' =>
      'Dados conflitantes: corrija as linhas na origem.',
    'repetido_no_arquivo' => 'Telefone repetido no arquivo.',
    'registro_arquivado' => 'Contato ou telefone arquivado; preservado.',
    'celula_numerica_confira_zeros' =>
      'Célula numérica: confira zeros e prefixos na origem.',
    'formula_nao_permitida' => 'Fórmula não permitida. Use valor textual.',
    'linha_vazia' => 'Linha vazia.',
    '' => '',
    _ => 'Valor inválido: confira telefone, país e tipo da célula.',
  };
}
