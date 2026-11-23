import 'dart:async';
import 'dart:convert';

import 'package:edisk/autenticacao/controlador_autenticacao.dart';
import 'package:edisk/app.dart';
import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:edisk/cadastro/arquivos_locais.dart';
import 'package:edisk/cadastro/controlador_cadastro.dart';
import 'package:edisk/cadastro/pagina_cadastro.dart';
import 'package:edisk/cadastro/pagina_importacao.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'autenticacao_test.dart' as auth;
import 'apoio_http.dart';

class ArquivosFicticios extends ArquivosLocais {
  List<int>? salvo;
  @override
  Future<ArquivoImportacao?> abrir() async => ArquivoImportacao(
    'exemplo.csv',
    utf8.encode('nome;telefone\nFictício;+442083661177'),
  );
  @override
  Future<bool> salvar(List<int> bytes) async {
    salvo = bytes;
    return true;
  }
}

class CadastroFicticio {
  final cenario = auth.Cenario();
  late final controlador = ControladorCadastro(cenario.controlador);
  final arquivos = ArquivosFicticios();
  final chamadas = <http.BaseRequest>[];
  var status = 'carregada';
  Map<String, dynamic> configuracao = {};
  var contatos = <Map<String, dynamic>>[];
  Map<String, dynamic> get item => {
    'id': 'c1',
    'nome': 'José Fictício',
    'ativo': true,
    'versao': 1,
    'observacoes': 'Exemplo',
    'listas': <String>[],
    'telefones': [
      {
        'id': 't1',
        'telefone_e164': '+442083661177',
        'ativo': true,
        'versao': 1,
      },
    ],
  };
  Map<String, dynamic> get importacao => {
    'id': 'i1',
    'nome_arquivo': 'exemplo.csv',
    'status': status,
    'configuracao': configuracao,
    'contagens': status == 'carregada' ? <String, int>{} : {'inserido': 1},
    'arquivo': {'abas': <String>[]},
  };
  Future<void> iniciar() async {
    await cenario.controlador.entrar('teste.local', auth.senhaFicticia);
    cenario.substituir = (request) {
      chamadas.add(request);
      Object data = <String, dynamic>{};
      final path = request.url.path;
      if (path == '/contatos' && request.method == 'POST') {
        contatos = [item];
        data = item;
      } else if (path == '/contatos') {
        data = {'itens': contatos, 'total': contatos.length};
      } else if (path == '/contatos/c1') {
        data = item;
      } else if (path == '/listas') {
        data = {'itens': <dynamic>[], 'total': 0};
      } else if (path == '/importacoes' && request.method == 'POST') {
        data = importacao;
      } else if (path == '/importacoes/i1/arquivo') {
        data = {'abas': <String>[]};
      } else if (path == '/importacoes/i1/amostra') {
        data = {
          'itens': [
            {
              'linha': 1,
              'celulas': ['nome', 'telefone'],
            },
            {
              'linha': 2,
              'celulas': ['Fictício', '+442083661177'],
            },
          ],
        };
      } else if (path == '/importacoes/i1/validacao') {
        configuracao = Map<String, dynamic>.from(
          jsonDecode((request as http.Request).body) as Map,
        );
        status = 'pronta';
        return resposta(codigo: 202, corpo: '{"status":"validando"}');
      } else if (path == '/importacoes/i1/confirmacao') {
        status = 'confirmada';
        data = importacao;
      } else if (path == '/importacoes/i1/linhas') {
        data = {
          'total': 1,
          'itens': [
            {'numero_linha': 2, 'resultado': 'inserido', 'motivo': ''},
          ],
        };
      } else if (path == '/importacoes/i1/relatorio') {
        return http.StreamedResponse(
          Stream.value(utf8.encode('linha;resultado;motivo\n2;inserido;')),
          200,
          headers: {'content-type': 'text/csv'},
        );
      } else if (path == '/importacoes/i1') {
        data = importacao;
      }
      return resposta(
        codigo: request.method == 'POST' && path == '/contatos' ? 201 : 200,
        corpo: jsonEncode(data),
      );
    };
  }

  void dispose() {
    controlador.dispose();
    cenario.dispose();
  }
}

void main() {
  testWidgets('duplicidade ao adicionar telefone permanece visível', (
    tester,
  ) async {
    final f = CadastroFicticio();
    await f.iniciar();
    f.contatos = [f.item];
    addTearDown(f.dispose);
    final handler = f.cenario.substituir!;
    f.cenario.substituir = (request) =>
        request.url.path == '/contatos/c1/telefones'
        ? resposta(codigo: 409, corpo: '{"codigo":"duplicidade"}')
        : handler(request);
    await tester.pumpWidget(
      MaterialApp(home: PaginaCadastro(autenticacao: f.cenario.controlador)),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('José Fictício'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Adicionar telefone'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).at(1), '+442083661177');
    await tester.tap(find.text('Adicionar'));
    await tester.pumpAndSettle();
    expect(find.textContaining('já está cadastrado'), findsOneWidget);
    f.cenario.controlador.desconectar();
  });
  testWidgets(
    'área integrada remove importação e diálogos ao encerrar sessão',
    (tester) async {
      final f = CadastroFicticio();
      await f.iniciar();
      final handler = f.cenario.substituir!;
      f.cenario.substituir = (request) => request.url.path == '/auth/logout'
          ? resposta(codigo: 204, corpo: '')
          : handler(request);
      final conexao = ControladorConexao(
        cliente: ClienteHealth(
          endpoint: auth.contexto().endpoint,
          token: tokenTeste,
          transporte: TransporteSimulado((_) => resposta()),
        ),
      );
      await tester.pumpWidget(
        EdiskApp(controlador: conexao, autenticacao: f.cenario.controlador),
      );
      await tester.pumpAndSettle();
      expect(find.text('Novo contato'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('Importar arquivo'));
      await tester.pumpAndSettle();
      expect(find.text('Importar contatos'), findsOneWidget);
      await f.cenario.controlador.sair();
      await tester.pumpAndSettle();
      expect(find.text('Importar contatos'), findsNothing);
      expect(find.text('Entrar no eDisk'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    },
  );
  testWidgets('cadastro manual envia país explícito e atualiza consulta', (
    tester,
  ) async {
    final f = CadastroFicticio();
    await f.iniciar();
    addTearDown(f.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: PaginaCadastro(
          autenticacao: f.cenario.controlador,
          arquivos: f.arquivos,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Novo contato'));
    await tester.pumpAndSettle();
    final fields = find.byType(TextFormField);
    await tester.enterText(fields.at(0), 'José Fictício');
    await tester.enterText(fields.at(1), '+442083661177');
    await tester.tap(find.text('Salvar'));
    await tester.pumpAndSettle();
    final request = f.chamadas.firstWhere(
      (r) => r.method == 'POST' && r.url.path == '/contatos',
    ) as http.Request;
    expect(jsonDecode(request.body)['pais'], 'BR');
    expect(find.text('José Fictício'), findsOneWidget);
    expect(tester.takeException(), isNull);
    f.cenario.controlador.desconectar();
  });

  testWidgets('busca usa query codificada e detalhes mostram telefones', (
    tester,
  ) async {
    final f = CadastroFicticio();
    await f.iniciar();
    f.contatos = [f.item];
    addTearDown(f.dispose);
    await tester.pumpWidget(
      MaterialApp(home: PaginaCadastro(autenticacao: f.cenario.controlador)),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '+44 20');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    expect(f.chamadas.last.url.queryParameters['q'], '+44 20');
    expect(f.chamadas.last.headers['X-eDisk-Session'], auth.tokenUsuario);
    await tester.tap(find.text('José Fictício'));
    await tester.pumpAndSettle();
    expect(find.text('Adicionar telefone'), findsOneWidget);
    expect(find.text('Organizar listas'), findsOneWidget);
    expect(find.text('Exemplo'), findsOneWidget);
    f.cenario.controlador.desconectar();
  });

  testWidgets('importação completa mostra amostra, confirmação e relatório', (
    tester,
  ) async {
    final f = CadastroFicticio();
    await f.iniciar();
    addTearDown(f.dispose);
    tester.view.physicalSize = const Size(1200, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: PaginaImportacao(
          controlador: f.controlador,
          arquivos: f.arquivos,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Selecionar arquivo'));
    await tester.pumpAndSettle();
    expect(
      find.text('Criar lista com todos os contatos desta importacao'),
      findsOneWidget,
    );
    expect(
      f.chamadas.any(
        (r) => r.method == 'PUT' && r.url.path.endsWith('/arquivo'),
      ),
      isTrue,
    );
    await tester.tap(find.text('Ver primeiras linhas'));
    await tester.pumpAndSettle();
    expect(find.text('Coluna 1'), findsOneWidget);
    await tester.ensureVisible(find.text('Validar arquivo inteiro'));
    await tester.tap(find.text('Validar arquivo inteiro'));
    await tester.pumpAndSettle();
    final country = find.byType(TextField).last;
    await tester.enterText(country, 'GB');
    await tester.pump();
    expect(
      tester
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Confirmar alterações'),
          )
          .onPressed,
      isNull,
    );
    expect(find.textContaining('As opções mudaram.'), findsOneWidget);
    await tester.enterText(country, 'BR');
    await tester.pump();
    await tester.ensureVisible(find.text('Confirmar alterações'));
    await tester.tap(find.text('Confirmar alterações'));
    await tester.pumpAndSettle();
    expect(find.text('Confirmar importação?'), findsOneWidget);
    await tester.tap(
      find.widgetWithText(FilledButton, 'Confirmar alterações').last,
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('importação concluída'), findsOneWidget);
    expect(
      f.chamadas.where((r) => r.url.path.endsWith('/confirmacao')),
      hasLength(1),
    );
    await tester.ensureVisible(find.text('Salvar relatório CSV'));
    await tester.tap(find.text('Salvar relatório CSV'));
    await tester.pumpAndSettle();
    expect(f.arquivos.salvo, isNotNull);
    expect(find.text('Relatório salvo.'), findsOneWidget);
    f.cenario.controlador.desconectar();
  });

  test(
    'resposta tardia após logout não atualiza controlador de negócio',
    () async {
      final f = CadastroFicticio();
      await f.iniciar();
      addTearDown(f.dispose);
      final pending = Completer<http.StreamedResponse>();
      f.cenario.substituir = (request) => request.url.path == '/auth/logout'
          ? resposta(codigo: 204, corpo: '')
          : pending.future;
      final operation = f.controlador.requisitar('GET', '/contatos');
      await f.cenario.controlador.sair();
      pending.complete(resposta(corpo: '{"itens":[{"nome":"antigo"}]}'));
      expect(await operation, isNull);
      expect(f.cenario.controlador.estado, EstadoAutenticacao.login);
    },
  );

  test('401 de negócio invalida sessão e preserva erro sanitizado', () async {
    final f = CadastroFicticio();
    await f.iniciar();
    addTearDown(f.dispose);
    f.cenario.substituir = (_) => resposta(
      codigo: 401,
      corpo: '{"codigo":"sessao_invalida","detail":"privado"}',
    );
    expect(await f.controlador.requisitar('GET', '/contatos'), isNull);
    expect(f.cenario.controlador.estado, EstadoAutenticacao.login);
    expect(f.controlador.mensagem, isNot(contains('privado')));
  });

  testWidgets('texto ampliado e janela estreita mantêm cadastro utilizável', (
    tester,
  ) async {
    final f = CadastroFicticio();
    await f.iniciar();
    addTearDown(f.dispose);
    tester.view.physicalSize = const Size(640, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData(brightness: Brightness.dark),
        home: MediaQuery(
          data: const MediaQueryData(textScaler: TextScaler.linear(2)),
          child: PaginaCadastro(autenticacao: f.cenario.controlador),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    f.cenario.controlador.desconectar();
  });

  test('cancelamento do diálogo nativo não abre arquivo', () async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('edisk/file_dialogs'),
          (_) async => null,
        );
    addTearDown(
      () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(
            const MethodChannel('edisk/file_dialogs'),
            null,
          ),
    );
    expect(await ArquivosLocais().abrir(), isNull);
  });
}
