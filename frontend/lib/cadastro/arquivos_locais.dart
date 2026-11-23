import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart';

class ArquivoImportacao {
  const ArquivoImportacao(this.nome, this.bytes);
  final String nome;
  final List<int> bytes;
}

/// Somente arquivos escolhidos explicitamente nos diálogos nativos.
class ArquivosLocais {
  static const _canal = MethodChannel('edisk/file_dialogs');
  Future<ArquivoImportacao?> abrir() async {
    final path = await _canal.invokeMethod<String>('abrir');
    if (path == null) return null;
    const limite = 10 * 1024 * 1024;
    final file = File(path);
    if (await file.length() > limite) {
      throw const FormatException('Arquivo excessivo');
    }
    final buffer = BytesBuilder(copy: false);
    await for (final chunk in file.openRead(0, limite + 1)) {
      buffer.add(chunk);
      if (buffer.length > limite) {
        throw const FormatException('Arquivo excessivo');
      }
    }
    return ArquivoImportacao(file.uri.pathSegments.last, buffer.takeBytes());
  }

  Future<bool> salvar(List<int> bytes) async {
    final path = await _canal.invokeMethod<String>('salvar');
    if (path == null) return false;
    await File(path).writeAsBytes(bytes, flush: true);
    return true;
  }
}
