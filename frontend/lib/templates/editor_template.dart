import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'texto_unicode.dart';

import 'controlador_templates.dart';

enum ModoEditorTemplate { criar, versionar, metadados }

class EditorTemplate extends StatefulWidget {
  const EditorTemplate({
    super.key,
    required this.controlador,
    required this.modo,
    this.template,
  });
  final ControladorTemplates controlador;
  final ModoEditorTemplate modo;
  final Map<String, dynamic>? template;
  @override
  State<EditorTemplate> createState() => _EditorTemplateState();
}

class _EditorTemplateState extends State<EditorTemplate> {
  final form = GlobalKey<FormState>();
  final nome = TextEditingController();
  final descricao = TextEditingController();
  final conteudo = TextEditingController();
  final operacao = OperacaoTemplate();
  String? categoria;
  String plataforma = 'whatsapp';
  int revisaoPrevia = 0;
  bool get criar => widget.modo == ModoEditorTemplate.criar;
  bool get metadados => widget.modo == ModoEditorTemplate.metadados;
  ControladorTemplates get c => widget.controlador;

  @override
  void initState() {
    super.initState();
    nome.text = widget.template?['nome'] as String? ?? '';
    descricao.text = widget.template?['descricao'] as String? ?? '';
    categoria = widget.template?['categoria'] as String?;
  }

  @override
  void dispose() {
    nome.dispose();
    descricao.dispose();
    conteudo.dispose();
    super.dispose();
  }

  Future<void> salvar() async {
    if (!form.currentState!.validate()) return;
    final campos = <String, dynamic>{
      if (criar || metadados) 'nome': nome.text.trim(),
      if (criar || metadados)
        'descricao': descricao.text.isEmpty ? null : descricao.text,
      if (criar) 'categoria': categoria,
      if (!metadados) ...{'plataforma': plataforma, 'conteudo': conteudo.text},
      if (metadados) 'versao': widget.template!['versao'],
    };
    final result = await c.requisitar(
      metadados ? 'PATCH' : 'POST',
      criar
          ? '/templates'
          : '/templates/${widget.template!['id']}${metadados ? '' : '/versoes'}',
      corpo: operacao.corpo(campos),
    );
    if (mounted && result != null) Navigator.pop(context, true);
  }

  Widget _editorMensagem() => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      DropdownButtonFormField<String>(
        isExpanded: true,
        initialValue: plataforma,
        decoration: const InputDecoration(labelText: 'Plataforma'),
        items: plataformasTemplate.entries
            .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
            .toList(),
        onChanged: c.ocupado
            ? null
            : (v) => setState(() {
                plataforma = v!;
                revisaoPrevia++;
              }),
      ),
      const SizedBox(height: 16),
      TextFormField(
        controller: conteudo,
        enabled: !c.ocupado,
        maxLength: 4000,
        maxLengthEnforcement: MaxLengthEnforcement.none,
        buildCounter: contadorTemplate(conteudo, 4000),
        minLines: 8,
        maxLines: 14,
        decoration: const InputDecoration(
          labelText: 'Conteúdo',
          alignLabelWithHint: true,
          helperText: 'Texto, links e {{nome}}.',
        ),
        onChanged: (_) => setState(() => revisaoPrevia++),
        validator: (v) =>
            validarLimiteTemplate(v, 4000) ??
            (v == null || v.trim().isEmpty ? 'Informe o conteúdo.' : null),
      ),
    ],
  );

  Widget _painelPrevia() => Card.outlined(
    child: Padding(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Prévia', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
          PreviaTemplate(
            key: ValueKey(revisaoPrevia),
            controlador: c,
            plataforma: plataforma,
            conteudo: conteudo.text,
          ),
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: Text(
          criar
              ? 'Novo template'
              : metadados
              ? 'Editar cadastro'
              : 'Nova versão',
        ),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1080),
            child: Form(
              key: form,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (criar || metadados) ...[
                    TextFormField(
                      controller: nome,
                      enabled: !c.ocupado,
                      maxLength: 120,
                      maxLengthEnforcement: MaxLengthEnforcement.none,
                      buildCounter: contadorTemplate(nome, 120),
                      decoration: const InputDecoration(
                        labelText: 'Nome do template',
                      ),
                      validator: (v) =>
                          validarLimiteTemplate(v, 120) ??
                          (v == null || v.trim().isEmpty
                              ? 'Informe o nome.'
                              : null),
                    ),
                    TextFormField(
                      controller: descricao,
                      enabled: !c.ocupado,
                      maxLength: 2000,
                      maxLengthEnforcement: MaxLengthEnforcement.none,
                      buildCounter: contadorTemplate(descricao, 2000),
                      validator: (v) => validarLimiteTemplate(v, 2000),
                      maxLines: 3,
                      decoration: const InputDecoration(labelText: 'Descrição'),
                    ),
                  ],
                  if (criar)
                    DropdownButtonFormField<String>(
                      isExpanded: true,
                      decoration: const InputDecoration(labelText: 'Categoria'),
                      items: categoriasTemplate.entries
                          .map(
                            (e) => DropdownMenuItem(
                              value: e.key,
                              child: Text(e.value),
                            ),
                          )
                          .toList(),
                      onChanged: c.ocupado
                          ? null
                          : (v) => setState(() => categoria = v),
                      validator: (v) =>
                          v == null ? 'Selecione a categoria.' : null,
                    )
                  else
                    Text(
                      'Categoria fixa: ${categoriasTemplate[categoria] ?? categoria}',
                    ),
                  if (!metadados) ...[
                    const SizedBox(height: 16),
                    LayoutBuilder(
                      builder: (context, constraints) {
                        if (constraints.maxWidth < 820) {
                          return Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              _editorMensagem(),
                              const SizedBox(height: 16),
                              _painelPrevia(),
                            ],
                          );
                        }
                        return Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Expanded(child: _editorMensagem()),
                            const SizedBox(width: 20),
                            SizedBox(width: 340, child: _painelPrevia()),
                          ],
                        );
                      },
                    ),
                    const SizedBox(height: 12),
                    const Text(
                      'Salvar cria uma versão imutável. Alterações futuras criam outra versão.',
                    ),
                  ],
                  if (c.mensagem != null)
                    Text(c.mensagem!, key: const Key('erro-template')),
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: c.ocupado ? null : salvar,
                    child: Text(
                      metadados ? 'Salvar cadastro' : 'Salvar versão',
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

class PreviaTemplate extends StatefulWidget {
  const PreviaTemplate({
    super.key,
    required this.controlador,
    required this.plataforma,
    required this.conteudo,
  });
  final ControladorTemplates controlador;
  final String plataforma;
  final String conteudo;
  @override
  State<PreviaTemplate> createState() => _PreviaTemplateState();
}

class _PreviaTemplateState extends State<PreviaTemplate> {
  final form = GlobalKey<FormState>();
  final nome = TextEditingController();
  String? resultado, erroConteudo;
  @override
  void dispose() {
    nome.dispose();
    super.dispose();
  }

  Future<void> gerar() async {
    final erro = validarLimiteTemplate(widget.conteudo, 4000);
    setState(() {
      resultado = null;
      erroConteudo = erro;
    });
    if (!form.currentState!.validate() || erro != null) return;
    final data = await widget.controlador.requisitar(
      'POST',
      '/templates/previa',
      corpo: {
        'plataforma': widget.plataforma,
        'conteudo': widget.conteudo,
        'valores': {if (nome.text.isNotEmpty) 'nome': nome.text},
      },
    );
    if (mounted && data != null) {
      setState(() => resultado = data['conteudo_renderizado'] as String);
    }
  }

  @override
  Widget build(BuildContext context) => Form(
    key: form,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text(avisoTemplate),
        TextFormField(
          controller: nome,
          enabled: !widget.controlador.ocupado,
          maxLength: 120,
          maxLengthEnforcement: MaxLengthEnforcement.none,
          buildCounter: contadorTemplate(nome, 120),
          validator: (v) => validarLimiteTemplate(v, 120),
          decoration: const InputDecoration(
            labelText: 'Nome fictício para prévia',
          ),
          onChanged: (_) => setState(() => resultado = null),
        ),
        if (erroConteudo != null) Text(erroConteudo!),
        OutlinedButton(
          onPressed: widget.controlador.ocupado ? null : gerar,
          child: const Text('Gerar prévia'),
        ),
        if (resultado != null)
          SelectableText(resultado!, key: const Key('resultado-previa')),
      ],
    ),
  );
}
