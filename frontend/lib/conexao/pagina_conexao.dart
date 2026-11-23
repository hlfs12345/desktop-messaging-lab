import 'package:flutter/material.dart';

import 'cliente_health.dart';
import 'controlador_conexao.dart';

/// Painel temporário de diagnóstico da Entrega 4, sem navegação de produto.
class PaginaConexao extends StatelessWidget {
  const PaginaConexao({super.key, required this.controlador});

  final ControladorConexao controlador;

  @override
  Widget build(BuildContext context) {
    final tema = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: ListenableBuilder(
          listenable: controlador,
          builder: (context, _) {
            final estado = controlador.estado;
            final cores = tema.colorScheme;
            final titulo = switch (estado) {
              EstadoConexao.inicializando => 'Verificando conexão',
              EstadoConexao.disponivel => 'Disponível',
              EstadoConexao.indisponivel => 'Indisponível',
              EstadoConexao.erro => 'Erro de conexão',
              EstadoConexao.iniciandoBackend => 'Iniciando serviço local',
              EstadoConexao.preparandoPersistencia => 'Preparando dados locais',
              EstadoConexao.falhaInicializacao => 'Falha na inicialização',
              EstadoConexao.backendEncerrado => 'Serviço local encerrado',
              EstadoConexao.encerrando => 'Encerrando aplicativo',
            };
            final icone = switch (estado) {
              EstadoConexao.inicializando => Icons.sync,
              EstadoConexao.disponivel => Icons.check_circle_outline,
              EstadoConexao.indisponivel => Icons.cloud_off_outlined,
              EstadoConexao.erro => Icons.error_outline,
              EstadoConexao.iniciandoBackend ||
              EstadoConexao.preparandoPersistencia ||
              EstadoConexao.encerrando => Icons.sync,
              EstadoConexao.falhaInicializacao ||
              EstadoConexao.backendEncerrado => Icons.error_outline,
            };
            final cor = switch (estado) {
              EstadoConexao.disponivel =>
                tema.brightness == Brightness.light
                    ? const Color(0xFF17683B)
                    : const Color(0xFF8AD8A8),
              EstadoConexao.indisponivel || EstadoConexao.erro => cores.error,
              _ => cores.primary,
            };
            final ultima = controlador.ultimaVerificacao?.toLocal();
            return SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Align(
                alignment: Alignment.topCenter,
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 880),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Messaging Lab',
                        style: tema.textTheme.headlineLarge?.copyWith(
                          fontWeight: FontWeight.w700,
                          color: cores.primary,
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(
                        'Estado do aplicativo',
                        style: tema.textTheme.titleLarge,
                      ),
                      const SizedBox(height: 32),
                      Card.outlined(
                        margin: EdgeInsets.zero,
                        child: Padding(
                          padding: const EdgeInsets.all(24),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Conexão com o serviço local',
                                style: tema.textTheme.titleLarge,
                              ),
                              const SizedBox(height: 24),
                              Semantics(
                                liveRegion: true,
                                child: Row(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    ExcludeSemantics(
                                      child: Icon(icone, color: cor, size: 28),
                                    ),
                                    const SizedBox(width: 12),
                                    Expanded(
                                      child: Text(
                                        titulo,
                                        style: tema.textTheme.titleLarge
                                            ?.copyWith(color: cor),
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              const SizedBox(height: 12),
                              Text(
                                _mensagem(controlador),
                                style: tema.textTheme.bodyLarge,
                              ),
                              if (controlador.emAndamento ||
                                  controlador.preparando ||
                                  estado == EstadoConexao.encerrando) ...[
                                const SizedBox(height: 20),
                                const LinearProgressIndicator(
                                  semanticsLabel: 'Operação em andamento',
                                ),
                              ],
                              const SizedBox(height: 24),
                              Text(
                                ultima == null
                                    ? 'Nenhuma verificação concluída.'
                                    : 'Última verificação: '
                                          '${MaterialLocalizations.of(context).formatTimeOfDay(TimeOfDay.fromDateTime(ultima), alwaysUse24HourFormat: true)}',
                              ),
                              const SizedBox(height: 8),
                              const Text(
                                'A conexão é verificada na abertura e por ação manual. '
                                'O encerramento do serviço é detectado automaticamente.',
                              ),
                              const SizedBox(height: 24),
                              if (controlador.podeVerificar ||
                                  controlador.emAndamento)
                                FilledButton.icon(
                                  onPressed: controlador.podeVerificar
                                      ? controlador.verificar
                                      : null,
                                  icon: const Icon(Icons.refresh),
                                  label: Text(
                                    controlador.emAndamento
                                        ? 'Verificando…'
                                        : estado == EstadoConexao.disponivel
                                        ? 'Verificar novamente'
                                        : 'Tentar novamente',
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );
  }

  String _mensagem(ControladorConexao controlador) {
    switch (controlador.estado) {
      case EstadoConexao.iniciandoBackend:
        return 'Aguarde enquanto o Messaging Lab inicia o serviço local.';
      case EstadoConexao.preparandoPersistencia:
        return 'Os dados locais estão sendo preparados e verificados.';
      case EstadoConexao.falhaInicializacao:
        if (controlador.codigo == 'backend_missing') {
          return 'O executável do serviço local está ausente ou inacessível. Obtenha novamente a pasta completa do Messaging Lab.';
        }
        if (controlador.codigo == 'launch') {
          return 'O Windows não conseguiu executar o serviço local. Verifique se a pasta está completa e se a execução é permitida.';
        }
        if (controlador.codigo == 'configuration') {
          return 'A configuração de execução ou os recursos do aplicativo são inválidos. Obtenha novamente a pasta completa do Messaging Lab.';
        }
        return controlador.codigo == 'instance_in_use'
            ? 'Outra instância do Messaging Lab já está usando estes dados. Feche esta janela e use a instância aberta.'
            : 'Não foi possível iniciar o serviço local. Feche e abra o Messaging Lab novamente. Se persistir, procure suporte.';
      case EstadoConexao.backendEncerrado:
        return 'O serviço local encerrou inesperadamente. Feche e abra o Messaging Lab novamente.';
      case EstadoConexao.encerrando:
        return 'Aguarde o encerramento seguro do aplicativo.';
      default:
        break;
    }
    if (controlador.emAndamento) {
      return 'Verificando conexão com o serviço local…';
    }
    return switch (controlador.resultado) {
      ResultadoHealth.disponivel => 'O serviço local respondeu corretamente.',
      ResultadoHealth.indisponivel =>
        'O serviço local respondeu que está indisponível. '
            'Aguarde um momento e tente novamente.',
      ResultadoHealth.falhaConexao =>
        'Não foi possível conectar ao serviço local. '
            'Verifique se ele está em execução e tente novamente.',
      ResultadoHealth.tempoEsgotado =>
        'O serviço local não respondeu dentro do prazo. Tente novamente.',
      ResultadoHealth.respostaInvalida || ResultadoHealth.httpInesperado =>
        'O serviço local retornou uma resposta inesperada. '
            'Tente novamente. Se o problema persistir, procure suporte.',
      ResultadoHealth.cancelada => 'A verificação foi interrompida.',
      null =>
        'A conexão local não foi configurada corretamente. '
            'Reinicie o aplicativo com a configuração de desenvolvimento válida.',
    };
  }
}
