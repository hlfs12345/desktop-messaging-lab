import 'dart:convert';

import 'package:edisk/app.dart';
import 'package:edisk/campanhas/controlador_campanhas.dart';
import 'package:edisk/campanhas/editor_campanha.dart';
import 'package:edisk/campanhas/pagina_campanhas.dart';
import 'package:edisk/campanhas/revisao_campanha.dart';
import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;
import 'cadastro_test.dart';

final cenariosAtivos = <CampanhasFicticias>[];

class CampanhasFicticias {
  final f = CadastroFicticio();
  final chamadas = <http.BaseRequest>[];
  final corpos = <Map<String, dynamic>>[];
  bool conflito = false,
      selecionada = false,
      candidata = true,
      bloqueada = false,
      telegram = false;
  final selecionadasTelegram = <String>{};
  int total = 1;
  int codigoPrevia = 200;
  String? erroPrevia;
  bool falharRevisao = false;
  Map<String, dynamic> conta = {
    'id': 'a1',
    'nome': 'Conta local',
    'plataforma': 'whatsapp',
    'provedor': 'qr_candidato',
    'ativo': true,
  };
  Map<String, dynamic> escopo = {'id': 'e1', 'nome': 'Escopo explícito'};
  Map<String, dynamic> template = {
    'id': 'v1',
    'nome': 'Texto local',
    'categoria': 'promocoes',
    'plataforma': 'whatsapp',
    'numero_versao': 1,
    'ativo': true,
  };
  Map<String, dynamic> lista = {
    'id': 'l1',
    'nome': 'Lista local',
    'ativo': true,
  };
  Map<String, dynamic> item = {
    'id': 'cp1',
    'nome': 'Campanha salva',
    'status': 'rascunho',
    'versao': 1,
    'conta_envio_id': 'a1',
    'escopo_id': 'e1',
    'template_versao_id': 'v1',
    'categoria': 'promocoes',
    'listas': ['l1'],
  };
  List<String> problemas = [];

  Future<void> iniciar() async {
    cenariosAtivos.add(this);
    await f.iniciar();
    final previous = f.cenario.substituir!;
    f.cenario.substituir = (r) {
      if (r.url.path == '/auth/logout') return resposta(codigo: 204, corpo: '');
      if (!r.url.path.startsWith('/campanhas')) return previous(r);
      chamadas.add(r);
      final path = r.url.path;
      Map<String, dynamic> body = {};
      if (r.method != 'GET') {
        body = Map<String, dynamic>.from(
          jsonDecode((r as http.Request).body) as Map,
        );
        corpos.add(body);
      }
      if (path == '/campanhas/opcoes') {
        final tipo = r.url.queryParameters['tipo'];
        final rows = switch (tipo) {
          'contas' => [conta],
          'escopos' => [escopo],
          'versoes' => [template],
          _ => [lista],
        };
        return resposta(
          corpo: jsonEncode({
            'itens': rows,
            'total': tipo == 'escopos' ? 1 : 21,
          }),
        );
      }
      if (path.endsWith('/previa')) {
        if (codigoPrevia >= 400) {
          return resposta(
            codigo: codigoPrevia,
            corpo: jsonEncode({'codigo': erroPrevia}),
          );
        }
        return resposta(
          corpo: jsonEncode({
            'conteudo_renderizado': 'Olá, ${body['nome']}!',
            'transitoria': true,
          }),
        );
      }
      if (r.method != 'GET') {
        if (conflito) {
          return resposta(codigo: 409, corpo: '{"codigo":"conflito"}');
        }
        if (path.endsWith('/selecoes-elegiveis')) {
          if (telegram) selecionadasTelegram.addAll(['it1', 'it2']);
          item['versao'] = (item['versao'] as int) + 1;
          return resposta(
            corpo: jsonEncode({
              'id': 'cp1',
              'versao': item['versao'],
              'selecionados': telegram ? 2 : 0,
            }),
          );
        } else if (path.endsWith('/selecoes')) {
          if (telegram) {
            final id = body['identidade_telegram_id'] as String;
            if (body['selecionada'] == true) {
              selecionadasTelegram.add(id);
            } else {
              selecionadasTelegram.remove(id);
            }
          } else {
            selecionada = body['selecionada'] as bool;
          }
        } else {
          item = {...item, ...body};
        }
        item['versao'] = (item['versao'] as int) + 1;
        return resposta(
          codigo: r.method == 'POST' ? 201 : 200,
          corpo: jsonEncode({'id': 'cp1', 'versao': item['versao']}),
        );
      }
      if (path == '/campanhas') {
        return resposta(
          corpo: jsonEncode({
            'itens': [item],
            'total': total,
          }),
        );
      }
      final refs = {
        'conta': conta,
        'escopo': escopo,
        'template': template,
        'listas': (item['listas'] as List).isEmpty ? [] : [lista],
        'problemas': problemas,
      };
      if (path.endsWith('/revisao')) {
        if (falharRevisao) {
          return resposta(
            codigo: 503,
            corpo: '{"codigo":"persistencia_indisponivel"}',
          );
        }
        final rows = telegram
            ? [
                for (final id in ['it1', 'it2'])
                  {
                    'identidade_contato_id': null,
                    'identidade_telegram_id': id,
                    'inscricao_id': 'ins-$id',
                    'contato_id': 'c-$id',
                    'nome': 'Pessoa $id',
                    'telefone': null,
                    'destino_mascarado': id == 'it1' ? '***1111' : '***2222',
                    'canal': 'telegram',
                    'selecionada': selecionadasTelegram.contains(id),
                    'pertence_listas': true,
                    'candidata_atual': true,
                    'consentimento': {
                      'estado': 'autorizado',
                      'elegivel_consentimento': true,
                      'motivos': <String>[],
                    },
                  },
              ]
            : (!candidata && !selecionada)
            ? []
            : [
                {
                  'identidade_contato_id': 'ic1',
                  'contato_id': 'c1',
                  'nome': 'Pessoa fictícia',
                  'telefone': '+442083661177',
                  'canal': 'whatsapp',
                  'identidades_do_contato': 2,
                  'selecionada': selecionada,
                  'pertence_listas': candidata,
                  'candidata_atual': candidata,
                  'consentimento': {
                    'estado': bloqueada ? 'bloqueado' : 'desconhecido',
                    'elegivel_consentimento': false,
                    'motivos': [
                      bloqueada ? 'bloqueio_global' : 'sem_autorizacao',
                    ],
                  },
                },
              ];
        return resposta(
          corpo: jsonEncode({
            'itens': rows,
            'total': rows.length,
            'versao': item['versao'],
            'referencias': refs,
          }),
        );
      }
      return resposta(corpo: jsonEncode({...item, 'referencias': refs}));
    };
  }

  Future<void> abrir(WidgetTester tester, {bool revisao = false}) async {
    await tester.pumpWidget(
      MaterialApp(
        home: revisao
            ? RevisaoCampanha(autenticacao: f.cenario.controlador, id: 'cp1')
            : PaginaCampanhas(autenticacao: f.cenario.controlador),
      ),
    );
    await tester.pumpAndSettle();
  }
}

Future<void> tocar(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<void> escolher(WidgetTester tester, String titulo, String opcao) async {
  await tocar(
    tester,
    find.descendant(
      of: find.widgetWithText(ListTile, titulo),
      matching: find.text('Escolher'),
    ),
  );
  await tocar(tester, find.textContaining(opcao).first);
}

void testeComSessao(String descricao, WidgetTesterCallback corpo) {
  testWidgets(descricao, (tester) async {
    await tester.binding.setSurfaceSize(const Size(1000, 1400));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    try {
      await corpo(tester);
    } finally {
      for (final cenario in cenariosAtivos) {
        cenario.f.cenario.controlador.desconectar();
      }
      cenariosAtivos.clear();
      await tester.pumpWidget(const SizedBox());
    }
  });
}

void main() {
  test('idempotência mantém UUID com dados iguais e troca em alterações', () {
    final op = OperacaoCampanha();
    final first = op.corpo({'versao': 1, 'nome': 'Exemplo'});
    expect(op.corpo({'versao': 1, 'nome': 'Exemplo'}), first);
    expect(
      op.corpo({'versao': 2, 'nome': 'Exemplo'})['operacao_id'],
      isNot(first['operacao_id']),
    );
  });

  testeComSessao('editor usa escopo automático, listas e reabre', (
    tester,
  ) async {
    final t = CampanhasFicticias();
    await t.iniciar();
    addTearDown(t.f.dispose);
    await t.abrir(tester);
    expect(find.text(avisoCampanha), findsOneWidget);
    await tocar(tester, find.text('Novo rascunho'));
    await tocar(tester, find.text('Salvar rascunho'));
    expect(find.text('Informe o nome.'), findsOneWidget);
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Nome da campanha'),
      'Rascunho novo',
    );
    await escolher(tester, 'Conta', 'Conta local');
    expect(find.text('Escopo de comunicação'), findsNothing);
    await escolher(tester, 'Versão do template', 'Texto local');
    await tocar(tester, find.text('Associar lista'));
    await tocar(tester, find.text('Lista local'));
    await tocar(tester, find.text('Salvar rascunho'));
    final body = t.corpos.single;
    expect(body['nome'], 'Rascunho novo');
    expect(body['escopo_id'], 'e1');
    expect(body['template_versao_id'], 'v1');
    expect(body['listas'], ['l1']);
    expect(body.containsKey('status'), isFalse);
    expect(find.byType(RevisaoCampanha), findsOneWidget);
    expect(find.text('Continuar para o resumo final'), findsOneWidget);
    expect(find.text('Revisar destinatários'), findsOneWidget);
    expect(find.text('Enviar'), findsNothing);
  });

  testeComSessao(
    'revisão seleciona individualmente, atualiza bloqueios e limpa prévia',
    (tester) async {
      final t = CampanhasFicticias();
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester, revisao: true);
      expect(find.text('Sem autorização registrada'), findsOneWidget);
      expect(find.textContaining('2 identidade(s)'), findsOneWidget);
      expect(
        tester.widget<CheckboxListTile>(find.byType(CheckboxListTile)).value,
        isFalse,
      );
      await tocar(tester, find.byType(CheckboxListTile));
      expect(t.corpos.last['identidade_contato_id'], 'ic1');
      expect(t.corpos.last['selecionada'], isTrue);
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Nome fictício para prévia'),
        'Fictício',
      );
      await tocar(tester, find.text('Consultar prévia'));
      expect(find.text('Olá, Fictício!'), findsOneWidget);
      t.bloqueada = true;
      await tocar(tester, find.byTooltip('Atualizar revisão'));
      expect(find.text('Olá, Fictício!'), findsNothing);
      expect(find.text('Bloqueio global do contato'), findsOneWidget);
      expect(
        tester.widget<CheckboxListTile>(find.byType(CheckboxListTile)).value,
        isTrue,
      );
    },
  );

  testeComSessao('seleção fora das listas é visível e pode ser desmarcada', (
    tester,
  ) async {
    final t = CampanhasFicticias()
      ..selecionada = true
      ..candidata = false;
    await t.iniciar();
    addTearDown(t.f.dispose);
    await t.abrir(tester, revisao: true);
    expect(
      find.text('Fora das listas atuais. Desmarque ou revise as listas.'),
      findsOneWidget,
    );
    await tocar(tester, find.byType(CheckboxListTile));
    expect(t.corpos.last['selecionada'], isFalse);
    expect(find.byType(CheckboxListTile), findsNothing);
  });

  testeComSessao('telegram seleciona e revisa multiplos sem iniciar envio', (
    tester,
  ) async {
    final t = CampanhasFicticias()
      ..telegram = true
      ..conta = {
        'id': 'a1',
        'nome': 'Bot Loja',
        'plataforma': 'telegram',
        'provedor': 'telegram_bot_api',
        'ativo': true,
      };
    await t.iniciar();
    addTearDown(t.f.dispose);
    await t.abrir(tester, revisao: true);
    expect(
      find.text('Selecione um ou mais destinatários Telegram.'),
      findsOneWidget,
    );
    expect(find.byType(CheckboxListTile), findsNWidgets(2));
    await tocar(tester, find.text('Todos os contatos elegiveis'));
    expect(t.selecionadasTelegram, {'it1', 'it2'});
    expect(
      tester
          .widgetList<CheckboxListTile>(find.byType(CheckboxListTile))
          .every((item) => item.value == true),
      isTrue,
    );
    expect(
      t.chamadas.any((request) => request.url.path.endsWith('/envio-telegram')),
      isFalse,
    );
  });

  testeComSessao('conflito preserva editor e exige recarregar revisão', (
    tester,
  ) async {
    final t = CampanhasFicticias();
    await t.iniciar();
    addTearDown(t.f.dispose);
    await t.abrir(tester);
    await tocar(tester, find.text('Campanha salva'));
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Nome da campanha'),
      'Edição local',
    );
    t.conflito = true;
    await tocar(tester, find.text('Salvar rascunho'));
    expect(find.textContaining('Os dados mudaram'), findsOneWidget);
    expect(find.widgetWithText(TextFormField, 'Edição local'), findsOneWidget);
    t.item['versao'] = 3;
    t.conflito = false;
    await tocar(tester, find.text('Recarregar dados salvos'));
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Nome da campanha'),
      'Edição nova',
    );
    await tocar(tester, find.text('Salvar rascunho'));
    expect(t.corpos.last['versao'], 3);
  });

  testeComSessao(
    'referências inativas e paginação aparecem sem gerenciar credenciais',
    (tester) async {
      final t = CampanhasFicticias()..total = 21;
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      await tocar(tester, find.text('Próxima'));
      expect(t.chamadas.last.url.queryParameters['pagina'], '2');
      await tocar(tester, find.text('Campanha salva'));
      t.problemas = ['conta_inativa'];
      await tocar(tester, find.text('Recarregar dados salvos'));
      expect(find.textContaining('conta_inativa'), findsOneWidget);
      await escolher(tester, 'Conta', 'Conta local');
      expect(
        t.chamadas.where((r) => r.url.path.startsWith('/contas-envio')),
        isEmpty,
      );
    },
  );

  for (final (status, codigo) in const [
    (409, 'conflito'),
    (409, 'referencia_invalida'),
    (422, 'referencia_invalida'),
  ]) {
    testeComSessao('prévia $status/$codigo invalida revisão até nova consulta', (
      tester,
    ) async {
      final t = CampanhasFicticias()..selecionada = true;
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester, revisao: true);
      final controle = find.byType(CheckboxListTile);
      final botaoPrevia = find.widgetWithText(
        OutlinedButton,
        'Consultar prévia',
      );
      expect(tester.widget<CheckboxListTile>(controle).onChanged, isNotNull);
      expect(tester.widget<CheckboxListTile>(controle).value, isTrue);
      expect(
        find.text('Consulta desatualizada. Atualize a revisão.'),
        findsNothing,
      );
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Nome fictício para prévia'),
        'Fictício',
      );

      t.codigoPrevia = status;
      t.erroPrevia = codigo;
      if (codigo == 'referencia_invalida') t.problemas = ['conta_inativa'];
      await tocar(tester, find.text('Consultar prévia'));
      expect(
        find.text('Consulta desatualizada. Atualize a revisão.'),
        findsOneWidget,
      );
      expect(tester.widget<CheckboxListTile>(controle).onChanged, isNull);
      expect(tester.widget<CheckboxListTile>(controle).value, isTrue);
      expect(tester.widget<OutlinedButton>(botaoPrevia).onPressed, isNull);
      expect(t.selecionada, isTrue);
      expect(t.chamadas.where((r) => r.method == 'PUT'), isEmpty);
      expect(
        t.chamadas.where((r) => r.url.path.endsWith('/revisao')).length,
        1,
      );

      // Uma tentativa de recarga que falha não reabilita as operações.
      t.falharRevisao = true;
      await tocar(tester, find.byTooltip('Atualizar revisão'));
      expect(tester.widget<CheckboxListTile>(controle).onChanged, isNull);
      expect(tester.widget<OutlinedButton>(botaoPrevia).onPressed, isNull);

      // Após reparar a referência e consultar com sucesso, preservar a escolha.
      t.falharRevisao = false;
      t.problemas = [];
      t.codigoPrevia = 200;
      t.erroPrevia = null;
      await tocar(tester, find.byTooltip('Atualizar revisão'));
      expect(
        find.text('Consulta desatualizada. Atualize a revisão.'),
        findsNothing,
      );
      expect(tester.widget<CheckboxListTile>(controle).onChanged, isNotNull);
      expect(tester.widget<CheckboxListTile>(controle).value, isTrue);
      expect(tester.widget<OutlinedButton>(botaoPrevia).onPressed, isNotNull);
      expect(t.chamadas.where((r) => r.method == 'PUT'), isEmpty);
      await tocar(tester, find.text('Consultar prévia'));
      expect(find.text('Olá, Fictício!'), findsOneWidget);
    });
  }

  testeComSessao('prévia com validação 422 preserva revisão válida', (
    tester,
  ) async {
    final t = CampanhasFicticias();
    await t.iniciar();
    addTearDown(t.f.dispose);
    await t.abrir(tester, revisao: true);
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Nome fictício para prévia'),
      'Fictício',
    );
    t.codigoPrevia = 422;
    t.erroPrevia = 'validacao';
    await tocar(tester, find.text('Consultar prévia'));
    expect(
      find.text('Consulta desatualizada. Atualize a revisão.'),
      findsNothing,
    );
    expect(
      tester.widget<CheckboxListTile>(find.byType(CheckboxListTile)).onChanged,
      isNotNull,
    );
    expect(t.chamadas.where((r) => r.method == 'PUT'), isEmpty);
  });

  for (final expirar in [false, true]) {
    testeComSessao(
      'sessão remove editor e revisão ao ${expirar ? 'expirar' : 'sair'}',
      (tester) async {
        final t = CampanhasFicticias();
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
        await tocar(tester, find.byTooltip('Campanhas'));
        await tocar(tester, find.text('Campanha salva'));
        await tocar(tester, find.text('Revisar destinatários'));
        final controller = tester
            .widget<TextFormField>(
              find.widgetWithText(TextFormField, 'Nome fictício para prévia'),
            )
            .controller!;
        await tester.enterText(
          find.widgetWithText(TextFormField, 'Nome fictício para prévia'),
          'Dados transitórios',
        );
        if (expirar) {
          final previous = t.f.cenario.substituir!;
          t.f.cenario.substituir = (r) => r.url.path.endsWith('/previa')
              ? resposta(codigo: 401, corpo: '{"codigo":"sessao_invalida"}')
              : previous(r);
          await tocar(tester, find.text('Consultar prévia'));
        } else {
          await t.f.cenario.controlador.sair();
          await tester.pumpAndSettle();
        }
        expect(find.text('Entrar no eDisk'), findsOneWidget);
        expect(find.byType(PaginaCampanhas), findsNothing);
        expect(find.byType(EditorCampanha), findsNothing);
        expect(find.byType(RevisaoCampanha), findsNothing);
        expect(() => controller.addListener(() {}), throwsFlutterError);
        expect(tester.takeException(), isNull);
        t.f.cenario.controlador.desconectar();
        await tester.pumpWidget(const SizedBox());
      },
    );
  }
}
