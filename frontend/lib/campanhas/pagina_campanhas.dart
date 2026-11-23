import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_campanhas.dart';
import 'editor_campanha.dart';
import 'preparacao_campanha.dart';
import 'chaves_campanhas.dart';
import 'pagina_execucoes.dart';

class PaginaCampanhas extends StatefulWidget {
  const PaginaCampanhas({super.key, required this.autenticacao});
  final ControladorAutenticacao autenticacao;
  @override
  State<PaginaCampanhas> createState() => _PaginaCampanhasState();
}

class _PaginaCampanhasState extends State<PaginaCampanhas> {
  late final c = ControladorCampanhas(widget.autenticacao);
  Map<String, dynamic> dados = {'itens': [], 'total': 0};
  int pagina = 1;

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
    final result = await c.requisitar(
      'GET',
      '/campanhas',
      parametros: {'pagina': '$pagina'},
    );
    if (mounted && result != null) setState(() => dados = result);
  }

  Future<void> abrir([String? id]) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) =>
            EditorCampanha(autenticacao: widget.autenticacao, id: id),
      ),
    );
    if (mounted) await carregar();
  }

  Future<void> fotografia(String id) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) =>
            PreparacaoCampanha(autenticacao: widget.autenticacao, id: id),
      ),
    );
    if (mounted) await carregar();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: const Text('Campanhas'),
        actions: [
          if (widget.autenticacao.usuario?.perfil == 'administrador')
            IconButton(
              tooltip: 'Protecao das campanhas',
              onPressed: () => Navigator.of(context).push<void>(
                MaterialPageRoute(
                  builder: (_) =>
                      ChavesCampanhas(autenticacao: widget.autenticacao),
                ),
              ),
              icon: const Icon(Icons.key),
            ),
          IconButton(
            tooltip: 'Atualizar campanhas',
            onPressed: c.ocupado ? null : carregar,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        children: [
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text(avisoCampanha),
          ),
          if (c.ocupado) const LinearProgressIndicator(),
          if (c.mensagem != null) Text(c.mensagem!),
          Padding(
            padding: const EdgeInsets.all(8),
            child: FilledButton(
              onPressed: c.ocupado ? null : () => abrir(),
              child: const Text('Novo rascunho'),
            ),
          ),
          Expanded(
            child: ListView(
              children: [
                for (final item in dados['itens'] as List)
                  ListTile(
                    title: Text(item['nome'] as String),
                    subtitle: Text(
                      item['status'] == 'preparada'
                          ? 'Preparada; consulte simulação e histórico'
                          : 'Rascunho',
                    ),
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        IconButton(
                          tooltip: 'Execuções mock',
                          icon: const Icon(Icons.playlist_play),
                          onPressed: c.ocupado
                              ? null
                              : () => Navigator.of(context).push<void>(
                                  MaterialPageRoute(
                                    builder: (_) => PaginaExecucoes(
                                      autenticacao: widget.autenticacao,
                                      campanhaId: item['id'] as String,
                                    ),
                                  ),
                                ),
                        ),
                        IconButton(
                          tooltip: 'Preparacao e historico',
                          onPressed: c.ocupado
                              ? null
                              : () => fotografia(item['id'] as String),
                          icon: const Icon(Icons.fact_check),
                        ),
                      ],
                    ),
                    onTap: c.ocupado
                        ? null
                        : () => item['status'] == 'preparada'
                              ? fotografia(item['id'] as String)
                              : abrir(item['id'] as String),
                  ),
              ],
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              TextButton(
                onPressed: c.ocupado || pagina <= 1
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
