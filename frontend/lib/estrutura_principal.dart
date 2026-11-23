import 'package:flutter/material.dart';

import 'autenticacao/controlador_autenticacao.dart';
import 'cadastro/pagina_cadastro.dart';
import 'cadastro/pagina_consentimentos.dart';
import 'campanhas/pagina_campanhas.dart';
import 'contas_envio/pagina_contas.dart';
import 'templates/pagina_templates.dart';

class EstruturaPrincipal extends StatefulWidget {
  const EstruturaPrincipal({super.key, required this.autenticacao});

  final ControladorAutenticacao autenticacao;

  @override
  State<EstruturaPrincipal> createState() => _EstruturaPrincipalState();
}

class _EstruturaPrincipalState extends State<EstruturaPrincipal> {
  int selecionado = 0;
  late final List<Widget?> paginas = [
    PaginaCadastro(autenticacao: widget.autenticacao),
    null,
    null,
    if (administrador) null,
    null,
  ];

  bool get administrador =>
      widget.autenticacao.usuario?.perfil == 'administrador';

  void selecionar(int indice) {
    setState(() {
      selecionado = indice;
      paginas[indice] ??= switch (indice) {
        1 => PaginaCampanhas(autenticacao: widget.autenticacao),
        2 => PaginaTemplates(autenticacao: widget.autenticacao),
        3 when administrador => PaginaContas(autenticacao: widget.autenticacao),
        3 ||
        4 => PaginaConfiguracoesAvancadas(autenticacao: widget.autenticacao),
        _ => PaginaCadastro(autenticacao: widget.autenticacao),
      };
    });
  }

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      final expandida = constraints.maxWidth >= 1100;
      final destinos = <NavigationRailDestination>[
        const NavigationRailDestination(
          icon: Tooltip(
            message: 'Contatos e listas',
            child: Icon(Icons.people_outline),
          ),
          selectedIcon: Icon(Icons.people),
          label: Text('Contatos'),
        ),
        const NavigationRailDestination(
          icon: Tooltip(
            message: 'Campanhas',
            child: Icon(Icons.campaign_outlined),
          ),
          selectedIcon: Icon(Icons.campaign),
          label: Text('Campanhas'),
        ),
        const NavigationRailDestination(
          icon: Tooltip(
            message: 'Templates',
            child: Icon(Icons.article_outlined),
          ),
          selectedIcon: Icon(Icons.article),
          label: Text('Templates'),
        ),
        if (administrador)
          const NavigationRailDestination(
            icon: Tooltip(
              message: 'Contas de envio',
              child: Icon(Icons.manage_accounts_outlined),
            ),
            selectedIcon: Icon(Icons.manage_accounts),
            label: Text('Contas de envio'),
          ),
        const NavigationRailDestination(
          icon: Tooltip(
            message: 'Configurações avançadas',
            child: Icon(Icons.settings_outlined),
          ),
          selectedIcon: Icon(Icons.settings),
          label: Text('Configurações avançadas'),
        ),
      ];
      return Scaffold(
        body: Row(
          children: [
            NavigationRail(
              extended: expandida,
              minWidth: 76,
              minExtendedWidth: 216,
              selectedIndex: selecionado.clamp(0, destinos.length - 1),
              onDestinationSelected: selecionar,
              leading: Padding(
                padding: const EdgeInsets.only(top: 12, bottom: 20),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(
                      Icons.send_rounded,
                      size: 26,
                      color: Colors.white,
                    ),
                    if (expandida) ...[
                      const SizedBox(width: 12),
                      Text(
                        'Messaging Lab',
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          color: Colors.white,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              trailing: Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      if (expandida)
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          child: Text(
                            widget.autenticacao.usuario?.nome ?? '',
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            textAlign: TextAlign.center,
                            style: const TextStyle(color: Color(0xFFCBD5E1)),
                          ),
                        ),
                      const SizedBox(height: 8),
                      Tooltip(
                        message: 'Sair',
                        child: TextButton.icon(
                          style: TextButton.styleFrom(
                            foregroundColor: const Color(0xFFCBD5E1),
                          ),
                          onPressed: widget.autenticacao.sair,
                          icon: const Icon(Icons.logout),
                          label: expandida
                              ? const Text('Sair')
                              : const SizedBox.shrink(),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              destinations: destinos,
            ),
            const VerticalDivider(width: 1, thickness: 1),
            Expanded(
              child: IndexedStack(
                index: selecionado,
                children: [
                  for (final pagina in paginas)
                    pagina ?? const SizedBox.shrink(),
                ],
              ),
            ),
          ],
        ),
      );
    },
  );
}
