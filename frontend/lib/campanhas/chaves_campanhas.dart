import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import '../templates/controlador_templates.dart' show novaOperacaoTemplate;
import 'controlador_campanhas.dart';

class ChavesCampanhas extends StatefulWidget {
  const ChavesCampanhas({super.key, required this.autenticacao});
  final ControladorAutenticacao autenticacao;
  @override
  State<ChavesCampanhas> createState() => _ChavesCampanhasState();
}

class _ChavesCampanhasState extends State<ChavesCampanhas> {
  Key editor = UniqueKey();
  bool recuperada = false;
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Protecao das campanhas')),
    body: Column(
      children: [
        if (recuperada)
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text(
              'Chave recuperada neste perfil. As fotografias nao foram verificadas nesta operacao; cada consulta verifica sua integridade. Isto nao restaura o banco.',
            ),
          ),
        Expanded(
          child: EditorChaveCampanhas(
            key: editor,
            autenticacao: widget.autenticacao,
            descartar: () => setState(() => editor = UniqueKey()),
            aoRecuperar: () => setState(() {
              recuperada = true;
              editor = UniqueKey();
            }),
          ),
        ),
      ],
    ),
  );
}

class EditorChaveCampanhas extends StatefulWidget {
  const EditorChaveCampanhas({
    super.key,
    required this.autenticacao,
    required this.descartar,
    this.aoRecuperar,
  });
  final ControladorAutenticacao autenticacao;
  final VoidCallback descartar;
  final VoidCallback? aoRecuperar;
  @override
  State<EditorChaveCampanhas> createState() => _EditorChaveCampanhasState();
}

class _EditorChaveCampanhasState extends State<EditorChaveCampanhas> {
  late final c = ControladorCampanhas(widget.autenticacao);
  final codigo = TextEditingController();
  final foco = FocusNode();
  final undo = UndoHistoryController();
  String? exibido;
  Map<String, dynamic>? estado;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  Future<void> carregar() async {
    final r = await c.requisitar('GET', '/campanhas/chaves/estado');
    if (mounted && r != null) setState(() => estado = r);
  }

  Future<void> executar(String acao) async {
    final r = await c.requisitar(
      'POST',
      '/campanhas/chaves/$acao',
      corpo: {
        'operacao_id': novaOperacaoTemplate(),
        if (acao == 'conferir' || acao == 'recuperar') 'codigo': codigo.text,
      },
    );
    if (!mounted) return;
    if (r != null) {
      if (r['codigo_recuperacao'] != null) {
        setState(() => exibido = r['codigo_recuperacao'] as String);
      } else if (acao == 'recuperar' && widget.aoRecuperar != null) {
        widget.aoRecuperar!();
      } else {
        widget.descartar();
      }
    }
  }

  @override
  void dispose() {
    exibido = null;
    codigo.clear();
    codigo.dispose();
    foco.dispose();
    undo.dispose();
    c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Guarde o codigo de recuperacao separado dos backups e deste computador. Ele permite acessar o conteudo de uma copia integra desta base apos recuperacao administrativa.',
        ),
        const Text(
          'Sem DPAPI acessivel e sem codigo, o conteudo e irrecuperavel. Este fluxo nao faz backup do banco nem recupera o login.',
        ),
        if (c.ocupado) const LinearProgressIndicator(),
        if (c.mensagem != null) Text(c.mensagem!),
        if (estado != null)
          Text(
            estado!['conferida'] == true
                ? 'Codigo conferido'
                : 'Conferencia de recuperacao pendente',
          ),
        if (estado?['administrador'] == true) ...[
          if (exibido != null) ...[
            const Text(
              'Apresentacao unica deste codigo. Guarde antes de fechar.',
            ),
            SelectableText(exibido!, key: const ValueKey('codigo-gerado')),
            FilledButton(
              onPressed: widget.descartar,
              child: const Text('Ja guardei; conferir codigo'),
            ),
          ] else ...[
            if (estado?['inicializada'] == false)
              FilledButton(
                onPressed: c.ocupado ? null : () => executar('inicializar'),
                child: const Text('Inicializar protecao'),
              ),
            if (estado?['inicializada'] == true) ...[
              TextField(
                key: const ValueKey('codigo-recuperacao'),
                controller: codigo,
                focusNode: foco,
                undoController: undo,
                obscureText: true,
                autocorrect: false,
                enableSuggestions: false,
                enableIMEPersonalizedLearning: false,
                decoration: const InputDecoration(
                  labelText: 'Codigo de recuperacao',
                ),
              ),
              FilledButton(
                onPressed: c.ocupado ? null : () => executar('conferir'),
                child: const Text('Conferir codigo'),
              ),
              OutlinedButton(
                onPressed: c.ocupado ? null : () => executar('recuperar'),
                child: const Text('Recuperar protecao neste perfil'),
              ),
              TextButton(
                onPressed: c.ocupado ? null : () => executar('reemitir'),
                child: const Text('Reemitir codigo com a chave atual'),
              ),
            ],
          ],
        ],
        TextButton(
          onPressed: widget.descartar,
          child: const Text('Descartar campos'),
        ),
      ],
    ),
  );
}
