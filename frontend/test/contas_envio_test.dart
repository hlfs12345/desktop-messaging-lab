import 'dart:convert';

import 'package:edisk/app.dart';
import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:edisk/contas_envio/pagina_contas.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'cadastro_test.dart';
import 'autenticacao_test.dart' as auth;

const segredoFicticio = 'token-ficticio_123:XYZ';

class ContasFicticias {
  final f = CadastroFicticio();
  final chamadas = <http.BaseRequest>[];
  final corpos = <Map<String, dynamic>>[];
  int codigo = 200;
  bool limpezaPendente = false;
  Map<String, dynamic> item = {
    'id': 'conta1',
    'nome': 'Bot local',
    'provedor': 'telegram_bot_api',
    'plataforma': 'telegram',
    'ativo': true,
    'estado_cadastral': 'ativa',
    'configuracao_local': 'pendente',
    'conexao': 'nao_verificada',
    'versao': 1,
  };
  Future<void> iniciar() async {
    await f.iniciar();
    final anterior = f.cenario.substituir!;
    f.cenario.substituir = (r) {
      if (r.url.path == '/auth/logout') return resposta(codigo: 204, corpo: '');
      if (r.url.path == '/auth/login') {
        return resposta(corpo: jsonEncode(auth.sessao()));
      }
      if (!r.url.path.startsWith('/contas-envio')) return anterior(r);
      chamadas.add(r);
      if (r.method != 'GET') {
        final body = Map<String, dynamic>.from(
          jsonDecode((r as http.Request).body) as Map,
        );
        corpos.add(body);
        if (codigo >= 400) {
          return resposta(
            codigo: codigo,
            corpo: jsonEncode({
              'codigo': codigo == 409 ? 'conflito' : 'segredo_indisponivel',
              'detail': segredoFicticio,
            }),
          );
        }
        if (r.method == 'POST' || r.method == 'PATCH') {
          item = {
            ...item,
            if (body.containsKey('nome')) 'nome': body['nome'],
            if (body.containsKey('provedor')) 'provedor': body['provedor'],
            if (body.containsKey('plataforma'))
              'plataforma': body['plataforma'],
            if (body.containsKey('ativo')) ...{
              'ativo': body['ativo'],
              'estado_cadastral': body['ativo'] == true
                  ? 'ativa'
                  : 'desativada',
            },
            if (body['remover'] == true) ...{
              'ativo': false,
              'estado_cadastral': 'removida',
              'configuracao_local': 'pendente',
            },
          };
        } else {
          item['configuracao_local'] = r.method == 'PUT'
              ? 'credencial_armazenada'
              : 'pendente';
        }
        item['versao'] = (item['versao'] as int) + 1;
        return resposta(
          codigo: r.method == 'POST' ? 201 : 200,
          corpo: jsonEncode({
            'id': item['id'],
            'versao': item['versao'],
            'limpeza_pendente': limpezaPendente,
          }),
        );
      }
      return resposta(
        corpo: jsonEncode(
          r.url.path == '/contas-envio'
              ? {
                  'itens': [item],
                  'total': 1,
                  'limpeza_pendente': limpezaPendente,
                }
              : {...item, 'limpeza_pendente': limpezaPendente},
        ),
      );
    };
  }

  Future<void> abrir(WidgetTester tester, {bool detalhe = false}) async {
    await tester.pumpWidget(
      MaterialApp(
        home: detalhe
            ? DetalheConta(autenticacao: f.cenario.controlador, id: 'conta1')
            : PaginaContas(autenticacao: f.cenario.controlador),
      ),
    );
    await tester.pumpAndSettle();
  }

  Future<void> fechar(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox());
    f.cenario.controlador.dispose();
  }
}

Finder get campoSegredo => find.byKey(const ValueKey('credencial-conta'));
String valorSegredo(WidgetTester tester) =>
    tester.widget<TextFormField>(campoSegredo).controller!.text;

Future<void> tocar(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'verificação Telegram mostra bot e descarta resultado transitório',
    (tester) async {
      final f = ContasFicticias();
      await f.iniciar();
      addTearDown(f.f.dispose);
      f.item['configuracao_local'] = 'credencial_armazenada';
      final anterior = f.f.cenario.substituir!;
      var consultas = 0;
      f.f.cenario.substituir = (r) {
        if (r.url.path.endsWith('/verificar-telegram')) {
          expect(r.method, 'POST');
          expect(r.url.path, '/contas-envio/conta1/verificar-telegram');
          expect((r as http.Request).body, isEmpty);
          consultas++;
          return resposta(
            codigo: consultas == 1 ? 200 : 504,
            corpo: jsonEncode(
              consultas == 1
                  ? {
                      'id': 123456,
                      'nome': 'Bot verificado',
                      'username': 'local_bot',
                    }
                  : {'codigo': 'telegram_timeout', 'detail': segredoFicticio},
            ),
          );
        }
        return anterior(r);
      };
      await f.abrir(tester, detalhe: true);
      expect(consultas, 0);
      await tocar(tester, find.text('Verificar conexão Telegram'));
      expect(find.text('Conexão verificada nesta consulta'), findsOneWidget);
      expect(
        find.textContaining('Bot verificado • ID: 123456 • @local_bot'),
        findsOneWidget,
      );
      await tocar(tester, find.text('Atualizar'));
      expect(find.text('Conexão: não verificada'), findsOneWidget);
      expect(find.textContaining('Bot verificado'), findsNothing);
      await tocar(tester, find.text('Verificar conexão Telegram'));
      expect(
        find.text('Telegram não respondeu no prazo. Tente novamente.'),
        findsOneWidget,
      );
      expect(find.text(segredoFicticio), findsNothing);
      expect(consultas, 2);
      await f.fechar(tester);
    },
  );

  testWidgets('cadastro, edição e estados separados, com pesquisa', (
    tester,
  ) async {
    final f = ContasFicticias();
    await f.iniciar();
    addTearDown(f.f.dispose);
    await f.abrir(tester);
    expect(find.textContaining('Conexão: não verificada'), findsOneWidget);
    await tocar(tester, find.text('Nova conta'));
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Nome amigável'),
      'Outro bot',
    );
    await tocar(tester, find.text('Salvar conta'));
    expect(f.corpos.last['provedor'], 'telegram_bot_api');
    expect(f.corpos.last['plataforma'], 'telegram');
    await tocar(tester, find.text('Outro bot'));
    await tocar(tester, find.text('Editar nome'));
    expect(find.text('Provedor da conta'), findsNothing);
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Nome amigável'),
      'Renomeado',
    );
    await tocar(tester, find.text('Salvar conta'));
    expect(find.text('Renomeado'), findsOneWidget);
    await tocar(tester, find.text('Desativar'));
    await tocar(tester, find.text('Confirmar'));
    expect(find.text('Estado cadastral: Desativada'), findsOneWidget);
    await tocar(tester, find.text('Reativar'));
    await tocar(tester, find.text('Confirmar'));
    expect(find.text('Estado cadastral: Ativa'), findsOneWidget);
    await tocar(tester, find.text('Remover conta'));
    await tocar(tester, find.text('Confirmar'));
    expect(find.text('Estado cadastral: Removida'), findsOneWidget);
    expect(campoSegredo, findsNothing);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.enterText(
      find.widgetWithText(TextField, 'Buscar conta'),
      'Bot',
    );
    await tocar(tester, find.text('Pesquisar'));
    expect(f.chamadas.last.url.queryParameters['q'], 'Bot');
    await f.fechar(tester);
  });

  testWidgets(
    'segredo oculto, limpeza no sucesso/cancelamento e exclusão local',
    (tester) async {
      final f = ContasFicticias();
      await f.iniciar();
      addTearDown(f.f.dispose);
      await f.abrir(tester, detalhe: true);
      expect(
        tester
            .widget<EditableText>(
              find.descendant(
                of: campoSegredo,
                matching: find.byType(EditableText),
              ),
            )
            .obscureText,
        isTrue,
      );
      expect(valorSegredo(tester), isEmpty);
      await tester.enterText(campoSegredo, segredoFicticio);
      await tocar(tester, find.text('Cancelar credencial'));
      expect(valorSegredo(tester), isEmpty);
      await tester.enterText(campoSegredo, segredoFicticio);
      await tocar(tester, find.text('Salvar credencial'));
      expect(f.corpos.last['credencial'], segredoFicticio);
      expect(valorSegredo(tester), isEmpty);
      expect(
        find.textContaining('Credencial armazenada localmente'),
        findsOneWidget,
      );
      await tocar(tester, find.text('Excluir credencial local'));
      await tocar(tester, find.text('Confirmar'));
      expect(f.chamadas.where((r) => r.method == 'DELETE').length, 1);
      expect(find.textContaining('Credencial pendente'), findsOneWidget);
      await f.fechar(tester);
    },
  );

  testWidgets(
    'erro sanitizado e repetição da mesma credencial sem duplicar operação',
    (tester) async {
      final f = ContasFicticias();
      await f.iniciar();
      addTearDown(f.f.dispose);
      await f.abrir(tester, detalhe: true);
      f.codigo = 503;
      await tester.enterText(campoSegredo, segredoFicticio);
      await tocar(tester, find.text('Salvar credencial'));
      final primeira = f.corpos.last['operacao_id'];
      expect(
        find.textContaining('Armazenamento protegido indisponível'),
        findsOneWidget,
      );
      await tocar(tester, find.text('Salvar credencial'));
      expect(f.corpos.last['operacao_id'], primeira);
      await tester.enterText(campoSegredo, '$segredoFicticio-outro');
      await tocar(tester, find.text('Salvar credencial'));
      expect(f.corpos.last['operacao_id'], isNot(primeira));
      f.codigo = 409;
      await tocar(tester, find.text('Salvar credencial'));
      expect(find.textContaining('Os dados mudaram'), findsOneWidget);
      await f.fechar(tester);
    },
  );

  for (final evento in ['logout', 'expiracao']) {
    testWidgets('limpa campo sensível no evento $evento', (tester) async {
      final f = ContasFicticias();
      if (evento == 'expiracao') {
        f.f.cenario.expira = DateTime.now().toUtc().add(
          const Duration(seconds: 10),
        );
      }
      await f.iniciar();
      addTearDown(f.f.dispose);
      await f.abrir(tester, detalhe: true);
      final campo = tester.widget<TextFormField>(campoSegredo).controller!;
      await tester.enterText(campoSegredo, segredoFicticio);
      if (evento == 'logout') {
        await f.f.cenario.controlador.sair();
      } else {
        await tester.pump(const Duration(seconds: 11));
      }
      await tester.pumpAndSettle();
      expect(campo.text, isEmpty);
      await f.fechar(tester);
    });
  }

  testWidgets('QR consulta status sem parear automaticamente', (tester) async {
    final f = ContasFicticias();
    f.item = {
      ...f.item,
      'provedor': 'qr_candidato',
      'plataforma': 'whatsapp',
      'configuracao_local': 'aguarda_integracao',
    };
    await f.iniciar();
    addTearDown(f.f.dispose);
    final anterior = f.f.cenario.substituir!;
    var estadoWhatsapp = 'pareamento_necessario';
    var sessaoPersistida = false;
    f.f.cenario.substituir = (r) {
      if (r.url.path == '/contas-envio/conta1/whatsapp') {
        expect(r.method, 'GET');
        return resposta(
          corpo: jsonEncode({
            'estado': estadoWhatsapp,
            'sessao_persistida': sessaoPersistida,
            'qr': null,
          }),
        );
      }
      if (r.url.path == '/contas-envio/conta1/whatsapp/desvincular') {
        expect(r.method, 'POST');
        f.chamadas.add(r);
        estadoWhatsapp = 'pareamento_necessario';
        sessaoPersistida = false;
        return resposta(
          corpo: jsonEncode({
            'estado': 'pareamento_necessario',
            'sessao_persistida': false,
            'qr': null,
          }),
        );
      }
      return anterior(r);
    };
    await f.abrir(tester, detalhe: true);
    expect(campoSegredo, findsNothing);
    expect(
      find.textContaining('Integração experimental disponível'),
      findsOneWidget,
    );
    expect(find.textContaining('Sem sessão vinculada'), findsOneWidget);
    expect(find.text('Iniciar pareamento'), findsOneWidget);
    final desvincular = find.widgetWithText(
      OutlinedButton,
      'Desvincular WhatsApp',
    );
    expect(desvincular, findsOneWidget);
    expect(tester.widget<OutlinedButton>(desvincular).onPressed, isNull);
    estadoWhatsapp = 'conectado';
    await tocar(tester, find.text('Atualizar status WhatsApp'));
    expect(find.textContaining('Sessão protegida vinculada'), findsOneWidget);
    expect(desvincular.hitTestable(), findsOneWidget);
    estadoWhatsapp = 'desconectado';
    await tocar(tester, find.text('Atualizar status WhatsApp'));
    expect(find.text('Reconectar'), findsOneWidget);
    expect(desvincular.hitTestable(), findsOneWidget);
    await tocar(tester, find.text('Desvincular WhatsApp'));
    expect(find.text('Desvincular WhatsApp?'), findsOneWidget);
    await tocar(tester, find.text('Cancelar'));
    expect(f.chamadas.where((r) => r.method == 'POST'), isEmpty);
    await tocar(tester, find.text('Desvincular WhatsApp'));
    await tocar(tester, find.text('Desvincular e apagar sessão'));
    expect(
      f.chamadas.singleWhere((r) => r.method == 'POST').url.path,
      '/contas-envio/conta1/whatsapp/desvincular',
    );
    expect(find.textContaining('Sem sessão vinculada'), findsOneWidget);
    expect(find.text('Iniciar pareamento'), findsOneWidget);
    expect(tester.widget<OutlinedButton>(desvincular).onPressed, isNull);
    expect(f.chamadas.every((r) => r.url.host == '127.0.0.1'), isTrue);
    await f.fechar(tester);
  });

  testWidgets(
    'navegação autenticada é destruída no logout com segredo no editor',
    (tester) async {
      final f = ContasFicticias();
      await f.iniciar();
      addTearDown(f.f.dispose);
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
      await tocar(tester, find.byTooltip('Contas de envio'));
      await tocar(tester, find.text('Bot local'));
      final campo = tester.widget<TextFormField>(campoSegredo).controller!;
      await tester.enterText(campoSegredo, segredoFicticio);
      await tocar(tester, find.text('Sair').last);
      expect(find.text('Contas de envio'), findsNothing);
      expect(find.text('Salvar credencial'), findsNothing);
      expect(campo.text, isEmpty);
      await tester.pumpWidget(const SizedBox());
    },
  );
}
