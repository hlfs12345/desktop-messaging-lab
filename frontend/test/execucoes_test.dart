import 'dart:async';
import 'dart:convert';

import 'package:edisk/campanhas/controlador_execucoes.dart';
import 'package:edisk/campanhas/execucao_mock.dart';
import 'package:edisk/campanhas/pagina_execucoes.dart';
import 'package:edisk/campanhas/preparacao_campanha.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;
import 'autorizacoes_simulacao_test.dart' as anterior;

class F18 {
  final a = auth.Cenario();
  final pedidos = <http.BaseRequest>[];
  final corpos = <Map<String, dynamic>>[];
  final recibos = <String, Map<String, dynamic>>{};
  String estado = 'executando', marcador = 'a' * 64;
  int versao = 1, geracao = 1, total = 40;
  bool existe = false, ativada = true, indisponivel = false;
  FutureOr<http.StreamedResponse>? Function(http.BaseRequest)? interceptar;
  Map<String, dynamic> get cfg => {
    'modo': 'simulacao',
    'provedor': 'mock',
    'plataforma': 'whatsapp',
    'cenario': 'desconhecida',
    'resumo_configuracao': 'c' * 64,
  };
  Map<String, dynamic> get execucao => {
    'id': 'e1',
    'campanha_id': 'c1',
    'preparacao_id': 'p1',
    'autorizacao_id': 'a1',
    'estado': estado,
    'versao': versao,
    'geracao': geracao,
    'criado_em': '2026-11-04T10:00:00.123456Z',
    'atualizado_em': '2026-11-04T10:01:00.654321Z',
    'simulacao': true,
    'validade_atual': 'nao_avaliada',
    'contagens': {
      for (final s in estadosItens)
        s: s == 'pendente'
            ? total - 2
            : (s == 'desconhecida' || s == 'ignorado' ? 1 : 0),
    },
    'operacional': {
      'admitida': ativada && estado == 'executando',
      'worker_ativo': ativada && estado == 'executando',
      'processamento_disponivel': true,
      'recuperacao_disponivel': true,
      'encerrando': false,
    },
  };
  Map<String, dynamic> review(bool retomar) => {
    if (retomar) 'campanha_id': 'c1' else 'id': 'c1',
    if (retomar) 'execucao_id': 'e1',
    'preparacao_id': 'p1',
    'autorizacao_id': 'a1',
    'versao': versao,
    'versao_autorizacao': 1,
    'geracao': geracao,
    'impressao': 'b' * 64,
    'resumo_configuracao': 'c' * 64,
    if (retomar)
      'marcador_retomada': 'ativar-v1.$marcador'
    else
      'marcador_inicio': 'inicio-v1.$marcador',
    if (retomar) 'retomavel': true else 'iniciavel': true,
    'simulacao': true,
    'processamento_disponivel': true,
    'configuracao_simulacao': cfg,
    'avaliacao_atual': {
      'incluidos': 40,
      'excluidos': 0,
      'elegiveis_atuais': 39,
      'impedidos_atuais': 1,
    },
    'pendentes': 38,
    'elegiveis_pendentes': 38,
  };
  http.StreamedResponse json(Object d, [int codigo = 200]) =>
      resposta(codigo: codigo, corpo: jsonEncode(d));
  Map<String, dynamic> pagina(
    http.BaseRequest r,
    List<Map<String, dynamic>> rows,
    int total,
  ) => {
    'itens': rows,
    'total': total,
    'pagina': int.parse(r.url.queryParameters['pagina']!),
    'limite': 20,
    'simulacao': true,
  };
  http.StreamedResponse base(http.BaseRequest r) {
    final path = r.url.path;
    if (path == '/campanhas/c1') {
      return json({
        'id': 'c1',
        'referencias': {
          'conta': {'nome': 'Conta de teste'},
        },
      });
    }
    if (path.endsWith('/revisao-inicio')) return json(review(false));
    if (path.endsWith('/revisao-retomada')) return json(review(true));
    if (r.method == 'POST') {
      final body = jsonDecode((r as http.Request).body) as Map<String, dynamic>;
      corpos.add(body);
      final id = body['operacao_id'] as String;
      final old = recibos[id];
      if (old != null) {
        return json({
          ...old,
          'replay': true,
          'atual': execucao,
          'ativacao': {
            'solicitada': false,
            'admissao_confirmada': false,
            'erro_codigo': null,
          },
        }, path.endsWith('/execucoes') ? 201 : 200);
      }
      existe = true;
      if (path.endsWith('/pausa')) {
        estado = 'pausada';
        versao++;
      } else {
        estado = ativada ? 'executando' : 'pausada';
        if (path.endsWith('/retomada')) {
          versao++;
          geracao++;
        }
      }
      final data = {
        'recibo': {
          'id': 'c1',
          'operacao_id': id,
          'execucao_id': 'e1',
          'preparacao_id': 'p1',
          'autorizacao_id': 'a1',
          'versao_execucao': versao,
          'recibo_historico': true,
          'validade_atual': 'nao_avaliada',
          'processamento_disponivel': false,
        },
        'replay': false,
        'simulacao': true,
        'atual': execucao,
        'ativacao': {
          'solicitada': !path.endsWith('/pausa'),
          'admissao_confirmada': ativada,
          'erro_codigo': ativada ? null : 'ativacao_indisponivel',
        },
      };
      recibos[id] = data;
      return json(data, path.endsWith('/execucoes') ? 201 : 200);
    }
    if (indisponivel) {
      return json({
        'codigo': 'conteudo_indisponivel',
        'detail': 'PII privada',
      }, 503);
    }
    if (path.endsWith('/tentativas')) {
      return json(
        pagina(r, [
          {
            'id': 't1',
            'execucao_id': 'e1',
            'item_id': path.split('/')[6],
            'numero': 1,
            'estado': 'desconhecida',
            'reservada_em': '2026-11-04T10:00:00Z',
            'invocacao_em': '2026-11-04T10:00:01Z',
            'finalizada_em': '2026-11-04T10:00:02Z',
          },
        ], 1),
      );
    }
    if (path.endsWith('/itens')) {
      final p = int.parse(r.url.queryParameters['pagina']!);
      return json(
        pagina(r, [
          for (var i = (p - 1) * 20; i < p * 20 && i < total; i++)
            {
              'id': 'i$i',
              'execucao_id': 'e1',
              'ordem': i + 1,
              'estado': i == 0 ? 'desconhecida' : 'pendente',
              'atualizado_em': '2026-11-04T10:00:00Z',
            },
        ], total),
      );
    }
    if (path.endsWith('/execucoes')) {
      return json(pagina(r, existe ? [execucao] : [], existe ? 1 : 0));
    }
    if (path.endsWith('/e1')) return json(execucao);
    return resposta(codigo: 404);
  }

  FutureOr<http.StreamedResponse> responder(http.BaseRequest r) {
    pedidos.add(r);
    return interceptar?.call(r) ?? base(r);
  }

  Future<void> entrar() async {
    await a.controlador.entrar('teste.local', auth.senhaFicticia);
    a.substituir = responder;
  }

  ControladorExecucoes controller() => ControladorExecucoes(
    autenticacao: a.controlador,
    campanhaId: 'c1',
    preparacaoId: 'p1',
  );
  Widget tela() => MaterialApp(
    home: PaginaExecucoes(
      autenticacao: a.controlador,
      campanhaId: 'c1',
      preparacaoId: 'p1',
    ),
  );
}

void main() {
  testWidgets(
    '8000 registros mantêm dashboard paginado e pausa/retomada explícitas',
    (tester) async {
      final f = F18();
      await f.entrar();
      f.total = 8000;
      f.existe = true;
      addTearDown(f.a.dispose);
      await tester.pumpWidget(f.tela());
      await tester.pumpAndSettle();
      await tester.tap(find.text('Executando simulação'));
      await tester.pumpAndSettle();
      expect(find.text('Total: 8000'), findsOneWidget);
      expect(f.pedidos.last.url.queryParameters['limite'], '20');
      await tester.scrollUntilVisible(find.text('Pausar'), 150);
      await tester.tap(find.text('Pausar'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(
              find.byKey(const ValueKey('confirmar-execucao')),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.byKey(const ValueKey('confirmacao-execucao')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('confirmar-execucao')));
      await tester.pumpAndSettle();
      expect(f.corpos.single.keys, contains('versao'));
      expect(f.estado, 'pausada');
      await tester.scrollUntilVisible(find.text('Revisar retomada'), 150);
      await tester.tap(find.text('Revisar retomada'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Confirmar retomada'), 200);
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Confirmar retomada'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirmar retomada'));
      await tester.pumpAndSettle();
      expect(f.corpos.length, 1);
      await tester.tap(find.byKey(const ValueKey('confirmacao-execucao')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('confirmar-execucao')));
      await tester.pumpAndSettle();
      expect(f.corpos.last['confirmo_retomada'], isTrue);
      expect(f.corpos.length, 2);
      await tester.pumpWidget(const SizedBox());
      f.a.controlador.desconectar();
      await tester.pumpAndSettle();
    },
  );
  test(
    'atualizar invalida revisão; resposta de configuração estranha é recusada',
    () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      await c.revisar();
      final anterior = c.revisao!;
      await c.atualizar();
      expect(c.revisao, isNull);
      await c.confirmarRevisao(anterior);
      expect(f.corpos, isEmpty);
      f.interceptar = (r) => r.url.path.endsWith('/revisao-inicio')
          ? f.json({
              ...f.review(false),
              'configuracao_simulacao': {...f.cfg, 'cenario': 'privado'},
            })
          : null;
      await c.revisar();
      expect(c.revisao, isNull);
      expect(c.mensagem, isNot(contains('privado')));
    },
  );

  test(
    '401 expira sessão e descarta pedido pendente sem sucesso falso',
    () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      await c.revisar();
      f.interceptar = (r) => r.method == 'POST'
          ? f.json({'codigo': 'sessao_invalida'}, 401)
          : null;
      await c.confirmarRevisao(c.revisao!);
      expect(c.ativo, isFalse);
      expect(c.temPendente, isFalse);
      expect(c.recibo, isNull);
    },
  );

  test(
    'falha de consulta depois de recibo não torna incerta operação confirmada',
    () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      await c.revisar();
      await c.confirmarRevisao(c.revisao!);
      final receipt = c.recibo;
      f.indisponivel = true;
      await c.atualizar();
      expect(identical(c.recibo, receipt), isTrue);
      expect(c.temPendente, isFalse);
      expect(c.resultadoDesconhecido, isFalse);
      expect(c.historicoIndisponivel, isTrue);
    },
  );

  test(
    '8000 itens carregam só uma página e mantêm limite nas próximas consultas',
    () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      f.total = 8000;
      f.existe = true;
      await c.abrirExecucao('e1');
      expect(c.itens!.total, 8000);
      expect(c.itens!.itens.length, 20);
      await c.carregarItens(400);
      expect(c.itens!.itens.first.ordem, 7981);
      expect(c.itens!.itens.length, 20);
      expect(
        f.pedidos
            .where((r) => r.url.path.endsWith('/itens'))
            .every((r) => r.url.queryParameters['limite'] == '20'),
        isTrue,
      );
    },
  );

  testWidgets(
    'troca de campanha fecha confirmação e não envia revisão anterior',
    (tester) async {
      final f = F18();
      await f.entrar();
      final id = ValueNotifier('c1');
      addTearDown(f.a.dispose);
      addTearDown(id.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: ValueListenableBuilder<String>(
            valueListenable: id,
            builder: (_, valor, _) => PaginaExecucoes(
              autenticacao: f.a.controlador,
              campanhaId: valor,
              preparacaoId: 'p1',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Revisar início da preparação confirmada'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Confirmar início'));
      await tester.tap(find.text('Confirmar início'));
      await tester.pumpAndSettle();
      id.value = 'c2';
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('confirmar-execucao')), findsNothing);
      expect(f.corpos, isEmpty);
      await tester.pumpWidget(const SizedBox());
      f.a.controlador.desconectar();
      await tester.pumpAndSettle();
    },
  );
  test(
    'revisão e consultas não iniciam; confirmação explícita inicia com UUID',
    () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      await c.carregar();
      await c.revisar();
      expect(f.corpos, isEmpty);
      expect(c.revisao!.permitida, isTrue);
      await c.confirmarRevisao(c.revisao!);
      expect(
        f.corpos.single['operacao_id'],
        matches(RegExp(r'^[0-9a-f-]{36}$')),
      );
      expect(c.recibo!.admitida, isTrue);
      expect(c.execucao!.estado, 'executando');
    },
  );

  test('timeout após commit consulta e replay mantém corpo e UUID sem nova ativação', () async {
    final f = F18();
    await f.entrar();
    final c = f.controller();
    addTearDown(f.a.dispose);
    addTearDown(c.dispose);
    await c.carregar();
    await c.revisar();
    f.interceptar = (r) {
      if (r.method == 'POST') {
        f.base(r);
        throw TimeoutException('privado');
      }
      return null;
    };
    await c.confirmarRevisao(c.revisao!);
    final id = c.operacaoPendente;
    expect(c.resultadoDesconhecido, isTrue);
    expect(c.recibo, isNull);
    expect(c.execucao, isNotNull);
    await c.revisar();
    expect(c.revisao, isNull);
    f.interceptar = null;
    await c.repetir();
    expect(c.temPendente, isFalse);
    expect(c.recibo!.replay, isTrue);
    expect(f.corpos.map((e) => e['operacao_id']), [id, id]);
    expect(jsonEncode(f.corpos[0]), jsonEncode(f.corpos[1]));
    expect(f.recibos.length, 1);
  });

  for (final status in [403, 409, 422, 503]) {
    test('HTTP $status invalida marcador sem exibir detalhe privado', () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      await c.revisar();
      f.interceptar = (r) =>
          r.method == 'POST' ? f.json({'detail': 'secreto'}, status) : null;
      await c.confirmarRevisao(c.revisao!);
      expect(c.revisao, isNull);
      expect(c.mensagem, isNot(contains('secreto')));
      expect(c.temPendente, status == 503);
      expect(c.recibo, isNull);
    });
  }

  test(
    'falha de ativação exibe persistência e exige revisão nova para retomar',
    () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      f.ativada = false;
      await c.revisar();
      await c.confirmarRevisao(c.revisao!);
      expect(c.execucao!.estado, 'pausada');
      expect(c.recibo!.falhaAtivacao, isTrue);
      expect(c.mensagem, contains('não foi ativado'));
      f.ativada = true;
      await c.revisar(retomar: true);
      expect(c.revisao!.elegiveis, 38);
      await c.confirmarRevisao(c.revisao!);
      expect(f.corpos.last['confirmo_retomada'], isTrue);
      expect(c.execucao!.geracao, 2);
    },
  );

  test('pausa usa versão real; retomada não altera desconhecidos', () async {
    final f = F18();
    await f.entrar();
    final c = f.controller();
    addTearDown(f.a.dispose);
    addTearDown(c.dispose);
    f.existe = true;
    await c.abrirExecucao('e1');
    await c.pausar(c.execucao!);
    expect(f.corpos.single['versao'], 1);
    expect(c.execucao!.estado, 'pausada');
    await c.revisar(retomar: true);
    expect(f.corpos.length, 1);
    await c.confirmarRevisao(c.revisao!);
    expect(c.execucao!.contagens['desconhecida'], 1);
  });

  test(
    'paginação real de itens/tentativas e histórico não abre fotografia',
    () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      f.existe = true;
      await c.carregar();
      await c.abrirExecucao('e1');
      expect(c.itens!.itens.length, 20);
      await c.carregarItens(2);
      expect(c.itens!.itens.first.ordem, 21);
      await c.abrirItem(c.itens!.itens.first);
      expect(c.tentativas!.itens.single.estado, 'desconhecida');
      expect(
        f.pedidos.any((r) => r.url.path.contains('/preparacoes')),
        isFalse,
      );
      expect(f.corpos, isEmpty);
    },
  );

  test(
    'consulta falha distingue indisponibilidade de histórico vazio',
    () async {
      final f = F18();
      await f.entrar();
      final c = f.controller();
      addTearDown(f.a.dispose);
      addTearDown(c.dispose);
      await c.carregar();
      expect(c.execucoes!.itens, isEmpty);
      f.indisponivel = true;
      await c.carregar();
      expect(c.historicoIndisponivel, isTrue);
      expect(c.mensagem, isNot(contains('PII')));
    },
  );

  test('resposta de outro recurso não confirma operação pendente', () async {
    final f = F18();
    await f.entrar();
    final c = f.controller();
    addTearDown(f.a.dispose);
    addTearDown(c.dispose);
    await c.revisar();
    f.interceptar = (r) => r.method == 'POST'
        ? f.json({
            'simulacao': true,
            'recibo': {'id': 'outra'},
          })
        : null;
    await c.confirmarRevisao(c.revisao!);
    expect(c.temPendente, isTrue);
    expect(c.recibo, isNull);
    expect(c.resultadoDesconhecido, isTrue);
  });

  testWidgets(
    'dashboard confirma desmarcado e navega entre histórico, itens e tentativas',
    (tester) async {
      final f = F18();
      await f.entrar();
      addTearDown(f.a.dispose);
      await tester.pumpWidget(f.tela());
      await tester.pumpAndSettle();
      expect(f.corpos, isEmpty);
      expect(find.text('Sem execução registrada.'), findsOneWidget);
      await tester.tap(find.text('Revisar início da preparação confirmada'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Confirmar início'));
      await tester.tap(find.text('Confirmar início'));
      await tester.pumpAndSettle();
      final confirmar = find.byKey(const ValueKey('confirmar-execucao'));
      expect(tester.widget<FilledButton>(confirmar).onPressed, isNull);
      await tester.tap(find.byKey(const ValueKey('confirmacao-execucao')));
      await tester.pumpAndSettle();
      await tester.tap(confirmar);
      await tester.pumpAndSettle();
      expect(f.corpos.length, 1);
      expect(find.textContaining('Resultado desconhecido'), findsWidgets);
      expect(
        find.textContaining('não serão repetidos automaticamente'),
        findsOneWidget,
      );
      await tester.scrollUntilVisible(find.text('Consultar itens'), 200);
      await tester.tap(find.text('Consultar itens'));
      await tester.pumpAndSettle();
      final item = find.text('Item 1 · Resultado desconhecido');
      await tester.scrollUntilVisible(item, 200);
      await tester.tap(item);
      await tester.pumpAndSettle();
      expect(find.text('Tentativas do item 1'), findsOneWidget);
      expect(find.text('Tentativa 1 · Resultado desconhecido'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      await tester.pumpAndSettle();
      f.a.controlador.desconectar();
    },
  );

  testWidgets('polling é GET moderado, sem sobreposição, cancela ao sair', (
    tester,
  ) async {
    final f = F18();
    await f.entrar();
    final c = f.controller();
    addTearDown(f.a.dispose);
    addTearDown(c.dispose);
    f.existe = true;
    await c.abrirExecucao('e1');
    final pendente = Completer<http.StreamedResponse>();
    f.interceptar = (r) => r.url.path.endsWith('/e1') ? pendente.future : null;
    final antes = f.pedidos.length;
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(f.pedidos.length, antes + 1);
    await tester.pump(const Duration(seconds: 2));
    expect(f.pedidos.length, antes + 1);
    c.visibilidade(false);
    pendente.complete(f.json(f.execucao));
    await tester.pumpAndSettle();
    final depois = f.pedidos.length;
    await tester.pump(const Duration(seconds: 6));
    expect(f.pedidos.length, depois);
    expect(f.corpos, isEmpty);
    f.a.controlador.desconectar();
    await tester.pumpAndSettle();
  });

  testWidgets('logout e resposta tardia limpam revisão, recibo e consultas', (
    tester,
  ) async {
    final f = F18();
    await f.entrar();
    final c = f.controller();
    addTearDown(f.a.dispose);
    addTearDown(c.dispose);
    final respostaTardia = Completer<http.StreamedResponse>();
    f.interceptar = (r) =>
        r.url.path.endsWith('/revisao-inicio') ? respostaTardia.future : null;
    final revisao = c.revisar();
    await tester.pump();
    f.a.controlador.desconectar();
    respostaTardia.complete(f.json(f.review(false)));
    await tester.pumpAndSettle();
    await revisao;
    expect(c.revisao, isNull);
    expect(c.recibo, isNull);
    expect(c.operacaoPendente, isNull);
    expect(c.ativo, isFalse);
    expect(f.corpos, isEmpty);
  });

  testWidgets('sessão expirada fecha diálogo sem enviar decisão antiga', (
    tester,
  ) async {
    final f = F18();
    await f.entrar();
    addTearDown(f.a.dispose);
    await tester.pumpWidget(f.tela());
    await tester.pumpAndSettle();
    await tester.tap(find.text('Revisar início da preparação confirmada'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Confirmar início'));
    await tester.tap(find.text('Confirmar início'));
    await tester.pumpAndSettle();
    f.a.controlador.desconectar();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('confirmar-execucao')), findsNothing);
    expect(f.corpos, isEmpty);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('navegação real da preparação preserva histórico sem DPAPI', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1440, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final f = anterior.F17();
    await f.f.controlador.entrar('teste.local', auth.senhaFicticia);
    f.f.substituir = f.responder;
    addTearDown(f.f.dispose);
    final pedidos = <String>[];
    f.interceptar = (r) {
      pedidos.add(r.url.path);
      if (r.url.path.endsWith('/preparacoes')) {
        return anterior.json17({
          'codigo': 'conteudo_indisponivel',
        }, codigo: 503);
      }
      if (r.url.path.endsWith('/execucoes')) {
        return resposta(
          corpo: jsonEncode({
            'itens': [],
            'total': 0,
            'pagina': 1,
            'limite': 20,
            'simulacao': true,
          }),
        );
      }
      return null;
    };
    await tester.pumpWidget(
      MaterialApp(
        home: PreparacaoCampanha(autenticacao: f.f.controlador, id: f.campanha),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Execuções mock'));
    await tester.pumpAndSettle();
    expect(find.text('Sem execução registrada.'), findsOneWidget);
    expect(pedidos.where((p) => p.endsWith('/execucoes')).length, 1);
    expect(find.text('Revisar início da preparação confirmada'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpAndSettle();
    f.f.controlador.desconectar();
  });
}
