import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_contas.dart';
import '../simulacao.dart';

class EditorConta extends StatefulWidget {
  const EditorConta({super.key, required this.autenticacao, this.conta});
  final ControladorAutenticacao autenticacao;
  final Map<String, dynamic>? conta;
  @override
  State<EditorConta> createState() => _EditorContaState();
}

class _EditorContaState extends State<EditorConta> {
  late final c = ControladorContas(widget.autenticacao);
  late final nome = TextEditingController(
    text: widget.conta?['nome'] as String?,
  );
  final form = GlobalKey<FormState>();
  var operacao = OperacaoConta();
  String provedor = 'telegram_bot_api';
  @override
  void initState() {
    super.initState();
    widget.autenticacao.addListener(sessao);
  }

  void sessao() {
    if (widget.autenticacao.estado != EstadoAutenticacao.autenticado &&
        mounted) {
      setState(() {
        nome.clear();
        operacao = OperacaoConta();
      });
    }
  }

  @override
  void dispose() {
    widget.autenticacao.removeListener(sessao);
    nome.dispose();
    c.dispose();
    super.dispose();
  }

  Future<void> salvar() async {
    if (!form.currentState!.validate()) return;
    final conta = widget.conta;
    final result = await c.requisitar(
      conta == null ? 'POST' : 'PATCH',
      conta == null ? '/contas-envio' : '/contas-envio/${conta['id']}',
      corpo: operacao.corpo({
        'nome': nome.text.trim(),
        if (conta == null) ...{
          'provedor': provedor,
          'plataforma': provedor == 'telegram_bot_api'
              ? 'telegram'
              : 'whatsapp',
        } else
          'versao': conta['versao'],
      }),
    );
    if (mounted && result != null) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: Text(widget.conta == null ? 'Nova conta' : 'Editar nome'),
      ),
      body: widget.autenticacao.usuario?.perfil != 'administrador'
          ? const Center(
              child: Text('Somente o administrador pode gerenciar contas.'),
            )
          : SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: Form(
                key: form,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    TextFormField(
                      controller: nome,
                      enabled: !c.ocupado,
                      decoration: const InputDecoration(
                        labelText: 'Nome amigável',
                      ),
                      maxLength: 120,
                      validator: (v) =>
                          v == null || v.trim().isEmpty || v.runes.length > 120
                          ? 'Informe um nome de até 120 caracteres.'
                          : null,
                    ),
                    if (widget.conta == null)
                      SizedBox(
                        width: 320,
                        child: DropdownButtonFormField<String>(
                          initialValue: provedor,
                          isExpanded: true,
                          decoration: const InputDecoration(
                            labelText: 'Provedor da conta',
                          ),
                          items: provedoresConta.entries
                              .map(
                                (e) => DropdownMenuItem(
                                  value: e.key,
                                  child: Text(e.value),
                                ),
                              )
                              .toList(),
                          onChanged: c.ocupado
                              ? null
                              : (v) => setState(() => provedor = v!),
                        ),
                      ),
                    if ((widget.conta?['provedor'] ?? provedor) == 'mock') ...[
                      const Text(avisoSimulacao),
                      const Text(
                        'Plataforma: WhatsApp. Credenciais não aplicáveis.',
                      ),
                      const Text(
                        'Ativa indica apenas o estado cadastral da conta.',
                      ),
                    ],
                    Text(
                      (widget.conta?['provedor'] ?? provedor) == 'mock'
                          ? 'Plataforma e provedor ficam fixos após o cadastro.'
                          : 'Plataforma e provedor ficam fixos após o cadastro. Conexão não verificada.',
                    ),
                    if (widget.conta == null && provedor == 'qr_candidato')
                      const Text(
                        'WhatsApp QR experimental; distribuição comercial não aprovada.',
                      ),
                    if (c.mensagem != null) Text(c.mensagem!),
                    Wrap(
                      spacing: 12,
                      children: [
                        FilledButton(
                          onPressed: c.ocupado ? null : salvar,
                          child: const Text('Salvar conta'),
                        ),
                        TextButton(
                          onPressed: c.ocupado
                              ? null
                              : () => Navigator.pop(context),
                          child: const Text('Cancelar'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
    ),
  );
}
