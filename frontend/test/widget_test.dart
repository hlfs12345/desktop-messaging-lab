import 'package:edisk/main.dart' as entrada;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('entrada sem runner informa falha sem abrir HTTP', (
    tester,
  ) async {
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(
      const MethodChannel('edisk/backend'),
      (_) async => {'sequence': 1, 'state': 'failed', 'code': 'configuration'},
    );
    addTearDown(() {
      messenger.setMockMethodCallHandler(
        const MethodChannel('edisk/backend'),
        null,
      );
    });
    entrada.main();
    await tester.pumpAndSettle();
    expect(find.text('eDisk'), findsOneWidget);
    expect(find.text('Estado do aplicativo'), findsOneWidget);
    expect(find.text('Falha na inicialização'), findsOneWidget);
    expect(find.text('Nenhuma verificação concluída.'), findsOneWidget);
    expect(find.byType(FilledButton), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
