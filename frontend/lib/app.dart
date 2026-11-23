import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'conexao/controlador_conexao.dart';
import 'conexao/pagina_conexao.dart';
import 'autenticacao/controlador_autenticacao.dart';
import 'autenticacao/pagina_autenticacao.dart';
import 'estrutura_principal.dart';
import 'tema.dart';

/// A raiz assume a propriedade do controlador e o descarta ao sair.
class EdiskApp extends StatefulWidget {
  const EdiskApp({super.key, required this.controlador, this.autenticacao});

  final ControladorConexao controlador;
  final ControladorAutenticacao? autenticacao;

  @override
  State<EdiskApp> createState() => _EdiskAppState();
}

class _EdiskAppState extends State<EdiskApp> {
  @override
  void didUpdateWidget(EdiskApp oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controlador != widget.controlador) {
      oldWidget.controlador.dispose();
    }
    if (oldWidget.autenticacao != widget.autenticacao) {
      oldWidget.autenticacao?.dispose();
    }
  }

  @override
  void dispose() {
    widget.controlador.dispose();
    widget.autenticacao?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Messaging Lab',
      debugShowCheckedModeBanner: false,
      locale: const Locale('pt', 'BR'),
      supportedLocales: const [Locale('pt', 'BR')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      theme: temaEdisk(Brightness.light),
      darkTheme: temaEdisk(Brightness.dark),
      themeMode: ThemeMode.system,
      home: ListenableBuilder(
        listenable: Listenable.merge([widget.controlador, widget.autenticacao]),
        builder: (context, _) {
          final auth = widget.autenticacao;
          if (widget.controlador.estado == EstadoConexao.disponivel &&
              auth?.estado == EstadoAutenticacao.autenticado) {
            // As rotas e diálogos de negócio pertencem à sessão autenticada.
            // Trocar a home por login destrói também as rotas internas.
            return Navigator(
              onGenerateRoute: (_) => MaterialPageRoute<void>(
                builder: (_) => EstruturaPrincipal(autenticacao: auth!),
              ),
            );
          }
          return widget.controlador.estado == EstadoConexao.disponivel &&
                  auth != null &&
                  auth.estado != EstadoAutenticacao.desconectado
              ? PaginaAutenticacao(controlador: auth)
              : PaginaConexao(controlador: widget.controlador);
        },
      ),
    );
  }
}
