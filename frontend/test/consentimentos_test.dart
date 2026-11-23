import 'dart:convert';

import 'package:edisk/app.dart';
import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:edisk/cadastro/pagina_cadastro.dart';
import 'package:edisk/cadastro/pagina_consentimentos.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;
import 'cadastro_test.dart';

class ConsentimentoFicticio {
  final f = CadastroFicticio();
  Map<String, dynamic> escopo = {'id': 'e1', 'nome': 'Comunicação local'};
  bool autorizado = false, semEscopo = false;
  int codigoRegistro = 201;
  final registros = <Map<String, dynamic>>[];
  final paginas = <String>[];
  final identidadesAdicionais = <Map<String, dynamic>>[];
  List<Map<String, dynamic>> eventos = [];
  Map<String, dynamic> get estado => {
    'versao': 1,
    'bloqueio_global_em': null,
    'identidades': [
      {
        'id': 'id1',
        'telefone_id': 't1',
        'canal': 'whatsapp',
        'preferencias': [
          {
            'categoria': 'promocoes',
            'estado': autorizado ? 'autorizado' : 'desconhecido',
            'motivos': autorizado ? <String>[] : ['sem_autorizacao'],
          },
        ],
      },
      ...identidadesAdicionais,
    ],
  };
  Future<void> iniciar() async {
    await f.iniciar();
    final anterior = f.cenario.substituir!;
    f.cenario.substituir = (request) {
      if (request.url.path == '/contatos/escopos-comunicacao') {
        if (request.method == 'POST') {
          escopo = {
            ...escopo,
            'nome': jsonDecode((request as http.Request).body)['nome'],
          };
          semEscopo = false;
          return resposta(codigo: 201, corpo: jsonEncode(escopo));
        }
        return resposta(
          corpo: jsonEncode({
            'itens': semEscopo ? [] : [escopo],
          }),
        );
      }
      if (request.url.path.endsWith('/consentimentos')) {
        return resposta(corpo: jsonEncode(estado));
      }
      if (request.url.path.endsWith('/consentimentos/eventos')) {
        if (request.method == 'POST') {
          final body = Map<String, dynamic>.from(
            jsonDecode((request as http.Request).body) as Map,
          );
          registros.add(body);
          if (codigoRegistro == 201) autorizado = body['tipo'] == 'autorizou';
          return resposta(
            codigo: codigoRegistro,
            corpo: codigoRegistro == 409 ? '{"codigo":"conflito"}' : '{}',
          );
        }
        paginas.add(request.url.queryParameters['pagina'] ?? '');
        return resposta(corpo: jsonEncode({'itens': eventos, 'total': 11}));
      }
      return anterior(request);
    };
  }

  Future<void> abrir(WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: PaginaConsentimentos(controlador: f.controlador, contato: f.item),
      ),
    );
    await tester.pumpAndSettle();
  }
}

Future<void> escolher(WidgetTester tester, int index, String text) async {
  final field = find.byType(DropdownButtonFormField<String>).at(index);
  await tester.ensureVisible(field);
  await tester.tap(field);
  await tester.pumpAndSettle();
  await tester.tap(find.text(text).last);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('consulta desconhecido e histórico sem selecionar autorização', (
    tester,
  ) async {
    final f = ConsentimentoFicticio();
    await f.iniciar();
    addTearDown(f.f.dispose);
    await f.abrir(tester);
    expect(find.text('Promoções: desconhecido'), findsOneWidget);
    expect(find.text('Sem autorização válida'), findsOneWidget);
    expect(find.text('Existência no canal não verificada'), findsOneWidget);
    await tester.tap(find.text('Registrar alteração'));
    await tester.pumpAndSettle();
    final campos = find.byType(DropdownButtonFormField<String>);
    for (final element in campos.evaluate()) {
      expect((element as StatefulElement).state, isA<FormFieldState<String>>());
      expect(((element).state as FormFieldState<String>).value, isNull);
    }
    await tester.tap(find.text('Confirmar registro'));
    await tester.pumpAndSettle();
    expect(f.registros, isEmpty);
    expect(find.text('Preencha este campo.'), findsWidgets);
    f.f.cenario.controlador.desconectar();
  });

  testWidgets('autorização exige método e evidência e envia escopo e versão', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 1100);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final f = ConsentimentoFicticio();
    await f.iniciar();
    addTearDown(f.f.dispose);
    await f.abrir(tester);
    await tester.tap(find.text('Registrar alteração'));
    await tester.pumpAndSettle();
    await escolher(tester, 0, 'Registrar autorização');
    await escolher(tester, 1, '+442083661177');
    await escolher(tester, 2, 'Promoções');
    await tester.tap(find.text('Confirmar registro'));
    await tester.pumpAndSettle();
    expect(f.registros, isEmpty);
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Método de captura'),
      'Formulário assinado',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Referência verificável da evidência'),
      'arquivo:prova-1',
    );
    await tester.tap(find.text('Confirmar registro'));
    await tester.pumpAndSettle();
    expect(f.registros.single, containsPair('escopo_id', 'e1'));
    expect(f.registros.single, containsPair('versao', 1));
    expect(f.registros.single, containsPair('origem', 'operador'));
    expect(
      f.registros.single['operacao_id'],
      matches(RegExp(r'^[0-9a-f-]{36}$')),
    );
    expect(find.text('Promoções: autorizado'), findsOneWidget);
    f.f.cenario.controlador.desconectar();
  });

  testWidgets(
    'bloqueio global mostra conflito e reutiliza operação ao repetir',
    (tester) async {
      final f = ConsentimentoFicticio()..codigoRegistro = 409;
      await f.iniciar();
      addTearDown(f.f.dispose);
      await f.abrir(tester);
      await tester.tap(find.text('Registrar alteração'));
      await tester.pumpAndSettle();
      await escolher(tester, 0, 'Bloquear contato globalmente');
      expect(
        find.widgetWithText(
          DropdownButtonFormField<String>,
          'Identidade WhatsApp',
        ),
        findsNothing,
      );
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Justificativa'),
        'Pedido de saída',
      );
      await tester.tap(find.text('Confirmar registro'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Os dados mudaram'), findsWidgets);
      await tester.tap(find.text('Confirmar registro'));
      await tester.pumpAndSettle();
      expect(f.registros.length, 2);
      expect(f.registros[0], f.registros[1]);
      expect(f.registros[0]['identidade_contato_id'], isNull);
      expect(f.registros[0]['categoria'], isNull);
      f.f.cenario.controlador.desconectar();
    },
  );

  testWidgets('escopo é configurado na área avançada e histórico é paginado', (
    tester,
  ) async {
    final f = ConsentimentoFicticio()..semEscopo = true;
    await f.iniciar();
    addTearDown(f.f.dispose);
    f.f.contatos = [f.f.item];
    final conexao = ControladorConexao(
      cliente: ClienteHealth(
        endpoint: auth.contexto().endpoint,
        token: tokenTeste,
        transporte: TransporteSimulado((_) => resposta()),
      ),
    );
    await tester.pumpWidget(
      EdiskApp(controlador: conexao, autenticacao: f.f.cenario.controlador),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Configurações avançadas'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Definir escopo local de comunicação'));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Nome do escopo'),
      'Operação real',
    );
    await tester.tap(find.text('Definir escopo'));
    await tester.pumpAndSettle();
    expect(find.text('Operação real'), findsOneWidget);
    await tester.tap(find.byTooltip('Contatos e listas'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('José Fictício'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Consentimento'));
    await tester.pumpAndSettle();
    expect(find.text('Escopo: Operação real'), findsOneWidget);
    await tester.ensureVisible(find.text('Próximos'));
    await tester.tap(find.text('Próximos'));
    await tester.pumpAndSettle();
    expect(f.paginas.last, '2');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('expiração na mutação invalida sessão e mostra erro', (
    tester,
  ) async {
    final f = ConsentimentoFicticio()..codigoRegistro = 401;
    await f.iniciar();
    addTearDown(f.f.dispose);
    await f.abrir(tester);
    await tester.tap(find.text('Registrar alteração'));
    await tester.pumpAndSettle();
    await escolher(tester, 0, 'Bloquear contato globalmente');
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Justificativa'),
      'Pedido de saída',
    );
    await tester.tap(find.text('Confirmar registro'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Entre novamente'), findsWidgets);
    expect(f.registros.length, 1);
    f.f.cenario.controlador.desconectar();
  });

  testWidgets('filtro de cadastro envia escopo categoria e estado', (
    tester,
  ) async {
    final f = ConsentimentoFicticio();
    await f.iniciar();
    addTearDown(f.f.dispose);
    await tester.pumpWidget(
      MaterialApp(home: PaginaCadastro(autenticacao: f.f.cenario.controlador)),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Filtrar consentimento'));
    await tester.pumpAndSettle();
    await escolher(tester, 0, 'Promoções');
    await escolher(tester, 1, 'bloqueado');
    await tester.tap(find.text('Aplicar filtro'));
    await tester.pumpAndSettle();
    final request = f.f.chamadas.lastWhere((r) => r.url.path == '/contatos');
    expect(request.url.queryParameters, containsPair('escopo_id', 'e1'));
    expect(request.url.queryParameters, containsPair('categoria', 'promocoes'));
    expect(
      request.url.queryParameters,
      containsPair('consentimento', 'bloqueado'),
    );
    f.f.cenario.controlador.desconectar();
  });

  testWidgets('consentimento permanece utilizável com texto ampliado', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(420, 740);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final f = ConsentimentoFicticio();
    await f.iniciar();
    addTearDown(f.f.dispose);
    await tester.pumpWidget(
      MaterialApp(
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: const TextScaler.linear(1.5)),
          child: child!,
        ),
        home: PaginaConsentimentos(
          controlador: f.f.controlador,
          contato: f.f.item,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.scrollUntilVisible(find.text('Registrar alteração'), 200);
    await tester.tap(find.text('Registrar alteração'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    f.f.cenario.controlador.desconectar();
  });

  testWidgets('logout remove a tela e o formulário de consentimento', (
    tester,
  ) async {
    final f = ConsentimentoFicticio();
    await f.iniciar();
    addTearDown(f.f.dispose);
    f.f.contatos = [f.f.item];
    final anterior = f.f.cenario.substituir!;
    f.f.cenario.substituir = (r) => r.url.path == '/auth/logout'
        ? resposta(codigo: 204, corpo: '')
        : anterior(r);
    final conexao = ControladorConexao(
      cliente: ClienteHealth(
        endpoint: auth.contexto().endpoint,
        token: tokenTeste,
        transporte: TransporteSimulado((_) => resposta()),
      ),
    );
    await tester.pumpWidget(
      EdiskApp(controlador: conexao, autenticacao: f.f.cenario.controlador),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('José Fictício'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Consentimento'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Registrar alteração'));
    await tester.pumpAndSettle();
    await f.f.cenario.controlador.sair();
    await tester.pumpAndSettle();
    expect(find.text('Consentimento e comunicação'), findsNothing);
    expect(find.text('Confirmar registro'), findsNothing);
    expect(find.text('Entrar no eDisk'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
    'histórico distingue telefones arquivados e eventos globais sem consultas extras',
    (tester) async {
      tester.view.physicalSize = const Size(1200, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final f = ConsentimentoFicticio();
      await f.iniciar();
      addTearDown(f.f.dispose);
      f.identidadesAdicionais.add({
        'id': 'id2',
        'telefone_id': 't2',
        'canal': 'whatsapp',
        'telefone_ativo': false,
        'preferencias': <dynamic>[],
      });
      f.eventos = [
        for (final pair in [
          ('ev1', 'id1'),
          ('ev2', 'id2'),
          ('ev3', 'nao-carregada'),
        ])
          {
            'id': pair.$1,
            'identidade_contato_id': pair.$2,
            'tipo': 'revogou',
            'categoria': 'promocoes',
            'ocorrido_em': '2026-01-01T12:00:00Z',
            'usuario_id': 'u1',
            'justificativa': 'Pedido registrado',
          },
        {
          'id': 'global',
          'identidade_contato_id': null,
          'tipo': 'bloqueio_global',
          'categoria': null,
          'ocorrido_em': '2026-01-01T12:01:00Z',
          'usuario_id': 'u1',
        },
      ];
      final contato = {
        ...f.f.item,
        'telefones': [
          ...(f.f.item['telefones'] as List),
          {
            'id': 't2',
            'telefone_e164': '+5511912345678',
            'ativo': false,
            'versao': 2,
          },
        ],
      };
      final paths = <String>[];
      final anterior = f.f.cenario.substituir!;
      f.f.cenario.substituir = (r) {
        paths.add(r.url.path);
        return anterior(r);
      };
      await tester.pumpWidget(
        MaterialApp(
          home: PaginaConsentimentos(
            controlador: f.f.controlador,
            contato: contato,
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('evento:ev1')),
          matching: find.textContaining('WhatsApp — +442083661177'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('evento:ev2')),
          matching: find.textContaining(
            'WhatsApp — +5511912345678 (arquivado)',
          ),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('evento:ev3')),
          matching: find.textContaining(
            'Identidade nao-carregada — dados indisponíveis',
          ),
        ),
        findsOneWidget,
      );
      final global = find.byKey(const ValueKey('evento:global'));
      expect(
        find.descendant(
          of: global,
          matching: find.textContaining('Contato global — todos os canais'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(of: global, matching: find.textContaining('+55')),
        findsNothing,
      );
      expect(paths, [
        '/contatos/escopos-comunicacao',
        '/contatos/c1/consentimentos',
        '/contatos/c1/consentimentos/eventos',
      ]);
      expect(tester.takeException(), isNull);
      f.f.cenario.controlador.desconectar();
    },
  );
}
