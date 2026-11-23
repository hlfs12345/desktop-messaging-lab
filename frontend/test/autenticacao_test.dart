import 'dart:async';
import 'dart:convert';

import 'package:edisk/autenticacao/cliente_autenticacao.dart';
import 'package:edisk/autenticacao/controlador_autenticacao.dart';
import 'package:edisk/autenticacao/pagina_autenticacao.dart';
import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:edisk/conexao/transporte_local.dart';
import 'package:edisk/processos/supervisor_backend.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';

const senhaFicticia = 'frase fictícia para teste 489!';
final tokenUsuario = 'edisk_u_${'b' * 43}';
const usuario = {
  'id': '00000000-0000-0000-0000-000000000002',
  'nome': 'Pessoa Fictícia',
  'login': 'teste.local',
  'perfil': 'administrador',
};

Map<String, dynamic> sessao({DateTime? expira}) => {
  'usuario': usuario,
  'expira_em': (expira ?? DateTime.utc(2099)).toIso8601String(),
  'token': tokenUsuario,
};

ContextoBackend contexto([String? id]) =>
    ContextoBackend(execucao: id ?? '1' * 32, porta: 49152, token: tokenTeste);

class Cenario {
  Cenario({this.inicial = false, DateTime Function()? agora}) {
    transporte = TransporteSimulado((request) => responder(request));
    cliente = ClienteAutenticacao(
      TransporteLocal(
        endpoint: contexto().endpoint,
        token: tokenTeste,
        transporte: transporte,
      ),
    );
    controlador = ControladorAutenticacao(
      criarCliente: (_) => cliente,
      agora: agora,
    );
    controlador.conectar(contexto());
  }
  bool inicial;
  bool falharLogout = false;
  bool expirada = false;
  bool falharLogin = false;
  DateTime? expira;
  late final TransporteSimulado transporte;
  late final ClienteAutenticacao cliente;
  late final ControladorAutenticacao controlador;

  FutureOr<http.StreamedResponse> Function(http.BaseRequest)? substituir;
  FutureOr<http.StreamedResponse> responder(http.BaseRequest request) {
    if (substituir != null) return substituir!(request);
    switch (request.url.path) {
      case '/auth/configuracao-inicial':
        if (request.method == 'POST') {
          inicial = false;
          return resposta(codigo: 201, corpo: jsonEncode(usuario));
        }
        return resposta(corpo: jsonEncode({'necessaria': inicial}));
      case '/auth/login':
        return falharLogin
            ? resposta(
                codigo: 401,
                corpo: '{"codigo":"credenciais_invalidas","detail":"privado"}',
              )
            : resposta(corpo: jsonEncode(sessao(expira: expira)));
      case '/auth/usuario':
        return expirada
            ? resposta(codigo: 401, corpo: '{"codigo":"sessao_invalida"}')
            : resposta(
                corpo: jsonEncode(sessao(expira: expira)..remove('token')),
              );
      case '/auth/logout':
        return resposta(
          codigo: falharLogout ? 503 : 204,
          corpo: falharLogout ? '{"codigo":"autenticacao_indisponivel"}' : '',
        );
      default:
        return resposta(codigo: 404);
    }
  }

  void dispose() => controlador.dispose();
}

void main() {
  test(
    'cliente envia ambos os cabeçalhos e nunca token em URL ou cookie',
    () async {
      final c = Cenario();
      addTearDown(c.dispose);
      await c.cliente.entrar('teste.local', senhaFicticia);
      await c.cliente.usuario();
      final login = c.transporte.requisicoes.first;
      final current = c.transporte.requisicoes.last;
      expect(login.headers['Authorization'], 'Bearer $tokenTeste');
      expect(login.headers.containsKey('X-eDisk-Session'), isFalse);
      expect(current.headers['X-eDisk-Session'], tokenUsuario);
      expect(current.headers.containsKey('Cookie'), isFalse);
      expect(current.url.hasQuery, isFalse);
      expect(current.followRedirects, isFalse);
      expect(current, isA<http.AbortableRequest>());
      await c.cliente.sair();
      await expectLater(c.cliente.usuario(), throwsA(isA<ErroAutenticacao>()));
    },
  );

  test('bootstrap não cria sessão automaticamente', () async {
    final c = Cenario(inicial: true);
    addTearDown(c.dispose);
    await c.controlador.consultar();
    expect(c.controlador.estado, EstadoAutenticacao.configurar);
    await c.controlador.configurar(
      'Pessoa Fictícia',
      'teste.local',
      senhaFicticia,
    );
    expect(c.controlador.estado, EstadoAutenticacao.login);
    expect(c.controlador.usuario, isNull);
    await expectLater(c.cliente.usuario(), throwsA(isA<ErroAutenticacao>()));
  });

  test('login, usuário e logout confirmado', () async {
    final c = Cenario();
    addTearDown(c.dispose);
    await c.controlador.consultar();
    await c.controlador.entrar('teste.local', senhaFicticia);
    expect(c.controlador.estado, EstadoAutenticacao.autenticado);
    expect(c.controlador.usuario?.nome, 'Pessoa Fictícia');
    await c.controlador.verificarUsuario();
    await c.controlador.sair();
    expect(c.controlador.estado, EstadoAutenticacao.login);
    expect(c.controlador.usuario, isNull);
    expect(c.controlador.mensagem, 'Sessão encerrada.');
  });

  test('falha de logout bloqueia área e permite retry', () async {
    final c = Cenario();
    addTearDown(c.dispose);
    await c.controlador.entrar('teste.local', senhaFicticia);
    c.falharLogout = true;
    await c.controlador.sair();
    expect(c.controlador.estado, EstadoAutenticacao.logoutPendente);
    expect(c.controlador.usuario, isNull);
    expect(c.controlador.mensagem, contains('Não foi possível confirmar'));
    c.falharLogout = false;
    await c.controlador.sair();
    expect(c.controlador.estado, EstadoAutenticacao.login);
  });

  test('sessão rejeitada retorna ao login sem conservar credencial', () async {
    final c = Cenario();
    addTearDown(c.dispose);
    await c.controlador.entrar('teste.local', senhaFicticia);
    c.expirada = true;
    await c.controlador.verificarUsuario();
    expect(c.controlador.estado, EstadoAutenticacao.login);
    expect(c.controlador.mensagem, 'Sua sessão terminou. Entre novamente.');
    await expectLater(c.cliente.usuario(), throwsA(isA<ErroAutenticacao>()));
  });

  testWidgets('expiração absoluta limpa sessão sem polling', (tester) async {
    final agora = DateTime.utc(2026, 10, 29);
    final c = Cenario(agora: () => agora)
      ..expira = agora.add(const Duration(seconds: 5));
    addTearDown(c.dispose);
    await c.controlador.entrar('teste.local', senhaFicticia);
    final quantidade = c.transporte.requisicoes.length;
    await tester.pump(const Duration(seconds: 5));
    expect(c.controlador.estado, EstadoAutenticacao.login);
    expect(c.transporte.requisicoes.length, quantidade);
  });

  test('reabertura e troca de contexto não reutilizam sessão', () async {
    final c = Cenario();
    await c.controlador.entrar('teste.local', senhaFicticia);
    c.controlador.desconectar();
    expect(c.controlador.usuario, isNull);
    await expectLater(c.cliente.usuario(), throwsA(isA<ErroAutenticacao>()));
    c.dispose();
    final novo = Cenario();
    addTearDown(novo.dispose);
    await novo.controlador.consultar();
    expect(novo.controlador.estado, EstadoAutenticacao.login);
    await expectLater(novo.cliente.usuario(), throwsA(isA<ErroAutenticacao>()));
  });

  testWidgets('expiração cancela consulta pendente e permite novo login', (
    tester,
  ) async {
    final agora = DateTime.utc(2026, 10, 29);
    final c = Cenario(agora: () => agora)
      ..expira = agora.add(const Duration(seconds: 5));
    addTearDown(c.dispose);
    await c.controlador.entrar('teste.local', senhaFicticia);
    final antiga = Completer<http.StreamedResponse>();
    c.substituir = (_) => antiga.future;
    final consulta = c.controlador.verificarUsuario();
    await tester.pump(const Duration(seconds: 5));
    await consulta;
    expect(c.controlador.estado, EstadoAutenticacao.login);
    antiga.complete(resposta(corpo: jsonEncode(sessao())));
    c.substituir = null;
    c.expira = DateTime.utc(2099);
    await c.controlador.entrar('teste.local', senhaFicticia);
    expect(c.controlador.estado, EstadoAutenticacao.autenticado);
    c.dispose();
  });

  test('descarte aborta HTTP e ignora resposta de login antiga', () async {
    final c = Cenario();
    final respostaTardia = Completer<http.StreamedResponse>();
    c.substituir = (_) => respostaTardia.future;
    final futuro = c.controlador.entrar('teste.local', senhaFicticia);
    c.dispose();
    respostaTardia.complete(resposta(corpo: jsonEncode(sessao())));
    await futuro;
    expect(c.controlador.usuario, isNull);
    expect(c.transporte.fechamentos, 1);
  });

  test('concorrência não dispara segundo login e erro fica genérico', () async {
    final c = Cenario();
    addTearDown(c.dispose);
    final aguardando = Completer<http.StreamedResponse>();
    c.substituir = (_) => aguardando.future;
    final primeiro = c.controlador.entrar('teste.local', senhaFicticia);
    await c.controlador.entrar('outro', 'outra frase fictícia');
    expect(c.transporte.requisicoes, hasLength(1));
    aguardando.complete(
      resposta(
        codigo: 401,
        corpo: '{"codigo":"credenciais_invalidas","detail":"senha privada"}',
      ),
    );
    await primeiro;
    expect(c.controlador.mensagem, 'Login ou senha inválidos.');
  });

  for (final (indice, dados) in [
    <String, dynamic>{},
    {...sessao(), 'token': 'inválido'},
    {...sessao(), 'expira_em': '2026-10-29T10:00:00'},
    {...sessao(), 'usuario': {}},
  ].indexed) {
    test(
      'cliente rejeita resposta de login incompatível $indice sem guardar sessão',
      () async {
        final c = Cenario();
        addTearDown(c.dispose);
        c.substituir = (_) => resposta(corpo: jsonEncode(dados));
        await expectLater(
          c.cliente.entrar('teste.local', senhaFicticia),
          throwsA(isA<ErroAutenticacao>()),
        );
        await expectLater(
          c.cliente.usuario(),
          throwsA(isA<ErroAutenticacao>()),
        );
      },
    );
  }

  test('429 respeita Retry-After e não mostra detalhe arbitrário', () async {
    final c = Cenario();
    addTearDown(c.dispose);
    c.substituir = (_) => http.StreamedResponse(
      Stream.value(
        utf8.encode('{"codigo":"tentativas_limitadas","detail":"privado"}'),
      ),
      429,
      headers: {'content-type': 'application/json', 'retry-after': '60'},
    );
    await c.controlador.entrar('teste.local', senhaFicticia);
    expect(c.controlador.mensagem, contains('60 segundos'));
    expect(c.transporte.requisicoes, hasLength(1));
  });

  testWidgets('configuração valida campos, confirmação e envia por Enter', (
    tester,
  ) async {
    final c = Cenario(inicial: true);
    addTearDown(c.dispose);
    await c.controlador.consultar();
    await tester.pumpWidget(
      MaterialApp(home: PaginaAutenticacao(controlador: c.controlador)),
    );
    await tester.ensureVisible(find.text('Criar administrador'));
    await tester.tap(find.text('Criar administrador'));
    await tester.pump();
    expect(find.text('Informe um login válido.'), findsOneWidget);
    expect(c.transporte.requisicoes, hasLength(1));
    final campos = find.byType(TextFormField);
    await tester.enterText(campos.at(0), 'Pessoa Fictícia');
    await tester.enterText(campos.at(1), 'teste.local');
    await tester.enterText(campos.at(2), senhaFicticia);
    await tester.enterText(campos.at(3), 'confirmação incorreta');
    await tester.ensureVisible(find.text('Criar administrador'));
    await tester.tap(find.text('Criar administrador'));
    await tester.pump();
    expect(find.text('As senhas devem ser iguais.'), findsOneWidget);
    await tester.enterText(campos.at(3), senhaFicticia);
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    expect(find.text('Entrar no eDisk'), findsOneWidget);
    expect(c.controlador.usuario, isNull);
  });

  testWidgets(
    'login e logout por teclado, senha oculta e mensagem sanitizada',
    (tester) async {
      final c = Cenario();
      addTearDown(c.dispose);
      await c.controlador.consultar();
      await tester.pumpWidget(
        MaterialApp(home: PaginaAutenticacao(controlador: c.controlador)),
      );
      await tester.pumpAndSettle();
      final campos = find.byType(TextFormField);
      await tester.enterText(campos.first, 'teste.local');
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      expect(
        tester.widget<TextField>(find.byType(TextField).last).obscureText,
        isTrue,
      );
      await tester.enterText(campos.last, senhaFicticia);
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      expect(find.text('Sessão iniciada'), findsOneWidget);
      expect(find.text('Pessoa Fictícia'), findsOneWidget);
      await tester.tap(find.text('Sair'));
      await tester.pumpAndSettle();
      expect(find.text('Entrar no eDisk'), findsOneWidget);
      expect(find.text('Sessão encerrada.'), findsOneWidget);
    },
  );

  testWidgets('falha de logout oferece nova tentativa sem identificação', (
    tester,
  ) async {
    final c = Cenario()..falharLogout = true;
    addTearDown(c.dispose);
    await c.controlador.entrar('teste.local', senhaFicticia);
    await tester.pumpWidget(
      MaterialApp(home: PaginaAutenticacao(controlador: c.controlador)),
    );
    await tester.tap(find.text('Sair'));
    await tester.pumpAndSettle();
    expect(find.text('Tentar sair novamente'), findsOneWidget);
    expect(find.text('Pessoa Fictícia'), findsNothing);
    expect(find.text('Entrar no eDisk'), findsNothing);
  });

  for (final brilho in Brightness.values) {
    testWidgets('tema $brilho e texto ampliado sem overflow', (tester) async {
      final c = Cenario(inicial: true);
      addTearDown(c.dispose);
      await c.controlador.consultar();
      tester.view.physicalSize = const Size(640, 600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData(useMaterial3: true, brightness: brilho),
          home: MediaQuery(
            data: const MediaQueryData(textScaler: TextScaler.linear(2)),
            child: PaginaAutenticacao(controlador: c.controlador),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }

  test('composição cancela autenticação antes de confirmar STOP', () async {
    final supervisor = _Supervisor();
    final health = TransporteSimulado((_) => resposta());
    final aguardando = Completer<http.StreamedResponse>();
    final transporteAuth = TransporteSimulado((_) => aguardando.future);
    final auth = ControladorAutenticacao(
      criarCliente: (ctx) => ClienteAutenticacao(
        TransporteLocal(
          endpoint: ctx.endpoint,
          token: ctx.token,
          transporte: transporteAuth,
        ),
      ),
    );
    var confirmou = false;
    supervisor.aoConfirmar = () {
      expect(health.fechamentos, 1);
      expect(transporteAuth.fechamentos, 1);
      expect(auth.estado, EstadoAutenticacao.desconectado);
      confirmou = true;
    };
    final conexao = ControladorConexao(
      supervisor: supervisor,
      criarCliente: (ctx) => ClienteHealth(
        endpoint: ctx.endpoint,
        token: ctx.token,
        transporte: health,
      ),
      aoConectar: auth.conectar,
      aoInvalidar: auth.desconectar,
      aoDisponivel: auth.iniciar,
    );
    supervisor.eventosController.add(
      SnapshotBackend(sequencia: 1, estado: 'ready', contexto: contexto()),
    );
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);
    expect(transporteAuth.requisicoes, hasLength(1));
    supervisor.eventosController.add(
      SnapshotBackend(sequencia: 2, estado: 'stopping'),
    );
    await Future<void>.delayed(Duration.zero);
    expect(confirmou, isTrue);
    aguardando.complete(resposta(corpo: '{"necessaria":true}'));
    await Future<void>.delayed(Duration.zero);
    expect(auth.estado, EstadoAutenticacao.desconectado);
    conexao.dispose();
    auth.dispose();
    await supervisor.close();
  });
}

class _Supervisor implements SupervisorBackend {
  final eventosController = StreamController<SnapshotBackend>.broadcast();
  void Function()? aoConfirmar;
  @override
  Stream<SnapshotBackend> get eventos => eventosController.stream;
  @override
  Future<void> anexar() async {}
  @override
  Future<void> confirmarPronto(String execucao) async {}
  @override
  Future<void> confirmarEncerramento() async {
    aoConfirmar?.call();
  }

  @override
  Future<void> close() async {
    if (!eventosController.isClosed) await eventosController.close();
  }
}
