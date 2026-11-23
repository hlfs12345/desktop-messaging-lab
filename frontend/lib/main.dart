import 'dart:io';

import 'package:flutter/material.dart';
import 'package:http/io_client.dart';

import 'app.dart';
import 'autenticacao/cliente_autenticacao.dart';
import 'autenticacao/controlador_autenticacao.dart';
import 'conexao/cliente_health.dart';
import 'conexao/controlador_conexao.dart';
import 'conexao/transporte_local.dart';
import 'processos/supervisor_backend.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final autenticacao = ControladorAutenticacao(
    criarCliente: (contexto) => ClienteAutenticacao(
      TransporteLocal(
        endpoint: contexto.endpoint,
        token: contexto.token,
        transporte: criarTransporteLocal(),
      ),
      negocio: TransporteLocal(
        endpoint: contexto.endpoint,
        token: contexto.token,
        transporte: criarTransporteLocal(),
        prazo: const Duration(seconds: 30),
      ),
    ),
  );
  runApp(
    EdiskApp(
      autenticacao: autenticacao,
      controlador: ControladorConexao(
        supervisor: SupervisorWindows(),
        aoConectar: autenticacao.conectar,
        aoInvalidar: autenticacao.desconectar,
        aoDisponivel: autenticacao.iniciar,
        criarCliente: (contexto) => ClienteHealth(
          endpoint: contexto.endpoint,
          token: contexto.token,
          transporte: criarTransporteLocal(),
        ),
      ),
    ),
  );
}

/// A comunicação local ignora proxies do ambiente e do sistema.
IOClient criarTransporteLocal() =>
    IOClient(HttpClient()..findProxy = (_) => 'DIRECT');
