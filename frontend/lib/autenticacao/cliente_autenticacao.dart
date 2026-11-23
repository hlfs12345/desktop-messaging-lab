import 'dart:async';

import '../conexao/transporte_local.dart';

class ErroAutenticacao implements Exception {
  const ErroAutenticacao(this.codigo, {this.espera = 0});
  final String codigo;
  final int espera;
}

/// Uma consulta pertence a um unico componente; nao contem token ou payload.
class ConsultaNegocio {
  final _cancelamento = Completer<void>();
  bool get cancelada => _cancelamento.isCompleted;
  Future<void> get cancelamento => _cancelamento.future;
  void _cancelar() {
    if (!cancelada) _cancelamento.complete();
  }
}

class UsuarioLocal {
  const UsuarioLocal(this.id, this.nome, this.login, this.perfil);
  factory UsuarioLocal.deJson(Object? raw) {
    if (raw is! Map<String, dynamic> ||
        raw['id'] is! String ||
        raw['nome'] is! String ||
        raw['login'] is! String ||
        !{'administrador', 'operador'}.contains(raw['perfil'])) {
      throw const ErroAutenticacao('resposta_invalida');
    }
    return UsuarioLocal(
      raw['id'] as String,
      raw['nome'] as String,
      raw['login'] as String,
      raw['perfil'] as String,
    );
  }
  final String id;
  final String nome;
  final String login;
  final String perfil;
}

class SessaoLocal {
  const SessaoLocal(this.usuario, this.expiraEm);
  final UsuarioLocal usuario;
  final DateTime expiraEm;
}

/// O token fica privado neste cliente e nunca passa ao supervisor/widgets.
class ClienteAutenticacao {
  ClienteAutenticacao(this._local, {TransporteLocal? negocio})
    : _negocio = negocio; // ignore: prefer_initializing_formals
  final TransporteLocal _local;
  final TransporteLocal? _negocio;
  String? _token;
  bool _fechado = false;
  int _geracao = 0;
  int _geracaoNegocio = 0;
  Completer<void>? _fimNegocio;
  ConsultaNegocio? _consultaNegocio;

  Future<RespostaLocal> negocio(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    Map<String, String>? parametros,
    List<int>? bytes,
    bool arquivo = false,
    ConsultaNegocio? consulta,
  }) async {
    if (_fechado || _token == null) {
      throw const ErroAutenticacao('sessao_invalida');
    }
    final geracao = _geracaoNegocio;
    final atual = consulta ?? ConsultaNegocio();
    // Espera somente a consulta anterior. O proprio fim so e publicado depois.
    while (_fimNegocio != null && !atual.cancelada) {
      await Future.any([_fimNegocio!.future, atual.cancelamento]);
    }
    if (_fechado ||
        _token == null ||
        geracao != _geracaoNegocio ||
        atual.cancelada) {
      throw const ErroAutenticacao('cancelada');
    }
    final fim = Completer<void>();
    _fimNegocio = fim;
    _consultaNegocio = atual;
    try {
      final result = await (_negocio ?? _local).solicitar(
        metodo,
        caminho,
        sessao: _token,
        corpo: corpo,
        parametros: parametros,
        bytes: bytes,
        arquivo: arquivo,
        limiteResposta: arquivo ? 2 * 1024 * 1024 : 512 * 1024,
      );
      if (_fechado ||
          geracao != _geracaoNegocio ||
          _token == null ||
          atual.cancelada) {
        throw const ErroAutenticacao('cancelada');
      }
      if (result.codigo == 401) throw const ErroAutenticacao('sessao_invalida');
      return result;
    } on ErroTransporte catch (erro) {
      throw ErroAutenticacao(
        erro.falha == FalhaTransporte.tempoEsgotado
            ? 'tempo_esgotado'
            : 'cancelada',
      );
    } finally {
      fim.complete();
      if (identical(_fimNegocio, fim)) {
        _fimNegocio = null;
        _consultaNegocio = null;
      }
    }
  }

  Future<void> cancelarEAguardarNegocio({ConsultaNegocio? consulta}) async {
    if (consulta != null) {
      consulta._cancelar();
      if (!identical(_consultaNegocio, consulta)) return;
      final fim = _fimNegocio?.future;
      (_negocio ?? _local).cancelar();
      await fim;
    } else {
      final fim = _fimNegocio?.future;
      cancelarNegocio();
      await fim;
    }
  }

  void cancelarNegocio() {
    ++_geracaoNegocio;
    _consultaNegocio?._cancelar();
    (_negocio ?? _local).cancelar();
  }

  Future<RespostaLocal> _solicitar(
    String metodo,
    String caminho, {
    Map<String, dynamic>? corpo,
    bool autenticada = false,
    int sucesso = 200,
  }) async {
    if (_fechado) throw const ErroAutenticacao('cancelada');
    if (autenticada && _token == null) {
      throw const ErroAutenticacao('sessao_invalida');
    }
    try {
      final resposta = await _local.solicitar(
        metodo,
        caminho,
        corpo: corpo,
        sessao: autenticada ? _token : null,
      );
      if (_fechado) throw const ErroAutenticacao('cancelada');
      if (resposta.codigo != sucesso) {
        final codigo = resposta.dados?['codigo'];
        const permitidos = {
          'credenciais_invalidas',
          'sessao_invalida',
          'configuracao_indisponivel',
          'validacao',
          'senha_comum',
          'tentativas_limitadas',
          'autenticacao_indisponivel',
          'corpo_excessivo',
        };
        if (resposta.codigo == 401 &&
            codigo != 'credenciais_invalidas' &&
            codigo != 'sessao_invalida') {
          throw const ErroAutenticacao('instancia_invalida');
        }
        throw ErroAutenticacao(
          permitidos.contains(codigo)
              ? codigo as String
              : 'servico_indisponivel',
          espera: resposta.espera.clamp(0, 900),
        );
      }
      return resposta;
    } on ErroTransporte catch (erro) {
      throw ErroAutenticacao(switch (erro.falha) {
        FalhaTransporte.cancelada => 'cancelada',
        FalhaTransporte.tempoEsgotado => 'tempo_esgotado',
        _ => 'servico_indisponivel',
      });
    }
  }

  Future<bool> configuracaoNecessaria() async {
    final resposta = await _solicitar('GET', '/auth/configuracao-inicial');
    if (resposta.dados?['necessaria'] is! bool) {
      throw const ErroAutenticacao('resposta_invalida');
    }
    return resposta.dados!['necessaria'] as bool;
  }

  Future<void> configurar(String nome, String login, String senha) async {
    final resposta = await _solicitar(
      'POST',
      '/auth/configuracao-inicial',
      corpo: {'nome': nome, 'login': login, 'senha': senha},
      sucesso: 201,
    );
    UsuarioLocal.deJson(resposta.dados);
  }

  Future<SessaoLocal> entrar(String login, String senha) async {
    final geracao = _geracao;
    final resposta = await _solicitar(
      'POST',
      '/auth/login',
      corpo: {'login': login, 'senha': senha},
    );
    final raw = resposta.dados;
    final token = raw?['token'];
    final sessao = _sessao(raw);
    if (_fechado || geracao != _geracao) {
      throw const ErroAutenticacao('cancelada');
    }
    if (token is! String ||
        !RegExp(r'^edisk_u_[A-Za-z0-9_-]{43}$').hasMatch(token)) {
      throw const ErroAutenticacao('resposta_invalida');
    }
    cancelarNegocio();
    _token = token;
    return sessao;
  }

  SessaoLocal _sessao(Map<String, dynamic>? raw) {
    final data = raw?['expira_em'];
    final expira = data is String ? DateTime.tryParse(data) : null;
    if (data is! String ||
        expira == null ||
        (!data.endsWith('Z') && !RegExp(r'[+-]\d\d:\d\d$').hasMatch(data))) {
      throw const ErroAutenticacao('resposta_invalida');
    }
    return SessaoLocal(UsuarioLocal.deJson(raw?['usuario']), expira.toUtc());
  }

  Future<SessaoLocal> usuario() async => _sessao(
    (await _solicitar('GET', '/auth/usuario', autenticada: true)).dados,
  );

  Future<void> sair() async {
    await _fimNegocio?.future;
    await _solicitar('POST', '/auth/logout', autenticada: true, sucesso: 204);
    esquecer();
  }

  void esquecer() {
    cancelarNegocio();
    ++_geracao;
    _token = null;
    _local.cancelar();
  }

  void close() {
    _fechado = true;
    esquecer();
    _local.close();
    _negocio?.close();
  }
}
