import 'dart:async';
import 'dart:convert';

import 'package:edisk/campanhas/autorizacao_simulacao.dart';
import 'package:edisk/campanhas/preparacao_campanha.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;

http.StreamedResponse json17(Object value, {int codigo = 200}) =>
    resposta(codigo: codigo, corpo: jsonEncode(value));

class F17 {
  final f = auth.Cenario(agora: () => DateTime.utc(2026, 11, 2));
  final contexto = ValueNotifier(0);
  String campanha = 'cp1', preparacao = 'p1', conta = 'conta1';
  String congelada = 'conta1', cenario = 'desconhecida', marcador = 'c' * 64;
  bool confirmada = true, mock = true, autorizada = false, revogada = false;
  bool elegivel = true, avaliada = true;
  String falhaFotografia = 'conteudo_indisponivel';
  int versao = 4, total = 1;
  final pedidos = <http.BaseRequest>[];
  final mutacoes = <Map<String, dynamic>>[];
  FutureOr<http.StreamedResponse>? Function(http.BaseRequest)? interceptar;

  Map<String, dynamic> get cfg => {
    'modo': 'simulacao',
    'provedor': 'mock',
    'plataforma': 'whatsapp',
    'conta_envio_id': congelada,
    'cenario': cenario,
    'resumo_configuracao': 'b' * 64,
  };
  Map<String, dynamic> get camp => {
    'id': campanha,
    'nome': 'Campanha local',
    'conta_envio_id': conta,
    'versao': versao,
    'status': 'preparada',
    'referencias': {
      'conta': {
        'id': conta,
        'provedor': mock ? 'mock' : 'qr_candidato',
        'nome': 'Conta atual',
      },
    },
  };
  Map<String, dynamic> get foto => {
    'preparacao_id': preparacao,
    'impressao': 'a' * 64,
    'confirmado_em': confirmada ? '2026-11-02T12:00:00Z' : null,
    'invalidado_em': null,
    'integridade': 'verificada',
    'bloqueada': false,
    'incluidos': 1,
    'excluidos': 0,
    'total': 1,
    'revisao_desatualizada': false,
    'referencias_congeladas': {
      'conta_envio_id': congelada,
      'template_versao_id': 'v1',
      if (mock) 'configuracao_simulacao': cfg,
    },
    'itens': [
      {
        'identidade_contato_id': 'i1',
        'incluido': true,
        'motivos': <String>[],
        'impedimentos_atuais': <String>[],
      },
    ],
  };
  Map<String, dynamic> get atual => {
    'integridade': 'verificada',
    'incluidos': 1,
    'excluidos': 0,
    'elegiveis_atuais': elegivel ? 1 : 0,
    'impedidos_atuais': elegivel ? 0 : 1,
    'impedimentos': elegivel ? <String>[] : ['categoria_revogada'],
    'condicoes_atuais_atendidas': elegivel,
  };
  Map<String, dynamic> get review => {
    'id': campanha,
    'preparacao_id': preparacao,
    'versao': versao,
    'impressao': 'a' * 64,
    'resumo_configuracao': 'b' * 64,
    'marcador_autorizacao': 'autorizacao-v1.$marcador',
    'autorizavel': elegivel,
    'avaliacao_atual': atual,
    'processamento_disponivel': false,
  };
  Map<String, dynamic> detalhe([String id = 'a1']) => {
    'id': id,
    'campanha_id': campanha,
    'preparacao_id': preparacao,
    'versao': revogada ? 2 : 1,
    'estado': revogada ? 'revogada' : 'vigente',
    'autorizado_por_usuario_id': auth.usuario['id'],
    'autorizado_em': '2026-11-02T12:00:00Z',
    'processamento_disponivel': false,
    'revogado_por_usuario_id': revogada ? auth.usuario['id'] : null,
    'revogado_em': revogada ? '2026-11-02T13:00:00Z' : null,
    'motivo_codigo': revogada ? 'revogacao_manual' : null,
    'validade_atual': {
      if (avaliada)
        ...atual
      else ...{
        'integridade': falhaFotografia == 'integridade_comprometida'
            ? 'comprometida'
            : 'nao_verificada',
        'condicoes_atuais_atendidas': false,
        'impedimentos': [falhaFotografia],
      },
      'avaliada': avaliada,
      'avaliado_em': '2026-11-02T13:00:00Z',
      if (revogada) 'condicoes_atuais_atendidas': false,
    },
  };
  Map<String, dynamic> recibo({bool revogar = false}) => {
    'id': campanha,
    'preparacao_id': preparacao,
    'autorizacao_id': 'a1',
    'versao': versao + 1,
    'versao_autorizacao': revogar ? 2 : 1,
    'estado_decisao': revogar ? 'revogada' : 'vigente',
    'recibo_historico': true,
    'validade_atual': 'nao_avaliada',
    'processamento_disponivel': false,
  };

  Future<void> iniciar({bool expirar = false}) async {
    if (expirar) {
      f.expira = DateTime.utc(2026, 11, 2).add(const Duration(seconds: 5));
    }
    await f.controlador.entrar('teste.local', auth.senhaFicticia);
    f.substituir = responder;
  }

  FutureOr<http.StreamedResponse> responder(http.BaseRequest r) {
    if (r.url.path == '/auth/login') return json17(auth.sessao());
    if (r.url.path == '/auth/logout') return resposta(codigo: 204, corpo: '');
    pedidos.add(r);
    if (r.method == 'POST') {
      mutacoes.add({
        'path': r.url.path,
        ...jsonDecode((r as http.Request).body) as Map<String, dynamic>,
      });
    }
    final custom = interceptar?.call(r);
    if (custom != null) return custom;
    final path = r.url.path;
    if (path.endsWith('/revisao-autorizacao')) return json17(review);
    if (path.endsWith('/autorizacoes-simulacao')) {
      if (r.method == 'POST') {
        autorizada = true;
        return json17(recibo(), codigo: 201);
      }
      final pagina = int.parse(r.url.queryParameters['pagina']!);
      return json17({
        'pagina': pagina,
        'limite': 20,
        'total': autorizada ? total : 0,
        'itens': autorizada
            ? [detalhe(pagina == 1 ? 'a1' : 'a$pagina')]
            : <Object>[],
        'processamento_disponivel': false,
      });
    }
    if (path.endsWith('/revogacao')) {
      revogada = true;
      return json17(recibo(revogar: true));
    }
    if (path.contains('/autorizacoes-simulacao/')) {
      return json17(detalhe(path.split('/').last));
    }
    if (path.endsWith('/preparacoes')) {
      return json17({
        'itens': [
          {'preparacao_id': preparacao, 'integridade': 'verificada'},
        ],
        'total': 1,
      });
    }
    if (path.contains('/preparacoes/')) return json17(foto);
    if (path == '/campanhas/$campanha') return json17(camp);
    return resposta(codigo: 404);
  }

  Widget tela({bool integrada = false}) => MaterialApp(
    home: ValueListenableBuilder<int>(
      valueListenable: contexto,
      builder: (_, _, _) => integrada
          ? PreparacaoCampanha(autenticacao: f.controlador, id: campanha)
          : Scaffold(
              body: ListView(
                padding: const EdgeInsets.all(12),
                children: [
                  AutorizacaoSimulacao(
                    key: const ValueKey('painel'),
                    autenticacao: f.controlador,
                    campanha: camp,
                    fotografia: foto,
                  ),
                ],
              ),
            ),
    ),
  );
  void mudar() => contexto.value++;
}

final cenarios17 = <F17>[];
void teste17(String nome, Future<void> Function(WidgetTester, F17) corpo) {
  testWidgets(nome, (t) async {
    final f = F17();
    cenarios17.add(f);
    t.view.physicalSize = const Size(1200, 1800);
    t.view.devicePixelRatio = 1;
    try {
      await corpo(t, f);
    } finally {
      f.f.controlador.desconectar();
      await t.pumpWidget(const SizedBox());
      await t.pump();
      f.f.dispose();
      f.contexto.dispose();
      t.view.resetPhysicalSize();
      t.view.resetDevicePixelRatio();
      cenarios17.remove(f);
    }
  }, variant: TargetPlatformVariant.only(TargetPlatform.windows));
}

Future<void> tocar17(WidgetTester t, String texto) async {
  final finder = find.text(texto).last;
  await t.ensureVisible(finder);
  await t.tap(finder);
  await t.pumpAndSettle();
}

Future<void> abrir17(WidgetTester t, F17 f, {bool integrada = false}) async {
  await f.iniciar();
  await t.pumpWidget(f.tela(integrada: integrada));
  await t.pumpAndSettle();
}

Future<void> confirmar17(WidgetTester t, {bool revogar = false}) async {
  await t.tap(find.byKey(const ValueKey('confirmacao-autorizacao')));
  await t.pumpAndSettle();
  await tocar17(t, revogar ? 'Confirmar revogação' : 'Confirmar autorização');
}

Future<void> autorizar17(WidgetTester t) async {
  await tocar17(t, 'Revisar autorização');
  await tocar17(t, 'Autorizar simulação');
  await confirmar17(t);
}

void main() {
  teste17('integra preparação confirmada mock sem ações de execução', (
    t,
    f,
  ) async {
    await abrir17(t, f, integrada: true);
    expect(find.byType(AutorizacaoSimulacao), findsOneWidget);
    await tocar17(t, 'Revisar autorização');
    expect(find.text('Conta mock congelada (ID): conta1'), findsOneWidget);
    expect(
      find.text('Cenário congelado: Resultado desconhecido simulado'),
      findsOneWidget,
    );
    expect(find.text('Incluídos originais: 1'), findsOneWidget);
    expect(find.text('Atualmente elegíveis: 1'), findsOneWidget);
    for (final text in ['Iniciar', 'Enviar', 'Pausar', 'Retomar', 'Reenviar']) {
      expect(find.widgetWithText(FilledButton, text), findsNothing);
    }
    expect(f.mutacoes, isEmpty);
  });

  teste17('fotografia real mantém histórico sem nova autorização mock', (
    t,
    f,
  ) async {
    f.mock = false;
    await abrir17(t, f, integrada: true);
    expect(find.byType(AutorizacaoSimulacao), findsOneWidget);
    expect(find.text('Autorizar simulação'), findsNothing);
    expect(find.text('Revisar autorização'), findsNothing);
    expect(f.mutacoes, isEmpty);
    expect(
      f.pedidos.where((r) => r.url.path.endsWith('/revisao-autorizacao')),
      isEmpty,
    );
  });

  teste17('preparação não confirmada não oferece revisão de autorização', (
    t,
    f,
  ) async {
    f.confirmada = false;
    await abrir17(t, f);
    expect(find.text('Revisar autorização'), findsNothing);
    expect(find.text('Autorizar simulação'), findsNothing);
    expect(
      f.pedidos.where((r) => r.url.path.endsWith('/revisao-autorizacao')),
      isEmpty,
    );
  });

  teste17(
    'contagens e impedimentos vêm do backend sem promover zero elegíveis',
    (t, f) async {
      f.elegivel = false;
      await abrir17(t, f);
      await tocar17(t, 'Revisar autorização');
      expect(find.text('Atualmente elegíveis: 0'), findsOneWidget);
      expect(find.text('Atualmente impedidos: 1'), findsOneWidget);
      expect(
        find.text('Impedimento: Consentimento da categoria revogado'),
        findsOneWidget,
      );
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Autorizar simulação'),
            )
            .onPressed,
        isNull,
      );
      expect(f.mutacoes, isEmpty);
    },
  );

  teste17('resposta incompleta não inventa contagens ou permite decisão', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    f.interceptar = (r) {
      if (!r.url.path.endsWith('/revisao-autorizacao')) return null;
      final data = f.review;
      (data['avaliacao_atual'] as Map).remove('elegiveis_atuais');
      return json17(data);
    };
    await tocar17(t, 'Revisar autorização');
    expect(find.textContaining('Resposta incompleta'), findsOneWidget);
    expect(find.text('Autorizar simulação'), findsNothing);
    expect(find.text('Atualmente elegíveis: 0'), findsNothing);
  });

  teste17('confirmar preparação não seleciona confirmação da decisão', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    await tocar17(t, 'Revisar autorização');
    await tocar17(t, 'Autorizar simulação');
    expect(
      t
          .widget<CheckboxListTile>(
            find.byKey(const ValueKey('confirmacao-autorizacao')),
          )
          .value,
      isFalse,
    );
    expect(
      t
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Confirmar autorização'),
          )
          .onPressed,
      isNull,
    );
    await tocar17(t, 'Cancelar');
    expect(f.mutacoes, isEmpty);
  });

  teste17('autorizar exige confirmação e impede duplo clique', (t, f) async {
    await abrir17(t, f);
    await tocar17(t, 'Revisar autorização');
    final acao = t
        .widget<FilledButton>(
          find.widgetWithText(FilledButton, 'Autorizar simulação'),
        )
        .onPressed!;
    acao();
    acao();
    await t.pumpAndSettle();
    expect(find.byType(AlertDialog), findsOneWidget);
    await confirmar17(t);
    expect(f.mutacoes.length, 1);
    expect(f.mutacoes.single['confirmo_simulacao'], isTrue);
    expect(f.mutacoes.single['versao'], 4);
    expect(f.mutacoes.single['impressao'], 'a' * 64);
    expect(
      find.textContaining('Recibo da operação (histórico): vigente'),
      findsOneWidget,
    );
    expect(find.text('Decisão histórica: vigente'), findsOneWidget);
    expect(
      find.textContaining('Condições avaliadas atendidas'),
      findsOneWidget,
    );
  });

  teste17('timeout conserva UUID e consulta não cria outra decisão', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    await tocar17(t, 'Revisar autorização');
    final antiga = Completer<http.StreamedResponse>();
    f.interceptar = (r) => r.method == 'POST' ? antiga.future : null;
    await tocar17(t, 'Autorizar simulação');
    await t.tap(find.byKey(const ValueKey('confirmacao-autorizacao')));
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Confirmar autorização'));
    await t.pump();
    expect(f.mutacoes.length, 1);
    await t.pump(const Duration(seconds: 8));
    await t.pumpAndSettle();
    expect(
      find.textContaining('Resultado da decisão não confirmado.'),
      findsWidgets,
    );
    final primeiro = Map<String, dynamic>.from(f.mutacoes.single);
    f.autorizada =
        true; // Servidor pode ter confirmado antes de perder a resposta.
    f.interceptar = null;
    await tocar17(t, 'Consultar autorizações');
    expect(f.mutacoes.length, 1);
    expect(find.text('Decisão histórica: vigente'), findsOneWidget);
    await tocar17(t, 'Repetir a mesma operação');
    expect(f.mutacoes.length, 2);
    expect(f.mutacoes.last, primeiro);
    antiga.complete(json17(f.recibo(), codigo: 201));
    await t.pumpAndSettle();
    expect(f.mutacoes.length, 2);
    expect(t.takeException(), isNull);
  });

  teste17('409 invalida marcador e entrada alterada recebe UUID novo', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    f.interceptar = (r) =>
        r.method == 'POST' ? json17({'codigo': 'conflito'}, codigo: 409) : null;
    await autorizar17(t);
    final anterior = Map<String, dynamic>.from(f.mutacoes.single);
    expect(find.text('Autorizar simulação'), findsNothing);
    expect(find.textContaining('nova revisão'), findsOneWidget);
    f.interceptar = null;
    f.versao = 5;
    f.marcador = 'd' * 64;
    await autorizar17(t);
    expect(f.mutacoes.last['operacao_id'], isNot(anterior['operacao_id']));
    expect(f.mutacoes.last['versao'], 5);
    expect(
      f.mutacoes.last['marcador_autorizacao'],
      'autorizacao-v1.${'d' * 64}',
    );
  });

  for (final status in [401, 403, 503]) {
    teste17('revisão trata HTTP $status sem autorizar', (t, f) async {
      await abrir17(t, f);
      f.interceptar = (r) => r.url.path.endsWith('/revisao-autorizacao')
          ? json17({
              'codigo': status == 401 ? 'sessao_invalida' : 'indisponivel',
            }, codigo: status)
          : null;
      await tocar17(t, 'Revisar autorização');
      expect(f.mutacoes, isEmpty);
      expect(find.text('Autorizar simulação'), findsNothing);
      expect(
        find.textContaining(
          status == 401
              ? 'Sessão encerrada'
              : status == 403
              ? 'Permissão insuficiente'
              : 'Serviço indisponível',
        ),
        findsOneWidget,
      );
    });
  }

  teste17(
    'histórico vigente distingue impedimento atual e pagina sob demanda',
    (t, f) async {
      f.autorizada = true;
      f.elegivel = false;
      f.total = 41;
      await abrir17(t, f);
      expect(find.text('Decisão histórica: vigente'), findsOneWidget);
      expect(find.text('Autorização atualmente impedida.'), findsOneWidget);
      expect(find.textContaining('Processamento indisponível.'), findsWidgets);
      List<String?> paginas() => f.pedidos
          .where(
            (r) =>
                r.method == 'GET' &&
                r.url.path.endsWith('/autorizacoes-simulacao'),
          )
          .map((r) => r.url.queryParameters['pagina'])
          .toList();
      expect(paginas(), ['1']);
      await tocar17(t, 'Próximas autorizações');
      expect(paginas(), ['1', '2']);
      expect(
        f.pedidos
            .lastWhere((r) => r.url.path.endsWith('/autorizacoes-simulacao'))
            .url
            .queryParameters['limite'],
        '20',
      );
    },
  );

  teste17('revogação confirmada preserva decisão histórica e não reativa', (
    t,
    f,
  ) async {
    f.autorizada = true;
    await abrir17(t, f);
    await tocar17(t, 'Revogar autorização');
    await tocar17(t, 'Cancelar');
    expect(f.mutacoes, isEmpty);
    await tocar17(t, 'Revogar autorização');
    await confirmar17(t, revogar: true);
    expect(f.mutacoes.single['motivo_codigo'], 'revogacao_manual');
    expect(f.mutacoes.single['versao'], 1);
    expect(find.text('Autorização revogada'), findsOneWidget);
    expect(find.text('Decisão histórica: revogada'), findsOneWidget);
    expect(
      t
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Revogar autorização'),
          )
          .onPressed,
      isNull,
    );
    expect(find.text('Reativar'), findsNothing);
  });

  teste17('revogação com timeout repete exatamente o mesmo pedido', (
    t,
    f,
  ) async {
    f.autorizada = true;
    await abrir17(t, f);
    final antiga = Completer<http.StreamedResponse>();
    f.interceptar = (r) => r.method == 'POST' ? antiga.future : null;
    await tocar17(t, 'Revogar autorização');
    await t.tap(find.byKey(const ValueKey('confirmacao-autorizacao')));
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Confirmar revogação'));
    await t.pump();
    await t.pump(const Duration(seconds: 8));
    await t.pumpAndSettle();
    f.revogada = true;
    f.interceptar = null;
    await tocar17(t, 'Repetir a mesma operação');
    expect(f.mutacoes.length, 2);
    expect(f.mutacoes[0], f.mutacoes[1]);
    expect(find.text('Autorização revogada'), findsOneWidget);
    antiga.complete(json17(f.recibo(revogar: true)));
    await t.pumpAndSettle();
  });

  teste17('validade indisponível não inventa contagens e permite revogar', (
    t,
    f,
  ) async {
    f.autorizada = true;
    f.avaliada = false;
    await abrir17(t, f);
    expect(find.text('Validade atual não verificada.'), findsOneWidget);
    expect(find.textContaining('Atualmente elegíveis:'), findsNothing);
    await tocar17(t, 'Revogar autorização');
    await confirmar17(t, revogar: true);
    expect(f.revogada, isTrue);
  });

  teste17('nova sessão fecha confirmação antiga sem despachar decisão', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    await tocar17(t, 'Revisar autorização');
    await tocar17(t, 'Autorizar simulação');
    await t.tap(find.byKey(const ValueKey('confirmacao-autorizacao')));
    await t.pumpAndSettle();
    final antigo = t
        .widget<FilledButton>(
          find.widgetWithText(FilledButton, 'Confirmar autorização'),
        )
        .onPressed!;
    await f.f.controlador.entrar('teste.local', auth.senhaFicticia);
    await t.pumpAndSettle();
    antigo();
    await t.pumpAndSettle();
    expect(f.mutacoes, isEmpty);
    expect(find.byType(AlertDialog), findsNothing);
    expect(t.takeException(), isNull);
  });

  for (final expirar in [false, true]) {
    teste17('perda de sessão invalida diálogo (expirar=$expirar)', (
      t,
      f,
    ) async {
      await f.iniciar(expirar: expirar);
      await t.pumpWidget(f.tela());
      await t.pumpAndSettle();
      await tocar17(t, 'Revisar autorização');
      await tocar17(t, 'Autorizar simulação');
      if (expirar) {
        await t.pump(const Duration(seconds: 6));
      } else {
        f.f.controlador.desconectar();
      }
      await t.pumpAndSettle();
      expect(f.mutacoes, isEmpty);
      expect(find.byType(AlertDialog), findsNothing);
      expect(find.textContaining('Sessão encerrada'), findsOneWidget);
      expect(t.takeException(), isNull);
    });
  }

  for (final alteracao in ['campanha', 'preparacao', 'conta']) {
    teste17('mudança de $alteracao invalida confirmação capturada', (
      t,
      f,
    ) async {
      await abrir17(t, f);
      await tocar17(t, 'Revisar autorização');
      await tocar17(t, 'Autorizar simulação');
      await t.tap(find.byKey(const ValueKey('confirmacao-autorizacao')));
      await t.pumpAndSettle();
      final antigo = t
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Confirmar autorização'),
          )
          .onPressed!;
      if (alteracao == 'campanha') f.campanha = 'cp2';
      if (alteracao == 'preparacao') f.preparacao = 'p2';
      if (alteracao == 'conta') f.conta = 'conta2';
      f.mudar();
      await t.pumpAndSettle();
      antigo();
      await t.pumpAndSettle();
      expect(f.mutacoes, isEmpty);
      expect(find.byType(AlertDialog), findsNothing);
      expect(find.text('Autorizar simulação'), findsNothing);
      expect(t.takeException(), isNull);
    });
  }

  teste17('revisão antiga não atualiza outra preparação', (t, f) async {
    await abrir17(t, f);
    final antiga = Completer<http.StreamedResponse>();
    final velha = f.review;
    f.interceptar = (r) =>
        r.url.path.endsWith('/revisao-autorizacao') ? antiga.future : null;
    await t.tap(find.text('Revisar autorização'));
    await t.pump();
    f.preparacao = 'p2';
    f.interceptar = null;
    f.mudar();
    await t.pumpAndSettle();
    antiga.complete(json17(velha));
    await t.pumpAndSettle();
    expect(find.text('Preparação: p2'), findsOneWidget);
    expect(find.text('Autorizar simulação'), findsNothing);
    expect(f.mutacoes, isEmpty);
    expect(t.takeException(), isNull);
  });

  teste17('descarte de consulta aguardante não cancela outro componente', (
    t,
    f,
  ) async {
    await f.iniciar();
    final terceira = Completer<http.StreamedResponse>();
    var abortada = false;
    f.interceptar = (r) {
      if (r.url.path != '/terceira') return null;
      if (r is http.AbortableRequest) {
        r.abortTrigger?.then((_) => abortada = true);
      }
      return terceira.future;
    };
    final outra = f.f.controlador.negocio('GET', '/terceira');
    await t.pumpWidget(f.tela());
    await t.pump();
    await t.pumpWidget(const MaterialApp(home: Text('Outra tela')));
    await t.pump();
    expect(abortada, isFalse);
    terceira.complete(json17({'outra': true}));
    expect((await outra).dados?['outra'], isTrue);
    await t.pumpAndSettle();
    expect(t.takeException(), isNull);
  });

  teste17('descarte libera transporte para consulta de outro componente', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    final antiga = Completer<http.StreamedResponse>();
    f.interceptar = (r) {
      if (r.url.path.endsWith('/revisao-autorizacao')) return antiga.future;
      if (r.url.path == '/terceira') return json17({'outra': true});
      return null;
    };
    await t.tap(find.text('Revisar autorização'));
    await t.pump();
    final outra = f.f.controlador.negocio('GET', '/terceira');
    await t.pump();
    await t.pumpWidget(const MaterialApp(home: Text('Outra tela')));
    expect((await outra).dados?['outra'], isTrue);
    antiga.complete(json17(f.review));
    await t.pumpAndSettle();
    expect(t.takeException(), isNull);
  });

  teste17('resposta tardia de mutação não recarrega contexto novo', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    await tocar17(t, 'Revisar autorização');
    final antiga = Completer<http.StreamedResponse>();
    final reciboAntigo = f.recibo();
    f.interceptar = (r) => r.method == 'POST' ? antiga.future : null;
    await tocar17(t, 'Autorizar simulação');
    await t.tap(find.byKey(const ValueKey('confirmacao-autorizacao')));
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Confirmar autorização'));
    await t.pump();
    expect(f.mutacoes.length, 1);
    f.campanha = 'cp2';
    f.preparacao = 'p2';
    f.interceptar = null;
    f.mudar();
    await t.pumpAndSettle();
    final consultas = f.pedidos.length;
    antiga.complete(json17(reciboAntigo, codigo: 201));
    await t.pumpAndSettle();
    expect(f.pedidos.length, consultas);
    expect(find.text('Preparação: p2'), findsOneWidget);
    expect(find.textContaining('Recibo da operação'), findsNothing);
    expect(t.takeException(), isNull);
  });

  teste17('mutação aguardando transporte é descartada na perda da sessão', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    await tocar17(t, 'Revisar autorização');
    final terceira = Completer<http.StreamedResponse>();
    f.interceptar = (r) => r.url.path == '/terceira' ? terceira.future : null;
    final outra = f.f.controlador.negocio('GET', '/terceira');
    final encerrada = expectLater(outra, throwsA(anything));
    await tocar17(t, 'Autorizar simulação');
    await t.tap(find.byKey(const ValueKey('confirmacao-autorizacao')));
    await t.pump();
    await t.tap(find.widgetWithText(FilledButton, 'Confirmar autorização'));
    await t.pump();
    f.f.controlador.desconectar();
    await encerrada;
    await t.pumpAndSettle();
    expect(f.mutacoes, isEmpty);
    terceira.complete(json17({'antiga': true}));
    await t.pumpAndSettle();
    expect(f.mutacoes, isEmpty);
    expect(t.takeException(), isNull);
  });

  teste17('503 na decisão conserva pedido e não afirma rollback', (t, f) async {
    await abrir17(t, f);
    f.interceptar = (r) => r.method == 'POST'
        ? json17({'codigo': 'persistencia_indisponivel'}, codigo: 503)
        : null;
    await autorizar17(t);
    expect(
      find.textContaining('Resultado da decisão não confirmado.'),
      findsOneWidget,
    );
    expect(find.text('Repetir a mesma operação'), findsOneWidget);
    f.interceptar = null;
    await tocar17(t, 'Repetir a mesma operação');
    expect(f.mutacoes.length, 2);
    expect(f.mutacoes.first, f.mutacoes.last);
  });

  teste17('recibo de outra preparação não confirma decisão neste contexto', (
    t,
    f,
  ) async {
    await abrir17(t, f);
    f.interceptar = (r) => r.method == 'POST'
        ? json17(f.recibo()..['preparacao_id'] = 'outra', codigo: 201)
        : null;
    await autorizar17(t);
    expect(find.textContaining('Recibo da operação (histórico)'), findsNothing);
    expect(find.text('Repetir a mesma operação'), findsOneWidget);
    f.interceptar = null;
    await tocar17(t, 'Repetir a mesma operação');
    expect(f.mutacoes.first, f.mutacoes.last);
  });

  teste17('consulta indisponível remove validade atual anteriormente exibida', (
    t,
    f,
  ) async {
    f.autorizada = true;
    await abrir17(t, f);
    expect(
      find.textContaining('Condições avaliadas atendidas'),
      findsOneWidget,
    );
    f.interceptar = (r) => r.method == 'GET'
        ? json17({'codigo': 'conteudo_indisponivel'}, codigo: 503)
        : null;
    await tocar17(t, 'Consultar autorizações');
    expect(find.textContaining('Condições avaliadas atendidas'), findsNothing);
    expect(find.textContaining('Serviço indisponível'), findsOneWidget);
  });

  for (final codigo in ['conteudo_indisponivel', 'integridade_comprometida']) {
    teste17('P2 integrado revoga com fotografia indisponível: $codigo', (
      t,
      f,
    ) async {
      f.autorizada = true;
      f.avaliada = false;
      f.falhaFotografia = codigo;
      f.interceptar = (r) {
        final path = r.url.path;
        if (r.method == 'GET' &&
            (path.endsWith('/preparacoes/p1') ||
                (codigo == 'conteudo_indisponivel' &&
                    path.endsWith('/preparacoes')))) {
          return json17({
            'codigo': codigo,
            'detail': 'EDC1.nao-expor +5511999999999',
          }, codigo: 503);
        }
        return null;
      };
      await abrir17(t, f, integrada: true);
      expect(find.byType(PreparacaoCampanha), findsOneWidget);
      expect(find.byType(AutorizacaoSimulacao), findsOneWidget);
      expect(find.text('Decisão histórica: vigente'), findsOneWidget);
      expect(find.text('Validade atual não verificada.'), findsOneWidget);
      expect(find.text('Revisar autorização'), findsNothing);
      expect(find.text('Autorizar simulação'), findsNothing);
      expect(
        find.textContaining('Nenhuma autorização registrada'),
        findsNothing,
      );
      expect(find.textContaining('EDC1.nao-expor'), findsNothing);
      expect(find.textContaining('+5511999999999'), findsNothing);
      final inicio = f.pedidos.length;
      await tocar17(t, 'Revogar autorização');
      await confirmar17(t, revogar: true);
      expect(f.revogada, isTrue);
      expect(
        f.mutacoes.single['path'],
        '/campanhas/cp1/autorizacoes-simulacao/a1/revogacao',
      );
      expect(find.text('Autorização revogada'), findsOneWidget);
      expect(
        f.pedidos
            .skip(inicio)
            .where((r) => r.url.path.contains('/preparacoes')),
        isEmpty,
      );
      expect(
        f.pedidos.where(
          (r) =>
              r.url.path.contains('/conteudo') ||
              r.url.path.endsWith('/revisao-autorizacao'),
        ),
        isEmpty,
      );
    });
  }

  teste17('P2 integrado foto nula consulta histórico sem abrir preparação', (
    t,
    f,
  ) async {
    f.autorizada = true;
    f.interceptar = (r) =>
        r.method == 'GET' && r.url.path.endsWith('/preparacoes')
        ? json17({'itens': <Object>[], 'total': 0})
        : null;
    await abrir17(t, f, integrada: true);
    final painel = t.widget<AutorizacaoSimulacao>(
      find.byType(AutorizacaoSimulacao),
    );
    expect(painel.fotografia, isNull);
    expect(find.text('Decisão histórica: vigente'), findsOneWidget);
    expect(find.text('Revisar autorização'), findsNothing);
    expect(find.text('Autorizar simulação'), findsNothing);
    final inicio = f.pedidos.length;
    await tocar17(t, 'Consultar autorizações');
    expect(
      f.pedidos
          .skip(inicio)
          .every(
            (r) =>
                r.method == 'GET' &&
                r.url.path.contains('/autorizacoes-simulacao'),
          ),
      isTrue,
    );
    expect(
      f.pedidos.where((r) => r.url.path.contains('/preparacoes/')),
      isEmpty,
    );
    expect(f.mutacoes, isEmpty);
  });

  for (final falhar in [false, true]) {
    teste17('P2 integrado distingue vazio de falha (falhar=$falhar)', (
      t,
      f,
    ) async {
      f.interceptar = (r) {
        if (r.url.path.endsWith('/preparacoes')) {
          return json17({'itens': <Object>[], 'total': 0});
        }
        if (falhar && r.url.path.endsWith('/autorizacoes-simulacao')) {
          return json17({'codigo': 'persistencia_indisponivel'}, codigo: 503);
        }
        return null;
      };
      await abrir17(t, f, integrada: true);
      expect(find.byType(AutorizacaoSimulacao), findsOneWidget);
      expect(
        find.text('Nenhuma autorização registrada nesta campanha.'),
        falhar ? findsNothing : findsOneWidget,
      );
      expect(
        find.text('Histórico de autorizações não carregado.'),
        falhar ? findsOneWidget : findsNothing,
      );
      expect(find.text('Autorizar simulação'), findsNothing);
    });
  }

  teste17('P2 integrado não aplica histórico pendente da sessão anterior', (
    t,
    f,
  ) async {
    f.autorizada = true;
    final antiga = Completer<http.StreamedResponse>();
    final respostaAntiga = {
      'pagina': 1,
      'limite': 20,
      'total': 1,
      'itens': [f.detalhe()],
      'processamento_disponivel': false,
    };
    f.interceptar = (r) {
      if (r.url.path.endsWith('/preparacoes')) {
        return json17({'itens': <Object>[], 'total': 0});
      }
      if (r.url.path.endsWith('/autorizacoes-simulacao')) return antiga.future;
      return null;
    };
    await f.iniciar();
    await t.pumpWidget(f.tela(integrada: true));
    await t.pump();
    await t.pump();
    expect(
      f.pedidos.any((r) => r.url.path.endsWith('/autorizacoes-simulacao')),
      isTrue,
    );
    // Interrompe o transporte antigo antes do login no transporte único da fixture.
    await f.f.cliente.cancelarEAguardarNegocio();
    f.autorizada = false;
    f.interceptar = (r) => r.url.path.endsWith('/preparacoes')
        ? json17({'itens': <Object>[], 'total': 0})
        : null;
    await f.f.controlador.entrar('teste.local', auth.senhaFicticia);
    await t.pumpAndSettle();
    final consultas = f.pedidos.length;
    antiga.complete(json17(respostaAntiga));
    await t.pumpAndSettle();
    expect(f.pedidos.length, consultas);
    expect(find.text('Decisão histórica: vigente'), findsNothing);
    expect(
      find.text('Nenhuma autorização registrada nesta campanha.'),
      findsOneWidget,
    );
    expect(f.mutacoes, isEmpty);
    expect(t.takeException(), isNull);
  });

  teste17(
    'P2 integrado cancelar histórico aguardante preserva outro componente',
    (t, f) async {
      f.interceptar = (r) => r.url.path.endsWith('/preparacoes')
          ? json17({'itens': <Object>[], 'total': 0})
          : null;
      await abrir17(t, f, integrada: true);
      final outra = Completer<http.StreamedResponse>();
      var abortada = false;
      f.interceptar = (r) {
        if (r.url.path == '/terceira') {
          if (r is http.AbortableRequest) {
            r.abortTrigger?.then((_) => abortada = true);
          }
          return outra.future;
        }
        return null;
      };
      final externa = f.f.controlador.negocio('GET', '/terceira');
      await t.tap(find.text('Consultar autorizações'));
      await t.pump();
      await t.pumpWidget(const MaterialApp(home: Text('Outra tela')));
      await t.pump();
      expect(abortada, isFalse);
      outra.complete(json17({'outra': true}));
      expect((await externa).dados?['outra'], isTrue);
      await t.pumpAndSettle();
      expect(f.mutacoes, isEmpty);
      expect(t.takeException(), isNull);
    },
  );

  teste17('P2 integrado falha de chave remove revisão previamente carregada', (
    t,
    f,
  ) async {
    await abrir17(t, f, integrada: true);
    await tocar17(t, 'Revisar autorização');
    expect(find.text('Autorizar simulação'), findsOneWidget);
    f.interceptar = (r) => r.url.path.endsWith('/preparacoes/p1')
        ? json17({'codigo': 'conteudo_indisponivel'}, codigo: 503)
        : null;
    await tocar17(t, 'Fotografia p1');
    expect(
      t
          .widget<AutorizacaoSimulacao>(find.byType(AutorizacaoSimulacao))
          .fotografia,
      isNull,
    );
    expect(find.text('Autorizar simulação'), findsNothing);
    expect(find.text('Revisar autorização'), findsNothing);
    expect(find.text('Consultar autorizações'), findsOneWidget);
    expect(f.mutacoes, isEmpty);
  });
}
