import 'dart:async';

import 'package:flutter/material.dart';

import '../autenticacao/controlador_autenticacao.dart';
import 'controlador_contas.dart';
import 'editor_conta.dart';
import '../simulacao.dart';

class PaginaContas extends StatefulWidget {
  const PaginaContas({super.key, required this.autenticacao});
  final ControladorAutenticacao autenticacao;
  @override
  State<PaginaContas> createState() => _PaginaContasState();
}

class _PaginaContasState extends State<PaginaContas> {
  late var c = ControladorContas(widget.autenticacao);
  final busca = TextEditingController();
  String estado = 'ativa';
  String? provedor;
  int pagina = 1;
  Map<String, dynamic> dados = {'itens': [], 'total': 0};

  @override
  void initState() {
    super.initState();
    sessaoAtiva = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    widget.autenticacao.addListener(sessao);
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  bool? sessaoAtiva;
  void sessao() {
    final ativa = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    if (ativa == sessaoAtiva || !mounted) return;
    sessaoAtiva = ativa;
    setState(() {
      c.dispose();
      c = ControladorContas(widget.autenticacao);
      dados = {'itens': [], 'total': 0};
      busca.clear();
      pagina = 1;
    });
    if (ativa) WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void dispose() {
    widget.autenticacao.removeListener(sessao);
    busca.dispose();
    c.dispose();
    super.dispose();
  }

  Future<void> carregar() async {
    if (busca.text.runes.length > 120) return;
    final result = await c.requisitar(
      'GET',
      '/contas-envio',
      parametros: {
        'q': busca.text,
        'pagina': '$pagina',
        'limite': '20',
        'estado': estado,
        'provedor': ?provedor,
      },
    );
    if (mounted && result != null) setState(() => dados = result);
  }

  Future<void> abrir([String? id]) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => id == null
            ? EditorConta(autenticacao: widget.autenticacao)
            : DetalheConta(autenticacao: widget.autenticacao, id: id),
      ),
    );
    if (mounted) await carregar();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(title: const Text('Contas de envio')),
      body: widget.autenticacao.usuario?.perfil != 'administrador'
          ? const Center(
              child: Text('Somente o administrador pode gerenciar contas.'),
            )
          : Column(
              children: [
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      SizedBox(
                        width: 230,
                        child: TextField(
                          controller: busca,
                          maxLength: 120,
                          decoration: const InputDecoration(
                            labelText: 'Buscar conta',
                          ),
                          onSubmitted: c.ocupado
                              ? null
                              : (_) {
                                  pagina = 1;
                                  carregar();
                                },
                        ),
                      ),
                      SizedBox(
                        width: 180,
                        child: DropdownButtonFormField<String>(
                          initialValue: estado,
                          isExpanded: true,
                          decoration: const InputDecoration(
                            labelText: 'Estado cadastral',
                          ),
                          items: estadosConta.entries
                              .map(
                                (e) => DropdownMenuItem(
                                  value: e.key,
                                  child: Text(e.value),
                                ),
                              )
                              .toList(),
                          onChanged: c.ocupado
                              ? null
                              : (v) => setState(() => estado = v!),
                        ),
                      ),
                      SizedBox(
                        width: 270,
                        child: DropdownButtonFormField<String>(
                          initialValue: '',
                          isExpanded: true,
                          decoration: const InputDecoration(
                            labelText: 'Provedor',
                          ),
                          items: [
                            const DropdownMenuItem(
                              value: '',
                              child: Text('Todos os provedores'),
                            ),
                            ...provedoresConta.entries.map(
                              (e) => DropdownMenuItem(
                                value: e.key,
                                child: Text(e.value),
                              ),
                            ),
                          ],
                          onChanged: c.ocupado
                              ? null
                              : (v) => provedor = v == '' ? null : v,
                        ),
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
                        onPressed: c.ocupado ? null : () => abrir(),
                        child: const Text('Nova conta'),
                      ),
                    ],
                  ),
                ),
                if (c.ocupado) const LinearProgressIndicator(),
                if (c.mensagem != null) Text(c.mensagem!),
                if (c.limpezaPendente) const Text(avisoLimpezaConta),
                Expanded(
                  child: ListView(
                    children: [
                      if ((dados['itens'] as List).isEmpty)
                        const Padding(
                          padding: EdgeInsets.all(20),
                          child: Text('Nenhuma conta encontrada.'),
                        ),
                      for (final item in dados['itens'] as List)
                        ListTile(
                          title: Text(item['nome'] as String),
                          subtitle: Text(
                            '${provedoresConta[item['provedor']]} • ${estadosConta[item['estado_cadastral']]}\n'
                            '${item['provedor'] == 'mock'
                                ? '$avisoSimulacao\nCredenciais não aplicáveis'
                                : item['provedor'] == 'qr_candidato'
                                ? '${configuracoesConta[item['configuracao_local']]} • Gerencie a conexão no detalhe'
                                : '${configuracoesConta[item['configuracao_local']]} • Conexão: não verificada'}',
                          ),
                          isThreeLine: true,
                          trailing: const Icon(Icons.chevron_right),
                          onTap: c.ocupado
                              ? null
                              : () => abrir(item['id'] as String),
                        ),
                    ],
                  ),
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
                    Text('Página $pagina • ${dados['total']} contas'),
                    TextButton(
                      onPressed:
                          c.ocupado || pagina * 20 >= (dados['total'] as int)
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

class DetalheConta extends StatefulWidget {
  const DetalheConta({super.key, required this.autenticacao, required this.id});
  final ControladorAutenticacao autenticacao;
  final String id;
  @override
  State<DetalheConta> createState() => _DetalheContaState();
}

class _DetalheContaState extends State<DetalheConta> {
  late var c = ControladorContas(widget.autenticacao);
  Future<void> _fimAnterior = Future.value();
  Key editor = UniqueKey();
  bool focarEditor = false;
  var operacao = OperacaoConta();
  Map<String, dynamic>? item;
  Map<String, dynamic>? botVerificado;
  Map<String, dynamic>? whatsapp;
  Timer? atualizacaoWhatsapp;
  var operacaoWhatsapp = OperacaoConta();
  String get path => '/contas-envio/${widget.id}';

  @override
  void initState() {
    super.initState();
    _sessaoAtiva = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    widget.autenticacao.addListener(sessao);
    WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void didUpdateWidget(DetalheConta oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.id != widget.id ||
        oldWidget.autenticacao != widget.autenticacao) {
      oldWidget.autenticacao.removeListener(sessao);
      widget.autenticacao.addListener(sessao);
      _sessaoAtiva =
          widget.autenticacao.estado == EstadoAutenticacao.autenticado;
      if (c.ocupado) _fimAnterior = c.cancelar();
      c.dispose();
      c = ControladorContas(widget.autenticacao);
      item = null;
      botVerificado = null;
      whatsapp = null;
      atualizacaoWhatsapp?.cancel();
      operacaoWhatsapp = OperacaoConta();
      operacao = OperacaoConta();
      editor = UniqueKey();
      focarEditor = false;
      WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
    }
  }

  void limpar({bool refocar = false}) {
    if (!mounted) return;
    // Troca a subárvore inteira. O dispose do filho encerra controller/foco/histórico.
    setState(() {
      editor = UniqueKey();
      focarEditor = refocar;
      botVerificado = null;
    });
  }

  bool? _sessaoAtiva;
  void sessao() {
    limpar();
    final ativa = widget.autenticacao.estado == EstadoAutenticacao.autenticado;
    if (!mounted || ativa == _sessaoAtiva) return;
    _sessaoAtiva = ativa;
    setState(() {
      if (c.ocupado) _fimAnterior = c.cancelar();
      c.dispose();
      c = ControladorContas(widget.autenticacao);
      item = null;
      botVerificado = null;
      whatsapp = null;
      atualizacaoWhatsapp?.cancel();
      operacaoWhatsapp = OperacaoConta();
      operacao = OperacaoConta();
    });
    if (ativa) WidgetsBinding.instance.addPostFrameCallback((_) => carregar());
  }

  @override
  void dispose() {
    widget.autenticacao.removeListener(sessao);
    atualizacaoWhatsapp?.cancel();
    c.dispose();
    super.dispose();
  }

  Future<void> carregar() async {
    if (!mounted) return;
    final controlador = c;
    final id = widget.id;
    setState(() => botVerificado = null);
    await _fimAnterior;
    if (!mounted || controlador != c || id != widget.id) return;
    final result = await controlador.requisitar('GET', path);
    if (mounted && controlador == c && id == widget.id && result != null) {
      setState(() => item = result);
      if (result['provedor'] == 'qr_candidato') await consultarWhatsapp();
    }
  }

  void agendarWhatsapp() {
    atualizacaoWhatsapp?.cancel();
    final estado = whatsapp?['estado'];
    if (!{
      'conectando',
      'reconectando',
      'qr_disponivel',
      'desvinculando',
    }.contains(estado)) {
      return;
    }
    atualizacaoWhatsapp = Timer(
      const Duration(milliseconds: 1500),
      consultarWhatsapp,
    );
  }

  Future<void> consultarWhatsapp() async {
    final controlador = c;
    final conta = item;
    final id = widget.id;
    final geracao = widget.autenticacao.geracaoSessao;
    if (controlador.ocupado || conta?['provedor'] != 'qr_candidato') return;
    final result = await controlador.requisitar('GET', '$path/whatsapp');
    if (mounted &&
        controlador == c &&
        identical(conta, item) &&
        id == widget.id &&
        geracao == widget.autenticacao.geracaoSessao &&
        result != null) {
      setState(() => whatsapp = result);
      agendarWhatsapp();
    }
  }

  Future<void> acaoWhatsapp(String acao) async {
    final controlador = c;
    final conta = item;
    final id = widget.id;
    final geracao = widget.autenticacao.geracaoSessao;
    if (controlador.ocupado || conta?['provedor'] != 'qr_candidato') return;
    atualizacaoWhatsapp?.cancel();
    final result = await controlador.requisitar('POST', '$path/whatsapp/$acao');
    if (mounted &&
        controlador == c &&
        identical(conta, item) &&
        id == widget.id &&
        geracao == widget.autenticacao.geracaoSessao &&
        result != null) {
      setState(() => whatsapp = result);
      agendarWhatsapp();
    }
  }

  Future<void> desvincularWhatsapp() async {
    final autenticacao = widget.autenticacao;
    final controlador = c;
    final conta = item;
    final id = widget.id;
    final versao = conta?['versao'];
    final geracao = autenticacao.geracaoSessao;
    final operacaoAtual = operacaoWhatsapp;
    if (controlador.ocupado ||
        conta?['provedor'] != 'qr_candidato' ||
        versao is! int) {
      return;
    }
    bool contextoValido() =>
        mounted &&
        identical(widget.autenticacao, autenticacao) &&
        identical(c, controlador) &&
        identical(item, conta) &&
        identical(operacaoWhatsapp, operacaoAtual) &&
        autenticacao.geracaoSessao == geracao &&
        widget.id == id &&
        item?['versao'] == versao;
    final confirmado = await showDialog<bool>(
      context: context,
      useRootNavigator: false,
      builder: (ctx) => AlertDialog(
        title: const Text('Desvincular WhatsApp?'),
        content: const Text(
          'Isto desconecta e apaga permanentemente a sessão protegida desta conta. O cadastro e o histórico serão preservados.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Desvincular e apagar sessão'),
          ),
        ],
      ),
    );
    if (confirmado != true || !contextoValido() || controlador.ocupado) return;
    final result = await controlador.requisitar(
      'POST',
      '$path/whatsapp/desvincular',
      corpo: operacaoAtual.corpo({'versao': versao}),
    );
    if (contextoValido() && result != null) {
      setState(() {
        whatsapp = result;
        operacaoWhatsapp = OperacaoConta();
      });
      agendarWhatsapp();
    }
  }

  String estadoWhatsapp() => switch (whatsapp?['estado']) {
    'pareamento_necessario' => 'Sem sessão vinculada',
    'conectando' => 'Conectando',
    'reconectando' => 'Reconectando',
    'qr_disponivel' => 'QR Code disponível',
    'conectado' => 'Conectado',
    'desconectado' => 'Desconectado temporariamente',
    'desvinculando' => 'Desvinculando sessão',
    'sessao_invalida' => 'Sessão inválida; desvincule e pareie novamente',
    'erro_desvinculo' => 'Não foi possível desvincular; tente novamente',
    'erro_armazenamento' => 'Armazenamento protegido indisponível',
    'erro_conexao' || 'erro' => 'Falha na conexão',
    _ => 'Status não consultado',
  };

  bool sessaoWhatsappVinculada() =>
      whatsapp?['sessao_persistida'] == true ||
      {
        'conectado',
        'desconectado',
        'reconectando',
        'desvinculando',
        'sessao_invalida',
        'erro_desvinculo',
      }.contains(whatsapp?['estado']);

  void salvo() {
    limpar();
    carregar();
  }

  Future<void> verificarTelegram() async {
    final controlador = c;
    final conta = item;
    final id = widget.id;
    final geracao = widget.autenticacao.geracaoSessao;
    if (controlador.ocupado) return;
    limpar();
    setState(() => botVerificado = null);
    final result = await controlador.requisitar(
      'POST',
      '$path/verificar-telegram',
    );
    if (mounted &&
        controlador == c &&
        id == widget.id &&
        identical(conta, item) &&
        geracao == widget.autenticacao.geracaoSessao &&
        result != null) {
      setState(() => botVerificado = result);
    }
  }

  Future<void> editar() async {
    limpar();
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) =>
            EditorConta(autenticacao: widget.autenticacao, conta: item),
      ),
    );
    if (mounted) await carregar();
  }

  Future<void> alterar(String acao) async {
    limpar();
    final autenticacao = widget.autenticacao;
    final controlador = c;
    final conta = item;
    final id = widget.id;
    final versao = conta?['versao'];
    final geracao = autenticacao.geracaoSessao;
    final operacaoAtual = operacao;
    if (conta == null ||
        versao is! int ||
        conta['id'] != id ||
        autenticacao.estado != EstadoAutenticacao.autenticado ||
        autenticacao.usuario?.perfil != 'administrador' ||
        controlador.ocupado) {
      return;
    }

    bool contextoValido() =>
        mounted &&
        identical(widget.autenticacao, autenticacao) &&
        identical(c, controlador) &&
        identical(item, conta) &&
        identical(operacao, operacaoAtual) &&
        autenticacao.estado == EstadoAutenticacao.autenticado &&
        autenticacao.usuario?.perfil == 'administrador' &&
        autenticacao.geracaoSessao == geracao &&
        widget.id == id &&
        item?['id'] == id &&
        item?['versao'] == versao;

    final titulo = switch (acao) {
      'remover' => 'Remover conta?',
      'excluir' => 'Excluir credencial local?',
      'desativar' => 'Desativar conta?',
      _ => 'Reativar conta?',
    };
    final confirmado = await showDialog<bool>(
      context: context,
      useRootNavigator: false,
      builder: (ctx) => AlertDialog(
        title: Text(titulo),
        content: Text(
          conta['provedor'] == 'mock'
              ? 'O histórico será preservado. Esta ação não executa simulação.'
              : conta['provedor'] == 'qr_candidato' && acao == 'remover'
              ? 'O cadastro será removido e a sessão WhatsApp será desvinculada. Campanhas e históricos serão preservados.'
              : acao == 'excluir' || acao == 'remover'
              ? 'O histórico será preservado. A exclusão local não revoga o token no provedor.'
              : 'Esta ação não valida a conexão nem altera consentimentos.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Confirmar'),
          ),
        ],
      ),
    );
    if (confirmado != true || !contextoValido() || controlador.ocupado) return;
    final caminho = '/contas-envio/$id';
    final result = await controlador.requisitar(
      acao == 'excluir' ? 'DELETE' : 'PATCH',
      acao == 'excluir' ? '$caminho/credencial' : caminho,
      corpo: operacaoAtual.corpo({
        'versao': versao,
        if (acao == 'remover') 'remover': true,
        if (acao == 'desativar' || acao == 'reativar')
          'ativo': acao == 'reativar',
      }),
    );
    if (contextoValido() && result != null) await carregar();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: c,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: Text(item?['nome'] as String? ?? 'Conta de envio'),
        actions: [
          TextButton(
            onPressed: widget.autenticacao.sair,
            child: const Text('Sair'),
          ),
        ],
      ),
      body: widget.autenticacao.usuario?.perfil != 'administrador'
          ? const Center(
              child: Text('Somente o administrador pode gerenciar contas.'),
            )
          : item == null
          ? Center(child: Text(c.mensagem ?? 'Carregando conta…'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('Provedor: ${provedoresConta[item!['provedor']]}'),
                Text(
                  'Estado cadastral: ${estadosConta[item!['estado_cadastral']]}',
                ),
                Text(
                  'Configuração local: ${configuracoesConta[item!['configuracao_local']]}',
                ),
                if (item!['provedor'] == 'mock') ...[
                  const Text(avisoSimulacao),
                  const Text(
                    'Credenciais não aplicáveis. Conexão externa não aplicável.',
                  ),
                  const Text(
                    'Ativa indica apenas o estado cadastral da conta.',
                  ),
                ] else if (item!['provedor'] == 'qr_candidato') ...[
                  Text('Conexão: ${estadoWhatsapp()}'),
                  Text(
                    sessaoWhatsappVinculada()
                        ? 'Sessão protegida vinculada a esta conta.'
                        : 'Nenhuma sessão protegida vinculada a esta conta.',
                  ),
                  const Text(
                    'Desconectar é temporário e preserva a sessão. Desvincular apaga a sessão e exige novo pareamento.',
                  ),
                  const SizedBox(height: 12),
                  const Text('Gerenciamento do WhatsApp'),
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      if (item!['ativo'] == true &&
                          item!['estado_cadastral'] != 'removida' &&
                          !sessaoWhatsappVinculada())
                        FilledButton(
                          onPressed: c.ocupado
                              ? null
                              : () => acaoWhatsapp('parear'),
                          child: const Text('Iniciar pareamento'),
                        ),
                      if (item!['ativo'] == true &&
                          item!['estado_cadastral'] != 'removida' &&
                          sessaoWhatsappVinculada() &&
                          whatsapp?['estado'] != 'conectado')
                        FilledButton(
                          onPressed: c.ocupado
                              ? null
                              : () => acaoWhatsapp('reconectar'),
                          child: const Text('Reconectar'),
                        ),
                      if ({
                        'conectando',
                        'reconectando',
                        'qr_disponivel',
                        'conectado',
                      }.contains(whatsapp?['estado']))
                        OutlinedButton(
                          onPressed: c.ocupado
                              ? null
                              : () => acaoWhatsapp('desconectar'),
                          child: const Text('Desconectar temporariamente'),
                        ),
                      OutlinedButton(
                        onPressed: c.ocupado ? null : consultarWhatsapp,
                        child: const Text('Atualizar status WhatsApp'),
                      ),
                      OutlinedButton.icon(
                        onPressed:
                            c.ocupado ||
                                whatsapp?['estado'] == 'desvinculando' ||
                                !sessaoWhatsappVinculada()
                            ? null
                            : desvincularWhatsapp,
                        icon: const Icon(Icons.link_off),
                        label: const Text('Desvincular WhatsApp'),
                      ),
                    ],
                  ),
                  if (whatsapp?['qr'] is List) ...[
                    const SizedBox(height: 16),
                    const Text(
                      'No WhatsApp, abra Aparelhos conectados e escaneie:',
                    ),
                    const SizedBox(height: 8),
                    Center(
                      child: SizedBox.square(
                        dimension: 340,
                        child: CustomPaint(
                          painter: _QrWhatsappPainter(
                            whatsapp!['qr'] as List<dynamic>,
                          ),
                        ),
                      ),
                    ),
                  ],
                ] else ...[
                  Text(
                    botVerificado == null
                        ? 'Conexão: não verificada'
                        : 'Conexão verificada nesta consulta',
                  ),
                  if (botVerificado != null)
                    Text(
                      'Bot: ${botVerificado!['nome']} • ID: ${botVerificado!['id']}'
                      '${botVerificado!['username'] == null ? '' : ' • @${botVerificado!['username']}'}',
                    ),
                  const Text(
                    'Credencial salva não comprova validade, titularidade ou permissão no provedor.',
                  ),
                ],
                if (c.ocupado) const LinearProgressIndicator(),
                if (c.mensagem != null) Text(c.mensagem!),
                if (c.limpezaPendente) const Text(avisoLimpezaConta),
                Wrap(
                  spacing: 12,
                  children: [
                    if (item!['provedor'] == 'telegram_bot_api' &&
                        item!['ativo'] == true &&
                        item!['estado_cadastral'] != 'removida')
                      OutlinedButton(
                        onPressed: c.ocupado ? null : verificarTelegram,
                        child: const Text('Verificar conexão Telegram'),
                      ),
                    TextButton(
                      onPressed: c.ocupado ? null : carregar,
                      child: const Text('Atualizar'),
                    ),
                    if (item!['estado_cadastral'] != 'removida') ...[
                      OutlinedButton(
                        onPressed: c.ocupado ? null : editar,
                        child: const Text('Editar nome'),
                      ),
                      OutlinedButton(
                        onPressed: c.ocupado
                            ? null
                            : () => alterar(
                                item!['ativo'] == true
                                    ? 'desativar'
                                    : 'reativar',
                              ),
                        child: Text(
                          item!['ativo'] == true ? 'Desativar' : 'Reativar',
                        ),
                      ),
                      OutlinedButton(
                        onPressed: c.ocupado ? null : () => alterar('remover'),
                        child: const Text('Remover conta'),
                      ),
                    ],
                  ],
                ),
                if (item!['provedor'] == 'qr_candidato') ...[
                  const Text(
                    'Integração WhatsApp QR experimental; não aprovada para distribuição comercial.',
                  ),
                ],
                if (item!['provedor'] != 'qr_candidato' &&
                    item!['provedor'] != 'mock' &&
                    item!['estado_cadastral'] != 'removida' &&
                    widget.autenticacao.estado ==
                        EstadoAutenticacao.autenticado)
                  _EditorCredencial(
                    key: editor,
                    controlador: c,
                    caminho: '$path/credencial',
                    versao: item!['versao'] as int,
                    autofocus: focarEditor,
                    onSalvo: salvo,
                    onCancelar: () => limpar(refocar: true),
                    onExcluir: () => alterar('excluir'),
                  ),
              ],
            ),
    ),
  );
}

class _QrWhatsappPainter extends CustomPainter {
  const _QrWhatsappPainter(this.matrix);
  final List<dynamic> matrix;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(Offset.zero & size, Paint()..color = Colors.white);
    if (matrix.isEmpty) return;
    const quietZone = 4;
    final modules = matrix.length + quietZone * 2;
    final side = size.shortestSide / modules;
    final left = (size.width - side * modules) / 2 + quietZone * side;
    final top = (size.height - side * modules) / 2 + quietZone * side;
    final dark = Paint()..color = Colors.black;
    for (var row = 0; row < matrix.length; row++) {
      final values = matrix[row] as List<dynamic>;
      for (var column = 0; column < values.length; column++) {
        if (values[column] == true) {
          canvas.drawRect(
            Rect.fromLTWH(
              left + column * side,
              top + row * side,
              side + 0.01,
              side + 0.01,
            ),
            dark,
          );
        }
      }
    }
  }

  @override
  bool shouldRepaint(_QrWhatsappPainter oldDelegate) =>
      !identical(oldDelegate.matrix, matrix);
}

/// Cada instância contém uma única edição; nunca recebe uma credencial existente.
class _EditorCredencial extends StatefulWidget {
  const _EditorCredencial({
    super.key,
    required this.controlador,
    required this.caminho,
    required this.versao,
    required this.autofocus,
    required this.onSalvo,
    required this.onCancelar,
    required this.onExcluir,
  });
  final ControladorContas controlador;
  final String caminho;
  final int versao;
  final bool autofocus;
  final VoidCallback onSalvo, onCancelar, onExcluir;
  @override
  State<_EditorCredencial> createState() => _EditorCredencialState();
}

class _EditorCredencialState extends State<_EditorCredencial> {
  final segredo = TextEditingController();
  final foco = FocusNode();
  final form = GlobalKey<FormState>();
  String? operacao;

  @override
  void didUpdateWidget(_EditorCredencial oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.versao != widget.versao) operacao = null;
  }

  @override
  void dispose() {
    // Os descendentes EditableText/UndoHistory já são desmontados antes deste dispose.
    segredo.clear();
    segredo.dispose();
    foco.dispose();
    super.dispose();
  }

  Future<void> salvar() async {
    if (!form.currentState!.validate()) return;
    operacao ??= novaOperacaoConta();
    final result = await widget.controlador.requisitar(
      'PUT',
      widget.caminho,
      corpo: {
        'operacao_id': operacao,
        'versao': widget.versao,
        'credencial': segredo.text,
      },
    );
    if (mounted && result != null) widget.onSalvo();
  }

  @override
  Widget build(BuildContext context) => Form(
    key: form,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 16),
        TextFormField(
          key: const ValueKey('credencial-conta'),
          controller: segredo,
          focusNode: foco,
          autofocus: widget.autofocus,
          enabled: !widget.controlador.ocupado,
          obscureText: true,
          autocorrect: false,
          enableSuggestions: false,
          enableIMEPersonalizedLearning: false,
          decoration: const InputDecoration(
            labelText: 'Nova credencial (não será exibida novamente)',
          ),
          validator: (v) =>
              v == null ||
                  v.isEmpty ||
                  v.length > 4096 ||
                  v.codeUnits.any((c) => c < 33 || c > 126)
              ? 'Informe uma credencial de até 4.096 caracteres, sem espaços.'
              : null,
          onChanged: (_) => operacao = null,
        ),
        Wrap(
          spacing: 12,
          children: [
            FilledButton(
              onPressed: widget.controlador.ocupado ? null : salvar,
              child: const Text('Salvar credencial'),
            ),
            TextButton(
              onPressed: widget.controlador.ocupado ? null : widget.onCancelar,
              child: const Text('Cancelar credencial'),
            ),
            OutlinedButton(
              onPressed: widget.controlador.ocupado ? null : widget.onExcluir,
              child: const Text('Excluir credencial local'),
            ),
          ],
        ),
      ],
    ),
  );
}
