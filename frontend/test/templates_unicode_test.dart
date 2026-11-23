import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'templates_test.dart';

const amostrasUnicode = {
  'ASCII': 'a',
  'combinantes': 'e\u0301',
  'emoji composto': '\u{1f468}\u200d\u{1f469}\u200d\u{1f467}\u200d\u{1f466}',
};

String textoNoLimite(String unidade, int limite) {
  final tamanho = unidade.runes.length;
  return unidade * (limite ~/ tamanho) + 'a' * (limite % tamanho);
}

Future<void> digitar(WidgetTester tester, Finder campo, String texto) async {
  await tester.ensureVisible(campo);
  await tester.enterText(campo, texto);
  await tester.pump();
}

void main() {
  for (final amostra in amostrasUnicode.entries) {
    for (final campo in [
      ('Nome do template', 'nome', 120),
      ('Descrição', 'descricao', 2000),
      ('Conteúdo', 'conteudo', 4000),
    ]) {
      testWidgets(
        '${amostra.key}: ${campo.$1} conta pontos e rejeita excesso sem truncar',
        (tester) async {
          final t = TemplatesFicticios();
          await t.iniciar();
          addTearDown(t.f.dispose);
          await t.abrir(tester);
          await tocar(tester, find.text('Novo template'));
          await digitar(
            tester,
            find.widgetWithText(TextFormField, 'Nome do template'),
            'Exemplo',
          );
          await selecionar(tester, 'Categoria', 'Promoções');
          await digitar(
            tester,
            find.widgetWithText(TextFormField, 'Conteúdo'),
            'Texto local',
          );

          final finder = find.widgetWithText(TextFormField, campo.$1);
          final exato = textoNoLimite(amostra.value, campo.$3);
          final excedido = '${exato}a';
          await digitar(tester, finder, excedido);
          expect(
            tester.widget<TextFormField>(finder).controller!.text,
            excedido,
          );
          expect(
            find.text('${campo.$3 + 1}/${campo.$3} pontos de código'),
            findsOneWidget,
          );
          await tocar(tester, find.text('Salvar versão'));
          expect(
            find.text('Use até ${campo.$3} pontos de código Unicode.'),
            findsOneWidget,
          );
          expect(t.corpos, isEmpty);

          await digitar(tester, finder, exato);
          expect(
            find.text('${campo.$3}/${campo.$3} pontos de código'),
            findsOneWidget,
          );
          await tocar(tester, find.text('Salvar versão'));
          expect(t.corpos.single[campo.$2], exato);
          expect(tester.takeException(), isNull);
          t.f.cenario.controlador.desconectar();
        },
      );
    }

    testWidgets('${amostra.key}: nome da prévia valida limite antes da API', (
      tester,
    ) async {
      final t = TemplatesFicticios();
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      await tocar(tester, find.text('Aviso local'));
      await tocar(tester, find.text('WhatsApp • versão 1'));
      final finder = find.widgetWithText(
        TextFormField,
        'Nome fictício para prévia',
      );
      final exato = textoNoLimite(amostra.value, 120);
      final excedido = '${exato}a';
      await digitar(tester, finder, excedido);
      expect(tester.widget<TextFormField>(finder).controller!.text, excedido);
      expect(find.text('121/120 pontos de código'), findsOneWidget);
      await tocar(tester, find.text('Gerar prévia'));
      expect(
        find.text('Use até 120 pontos de código Unicode.'),
        findsOneWidget,
      );
      expect(t.corpos, isEmpty);

      await digitar(tester, finder, exato);
      expect(find.text('120/120 pontos de código'), findsOneWidget);
      await tocar(tester, find.text('Gerar prévia'));
      expect((t.corpos.single['valores'] as Map)['nome'], exato);
      expect(find.text('Olá, $exato!'), findsOneWidget);
      expect(tester.takeException(), isNull);
      t.f.cenario.controlador.desconectar();
    });

    testWidgets('${amostra.key}: busca valida pontos antes da consulta', (
      tester,
    ) async {
      final t = TemplatesFicticios();
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      final finder = find.widgetWithText(TextFormField, 'Buscar template');
      final exato = textoNoLimite(amostra.value, 120);
      final excedido = '${exato}a';
      final anteriores = t.chamadas.length;
      await digitar(tester, finder, excedido);
      expect(tester.widget<TextFormField>(finder).controller!.text, excedido);
      expect(find.text('121/120 pontos de código'), findsOneWidget);
      await tocar(tester, find.text('Pesquisar'));
      expect(
        find.text('Use até 120 pontos de código Unicode.'),
        findsOneWidget,
      );
      expect(t.chamadas.length, anteriores);

      await digitar(tester, finder, exato);
      await tocar(tester, find.text('Pesquisar'));
      expect(t.chamadas.last.url.queryParameters['q'], exato);
      expect(tester.takeException(), isNull);
      t.f.cenario.controlador.desconectar();
    });
  }

  testWidgets(
    'prévia rejeita conteúdo acima do limite antes da API sem truncar',
    (tester) async {
      final t = TemplatesFicticios();
      await t.iniciar();
      addTearDown(t.f.dispose);
      await t.abrir(tester);
      await tocar(tester, find.text('Novo template'));
      final content = find.widgetWithText(TextFormField, 'Conteúdo');
      final manual = find.widgetWithText(
        TextFormField,
        'Nome fictício para prévia',
      );
      final exato = textoNoLimite(amostrasUnicode['emoji composto']!, 4000);
      final excedido = '${exato}a';
      await digitar(tester, content, excedido);
      await digitar(tester, manual, 'Fictício');
      await tocar(tester, find.text('Gerar prévia'));
      expect(tester.widget<TextFormField>(content).controller!.text, excedido);
      expect(
        find.text('Use até 4000 pontos de código Unicode.'),
        findsOneWidget,
      );
      expect(t.corpos, isEmpty);

      await digitar(tester, content, exato);
      await digitar(tester, manual, 'Fictício');
      await tocar(tester, find.text('Gerar prévia'));
      expect(t.corpos.single['conteudo'], exato);
      final rendered = tester.widget<SelectableText>(
        find.byKey(const Key('resultado-previa')),
      );
      expect(rendered.data, exato);
      expect(tester.takeException(), isNull);
      t.f.cenario.controlador.desconectar();
    },
  );
}
