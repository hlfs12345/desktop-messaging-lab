import 'dart:math';

import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_cadastro.dart';

String operacaoConsentimento() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}

const categoriasConsentimento = {
  'promocoes': 'Promoções',
  'atualizacoes_solicitadas': 'Atualizações solicitadas',
  'eventos_grupos': 'Eventos e grupos',
};
const tiposConsentimento = {
  'autorizou': 'Registrar autorização',
  'revogou': 'Revogar categoria',
  'bloqueio_canal': 'Bloquear identidade WhatsApp',
  'desbloqueio_canal': 'Desbloquear identidade WhatsApp',
  'bloqueio_global': 'Bloquear contato globalmente',
  'desbloqueio_global': 'Desbloquear contato globalmente',
};
const motivosConsentimento = {
  'bloqueio_global': 'Contato bloqueado globalmente',
  'bloqueio_canal': 'Identidade bloqueada no canal',
  'categoria_revogada': 'Categoria revogada',
  'sem_autorizacao': 'Sem autorização válida',
  'contato_arquivado': 'Contato arquivado',
  'telefone_arquivado': 'Telefone arquivado',
  'identidade_nao_cadastrada': 'Identidade não cadastrada',
};

class PaginaConsentimentos extends StatefulWidget {
  const PaginaConsentimentos({
    super.key,
    required this.controlador,
    required this.contato,
  });
  final ControladorCadastro controlador;
  final Map<String, dynamic> contato;
  @override
  State<PaginaConsentimentos> createState() => _PaginaConsentimentosState();
}

class PaginaConfiguracoesAvancadas extends StatefulWidget {
  const PaginaConfiguracoesAvancadas({super.key, required this.autenticacao});

  final ControladorAutenticacao autenticacao;

  @override
  State<PaginaConfiguracoesAvancadas> createState() =>
      _PaginaConfiguracoesAvancadasState();
}

class _PaginaConfiguracoesAvancadasState
    extends State<PaginaConfiguracoesAvancadas> {
  late final c = ControladorCadastro(widget.autenticacao);
  Map<String, dynamic>? escopo;
  bool carregado = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void dispose() {
    c.dispose();
    super.dispose();
  }

  Future<void> carregar() async {
    final result = await c.requisitar('GET', '/contatos/escopos-comunicacao');
    if (!mounted || result == null) return;
    final itens = result['itens'] as List? ?? [];
    setState(() {
      escopo = itens.isEmpty
          ? null
          : Map<String, dynamic>.from(itens.single as Map);
      carregado = true;
    });
  }

  Future<void> configurar() async {
    final result = await showDialog<bool>(
      context: context,
      useRootNavigator: false,
      builder: (_) => FormularioConsentimento(
        controlador: c,
        raiz: '',
        escopo: null,
        estado: null,
        telefones: const [],
        novoEscopo: true,
      ),
    );
    if (result == true && mounted) await carregar();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: const Text('Configurações avançadas')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Text(
            'Escopo de comunicação',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const Text(
            'Define a fronteira local dos consentimentos desta instalação.',
          ),
          if (c.ocupado) const LinearProgressIndicator(),
          if (c.mensagem != null) Text(c.mensagem!),
          if (carregado && escopo != null)
            ListTile(
              leading: const Icon(Icons.verified_outlined),
              title: Text(escopo!['nome'] as String),
              subtitle: const Text(
                'Escopo único. Edição, exclusão e novos escopos não estão disponíveis.',
              ),
            ),
          if (carregado && escopo == null) ...[
            const Text(
              'Defina o escopo inicial antes de criar campanhas ou registrar consentimentos.',
            ),
            Align(
              alignment: Alignment.centerLeft,
              child: FilledButton(
                onPressed: c.ocupado ? null : configurar,
                child: const Text('Definir escopo local de comunicação'),
              ),
            ),
          ],
        ],
      ),
    ),
  );
}

class _PaginaConsentimentosState extends State<PaginaConsentimentos> {
  ControladorCadastro get c => widget.controlador;
  String get raiz => '/contatos/${widget.contato['id']}';
  Map<String, dynamic>? escopo, estado;
  Map<String, dynamic> historico = {'itens': [], 'total': 0};
  int pagina = 1;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  Future<void> carregar() async {
    final scopes = await c.requisitar('GET', '/contatos/escopos-comunicacao');
    if (!mounted || scopes == null) return;
    final itens = scopes['itens'] as List? ?? [];
    if (itens.isEmpty) {
      setState(() {
        escopo = null;
        estado = null;
      });
      return;
    }
    final scope = Map<String, dynamic>.from(itens.first as Map);
    final result = await c.requisitar(
      'GET',
      '$raiz/consentimentos',
      parametros: {'escopo_id': scope['id'] as String},
    );
    if (!mounted || result == null) return;
    setState(() {
      escopo = scope;
      estado = result;
    });
    await carregarHistorico();
  }

  Future<void> carregarHistorico() async {
    if (escopo == null) return;
    final result = await c.requisitar(
      'GET',
      '$raiz/consentimentos/eventos',
      parametros: {
        'escopo_id': escopo!['id'] as String,
        'pagina': '$pagina',
        'limite': '10',
      },
    );
    if (mounted && result != null) setState(() => historico = result);
  }

  Future<void> formulario({
    bool novoEscopo = false,
    String? tipo,
    String? identidade,
    String? categoria,
  }) async {
    final result = await showDialog<bool>(
      context: context,
      useRootNavigator: false,
      builder: (_) => FormularioConsentimento(
        controlador: c,
        raiz: raiz,
        escopo: escopo,
        estado: estado,
        telefones: widget.contato['telefones'] as List,
        novoEscopo: novoEscopo,
        tipoInicial: tipo,
        identidadeInicial: identidade,
        categoriaInicial: categoria,
      ),
    );
    if (result == true && mounted) {
      pagina = 1;
      await carregar();
    }
  }

  Future<void> criarIdentidade(Map phone) async {
    final result = await c.requisitar(
      'POST',
      '$raiz/identidades',
      corpo: {
        'operacao_id': operacaoConsentimento(),
        'telefone_id': phone['id'],
        'canal': 'whatsapp',
        'versao': estado!['versao'],
      },
    );
    if (result != null && mounted) {
      await carregar();
      if (mounted) {
        await formulario(
          tipo: 'autorizou',
          identidade: result['id'] as String,
          categoria: 'promocoes',
        );
      }
    }
  }

  String telefone(Object? id) =>
      ((widget.contato['telefones'] as List).firstWhere(
            (p) => p['id'] == id,
            orElse: () => {'telefone_e164': 'Telefone arquivado'},
          )['telefone_e164'])
          as String;

  String alvoEvento(Map evento) {
    final id = evento['identidade_contato_id'];
    if (id == null) return 'Contato global — todos os canais';
    Map? identidade;
    for (final item in estado?['identidades'] as List? ?? const []) {
      if (item['id'] == id) {
        identidade = item as Map;
        break;
      }
    }
    if (identidade == null) return 'Identidade $id — dados indisponíveis';
    Map? phone;
    for (final item in widget.contato['telefones'] as List) {
      if (item['id'] == identidade['telefone_id']) {
        phone = item as Map;
        break;
      }
    }
    final canal = identidade['canal'] == 'whatsapp'
        ? 'WhatsApp'
        : identidade['canal'] as String? ?? 'Canal não identificado';
    if (phone == null) return '$canal — telefone indisponível (identidade $id)';
    final arquivado =
        phone['ativo'] == false || identidade['telefone_ativo'] == false;
    return '$canal — ${phone['telefone_e164']}${arquivado ? ' (arquivado)' : ''}';
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: const Text('Consentimento e comunicação')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Text(
            widget.contato['nome'] as String? ?? 'Contato sem nome',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const Text(
            'Esta avaliação considera somente consentimento e supressão. '
            'Não confirma existência do WhatsApp, capacidade de envio ou liberação de uma mensagem.',
          ),
          if (c.mensagem != null)
            Text(c.mensagem!, key: const Key('erro-consentimento')),
          if (c.ocupado) const LinearProgressIndicator(),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton(
              onPressed: c.ocupado ? null : carregar,
              child: const Text('Atualizar estado'),
            ),
          ),
          if (escopo == null)
            FilledButton(
              onPressed: c.ocupado ? null : () => formulario(novoEscopo: true),
              child: const Text('Definir escopo local de comunicação'),
            ),
          if (escopo != null && estado != null) ...[
            Text(
              'Situação das comunicações',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text('Escopo: ${escopo!['nome']}'),
            Text(
              estado!['bloqueio_global_em'] == null
                  ? 'Sem bloqueio global'
                  : 'Contato bloqueado globalmente',
            ),
            const Text(
              'Desbloquear exige nova autorização. Autorizações anteriores não são restauradas.',
            ),
            Wrap(
              spacing: 12,
              children: [
                if (estado!['bloqueio_global_em'] == null)
                  OutlinedButton(
                    onPressed: c.ocupado
                        ? null
                        : () => formulario(tipo: 'bloqueio_global'),
                    child: const Text('Bloquear todas as comunicações'),
                  )
                else
                  FilledButton.tonal(
                    onPressed: c.ocupado
                        ? null
                        : () => formulario(tipo: 'desbloqueio_global'),
                    child: const Text('Remover bloqueio administrativo'),
                  ),
              ],
            ),
            for (final raw in widget.contato['telefones'] as List)
              if (!(estado!['identidades'] as List).any(
                (i) => i['telefone_id'] == raw['id'],
              ))
                ListTile(
                  title: Text(raw['telefone_e164'] as String),
                  subtitle: const Text(
                    'Sem identidade de canal — desconhecido, sem autorização',
                  ),
                  trailing:
                      raw['ativo'] == true && widget.contato['ativo'] == true
                      ? TextButton(
                          onPressed: c.ocupado
                              ? null
                              : () => criarIdentidade(raw as Map),
                          child: const Text('Registrar autorização'),
                        )
                      : null,
                ),
            for (final raw in estado!['identidades'] as List)
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('WhatsApp — ${telefone(raw['telefone_id'])}'),
                      const Text('Existência no canal não verificada'),
                      for (final pref in raw['preferencias'] as List)
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(
                            '${categoriasConsentimento[pref['categoria']]}: ${pref['estado']}',
                          ),
                          subtitle: Text(
                            (pref['motivos'] as List).isEmpty
                                ? 'Elegível quanto a consentimento; envio não avaliado'
                                : (pref['motivos'] as List)
                                      .map((m) => motivosConsentimento[m] ?? m)
                                      .join('; '),
                          ),
                        ),
                      Wrap(
                        spacing: 12,
                        children: [
                          FilledButton.tonal(
                            onPressed:
                                c.ocupado ||
                                    estado!['bloqueio_global_em'] != null ||
                                    raw['bloqueio_canal_em'] != null
                                ? null
                                : () => formulario(
                                    tipo: 'autorizou',
                                    identidade: raw['id'] as String,
                                    categoria: 'promocoes',
                                  ),
                            child: const Text('Registrar nova autorização'),
                          ),
                          if (raw['bloqueio_canal_em'] == null)
                            OutlinedButton(
                              onPressed: c.ocupado
                                  ? null
                                  : () => formulario(
                                      tipo: 'bloqueio_canal',
                                      identidade: raw['id'] as String,
                                    ),
                              child: const Text('Bloquear WhatsApp'),
                            )
                          else
                            OutlinedButton(
                              onPressed: c.ocupado
                                  ? null
                                  : () => formulario(
                                      tipo: 'desbloqueio_canal',
                                      identidade: raw['id'] as String,
                                    ),
                              child: const Text('Remover bloqueio administrativo'),
                            ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            const Divider(),
            Text(
              'Histórico (${historico['total']})',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            if ((historico['itens'] as List).isEmpty)
              const Text('Nenhuma alteração registrada.'),
            for (final evento in historico['itens'] as List)
              ListTile(
                key: ValueKey('evento:${evento['id']}'),
                title: Text(
                  tiposConsentimento[evento['tipo']] ??
                      evento['tipo'] as String,
                ),
                subtitle: Text(
                  [
                    alvoEvento(evento as Map),
                    '${evento['ocorrido_em']} — operador ${evento['usuario_id']}',
                    if (evento['categoria'] != null)
                      '${categoriasConsentimento[evento['categoria']]}',
                    if (evento['metodo_captura'] != null)
                      'Método: ${evento['metodo_captura']}',
                    if (evento['evidencia_referencia'] != null)
                      'Evidência: ${evento['evidencia_referencia']}',
                    if (evento['justificativa'] != null)
                      '${evento['justificativa']}',
                  ].join('\n'),
                ),
              ),
            Wrap(
              spacing: 12,
              children: [
                TextButton(
                  onPressed: c.ocupado || pagina == 1
                      ? null
                      : () {
                          setState(() => pagina--);
                          carregarHistorico();
                        },
                  child: const Text('Anteriores'),
                ),
                Text('Página $pagina'),
                TextButton(
                  onPressed:
                      c.ocupado || pagina * 10 >= (historico['total'] as int)
                      ? null
                      : () {
                          setState(() => pagina++);
                          carregarHistorico();
                        },
                  child: const Text('Próximos'),
                ),
              ],
            ),
          ],
        ],
      ),
    ),
  );
}

class FormularioConsentimento extends StatefulWidget {
  const FormularioConsentimento({
    super.key,
    required this.controlador,
    required this.raiz,
    required this.escopo,
    required this.estado,
    required this.telefones,
    this.novoEscopo = false,
    this.tipoInicial,
    this.identidadeInicial,
    this.categoriaInicial,
  });
  final ControladorCadastro controlador;
  final String raiz;
  final Map<String, dynamic>? escopo, estado;
  final List telefones;
  final bool novoEscopo;
  final String? tipoInicial, identidadeInicial, categoriaInicial;
  @override
  State<FormularioConsentimento> createState() =>
      _FormularioConsentimentoState();
}

class _FormularioConsentimentoState extends State<FormularioConsentimento> {
  final form = GlobalKey<FormState>();
  final operacao = operacaoConsentimento();
  final nome = TextEditingController();
  final data = TextEditingController(
    text: DateTime.now().toUtc().toIso8601String(),
  );
  final metodo = TextEditingController(),
      evidencia = TextEditingController(),
      justificativa = TextEditingController();
  late String? tipo, identidade, categoria;
  bool get global => tipo?.endsWith('_global') == true;
  bool get porCategoria => tipo == 'autorizou' || tipo == 'revogou';
  @override
  void initState() {
    super.initState();
    tipo = widget.tipoInicial;
    identidade = widget.identidadeInicial;
    categoria = widget.categoriaInicial;
  }

  @override
  void dispose() {
    for (final controller in [nome, data, metodo, evidencia, justificativa]) {
      controller.dispose();
    }
    super.dispose();
  }

  String? obrigatorio(String? value) =>
      value == null || value.trim().isEmpty ? 'Preencha este campo.' : null;
  Future<void> confirmar() async {
    if (!form.currentState!.validate()) return;
    final result = await widget.controlador.requisitar(
      'POST',
      widget.novoEscopo
          ? '/contatos/escopos-comunicacao'
          : '${widget.raiz}/consentimentos/eventos',
      corpo: widget.novoEscopo
          ? {'operacao_id': operacao, 'nome': nome.text.trim()}
          : {
              'operacao_id': operacao,
              'escopo_id': widget.escopo!['id'],
              'versao': widget.estado!['versao'],
              'tipo': tipo,
              'identidade_contato_id': global ? null : identidade,
              'categoria': porCategoria ? categoria : null,
              'origem': 'operador',
              'ocorrido_em': data.text,
              if (tipo == 'autorizou') 'metodo_captura': metodo.text.trim(),
              if (tipo == 'autorizou')
                'evidencia_referencia': evidencia.text.trim(),
              if (tipo != 'autorizou')
                'justificativa': justificativa.text.trim(),
            },
    );
    if (mounted && result != null) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.controlador,
    builder: (context, _) => AlertDialog(
      title: Text(
        widget.novoEscopo
            ? 'Escopo local de comunicação'
            : 'Registrar alteração de consentimento',
      ),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Form(
            key: form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (widget.novoEscopo) ...[
                  const Text(
                    'Informe o nome real desta comunicação. O escopo será único nesta instalação. '
                    'Ele não cria empresa ou conta e não compartilha consentimentos entre marcas.',
                  ),
                  TextFormField(
                    controller: nome,
                    maxLength: 120,
                    validator: obrigatorio,
                    decoration: const InputDecoration(
                      labelText: 'Nome do escopo',
                    ),
                  ),
                ] else ...[
                  Text('Escopo: ${widget.escopo!['nome']} — origem: operador'),
                  if (widget.tipoInicial == null)
                    DropdownButtonFormField<String>(
                      initialValue: tipo,
                      isExpanded: true,
                      decoration: const InputDecoration(labelText: 'Operação'),
                      items: tiposConsentimento.entries
                          .map(
                            (e) => DropdownMenuItem(
                              value: e.key,
                              child: Text(e.value),
                            ),
                          )
                          .toList(),
                      onChanged: (value) => setState(() => tipo = value),
                      validator: obrigatorio,
                    )
                  else
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Text(tiposConsentimento[tipo]!),
                    ),
                  if (!global)
                    DropdownButtonFormField<String>(
                      initialValue: identidade,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Identidade WhatsApp',
                      ),
                      items: (widget.estado!['identidades'] as List)
                          .map(
                            (i) => DropdownMenuItem<String>(
                              value: i['id'] as String,
                              child: Text(
                                widget.telefones.firstWhere(
                                      (p) => p['id'] == i['telefone_id'],
                                      orElse: () => {
                                        'telefone_e164': 'Telefone arquivado',
                                      },
                                    )['telefone_e164']
                                    as String,
                              ),
                            ),
                          )
                          .toList(),
                      onChanged: widget.identidadeInicial == null
                          ? (value) => setState(() => identidade = value)
                          : null,
                      validator: obrigatorio,
                    ),
                  if (porCategoria)
                    DropdownButtonFormField<String>(
                      initialValue: categoria,
                      isExpanded: true,
                      decoration: const InputDecoration(labelText: 'Categoria'),
                      items: categoriasConsentimento.entries
                          .map(
                            (e) => DropdownMenuItem(
                              value: e.key,
                              child: Text(e.value),
                            ),
                          )
                          .toList(),
                      onChanged: (value) => setState(() => categoria = value),
                      validator: obrigatorio,
                    ),
                  TextFormField(
                    controller: data,
                    decoration: const InputDecoration(
                      labelText:
                          'Data/hora da manifestação (ISO 8601 com fuso)',
                    ),
                    validator: (value) =>
                        DateTime.tryParse(value ?? '') == null ||
                            !RegExp(r'(Z|[+-]\d\d:\d\d)$').hasMatch(value ?? '')
                        ? 'Informe data/hora com Z ou deslocamento de fuso.'
                        : null,
                  ),
                  if (tipo == 'autorizou') ...[
                    const Text(
                      'A referência deve permitir verificar a prova da nova autorização.',
                    ),
                    TextFormField(
                      controller: metodo,
                      maxLength: 120,
                      validator: obrigatorio,
                      decoration: const InputDecoration(
                        labelText: 'Método de captura',
                      ),
                    ),
                    TextFormField(
                      controller: evidencia,
                      maxLength: 500,
                      validator: obrigatorio,
                      decoration: const InputDecoration(
                        labelText: 'Referência verificável da evidência',
                      ),
                    ),
                  ],
                  if (tipo != null && tipo != 'autorizou')
                    TextFormField(
                      controller: justificativa,
                      maxLength: 500,
                      validator: obrigatorio,
                      decoration: const InputDecoration(
                        labelText: 'Justificativa',
                      ),
                    ),
                  const Text(
                    'Desbloquear não restaura autorizações anteriores.',
                  ),
                ],
                if (widget.controlador.mensagem != null)
                  Text(widget.controlador.mensagem!),
              ],
            ),
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: widget.controlador.ocupado
              ? null
              : () => Navigator.pop(context),
          child: const Text('Cancelar'),
        ),
        FilledButton(
          onPressed: widget.controlador.ocupado ? null : confirmar,
          child: Text(
            widget.novoEscopo ? 'Definir escopo' : 'Confirmar registro',
          ),
        ),
      ],
    ),
  );
}
