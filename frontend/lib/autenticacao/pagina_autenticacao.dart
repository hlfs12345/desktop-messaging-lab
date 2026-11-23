import 'package:flutter/material.dart';

import 'controlador_autenticacao.dart';

class PaginaAutenticacao extends StatelessWidget {
  const PaginaAutenticacao({super.key, required this.controlador});
  final ControladorAutenticacao controlador;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: ListenableBuilder(
        listenable: controlador,
        builder: (context, _) => LayoutBuilder(
          builder: (context, constraints) => SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                minHeight: (constraints.maxHeight - 48).clamp(
                  0,
                  double.infinity,
                ),
              ),
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 460),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Container(
                            width: 48,
                            height: 48,
                            decoration: BoxDecoration(
                              color: Theme.of(context).colorScheme.primary,
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: const Icon(
                              Icons.send_rounded,
                              color: Colors.white,
                              size: 26,
                            ),
                          ),
                          const SizedBox(width: 14),
                          Text(
                            'Messaging Lab',
                            style: Theme.of(context).textTheme.headlineLarge,
                          ),
                        ],
                      ),
                      const SizedBox(height: 28),
                      Card.outlined(
                        child: Padding(
                          padding: const EdgeInsets.all(28),
                          child: _conteudo(context),
                        ),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        'Aplicativo local para Windows',
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: Theme.of(context).colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    ),
  );

  Widget _conteudo(BuildContext context) {
    final estado = controlador.estado;
    if (estado == EstadoAutenticacao.configurar ||
        estado == EstadoAutenticacao.login) {
      return _Formulario(
        key: ValueKey(estado),
        controlador: controlador,
        inicial: estado == EstadoAutenticacao.configurar,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(switch (estado) {
          EstadoAutenticacao.autenticado => 'Sessão iniciada',
          EstadoAutenticacao.logoutPendente => 'Encerrando sessão',
          EstadoAutenticacao.erro => 'Não foi possível consultar o acesso',
          _ => 'Verificando acesso',
        }, style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 20),
        if (controlador.usuario case final usuario?) ...[
          Text(usuario.nome, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text('Login: ${usuario.login}'),
          const SizedBox(height: 24),
        ],
        if (controlador.ocupado || estado == EstadoAutenticacao.consultando)
          const LinearProgressIndicator(
            semanticsLabel: 'Operação em andamento',
          ),
        if (controlador.mensagem case final mensagem?) ...[
          const SizedBox(height: 16),
          Semantics(
            liveRegion: true,
            child: Text(
              mensagem,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
          const SizedBox(height: 16),
        ],
        if (estado == EstadoAutenticacao.autenticado ||
            estado == EstadoAutenticacao.logoutPendente)
          FilledButton(
            onPressed: controlador.ocupado ? null : controlador.sair,
            child: Text(
              estado == EstadoAutenticacao.logoutPendente
                  ? 'Tentar sair novamente'
                  : 'Sair',
            ),
          ),
        if (estado == EstadoAutenticacao.erro)
          FilledButton(
            onPressed: controlador.ocupado ? null : controlador.consultar,
            child: const Text('Tentar novamente'),
          ),
      ],
    );
  }
}

class _Formulario extends StatefulWidget {
  const _Formulario({
    super.key,
    required this.controlador,
    required this.inicial,
  });
  final ControladorAutenticacao controlador;
  final bool inicial;
  @override
  State<_Formulario> createState() => _FormularioState();
}

class _FormularioState extends State<_Formulario> {
  final _form = GlobalKey<FormState>();
  final _nome = TextEditingController();
  final _login = TextEditingController();
  final _senha = TextEditingController();
  final _confirmacao = TextEditingController();
  bool _visivel = false;

  @override
  void dispose() {
    _senha.clear();
    _confirmacao.clear();
    for (final campo in [_nome, _login, _senha, _confirmacao]) {
      campo.dispose();
    }
    super.dispose();
  }

  void _enviar() {
    if (widget.controlador.ocupado || !_form.currentState!.validate()) return;
    final senha = _senha.text;
    _senha.clear();
    _confirmacao.clear();
    _visivel = false;
    if (widget.inicial) {
      widget.controlador.configurar(_nome.text, _login.text, senha);
    } else {
      widget.controlador.entrar(_login.text, senha);
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = widget.controlador;
    return Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            widget.inicial ? 'Configurar administrador' : 'Entrar no Messaging Lab',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 12),
          Text(
            widget.inicial
                ? 'Crie o acesso local para este computador. Guarde sua senha em um local seguro.'
                : 'Use seu login e senha para continuar.',
          ),
          const SizedBox(height: 24),
          if (widget.inicial) ...[
            TextFormField(
              controller: _nome,
              enabled: !auth.ocupado,
              autofocus: true,
              decoration: const InputDecoration(labelText: 'Nome'),
              textInputAction: TextInputAction.next,
              validator: (value) =>
                  value == null ||
                      value.trim().isEmpty ||
                      value.trim().runes.length > 120
                  ? 'Informe um nome de 1 a 120 caracteres.'
                  : null,
            ),
            const SizedBox(height: 16),
          ],
          TextFormField(
            controller: _login,
            enabled: !auth.ocupado,
            autofocus: !widget.inicial,
            decoration: const InputDecoration(
              labelText: 'Login',
              helperText: '3 a 64 letras sem acento, números, ponto, hífen ou sublinhado.',
            ),
            textInputAction: TextInputAction.next,
            autocorrect: false,
            enableSuggestions: false,
            validator: (value) =>
                value == null ||
                    !RegExp(r'^[A-Za-z0-9_.-]{3,64}$').hasMatch(value.trim())
                ? 'Informe um login válido.'
                : null,
          ),
          const SizedBox(height: 16),
          TextFormField(
            controller: _senha,
            enabled: !auth.ocupado,
            obscureText: !_visivel,
            autocorrect: false,
            enableSuggestions: false,
            decoration: InputDecoration(
              labelText: 'Senha',
              helperText:
                  '15 a 128 caracteres. Espaços e acentos são permitidos.',
              suffixIcon: IconButton(
                tooltip: _visivel ? 'Ocultar senha' : 'Mostrar senha',
                onPressed: auth.ocupado
                    ? null
                    : () => setState(() => _visivel = !_visivel),
                icon: Icon(_visivel ? Icons.visibility_off : Icons.visibility),
              ),
            ),
            textInputAction: widget.inicial
                ? TextInputAction.next
                : TextInputAction.done,
            onFieldSubmitted: widget.inicial ? null : (_) => _enviar(),
            // O backend aplica NFC e os limites finais. Não rejeitar aqui uma
            // sequência decomposta que seja válida depois da normalização.
            validator: (value) =>
                value == null ||
                    value.runes.length < 15 ||
                    value.runes.length > 1024
                ? 'Use de 15 a 128 caracteres na senha.'
                : null,
          ),
          if (widget.inicial) ...[
            const SizedBox(height: 16),
            TextFormField(
              controller: _confirmacao,
              enabled: !auth.ocupado,
              obscureText: !_visivel,
              autocorrect: false,
              enableSuggestions: false,
              decoration: const InputDecoration(labelText: 'Confirmar senha'),
              textInputAction: TextInputAction.done,
              onFieldSubmitted: (_) => _enviar(),
              validator: (value) =>
                  value != _senha.text ? 'As senhas devem ser iguais.' : null,
            ),
          ],
          if (auth.mensagem case final mensagem?) ...[
            const SizedBox(height: 20),
            Semantics(
              liveRegion: true,
              child: Text(
                mensagem,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          ],
          const SizedBox(height: 24),
          if (auth.ocupado) ...[
            const LinearProgressIndicator(
              semanticsLabel: 'Operação em andamento',
            ),
            const SizedBox(height: 16),
          ],
          FilledButton(
            onPressed: auth.ocupado ? null : _enviar,
            child: Text(widget.inicial ? 'Criar administrador' : 'Entrar'),
          ),
          if (widget.inicial)
            TextButton(
              onPressed: auth.ocupado ? null : auth.consultar,
              child: const Text('Consultar estado novamente'),
            ),
        ],
      ),
    );
  }
}
