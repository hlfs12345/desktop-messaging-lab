import 'package:edisk/app.dart';
import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:edisk/contas_envio/controlador_contas.dart';
import 'package:edisk/contas_envio/pagina_contas.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;
import 'contas_envio_test.dart' as contas;

Finder get editavel => find.descendant(
  of: contas.campoSegredo,
  matching: find.byType(EditableText),
);
Finder get historico => find.descendant(
  of: contas.campoSegredo,
  matching: find.byType(UndoHistory<TextEditingValue>),
);

class EditorAnterior {
  EditorAnterior(WidgetTester tester)
    : estado = tester.state<EditableTextState>(editavel),
      undo = tester.state<UndoHistoryState<TextEditingValue>>(historico),
      controller = tester.widget<EditableText>(editavel).controller,
      foco = tester.widget<EditableText>(editavel).focusNode;
  final EditableTextState estado;
  final UndoHistoryState<TextEditingValue> undo;
  final TextEditingController controller;
  final FocusNode foco;

  void verificarDescarte() {
    expect(estado.mounted, isFalse);
    expect(undo.mounted, isFalse);
    expect(controller.text, isEmpty);
    expect(() => controller.addListener(() {}), throwsFlutterError);
    expect(() => foco.addListener(() {}), throwsFlutterError);
  }
}

Future<void> atalho(
  WidgetTester tester,
  LogicalKeyboardKey tecla, {
  bool shift = false,
}) async {
  await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
  if (shift) await tester.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
  await tester.sendKeyEvent(tecla);
  if (shift) await tester.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
  await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
  await tester.pump(const Duration(milliseconds: 600));
}

Future<EditorAnterior> registrarHistorico(WidgetTester tester) async {
  await tester.enterText(contas.campoSegredo, 'rascunho-ficticio');
  await tester.pump(const Duration(milliseconds: 600));
  await tester.enterText(contas.campoSegredo, contas.segredoFicticio);
  await tester.pump(const Duration(milliseconds: 600));
  final anterior = EditorAnterior(tester);
  expect(anterior.undo.canUndo, isTrue);
  // Comprova funcionamento real do UndoHistory antes do descarte.
  await atalho(tester, LogicalKeyboardKey.keyZ);
  expect(contas.valorSegredo(tester), 'rascunho-ficticio');
  // O SDK instalado usa Ctrl+Shift+Z para refazer no Windows.
  await atalho(tester, LogicalKeyboardKey.keyZ, shift: true);
  expect(contas.valorSegredo(tester), contas.segredoFicticio);
  return anterior;
}

Future<void> verificarNovoEditor(
  WidgetTester tester,
  EditorAnterior anterior,
) async {
  anterior.verificarDescarte();
  expect(
    tester.state<EditableTextState>(editavel),
    isNot(same(anterior.estado)),
  );
  expect(
    tester.widget<EditableText>(editavel).controller,
    isNot(same(anterior.controller)),
  );
  await tester.ensureVisible(contas.campoSegredo);
  await tester.tap(contas.campoSegredo);
  await tester.pumpAndSettle();
  expect(tester.widget<EditableText>(editavel).focusNode.hasFocus, isTrue);
  await atalho(tester, LogicalKeyboardKey.keyZ);
  expect(contas.valorSegredo(tester), isEmpty);
  await atalho(tester, LogicalKeyboardKey.keyY);
  expect(contas.valorSegredo(tester), isEmpty);
  await atalho(tester, LogicalKeyboardKey.keyZ, shift: true);
  expect(contas.valorSegredo(tester), isEmpty);
}

Future<void> abrirAplicativo(
  WidgetTester tester,
  contas.ContasFicticias f,
) async {
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
  await contas.tocar(tester, find.byTooltip('Contas de envio'));
  await contas.tocar(tester, find.text('Bot local'));
}

void testeWindows(String descricao, WidgetTesterCallback corpo) {
  testWidgets(
    descricao,
    corpo,
    variant: TargetPlatformVariant.only(TargetPlatform.windows),
  );
}

void main() {
  for (final acao in ['Cancelar credencial', 'Salvar credencial']) {
    testeWindows(
      '$acao desmonta controller e UndoHistory antes de nova edição',
      (tester) async {
        final f = contas.ContasFicticias();
        await f.iniciar();
        addTearDown(f.f.dispose);
        await f.abrir(tester, detalhe: true);
        final anterior = await registrarHistorico(tester);
        await contas.tocar(tester, find.text(acao));
        await verificarNovoEditor(tester, anterior);
        // Novo formulário continua funcional; descarte não dispara gravação adicional.
        final gravacoes = f.corpos.length;
        await contas.tocar(tester, find.text('Salvar credencial'));
        expect(find.textContaining('Informe uma credencial'), findsOneWidget);
        expect(f.corpos.length, gravacoes);
        await f.fechar(tester);
      },
    );
  }

  testeWindows(
    'troca de conta no mesmo State encerra editor e histórico anteriores',
    (tester) async {
      final f = contas.ContasFicticias();
      await f.iniciar();
      addTearDown(f.f.dispose);
      final conta = ValueNotifier('conta1');
      addTearDown(conta.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: ValueListenableBuilder<String>(
            valueListenable: conta,
            builder: (_, id, _) =>
                DetalheConta(autenticacao: f.f.cenario.controlador, id: id),
          ),
        ),
      );
      await tester.pumpAndSettle();
      final pagina = tester.state(find.byType(DetalheConta));
      final anterior = await registrarHistorico(tester);
      conta.value = 'conta2';
      await tester.pumpAndSettle();
      expect(tester.state(find.byType(DetalheConta)), same(pagina));
      expect(f.chamadas.last.url.path, '/contas-envio/conta2');
      await verificarNovoEditor(tester, anterior);
      await f.fechar(tester);
    },
  );

  testeWindows(
    'fechar formulário e reabrir não recupera credencial descartada',
    (tester) async {
      final f = contas.ContasFicticias();
      await f.iniciar();
      addTearDown(f.f.dispose);
      await f.abrir(tester);
      await contas.tocar(tester, find.text('Bot local'));
      final anterior = await registrarHistorico(tester);
      await tester.pageBack();
      await tester.pumpAndSettle();
      anterior.verificarDescarte();
      await contas.tocar(tester, find.text('Bot local'));
      await verificarNovoEditor(tester, anterior);
      await f.fechar(tester);
    },
  );

  for (final evento in ['logout', 'expiracao']) {
    testeWindows('$evento descarta editor e histórico com edição registrada', (
      tester,
    ) async {
      final f = contas.ContasFicticias();
      if (evento == 'expiracao') {
        f.f.cenario.expira = DateTime.now().toUtc().add(
          const Duration(seconds: 20),
        );
      }
      await f.iniciar();
      addTearDown(f.f.dispose);
      await abrirAplicativo(tester, f);
      final anterior = await registrarHistorico(tester);
      if (evento == 'logout') {
        await f.f.cenario.controlador.sair();
      } else {
        await tester.pump(const Duration(seconds: 21));
      }
      await tester.pumpAndSettle();
      anterior.verificarDescarte();
      expect(contas.campoSegredo, findsNothing);
      await atalho(tester, LogicalKeyboardKey.keyZ);
      await atalho(tester, LogicalKeyboardKey.keyY);
      expect(contas.campoSegredo, findsNothing);
      expect(find.text(contas.segredoFicticio), findsNothing);
      // Nova sessão constrói uma nova área autenticada e um editor vazio.
      await f.f.cenario.controlador.entrar('teste.local', auth.senhaFicticia);
      await tester.pumpAndSettle();
      await contas.tocar(tester, find.byTooltip('Contas de envio'));
      await contas.tocar(tester, find.text('Bot local'));
      await verificarNovoEditor(tester, anterior);
      await tester.pumpWidget(const SizedBox());
    });
  }

  for (final detalhe in [false, true]) {
    testeWindows(
      'aviso de limpeza permanece ao reabrir e desaparece após recuperação ($detalhe)',
      (tester) async {
        final f = contas.ContasFicticias()..limpezaPendente = true;
        await f.iniciar();
        addTearDown(f.f.dispose);
        await f.abrir(tester, detalhe: detalhe);
        expect(find.text(avisoLimpezaConta), findsOneWidget);
        await tester.pumpWidget(const SizedBox());
        await f.abrir(tester, detalhe: detalhe);
        expect(find.text(avisoLimpezaConta), findsOneWidget);
        expect(find.textContaining('Conexão: não verificada'), findsOneWidget);
        f.limpezaPendente = false;
        await contas.tocar(
          tester,
          find.text(detalhe ? 'Atualizar' : 'Pesquisar'),
        );
        expect(find.text(avisoLimpezaConta), findsNothing);
        expect(f.item['estado_cadastral'], 'ativa');
        await f.fechar(tester);
      },
    );
  }
}
