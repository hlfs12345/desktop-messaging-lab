import 'dart:async';
import 'dart:io';

import 'package:edisk/app.dart';
import 'package:edisk/conexao/cliente_health.dart';
import 'package:edisk/conexao/controlador_conexao.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';

ControladorConexao criarControlador(TransporteSimulado transporte) =>
    ControladorConexao(
      cliente: ClienteHealth(
        token: tokenTeste,
        endpoint: Uri.parse('http://127.0.0.1:49152'),
        transporte: transporte,
      ),
      agora: () => DateTime.utc(2026, 10, 29, 12, 34),
    );

void main() {
  testWidgets('estado inicial, indicador e botão bloqueado', (tester) async {
    final pendente = Completer<http.StreamedResponse>();
    final transporte = TransporteSimulado((_) => pendente.future);
    await tester.pumpWidget(
      EdiskApp(controlador: criarControlador(transporte)),
    );
    expect(find.text('Verificando conexão'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(
      tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
      isNull,
    );
    pendente.complete(resposta());
    await tester.pumpAndSettle();
    expect(find.text('Disponível'), findsOneWidget);
    expect(find.text('Verificar novamente'), findsOneWidget);
    expect(find.textContaining('Última verificação:'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    expect(transporte.fechamentos, 1);
  });

  testWidgets('falha, nova tentativa pelo teclado e recuperação', (
    tester,
  ) async {
    var chamadas = 0;
    final transporte = TransporteSimulado((_) {
      if (++chamadas == 1) throw const SocketException('segredo');
      return resposta();
    });
    await tester.pumpWidget(
      EdiskApp(controlador: criarControlador(transporte)),
    );
    await tester.pumpAndSettle();
    expect(find.text('Indisponível'), findsOneWidget);
    expect(find.text('Tentar novamente'), findsOneWidget);
    expect(find.textContaining('segredo'), findsNothing);
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(find.text('Disponível'), findsOneWidget);
    expect(chamadas, 2);
    await tester.pump(const Duration(minutes: 1));
    expect(chamadas, 2, reason: 'Não há monitoramento periódico.');
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('503 e resposta inválida têm diagnósticos distintos', (
    tester,
  ) async {
    var chamadas = 0;
    final transporte = TransporteSimulado(
      (_) => ++chamadas == 1
          ? resposta(codigo: 503)
          : resposta(corpo: '{"senha":"privada"}'),
    );
    await tester.pumpWidget(
      EdiskApp(controlador: criarControlador(transporte)),
    );
    await tester.pumpAndSettle();
    expect(
      find.textContaining('respondeu que está indisponível'),
      findsOneWidget,
    );
    await tester.tap(find.text('Tentar novamente'));
    await tester.pumpAndSettle();
    expect(find.text('Erro de conexão'), findsOneWidget);
    expect(find.textContaining('resposta inesperada'), findsOneWidget);
    expect(find.textContaining('privada'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('descarte durante requisição não atualiza widgets', (
    tester,
  ) async {
    final pendente = Completer<http.StreamedResponse>();
    final transporte = TransporteSimulado((_) => pendente.future);
    await tester.pumpWidget(
      EdiskApp(controlador: criarControlador(transporte)),
    );
    await tester.pumpWidget(const SizedBox.shrink());
    pendente.complete(resposta());
    await tester.pump();
    expect(tester.takeException(), isNull);
    expect(transporte.fechamentos, 1);
  });

  testWidgets('prazo de oito segundos apresenta diagnóstico de timeout', (
    tester,
  ) async {
    final transporte = TransporteSimulado((request) async {
      await (request as http.AbortableRequest).abortTrigger;
      throw http.RequestAbortedException(request.url);
    });
    await tester.pumpWidget(
      EdiskApp(controlador: criarControlador(transporte)),
    );
    await tester.pump(const Duration(seconds: 8));
    await tester.pumpAndSettle();
    expect(find.text('Indisponível'), findsOneWidget);
    expect(
      find.textContaining('não respondeu dentro do prazo'),
      findsOneWidget,
    );
    expect(find.text('Tentar novamente'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    expect(tester.takeException(), isNull);
  });

  for (final brilho in Brightness.values) {
    for (final tamanho in [
      const Size(640, 480),
      const Size(1024, 768),
      const Size(1440, 900),
    ]) {
      for (final escala in [1.0, 2.0]) {
        testWidgets('layout $brilho $tamanho texto $escala sem overflow', (
          tester,
        ) async {
          tester.view.devicePixelRatio = 1;
          tester.view.physicalSize = tamanho;
          tester.platformDispatcher.platformBrightnessTestValue = brilho;
          tester.platformDispatcher.textScaleFactorTestValue = escala;
          addTearDown(tester.view.reset);
          addTearDown(tester.platformDispatcher.clearAllTestValues);
          final transporte = TransporteSimulado((_) => resposta());
          await tester.pumpWidget(
            EdiskApp(controlador: criarControlador(transporte)),
          );
          await tester.pumpAndSettle();
          final contexto = tester.element(find.text('Disponível'));
          expect(Theme.of(contexto).brightness, brilho);
          expect(Localizations.localeOf(contexto), const Locale('pt', 'BR'));
          expect(Theme.of(contexto).useMaterial3, isTrue);
          await tester.ensureVisible(find.byType(FilledButton));
          expect(tester.takeException(), isNull);
          await tester.tap(find.byType(FilledButton));
          await tester.pumpAndSettle();
          expect(transporte.requisicoes, hasLength(2));
          await tester.pumpWidget(const SizedBox.shrink());
        });
      }
    }
  }
}
