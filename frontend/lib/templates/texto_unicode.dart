import 'package:flutter/material.dart';

/// Mesma unidade de len(str) no backend: pontos de código, sem normalizar o texto.
String? validarLimiteTemplate(String? texto, int limite) {
  if ((texto ?? '').runes.length > limite) {
    return 'Use até $limite pontos de código Unicode.';
  }
  return null;
}

InputCounterWidgetBuilder contadorTemplate(
  TextEditingController controlador,
  int limite,
) =>
    (
      BuildContext context, {
      required int currentLength,
      required bool isFocused,
      required int? maxLength,
    }) {
      final quantidade = controlador.text.runes.length;
      return Text(
        '$quantidade/$limite pontos de código',
        style: Theme.of(context).textTheme.bodySmall?.copyWith(
          color: quantidade > limite
              ? Theme.of(context).colorScheme.error
              : null,
        ),
      );
    };
