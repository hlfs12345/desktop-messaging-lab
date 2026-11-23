import 'dart:convert';

import 'package:edisk/app.dart';
import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:edisk/templates/controlador_templates.dart';
import 'package:edisk/templates/pagina_templates.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;
import 'cadastro_test.dart';

class TemplatesFicticios {
  final f = CadastroFicticio();
  final chamadas = <http.BaseRequest>[];
  final corpos = <Map<String, dynamic>>[];
  int codigoMutacao = 201;
  int total = 1, totalVersoes = 1;
  Map<String, dynamic> item = {
    'id': 'tm1',
    'nome': 'Aviso local',
    'descricao': 'Descrição',
    'categoria': 'promocoes',
    'ativo': true,
    'versao': 1,
  };
  List<Map<String, dynamic>> versoes = [
    {
      'id': 'v1',
      'plataforma': 'whatsapp',
      'numero_versao': 1,
      'conteudo': 'Olá, {{nome}}!',
      'criado_em': '2026-11-01T12:00:00Z',
    },
  ];

  Future<void> iniciar() async {
    await f.iniciar();
    final anterior = f.cenario.substituir!;
    f.cenario.substituir = (r) {
      if (r.url.path == '/auth/logout') return resposta(codigo: 204, corpo: '');
      if (!r.url.path.startsWith('/templates')) return anterior(r);
      chamadas.add(r);
      final path = r.url.path;
      Map<String, dynamic> body = {};
      if (r.method != 'GET') {
        body = Map<String, dynamic>.from(
          jsonDecode((r as http.Request).body) as Map,
        );
        corpos.add(body);
      }
      if (path == '/templates/previa') {
        final values = body['valores'] as Map;
        if (values['nome'] == null) {
          return resposta(codigo: 422, corpo: '{"codigo":"valor_ausente"}');
        }
        return resposta(
          corpo: jsonEncode({
            'conteudo_renderizado': (body['conteudo'] as String).replaceAll(
              '{{nome}}',
              values['nome'] as String,
            ),
            'validacao': 'somente_conteudo_local',
          }),
        );
      }
      if (r.method != 'GET') {
        if (codigoMutacao >= 400) {
          return resposta(
            codigo: codigoMutacao,
            corpo: '{"codigo":"conflito"}',
          );
        }
        if (path == '/templates') {
          item = {
            ...item,
            'nome': body['nome'],
            'descricao': body['descricao'],
            'categoria': body['categoria'],
          };
        } else if (r.method == 'PATCH') {
          item = {
            ...item,
            if (body.containsKey('ativo')) 'ativo': body['ativo'],
            if (body.containsKey('nome')) 'nome': body['nome'],
            'versao': (item['versao'] as int) + 1,
          };
        } else if (path.endsWith('/versoes')) {
          versoes = [
            {
              'id': 'v2',
              'plataforma': body['plataforma'],
              'numero_versao': 2,
              'conteudo': body['conteudo'],
              'criado_em': '2026-11-01T12:30:00Z',
            },
            ...versoes,
          ];
          totalVersoes++;
        }
        return resposta(
          codigo: r.method == 'PATCH' ? 200 : 201,
          corpo: jsonEncode({'id': 'tm1', 'versao': item['versao']}),
        );
      }
      if (path == '/templates') {
        final archived = r.url.queryParameters['ativo'] == 'false';
        return resposta(
          corpo: jsonEncode({
            'itens': item['ativo'] == !archived ? [item] : [],
            'total': total,
          }),
        );
      }
      if (path.endsWith('/versoes')) {
        return resposta(
          corpo: jsonEncode({'itens': versoes, 'total': totalVersoes}),
        );
      }
      return resposta(corpo: jsonEncode(item));
    };
  }

  Future<void> abrir(WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(home: PaginaTemplates(autenticacao: f.cenario.controlador)),
    );
    await tester.pumpAndSettle();
  }
}

Future<void> tocar(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<void> selecionar(WidgetTester tester, String label, String value) async {
  await tocar(
    tester,
    find.widgetWithText(DropdownButtonFormField<String>, label),
  );
  await tocar(tester, find.text(value).last);
}

void main() {
  test(
    'repetição de formulário mantém UUID e alterações produzem outra operação',
    () {
      final operation = OperacaoTemplate();
      final first = operation.corpo({'conteudo': 'Um'});
      expect(operation.corpo({'conteudo': 'Um'}), first);
      expect(
        operation.corpo({'conteudo': 'Dois'})['operacao_id'],
        isNot(first['operacao_id']),
      );
      expect(
        first['operacao_id'],
        matches(
          RegExp(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
          ),
        ),
      );
    },
  );

  testWidgets(
    'cria template e solicita prévia com valor exclusivamente manual',
    (tester) async {
      final t = TemplatesFicticios();
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      await tocar(tester, find.text('Novo template'));
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Nome do template'),
        'Template novo',
      );
      await selecionar(tester, 'Categoria', 'Promoções');
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Conteúdo'),
        'Olá {{nome}}',
      );
      await tocar(tester, find.text('Gerar prévia'));
      expect(
        find.text('Informe um nome fictício para a prévia.'),
        findsOneWidget,
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Nome fictício para prévia'),
        'Pessoa fictícia',
      );
      await tocar(tester, find.text('Gerar prévia'));
      expect(find.text('Olá Pessoa fictícia'), findsOneWidget);
      expect(find.text(avisoTemplate), findsOneWidget);
      await tocar(tester, find.text('Salvar versão'));
      final saved = t.corpos.last;
      expect(saved['plataforma'], 'whatsapp');
      expect(saved['conteudo'], 'Olá {{nome}}');
      expect(saved.containsKey('valores'), isFalse);
      expect(find.text('Template novo'), findsOneWidget);
      expect(t.f.chamadas, isEmpty);
      expect(tester.takeException(), isNull);
      t.f.cenario.controlador.desconectar();
    },
  );

  testWidgets(
    'nova versão Telegram preserva consulta da versão antiga sem editor',
    (tester) async {
      final t = TemplatesFicticios();
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      await tocar(tester, find.text('Aviso local'));
      await tocar(tester, find.text('WhatsApp • versão 1'));
      expect(find.text('Olá, {{nome}}!'), findsOneWidget);
      expect(find.widgetWithText(TextFormField, 'Conteúdo'), findsNothing);
      await tocar(tester, find.text('Nova versão'));
      await selecionar(tester, 'Plataforma', 'Telegram');
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Conteúdo'),
        'Texto Telegram',
      );
      await tocar(tester, find.text('Salvar versão'));
      expect(t.corpos.last['plataforma'], 'telegram');
      expect(t.versoes.last['conteudo'], 'Olá, {{nome}}!');
      expect(find.text('WhatsApp • versão 1'), findsOneWidget);
      expect(tester.takeException(), isNull);
      t.f.cenario.controlador.desconectar();
    },
  );

  testWidgets('pesquisa filtros e paginação enviam parâmetros à API', (
    tester,
  ) async {
    final t = TemplatesFicticios()..total = 25;
    await t.iniciar();
    addTearDown(t.f.dispose);
    await t.abrir(tester);
    await tester.enterText(
      find.widgetWithText(TextField, 'Buscar template'),
      'promo',
    );
    await selecionar(tester, 'Filtrar categoria', 'Promoções');
    await selecionar(tester, 'Filtrar plataforma', 'Telegram');
    await tocar(tester, find.text('Pesquisar'));
    final query = t.chamadas.last.url.queryParameters;
    expect(query['q'], 'promo');
    expect(query['categoria'], 'promocoes');
    expect(query['plataforma'], 'telegram');
    await tocar(tester, find.text('Próxima'));
    expect(t.chamadas.last.url.queryParameters['pagina'], '2');
    await tocar(tester, find.text('Arquivados'));
    await tocar(tester, find.text('Pesquisar'));
    expect(t.chamadas.last.url.queryParameters['ativo'], 'false');
    expect(t.chamadas.last.url.queryParameters['pagina'], '1');
    t.f.cenario.controlador.desconectar();
  });

  testWidgets(
    'arquivamento preserva histórico e bloqueia botão de nova versão',
    (tester) async {
      final t = TemplatesFicticios();
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      await tocar(tester, find.text('Aviso local'));
      await tocar(tester, find.text('Arquivar'));
      await tocar(tester, find.text('Confirmar arquivamento'));
      expect(t.item['ativo'], isFalse);
      expect(t.versoes.length, 1);
      t.f.cenario.controlador.desconectar();
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Nova versão'),
            )
            .onPressed,
        isNull,
      );
      expect(find.text('WhatsApp • versão 1'), findsOneWidget);
    },
  );

  testWidgets(
    'conflito mantém editor e repetir mesmos dados reutiliza operação',
    (tester) async {
      final t = TemplatesFicticios()..codigoMutacao = 409;
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      await tocar(tester, find.text('Aviso local'));
      await tocar(tester, find.text('Nova versão'));
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Conteúdo'),
        'Versão pendente',
      );
      await tocar(tester, find.text('Salvar versão'));
      expect(find.textContaining('Os dados mudaram'), findsOneWidget);
      final first = t.corpos.last;
      await tocar(tester, find.text('Salvar versão'));
      expect(t.corpos.last, first);
      expect(find.text('Nova versão'), findsOneWidget);
      t.f.cenario.controlador.desconectar();
    },
  );

  testWidgets(
    'histórico pagina por plataforma e edição mantém categoria fixa',
    (tester) async {
      final t = TemplatesFicticios()..totalVersoes = 11;
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      await tocar(tester, find.text('Aviso local'));
      await selecionar(tester, 'Plataforma do histórico', 'Telegram');
      expect(t.chamadas.last.url.queryParameters['plataforma'], 'telegram');
      await tocar(tester, find.text('Próxima'));
      expect(t.chamadas.last.url.queryParameters['pagina'], '2');
      await tocar(tester, find.text('Editar cadastro'));
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Nome do template'),
        'Nome atualizado',
      );
      await tocar(tester, find.text('Salvar cadastro'));
      expect(t.corpos.last['nome'], 'Nome atualizado');
      expect(t.corpos.last.containsKey('categoria'), isFalse);
      expect(t.item['categoria'], 'promocoes');
      expect(tester.takeException(), isNull);
      t.f.cenario.controlador.desconectar();
    },
  );

  for (final expire in [false, true]) {
    testWidgets(
      'navegação autenticada remove catálogo e editor ao ${expire ? 'expirar' : 'sair'}',
      (tester) async {
        final t = TemplatesFicticios();
        await t.iniciar();
        addTearDown(t.f.dispose);
        final connection = ControladorConexao(
          cliente: ClienteHealth(
            endpoint: auth.contexto().endpoint,
            token: tokenTeste,
            transporte: TransporteSimulado((_) => resposta()),
          ),
        );
        await tester.pumpWidget(
          EdiskApp(
            controlador: connection,
            autenticacao: t.f.cenario.controlador,
          ),
        );
        await tester.pumpAndSettle();
        await tocar(tester, find.byTooltip('Templates'));
        await tocar(tester, find.text('Novo template'));
        if (expire) {
          final previous = t.f.cenario.substituir!;
          t.f.cenario.substituir = (r) => r.url.path == '/templates/previa'
              ? resposta(codigo: 401, corpo: '{"codigo":"sessao_invalida"}')
              : previous(r);
          await tester.enterText(
            find.widgetWithText(TextFormField, 'Conteúdo'),
            'Olá {{nome}}',
          );
          await tocar(tester, find.text('Gerar prévia'));
        } else {
          await t.f.cenario.controlador.sair();
          await tester.pumpAndSettle();
        }
        expect(find.text('Entrar no eDisk'), findsOneWidget);
        expect(find.text('Salvar versão'), findsNothing);
        expect(find.byType(PaginaTemplates), findsNothing);
        expect(tester.takeException(), isNull);
        t.f.cenario.controlador.desconectar();
        await tester.pumpWidget(const SizedBox());
      },
    );
  }
}
