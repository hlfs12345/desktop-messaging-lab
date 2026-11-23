import 'dart:async';
import 'dart:convert';

import 'package:edisk/autenticacao/controlador_autenticacao.dart';
import 'package:edisk/campanhas/chaves_campanhas.dart';
import 'package:edisk/campanhas/preparacao_campanha.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;

final cenarios14 = <F14>[];

class F14 {
  final f = auth.Cenario(agora: () => DateTime.utc(2026, 11, 2));
  bool preparada = false,
      confirmada = false,
      invalidada = false,
      conflito = false,
      corrompida = false,
      telegram = false;
  int enviosTelegram = 0;
  final corpos = <Map<String, dynamic>>[];
  final impressao = List.filled(64, 'a').join();
  Completer<http.StreamedResponse>? pendente;
  Future<void> iniciar({bool expirar = false}) async {
    if (expirar) {
      f.expira = DateTime.utc(2026, 11, 2).add(const Duration(seconds: 10));
    }
    await f.controlador.entrar('teste.local', auth.senhaFicticia);
    f.substituir = (r) {
      final path = r.url.path;
      if (path == '/auth/logout') return resposta(codigo: 204, corpo: '');
      Map<String, dynamic> body = {};
      if (r.method == 'POST') {
        body = Map<String, dynamic>.from(
          jsonDecode((r as http.Request).body) as Map,
        );
        corpos.add(body);
      }
      http.StreamedResponse json(Object value) =>
          resposta(corpo: jsonEncode(value));
      http.StreamedResponse comprometida() =>
          resposta(codigo: 503, corpo: '{"codigo":"integridade_comprometida"}');
      if (path.endsWith('/chaves/estado')) {
        return json({
          'inicializada': true,
          'conferida': true,
          'administrador': true,
        });
      }
      if (path.endsWith('/chaves/reemitir')) {
        return json({'codigo_recuperacao': 'EDC1.codigo-ficticio'});
      }
      if (path.contains('/chaves/')) {
        return json({'concluida': true});
      }
      if (path.endsWith('/conteudo')) {
        if (corrompida) return comprometida();
        if (pendente != null) {
          return pendente!.future;
        }
        return json({'conteudo_renderizado': 'Texto pessoal congelado'});
      }
      if (path.endsWith('/confirmacao')) {
        if (corrompida) return comprometida();
        confirmada = true;
        return json({'versao': 4});
      }
      if (path.endsWith('/retorno-rascunho')) {
        invalidada = true;
        return json({'versao': 5});
      }
      if (path.endsWith('/revisao-envio-telegram')) {
        return json({
          'campanha_id': 'cp1',
          'preparacao_id': 'p1',
          'versao': 4,
          'impressao': impressao,
          'conta': {'id': 'a1', 'nome': 'Bot Loja', 'bot_id': '1001'},
          'destinatario': {
            'identidade_telegram_id': 'i1',
            'nome': 'Pessoa Telegram',
            'destino_mascarado': '***6789',
          },
          'categoria': 'promocoes',
          'conteudo': 'Mensagem Telegram final',
          'marcador_envio': 'envio-telegram-v1.${List.filled(64, 'b').join()}',
        });
      }
      if (path.endsWith('/envio-telegram')) {
        enviosTelegram++;
        return json({
          'tentativa_id': 't1',
          'estado': 'confirmada',
          'codigo_resultado': 'aceita',
          'message_id': 321,
          'retry_after': null,
          'reenvio_automatico': false,
        });
      }
      if (path.endsWith('/revisao-preparacao')) {
        return json({
          'versao': 2,
          'marcador': 'base.abertura.2.10',
          'incluidos': 1,
          'excluidos': 1,
          'total': 2,
          'itens': [
            {'identidade_contato_id': 'i1', 'incluido': true, 'motivos': []},
            {
              'identidade_contato_id': 'i2',
              'incluido': false,
              'motivos': ['sem_autorizacao'],
            },
          ],
        });
      }
      if (path.endsWith('/preparacoes')) {
        if (r.method == 'POST') {
          if (conflito) {
            return resposta(codigo: 409, corpo: '{"codigo":"conflito"}');
          }
          preparada = true;
          return json({'preparacao_id': 'p1', 'versao': 3});
        }
        return json({
          'itens': preparada
              ? [
                  if (corrompida)
                    {
                      'preparacao_id': 'p1',
                      'integridade': 'comprometida',
                      'bloqueada': true,
                    }
                  else
                    {'preparacao_id': 'p1', 'integridade': 'verificada'},
                ]
              : [],
          'total': preparada ? 1 : 0,
        });
      }
      if (path.contains('/preparacoes/')) {
        if (corrompida) return comprometida();
        return json({
          'preparacao_id': 'p1',
          'impressao': impressao,
          'incluidos': 1,
          'excluidos': telegram ? 0 : 1,
          'total': telegram ? 1 : 2,
          'confirmado_em': confirmada ? '2026-11-02' : null,
          'invalidado_em': invalidada ? '2026-11-02' : null,
          'revisao_desatualizada': false,
          'itens': [
            {
              'identidade_contato_id': telegram ? null : 'i1',
              'identidade_telegram_id': telegram ? 'i1' : null,
              'nome_atual': telegram ? 'Pessoa Telegram' : null,
              'destino_mascarado': telegram ? '***6789' : null,
              'incluido': true,
              'motivos': [],
              'impedimentos_atuais': [],
            },
            if (!telegram)
              {
                'identidade_contato_id': 'i2',
                'incluido': false,
                'motivos': ['sem_autorizacao'],
                'impedimentos_atuais': ['sem_autorizacao'],
              },
          ],
        });
      }
      return json({
        'status': preparada && !invalidada ? 'preparada' : 'rascunho',
        'categoria': 'promocoes',
        'versao': confirmada
            ? 4
            : preparada
            ? 3
            : 2,
        if (telegram)
          'referencias': {
            'conta': {
              'id': 'a1',
              'nome': 'Bot Loja',
              'provedor': 'telegram_bot_api',
            },
          },
      });
    };
  }

  Future<void> abrir(
    WidgetTester t, {
    bool chaves = false,
    bool texto = false,
  }) async {
    await t.pumpWidget(
      MaterialApp(
        home: ListenableBuilder(
          listenable: f.controlador,
          builder: (_, _) =>
              f.controlador.estado == EstadoAutenticacao.autenticado
              ? chaves
                    ? ChavesCampanhas(autenticacao: f.controlador)
                    : texto
                    ? ConteudoFotografia(
                        autenticacao: f.controlador,
                        caminho:
                            '/campanhas/cp1/preparacoes/p1/itens/i1/conteudo',
                      )
                    : PreparacaoCampanha(autenticacao: f.controlador, id: 'cp1')
              : const Text('Sessao encerrada'),
        ),
      ),
    );
    await t.pumpAndSettle();
  }
}

Future<void> tocar(WidgetTester t, String text) async {
  final finder = find.text(text);
  await t.ensureVisible(finder);
  await t.tap(finder);
  await t.pumpAndSettle();
}

void testeWindows(String nome, WidgetTesterCallback corpo) {
  testWidgets(nome, (tester) async {
    try {
      await corpo(tester);
    } finally {
      for (final f in cenarios14) {
        f.f.controlador.desconectar();
      }
      cenarios14.clear();
      await tester.pumpWidget(const SizedBox());
    }
  }, variant: TargetPlatformVariant.only(TargetPlatform.windows));
}

void main() {
  Future<F14> iniciar(WidgetTester t, {bool expirar = false}) async {
    await t.binding.setSurfaceSize(const Size(1000, 1400));
    addTearDown(() => t.binding.setSurfaceSize(null));
    final f = F14();
    await f.iniciar(expirar: expirar);
    cenarios14.add(f);
    return f;
  }

  testeWindows('prepara confirma e retorna preservando fotografia', (t) async {
    final f = await iniciar(t);
    await f.abrir(t);
    await tocar(t, 'Revisar para preparar');
    expect(find.textContaining('Incluidos: 1 | Excluidos: 1'), findsOneWidget);
    await tocar(t, 'Preparar fotografia imutavel');
    await tocar(t, 'Ver texto');
    expect(find.text('Texto pessoal congelado'), findsOneWidget);
    await tocar(t, 'Fechar');
    expect(find.text('Texto pessoal congelado'), findsNothing);
    await tocar(t, 'Confirmar prepara\u00e7\u00e3o');
    expect(f.corpos.any((b) => b['impressao'] == f.impressao), isTrue);
    expect(
      find.text('Revisao confirmada. Envio indisponivel.'),
      findsOneWidget,
    );
    await tocar(t, 'Rejeitar e retornar ao rascunho');
    expect(find.text('Fotografia invalidada'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Enviar'), findsNothing);
  });
  testeWindows('telegram revisa confirma e nunca envia automaticamente', (
    t,
  ) async {
    final f = await iniciar(t);
    f.telegram = true;
    f.preparada = true;
    f.confirmada = true;
    await f.abrir(t);
    expect(f.enviosTelegram, 0);
    expect(find.text('Mensagem Telegram final'), findsNothing);
    await tocar(t, 'Revisar envio real Telegram');
    expect(find.text('Conta/bot: Bot Loja (1001)'), findsOneWidget);
    expect(find.text('Destinatario: Pessoa Telegram ***6789'), findsOneWidget);
    expect(find.text('Categoria: promocoes'), findsOneWidget);
    expect(find.text('Mensagem Telegram final'), findsOneWidget);
    final enviar = find.byKey(const Key('enviar-telegram-real'));
    expect(t.widget<FilledButton>(enviar).onPressed, isNull);
    await t.tap(find.byKey(const Key('confirmar-envio-real')));
    await t.pumpAndSettle();
    expect(t.widget<FilledButton>(enviar).onPressed, isNotNull);
    await t.tap(enviar);
    await t.pumpAndSettle();
    expect(f.enviosTelegram, 1);
    expect(find.text('Resultado: confirmada (aceita)'), findsOneWidget);
  });
  testeWindows('409 exige nova revisao antes de preparar', (t) async {
    final f = await iniciar(t);
    await f.abrir(t);
    await tocar(t, 'Revisar para preparar');
    f.conflito = true;
    await tocar(t, 'Preparar fotografia imutavel');
    expect(find.text('Preparar fotografia imutavel'), findsNothing);
    expect(find.textContaining('Os dados mudaram'), findsOneWidget);
  });
  testeWindows('descarte desmonta controller foco e UndoHistory do codigo', (
    t,
  ) async {
    final f = await iniciar(t);
    await f.abrir(t, chaves: true);
    final campo = find.byKey(const ValueKey('codigo-recuperacao'));
    await t.enterText(campo, 'primeira edicao');
    await t.pump(const Duration(milliseconds: 600));
    await t.enterText(campo, 'codigo pessoal ficticio');
    await t.pump(const Duration(milliseconds: 600));
    final editable = find.descendant(
      of: campo,
      matching: find.byType(EditableText),
    );
    final history = find.descendant(
      of: campo,
      matching: find.byType(UndoHistory<TextEditingValue>),
    );
    final estado = t.state<EditableTextState>(editable);
    final undo = t.state<UndoHistoryState<TextEditingValue>>(history);
    final controller = t.widget<EditableText>(editable).controller;
    final focus = t.widget<EditableText>(editable).focusNode;
    expect(undo.canUndo, isTrue);
    await tocar(t, 'Descartar campos');
    expect(estado.mounted, isFalse);
    expect(undo.mounted, isFalse);
    expect(controller.text, isEmpty);
    expect(() => controller.addListener(() {}), throwsFlutterError);
    expect(() => focus.addListener(() {}), throwsFlutterError);
    await t.tap(campo);
    await t.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await t.sendKeyEvent(LogicalKeyboardKey.keyZ);
    await t.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await t.pumpAndSettle();
    expect(
      t
          .widget<EditableText>(
            find.descendant(of: campo, matching: find.byType(EditableText)),
          )
          .controller
          .text,
      isEmpty,
    );
  });
  testeWindows('codigo apresentado e descartado antes da conferencia', (
    t,
  ) async {
    final f = await iniciar(t);
    await f.abrir(t, chaves: true);
    await tocar(t, 'Reemitir codigo com a chave atual');
    expect(find.text('EDC1.codigo-ficticio'), findsOneWidget);
    await tocar(t, 'Ja guardei; conferir codigo');
    expect(find.text('EDC1.codigo-ficticio'), findsNothing);
    expect(find.byKey(const ValueKey('codigo-recuperacao')), findsOneWidget);
  });
  testeWindows('resposta de conteudo tardia nao sobrevive a sessao', (t) async {
    final f = await iniciar(t);
    f.pendente = Completer<http.StreamedResponse>();
    await f.abrir(t, texto: true);
    f.f.controlador.desconectar();
    await t.pumpAndSettle();
    f.pendente!.complete(
      resposta(corpo: '{"conteudo_renderizado":"SEGREDO TARDIO"}'),
    );
    await t.pumpAndSettle();
    expect(find.text('Sessao encerrada'), findsOneWidget);
    expect(find.text('SEGREDO TARDIO'), findsNothing);
    expect(t.takeException(), isNull);
  });
  testeWindows('encerramento de sessao remove o codigo e seu historico', (
    t,
  ) async {
    final f = await iniciar(t);
    await f.abrir(t, chaves: true);
    final campo = find.byKey(const ValueKey('codigo-recuperacao'));
    await t.enterText(campo, 'codigo privado');
    final editable = find.descendant(
      of: campo,
      matching: find.byType(EditableText),
    );
    final estado = t.state<EditableTextState>(editable);
    final controller = t.widget<EditableText>(editable).controller;
    await f.f.controlador.sair();
    await t.pumpAndSettle();
    expect(estado.mounted, isFalse);
    expect(controller.text, isEmpty);
    expect(find.text('codigo privado'), findsNothing);
  });
  testeWindows('expiracao desmonta campo e historico da recuperacao', (
    t,
  ) async {
    final f = await iniciar(t, expirar: true);
    await f.abrir(t, chaves: true);
    final campo = find.byKey(const ValueKey('codigo-recuperacao'));
    await t.enterText(campo, 'codigo de sessao');
    final editavel = find.descendant(
      of: campo,
      matching: find.byType(EditableText),
    );
    final estado = t.state<EditableTextState>(editavel);
    final controller = t.widget<EditableText>(editavel).controller;
    await t.pump(const Duration(seconds: 11));
    await t.pumpAndSettle();
    expect(estado.mounted, isFalse);
    expect(controller.text, isEmpty);
    expect(find.text('Sessao encerrada'), findsOneWidget);
  });
  for (final confirmada in [false, true]) {
    testeWindows('fotografia comprometida nao aparece valida: $confirmada', (
      t,
    ) async {
      final f = await iniciar(t);
      f.preparada = true;
      f.confirmada = confirmada;
      f.corrompida = true;
      await f.abrir(t);
      expect(
        find.text('Fotografia bloqueada por integridade comprometida.'),
        findsOneWidget,
      );
      expect(
        find.textContaining('integridade comprometida; bloqueada'),
        findsOneWidget,
      );
      expect(
        find.text('Revisao confirmada. Envio indisponivel.'),
        findsNothing,
      );
      expect(find.text('Confirmar prepara\u00e7\u00e3o'), findsNothing);
      expect(find.text('Ver texto'), findsNothing);
    });
  }
  testeWindows(
    'falha de integridade ao confirmar remove a fotografia anterior',
    (t) async {
      final f = await iniciar(t);
      f.preparada = true;
      await f.abrir(t);
      f.corrompida = true;
      await tocar(t, 'Confirmar prepara\u00e7\u00e3o');
      expect(
        find.text('Fotografia bloqueada por integridade comprometida.'),
        findsOneWidget,
      );
      expect(find.text('Confirmar prepara\u00e7\u00e3o'), findsNothing);
      expect(find.text('Ver texto'), findsNothing);
      expect(f.confirmada, isFalse);
    },
  );
  testeWindows(
    'falha de integridade no texto bloqueia tambem a tela da fotografia',
    (t) async {
      final f = await iniciar(t);
      f.preparada = true;
      await f.abrir(t);
      f.corrompida = true;
      await tocar(t, 'Ver texto');
      expect(find.text('Texto pessoal congelado'), findsNothing);
      await tocar(t, 'Fechar');
      expect(
        find.text('Fotografia bloqueada por integridade comprometida.'),
        findsOneWidget,
      );
      expect(find.text('Confirmar prepara\u00e7\u00e3o'), findsNothing);
      expect(find.text('Ver texto'), findsNothing);
    },
  );
  testeWindows(
    'recuperacao descarta segredo e distingue fotografias nao verificadas',
    (t) async {
      final f = await iniciar(t);
      await f.abrir(t, chaves: true);
      final campo = find.byKey(const ValueKey('codigo-recuperacao'));
      await t.enterText(campo, 'edicao anterior');
      await t.pump(const Duration(milliseconds: 600));
      await t.enterText(campo, 'codigo privado de recuperacao');
      await t.pump(const Duration(milliseconds: 600));
      final editable = find.descendant(
        of: campo,
        matching: find.byType(EditableText),
      );
      final state = t.state<EditableTextState>(editable);
      final controller = t.widget<EditableText>(editable).controller;
      final undo = t.state<UndoHistoryState<TextEditingValue>>(
        find.descendant(
          of: campo,
          matching: find.byType(UndoHistory<TextEditingValue>),
        ),
      );
      expect(undo.canUndo, isTrue);
      await tocar(t, 'Recuperar protecao neste perfil');
      expect(state.mounted, isFalse);
      expect(undo.mounted, isFalse);
      expect(controller.text, isEmpty);
      expect(() => controller.addListener(() {}), throwsFlutterError);
      expect(
        find.textContaining(
          'As fotografias nao foram verificadas nesta operacao',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('Isto nao restaura o banco.'), findsOneWidget);
      expect(
        t
            .widget<EditableText>(
              find.descendant(of: campo, matching: find.byType(EditableText)),
            )
            .controller
            .text,
        isEmpty,
      );
    },
  );
}
