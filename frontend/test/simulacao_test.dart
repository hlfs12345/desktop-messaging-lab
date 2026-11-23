import 'dart:async';
import 'dart:convert';

import 'package:edisk/campanhas/preparacao_campanha.dart';
import 'package:edisk/contas_envio/editor_conta.dart';
import 'package:edisk/contas_envio/pagina_contas.dart';
import 'package:edisk/simulacao.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;
import 'contas_envio_test.dart' as contas;

class F16 {
  final auth.Cenario f = auth.Cenario(agora: () => DateTime.utc(2026, 11, 2));
  final chamadas = <http.BaseRequest>[];
  final corpos = <Map<String, dynamic>>[];
  bool mock = true, preparada = false, confirmada = false, invalidada = false;
  bool historicaReal = false, conflito = false, semConfiguracao = false;
  String conta = '00000000-0000-0000-0000-000000000016';
  Map<String, dynamic>? congelada;
  Completer<http.StreamedResponse>? revisaoPendente;
  final impressao = List.filled(64, 'f').join();

  Map<String, dynamic> configuracao(String cenario) => {
    'configuracao_id': '00000000-0000-0000-0000-000000000017',
    'modo': 'simulacao',
    'provedor': 'mock',
    'plataforma': 'whatsapp',
    'conta_envio_id': conta,
    'versao_configuracao': 1,
    'versao_contrato_mock': 'texto_v1',
    'politica_cenarios': 'cenario_unico_v1',
    'cenario': cenario,
    'capacidades': {
      'texto': true,
      'simulacao': true,
      'entrega': false,
      'leitura': false,
      'botoes': false,
      'anexos': false,
      'conexao_externa_verificada': false,
    },
    'resumo_configuracao': List.filled(64, 'a').join(),
  };
  Map<String, dynamic> get referencias => {
    'conta': {
      'id': conta,
      'nome': 'Conta local',
      'provedor': mock ? 'mock' : 'qr_candidato',
      'plataforma': 'whatsapp',
      'ativo': true,
    },
  };
  http.StreamedResponse json(Object value, {int codigo = 200}) =>
      resposta(codigo: codigo, corpo: jsonEncode(value));
  Map<String, dynamic> review(String cenario) => {
    'versao': 2,
    'marcador':
        'base.abertura.2.30.${configuracao(cenario)['resumo_configuracao']}',
    'referencias': referencias,
    if (mock && !semConfiguracao)
      'configuracao_simulacao': configuracao(cenario),
    'incluidos': 1,
    'excluidos': 0,
    'total': 1,
    'itens': [
      {'identidade_contato_id': 'i1', 'incluido': true, 'motivos': []},
    ],
  };

  Future<void> iniciar({bool expirar = false}) async {
    if (expirar) {
      f.expira = DateTime.utc(2026, 11, 2).add(const Duration(seconds: 5));
    }
    await f.controlador.entrar('teste.local', auth.senhaFicticia);
    f.substituir = (r) {
      final path = r.url.path;
      if (path == '/auth/logout') return resposta(codigo: 204, corpo: '');
      if (path == '/auth/login') return json(auth.sessao(expira: f.expira));
      chamadas.add(r);
      if (path.endsWith('/revisao-preparacao')) {
        if (revisaoPendente != null) return revisaoPendente!.future;
        return json(
          review(r.url.queryParameters['cenario'] ?? 'aceita_provedor'),
        );
      }
      if (r.method == 'POST') {
        final body = Map<String, dynamic>.from(
          jsonDecode((r as http.Request).body) as Map,
        );
        corpos.add(body);
        if (conflito) {
          return json({
            'codigo': 'referencia_invalida',
            'detail': 'segredo privado',
          }, codigo: 409);
        }
        if (path.endsWith('/confirmacao')) {
          confirmada = true;
        } else if (path.endsWith('/retorno-rascunho')) {
          invalidada = true;
        } else if (path.endsWith('/preparacoes')) {
          preparada = true;
          congelada = body['configuracao_simulacao'] as Map<String, dynamic>?;
        } else {
          throw StateError('Ação de execução inesperada: $path');
        }
        return json({
          'preparacao_id': 'p1',
          'versao': 3,
        }, codigo: path.endsWith('/preparacoes') ? 201 : 200);
      }
      if (path.endsWith('/preparacoes')) {
        return json({
          'itens': preparada
              ? [
                  {
                    'preparacao_id': 'p1',
                    'integridade': 'verificada',
                    if (congelada != null && !historicaReal)
                      'configuracao_simulacao': congelada,
                  },
                ]
              : [],
          'total': preparada ? 1 : 0,
        });
      }
      if (path.contains('/preparacoes/')) {
        return json({
          'preparacao_id': 'p1',
          'impressao': impressao,
          'incluidos': 1,
          'excluidos': 0,
          'total': 1,
          'confirmado_em': confirmada ? '2026-11-02' : null,
          'invalidado_em': invalidada ? '2026-11-02' : null,
          'revisao_desatualizada': false,
          'referencias_congeladas': {
            'conta_envio_id': historicaReal ? 'conta-real-anterior' : conta,
            'template_versao_id': 'v1',
            if (congelada != null && !historicaReal)
              'configuracao_simulacao': congelada,
          },
          'itens': [
            {
              'identidade_contato_id': 'i1',
              'incluido': true,
              'motivos': [],
              'impedimentos_atuais': [],
            },
          ],
        });
      }
      return json({
        'status': preparada && !invalidada ? 'preparada' : 'rascunho',
        'versao': confirmada
            ? 4
            : preparada
            ? 3
            : 2,
        'conta_envio_id': conta,
        'referencias': referencias,
      });
    };
  }

  Widget tela({String id = 'cp1'}) => MaterialApp(
    home: PreparacaoCampanha(
      key: const ValueKey('preparacao'),
      autenticacao: f.controlador,
      id: id,
    ),
  );
  Future<void> abrir(WidgetTester t, {String id = 'cp1'}) async {
    await t.pumpWidget(tela(id: id));
    await t.pumpAndSettle();
  }
}

Future<void> tocar(WidgetTester t, String texto) async {
  await t.ensureVisible(find.text(texto).last);
  await t.tap(find.text(texto).last);
  await t.pumpAndSettle();
}

Future<void> escolher(WidgetTester t, String valor) async {
  final campo = find.byType(DropdownButtonFormField<String>);
  await t.ensureVisible(campo);
  await t.tap(campo);
  await t.pumpAndSettle();
  await t.tap(find.text(cenariosSimulacao[valor]!).last);
  await t.pumpAndSettle();
}

final cenarios16 = <F16>[];

void teste16(String nome, WidgetTesterCallback corpo) {
  testWidgets(nome, (t) async {
    try {
      await corpo(t);
    } finally {
      for (final f in cenarios16) {
        f.f.controlador.desconectar();
      }
      await t.pumpWidget(const SizedBox());
      for (final f in cenarios16) {
        f.f.dispose();
      }
      cenarios16.clear();
    }
  }, variant: TargetPlatformVariant.only(TargetPlatform.windows));
}

void main() {
  Future<F16> iniciar(WidgetTester t, {bool expirar = false}) async {
    await t.binding.setSurfaceSize(const Size(1100, 1500));
    addTearDown(() => t.binding.setSurfaceSize(null));
    final f = F16();
    cenarios16.add(f);
    await f.iniciar(expirar: expirar);
    return f;
  }

  teste16(
    'mock exige cenário e envia exatamente configuração e marcador revisados',
    (t) async {
      final f = await iniciar(t);
      await f.abrir(t);
      expect(find.text(avisoSimulacao), findsOneWidget);
      expect(
        t
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Revisar para preparar'),
            )
            .onPressed,
        isNull,
      );
      expect(
        f.chamadas.where((r) => r.url.path.endsWith('/revisao-preparacao')),
        isEmpty,
      );
      await escolher(t, 'desconhecida');
      await tocar(t, 'Revisar para preparar');
      expect(f.chamadas.last.url.queryParameters['cenario'], 'desconhecida');
      expect(find.text('Configuração de simulação revisada'), findsOneWidget);
      await tocar(t, 'Preparar fotografia imutavel');
      final body = f.corpos.first;
      expect(body['configuracao_simulacao'], f.configuracao('desconhecida'));
      expect(body['marcador'], f.review('desconhecida')['marcador']);
      expect(find.text('Preparação simulada'), findsOneWidget);
      await tocar(t, 'Detalhes da configuração');
      expect(find.text('Contrato mock: texto_v1'), findsOneWidget);
      expect(find.textContaining('Resumo: '), findsOneWidget);
      await tocar(t, 'Confirmar preparação');
      expect(f.corpos.last['impressao'], f.impressao);
      expect(f.confirmada, isTrue);
      for (final label in [
        'Iniciar',
        'Executar',
        'Retomar',
        'Reenviar',
        'Enviar',
      ]) {
        expect(find.widgetWithText(FilledButton, label), findsNothing);
      }
      expect(f.corpos.length, 2);
      expect(f.chamadas.every((r) => !r.url.path.contains('executar')), isTrue);
    },
  );

  teste16('trocar cenário descarta revisão e exige nova consulta explícita', (
    t,
  ) async {
    final f = await iniciar(t);
    await f.abrir(t);
    await escolher(t, 'falhou');
    await tocar(t, 'Revisar para preparar');
    await escolher(t, 'excecao');
    expect(find.text('Preparar fotografia imutavel'), findsNothing);
    expect(find.text('Configuração de simulação revisada'), findsNothing);
    expect(f.corpos, isEmpty);
    await tocar(t, 'Revisar para preparar');
    await tocar(t, 'Preparar fotografia imutavel');
    expect(
      f.corpos.single['configuracao_simulacao'],
      f.configuracao('excecao'),
    );
  });

  teste16('conta real mantém revisão sem cenário e sem configuração simulada', (
    t,
  ) async {
    final f = await iniciar(t);
    f.mock = false;
    await f.abrir(t);
    expect(find.byType(DropdownButtonFormField<String>), findsNothing);
    expect(find.text(avisoSimulacao), findsNothing);
    await tocar(t, 'Revisar para preparar');
    expect(f.chamadas.last.url.queryParameters.containsKey('cenario'), isFalse);
    await tocar(t, 'Preparar fotografia imutavel');
    expect(f.corpos.single.containsKey('configuracao_simulacao'), isFalse);
    expect(find.text('Preparação simulada'), findsNothing);
  });

  teste16('fotografia antiga não é rotulada como simulada pela conta atual', (
    t,
  ) async {
    final f = await iniciar(t);
    f.preparada = true;
    f.confirmada = true;
    f.historicaReal = true;
    f.congelada = f.configuracao('falhou');
    await f.abrir(t);
    expect(find.text('Preparação simulada'), findsNothing);
    expect(find.text('Preparação simulada p1'), findsNothing);
    expect(find.text('Fotografia p1'), findsOneWidget);
    expect(find.text(avisoSimulacao), findsNothing);
    expect(find.textContaining('conta-real-anterior'), findsOneWidget);
  });

  teste16('409 invalida revisão sem revelar detalhe de erro', (t) async {
    final f = await iniciar(t);
    await f.abrir(t);
    await escolher(t, 'interrupcao');
    await tocar(t, 'Revisar para preparar');
    f.conflito = true;
    await tocar(t, 'Preparar fotografia imutavel');
    expect(find.text('Preparar fotografia imutavel'), findsNothing);
    expect(find.textContaining('segredo privado'), findsNothing);
    expect(f.preparada, isFalse);
  });

  teste16('resposta mock incompleta não libera preparação', (t) async {
    final f = await iniciar(t);
    f.semConfiguracao = true;
    await f.abrir(t);
    await escolher(t, 'aceita_provedor');
    await tocar(t, 'Revisar para preparar');
    expect(find.text('Preparar fotografia imutavel'), findsNothing);
    expect(find.textContaining('não foi retornada'), findsOneWidget);
  });

  teste16('atualizar após troca de conta limpa cenário e revisão', (t) async {
    final f = await iniciar(t);
    await f.abrir(t);
    await escolher(t, 'falhou');
    await tocar(t, 'Revisar para preparar');
    f.conta = '00000000-0000-0000-0000-000000000018';
    await t.tap(find.byTooltip('Atualizar preparacao'));
    await t.pumpAndSettle();
    expect(find.text('Preparar fotografia imutavel'), findsNothing);
    final dropdown = t.widget<DropdownButtonFormField<String>>(
      find.byType(DropdownButtonFormField<String>),
    );
    expect(dropdown.initialValue, isNull);
    expect(
      t
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Revisar para preparar'),
          )
          .onPressed,
      isNull,
    );
  });

  for (final evento in [
    'troca de campanha',
    'logout e nova sessão',
    'expiração',
  ]) {
    teste16('descarta revisão e resposta pendente em $evento', (t) async {
      final f = await iniciar(t, expirar: evento == 'expiração');
      await f.abrir(t);
      await escolher(t, 'falhou');
      final pending = Completer<http.StreamedResponse>();
      f.revisaoPendente = pending;
      await t.tap(find.text('Revisar para preparar'));
      await t.pump();
      if (evento == 'troca de campanha') {
        await f.abrir(t, id: 'cp2');
      } else if (evento == 'logout e nova sessão') {
        await f.f.controlador.sair();
        f.revisaoPendente = null;
        await f.f.controlador.entrar('teste.local', auth.senhaFicticia);
        await t.pumpAndSettle();
      } else {
        await t.pump(const Duration(seconds: 6));
      }
      pending.complete(f.json(f.review('falhou')));
      await t.pumpAndSettle();
      expect(find.text('Configuração de simulação revisada'), findsNothing);
      expect(find.text('Preparar fotografia imutavel'), findsNothing);
      if (evento == 'expiração') {
        expect(find.text('Sessão encerrada. Entre novamente.'), findsOneWidget);
      }
      expect(f.corpos, isEmpty);
      expect(t.takeException(), isNull);
    });
  }

  teste16(
    'cadastro mock usa WhatsApp e edição não altera identidade nem mostra credenciais',
    (t) async {
      final f = contas.ContasFicticias();
      await f.iniciar();
      addTearDown(f.f.dispose);
      await f.abrir(t);
      await contas.tocar(t, find.text('Nova conta'));
      await t.enterText(find.byType(TextFormField), 'Teste local');
      await t.tap(find.byType(DropdownButtonFormField<String>));
      await t.pumpAndSettle();
      await t.tap(find.text('Conta de simulação (WhatsApp)').last);
      await t.pumpAndSettle();
      expect(find.text(avisoSimulacao), findsOneWidget);
      await contas.tocar(t, find.text('Salvar conta'));
      expect(f.corpos.last['provedor'], 'mock');
      expect(f.corpos.last['plataforma'], 'whatsapp');
      expect(f.corpos.last.containsKey('credencial'), isFalse);
      f.item['configuracao_local'] = 'simulacao_sem_credenciais';
      await contas.tocar(t, find.text('Teste local'));
      expect(contas.campoSegredo, findsNothing);
      expect(find.text('Salvar credencial'), findsNothing);
      expect(find.text('Testar conexão'), findsNothing);
      expect(find.text(avisoSimulacao), findsOneWidget);
      await contas.tocar(t, find.text('Editar nome'));
      expect(find.byType(DropdownButtonFormField<String>), findsNothing);
      await t.enterText(find.byType(TextFormField), 'Nome revisado');
      await contas.tocar(t, find.text('Salvar conta'));
      expect(
        f.corpos.last.keys,
        containsAll(['nome', 'versao', 'operacao_id']),
      );
      expect(f.corpos.last.containsKey('provedor'), isFalse);
      expect(
        f.chamadas.where((r) => r.url.path.endsWith('/credencial')),
        isEmpty,
      );
      await f.fechar(t);
    },
  );

  teste16('operador não recebe editor administrativo de conta mock', (t) async {
    final f = contas.ContasFicticias();
    await f.iniciar();
    addTearDown(f.f.dispose);
    final previous = f.f.cenario.substituir!;
    f.f.cenario.substituir = (r) => r.url.path == '/auth/usuario'
        ? resposta(
            corpo: jsonEncode({
              ...(auth.sessao()..remove('token')),
              'usuario': {...auth.usuario, 'perfil': 'operador'},
            }),
          )
        : previous(r);
    await f.f.cenario.controlador.verificarUsuario();
    await t.pumpWidget(
      MaterialApp(home: EditorConta(autenticacao: f.f.cenario.controlador)),
    );
    await t.pumpAndSettle();
    expect(
      find.text('Somente o administrador pode gerenciar contas.'),
      findsOneWidget,
    );
    expect(find.byType(TextFormField), findsNothing);
    expect(find.text('Salvar conta'), findsNothing);
    await f.fechar(t);
  });
  for (final evento in ['logout', 'expiração']) {
    teste16('limpa revisão carregada e cenário em $evento', (t) async {
      final f = await iniciar(t, expirar: evento == 'expiração');
      await f.abrir(t);
      await escolher(t, 'falhou');
      await tocar(t, 'Revisar para preparar');
      expect(find.text('Configuração de simulação revisada'), findsOneWidget);
      if (evento == 'logout') {
        await f.f.controlador.sair();
      } else {
        await t.pump(const Duration(seconds: 6));
      }
      await t.pumpAndSettle();
      expect(find.text('Preparar fotografia imutavel'), findsNothing);
      expect(find.text('Configuração de simulação revisada'), findsNothing);
      await f.f.controlador.entrar('teste.local', auth.senhaFicticia);
      await t.pumpAndSettle();
      final field = t.widget<DropdownButtonFormField<String>>(
        find.byType(DropdownButtonFormField<String>),
      );
      expect(field.initialValue, isNull);
      expect(find.text('Preparar fotografia imutavel'), findsNothing);
    });
  }

  teste16('conteúdo antigo não reaparece ao trocar o caminho consultado', (
    t,
  ) async {
    final f = await iniciar(t);
    final previous = f.f.substituir!;
    final pending = Completer<http.StreamedResponse>();
    f.f.substituir = (r) {
      if (r.url.path.endsWith('/antigo/conteudo')) return pending.future;
      if (r.url.path.endsWith('/novo/conteudo')) {
        return f.json({'conteudo_renderizado': 'Texto novo'});
      }
      return previous(r);
    };
    Widget tela(String id) => MaterialApp(
      home: ConteudoFotografia(
        key: const ValueKey('texto'),
        autenticacao: f.f.controlador,
        caminho: '/campanhas/cp1/preparacoes/p1/itens/$id/conteudo',
      ),
    );
    await t.pumpWidget(tela('antigo'));
    await t.pumpAndSettle();
    await t.pumpWidget(tela('novo'));
    await t.pumpAndSettle();
    expect(find.text('Texto novo'), findsOneWidget);
    pending.complete(
      f.json({'conteudo_renderizado': 'Texto anterior privado'}),
    );
    await t.pumpAndSettle();
    expect(find.text('Texto anterior privado'), findsNothing);
    expect(find.text('Texto novo'), findsOneWidget);
  });
  teste16('troca de conta descarta consulta pendente do detalhe', (t) async {
    final f = contas.ContasFicticias();
    await f.iniciar();
    addTearDown(f.f.dispose);
    final previous = f.f.cenario.substituir!;
    final pending = Completer<http.StreamedResponse>();
    final mock = {
      ...f.item,
      'provedor': 'mock',
      'plataforma': 'whatsapp',
      'configuracao_local': 'simulacao_sem_credenciais',
    };
    f.f.cenario.substituir = (r) {
      if (r.url.path == '/contas-envio/conta1') return pending.future;
      if (r.url.path == '/contas-envio/conta2') {
        return resposta(
          corpo: jsonEncode({...mock, 'id': 'conta2', 'nome': 'Conta nova'}),
        );
      }
      return previous(r);
    };
    Widget tela(String id) => MaterialApp(
      home: DetalheConta(
        key: const ValueKey('detalhe'),
        autenticacao: f.f.cenario.controlador,
        id: id,
      ),
    );
    try {
      await t.pumpWidget(tela('conta1'));
      await t.pumpAndSettle();
      await t.pumpWidget(tela('conta2'));
      await t.pumpAndSettle();
      expect(find.text('Conta nova'), findsOneWidget);
      pending.complete(
        resposta(corpo: jsonEncode({...mock, 'nome': 'Conta anterior'})),
      );
      await t.pumpAndSettle();
      expect(find.text('Conta anterior'), findsNothing);
      expect(find.text('Conta nova'), findsOneWidget);
      expect(contas.campoSegredo, findsNothing);
    } finally {
      await f.fechar(t);
    }
  });
}
