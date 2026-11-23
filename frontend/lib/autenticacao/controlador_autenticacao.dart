import 'dart:async';

import 'package:flutter/foundation.dart';

import '../processos/supervisor_backend.dart';
import 'cliente_autenticacao.dart';
import '../conexao/transporte_local.dart';

enum EstadoAutenticacao {
  desconectado,
  consultando,
  configurar,
  login,
  autenticado,
  erro,
  logoutPendente,
}

class ControladorAutenticacao extends ChangeNotifier {
  ControladorAutenticacao({
    required this.criarCliente,
    DateTime Function()? agora,
  }) : _agora = agora ?? DateTime.now;

  final ClienteAutenticacao Function(ContextoBackend) criarCliente;
  final DateTime Function() _agora;
  ClienteAutenticacao? _cliente;
  Timer? _expiracao;
  int _geracao = 0;
  int _geracaoSessao = 0;
  bool _descartado = false;
  bool _ocupado = false;
  bool _iniciado = false;
  EstadoAutenticacao _estado = EstadoAutenticacao.desconectado;
  UsuarioLocal? _usuario;
  String? _mensagem;

  EstadoAutenticacao get estado => _estado;
  bool get ocupado => _ocupado;
  int get geracaoSessao => _geracaoSessao;
  UsuarioLocal? get usuario => _usuario;
  String? get mensagem => _mensagem;

  Future<void> cancelarNegocio({ConsultaNegocio? consulta}) async {
    await _cliente?.cancelarEAguardarNegocio(consulta: consulta);
  }

  Future<RespostaLocal> negocio(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    Map<String, String>? parametros,
    List<int>? bytes,
    bool arquivo = false,
    ConsultaNegocio? consulta,
  }) async {
    if (_estado != EstadoAutenticacao.autenticado || _cliente == null) {
      throw const ErroAutenticacao('sessao_invalida');
    }
    final geracao = _geracao;
    final sessao = _geracaoSessao;
    try {
      final result = await _cliente!.negocio(
        metodo,
        caminho,
        corpo: corpo,
        parametros: parametros,
        bytes: bytes,
        arquivo: arquivo,
        consulta: consulta,
      );
      if (_descartado ||
          geracao != _geracao ||
          sessao != _geracaoSessao ||
          _estado != EstadoAutenticacao.autenticado) {
        throw const ErroAutenticacao('cancelada');
      }
      return result;
    } on ErroAutenticacao catch (erro) {
      if (!_descartado &&
          geracao == _geracao &&
          sessao == _geracaoSessao &&
          erro.codigo == 'sessao_invalida') {
        ++_geracao;
        _encerrarSessao();
        notifyListeners();
      }
      rethrow;
    }
  }

  void conectar(ContextoBackend contexto) {
    desconectar();
    _cliente = criarCliente(contexto);
  }

  void desconectar() {
    ++_geracao;
    ++_geracaoSessao;
    _expiracao?.cancel();
    _cliente?.close();
    _cliente = null;
    _usuario = null;
    _mensagem = null;
    _ocupado = false;
    _iniciado = false;
    _estado = EstadoAutenticacao.desconectado;
    if (!_descartado) notifyListeners();
  }

  void iniciar() {
    if (_iniciado || _descartado || _cliente == null) return;
    _iniciado = true;
    unawaited(consultar());
  }

  Future<void> consultar() => _operar(() async {
    _estado = EstadoAutenticacao.consultando;
    final necessaria = await _cliente!.configuracaoNecessaria();
    return () => _estado = necessaria
        ? EstadoAutenticacao.configurar
        : EstadoAutenticacao.login;
  }, erroConsulta: true);

  Future<void> configurar(String nome, String login, String senha) =>
      _operar(() async {
        await _cliente!.configurar(nome, login, senha);
        return () {
          _estado = EstadoAutenticacao.login;
          _mensagem = 'Administrador configurado. Entre com seu login e senha.';
        };
      });

  Future<void> entrar(String login, String senha) => _operar(() async {
    final sessao = await _cliente!.entrar(login, senha);
    return () => _autenticar(sessao, novaSessao: true);
  });

  void _autenticar(SessaoLocal sessao, {bool novaSessao = false}) {
    if (novaSessao) ++_geracaoSessao;
    final restante = sessao.expiraEm.difference(_agora().toUtc());
    if (restante <= Duration.zero) {
      _encerrarSessao();
      return;
    }
    _usuario = sessao.usuario;
    _estado = EstadoAutenticacao.autenticado;
    _expiracao?.cancel();
    final geracao = _geracao;
    _expiracao = Timer(restante, () {
      if (_descartado || geracao != _geracao) return;
      ++_geracao;
      _encerrarSessao();
      notifyListeners();
    });
  }

  void _encerrarSessao() {
    ++_geracaoSessao;
    _expiracao?.cancel();
    _cliente?.esquecer();
    _usuario = null;
    _ocupado = false;
    _estado = EstadoAutenticacao.login;
    _mensagem = 'Sua sessão terminou. Entre novamente.';
  }

  Future<void> verificarUsuario() => _operar(() async {
    final sessao = await _cliente!.usuario();
    return () => _autenticar(sessao);
  });

  Future<void> sair() => _operar(() async {
    ++_geracaoSessao;
    _cliente?.cancelarNegocio();
    _estado = EstadoAutenticacao.logoutPendente;
    _usuario = null;
    _expiracao?.cancel();
    notifyListeners();
    await _cliente!.sair();
    return () {
      _estado = EstadoAutenticacao.login;
      _mensagem = 'Sessão encerrada.';
    };
  }, logout: true);

  Future<void> _operar(
    Future<VoidCallback> Function() operacao, {
    bool erroConsulta = false,
    bool logout = false,
  }) async {
    if (_ocupado || _descartado || _cliente == null) return;
    final geracao = _geracao;
    _ocupado = true;
    _mensagem = null;
    notifyListeners();
    try {
      final aplicar = await operacao();
      if (_descartado || geracao != _geracao) return;
      aplicar();
    } on ErroAutenticacao catch (erro) {
      if (_descartado || geracao != _geracao) return;
      if (erro.codigo == 'sessao_invalida' && !logout) {
        _encerrarSessao();
      } else {
        if (erroConsulta) _estado = EstadoAutenticacao.erro;
        _mensagem = logout
            ? 'Não foi possível confirmar o encerramento da sessão. Tente sair novamente ou feche o aplicativo.'
            : _mensagemErro(erro);
      }
    } finally {
      if (!_descartado && geracao == _geracao) {
        _ocupado = false;
        notifyListeners();
      }
    }
  }

  String _mensagemErro(ErroAutenticacao erro) => switch (erro.codigo) {
    'credenciais_invalidas' => 'Login ou senha inválidos.',
    'tentativas_limitadas' =>
      'Aguarde ${erro.espera > 0 ? erro.espera : 1} segundos antes de tentar novamente.',
    'senha_comum' => 'Escolha uma senha menos previsível.',
    'validacao' => 'Confira os campos informados.',
    'configuracao_indisponivel' =>
      'A configuração inicial já foi concluída. Consulte o estado novamente.',
    'instancia_invalida' =>
      'A conexão local foi invalidada. Feche e abra o aplicativo.',
    'tempo_esgotado' => 'O serviço local não respondeu no prazo. Consulte o estado ou tente entrar novamente.',
    _ => 'Não foi possível concluir a operação. Tente novamente. Se persistir, procure suporte.',
  };

  @override
  void dispose() {
    if (_descartado) return;
    _descartado = true;
    desconectar();
    super.dispose();
  }
}
