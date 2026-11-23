import 'dart:async';
import 'dart:convert';

import 'package:edisk/autenticacao/cliente_autenticacao.dart';
import 'package:edisk/autenticacao/controlador_autenticacao.dart';
import 'package:edisk/contas_envio/pagina_contas.dart';
import 'package:edisk/conexao/transporte_local.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;

import 'apoio_http.dart';
import 'autenticacao_test.dart' as auth;
import 'preparacoes_test.dart' as preparacoes;

final sessoesP2 = <ControladorAutenticacao>[];
void testeP2(String nome, WidgetTesterCallback corpo) {
  testWidgets(nome, (t) async {
    try {
      await corpo(t);
    } finally {
      for (final sessao in sessoesP2) {
        sessao.desconectar();
      }
      await t.pumpWidget(const SizedBox());
      for (final sessao in sessoesP2) {
        sessao.dispose();
      }
      sessoesP2.clear();
    }
  }, variant: TargetPlatformVariant.only(TargetPlatform.windows));
}

http.StreamedResponse json(Object value) => resposta(corpo: jsonEncode(value));

class AdminP2 {
  final f = auth.Cenario(agora: () => DateTime.utc(2026, 11, 2));
  final id = ValueNotifier('conta1');
  final mutacoes = <Map<String, dynamic>>[];
  final consultas = <String>[];
  int versao = 1;
  bool ativo = true;
  bool real = false;
  Future<void> iniciar({bool expirar = false}) async {
    sessoesP2.add(f.controlador);
    if (expirar) {
      f.expira = DateTime.utc(2026, 11, 2).add(const Duration(seconds: 5));
    }
    await f.controlador.entrar('teste.local', auth.senhaFicticia);
    f.substituir = (r) {
      if (r.url.path == '/auth/logout') return resposta(codigo: 204, corpo: '');
      if (r.url.path == '/auth/login') return json(auth.sessao());
      if (r.method != 'GET') {
        mutacoes.add({
          'path': r.url.path,
          'method': r.method,
          ...jsonDecode((r as http.Request).body) as Map<String, dynamic>,
        });
        versao++;
        return json({
          'id': id.value,
          'versao': versao,
          'limpeza_pendente': false,
        });
      }
      consultas.add(r.url.path);
      return json({
        'id': r.url.path.split('/')[2],
        'nome': 'Conta ${id.value}',
        'provedor': real ? 'telegram_bot_api' : 'mock',
        'plataforma': real ? 'telegram' : 'whatsapp',
        'configuracao_local': real
            ? 'credencial_armazenada'
            : 'simulacao_sem_credenciais',
        'ativo': ativo,
        'estado_cadastral': ativo ? 'ativa' : 'desativada',
        'versao': versao,
        'limpeza_pendente': false,
      });
    };
  }

  Widget tela() => MaterialApp(
    home: ValueListenableBuilder<String>(
      valueListenable: id,
      builder: (_, conta, _) => DetalheConta(
        key: const ValueKey('detalhe'),
        autenticacao: f.controlador,
        id: conta,
      ),
    ),
  );
}

Future<void> tocar(WidgetTester t, String label) async {
  await t.ensureVisible(find.text(label).last);
  await t.tap(find.text(label).last);
  await t.pumpAndSettle();
}

void main() {
  for (final fechamento in ['botao', 'barreira', 'escape']) {
    testeP2(
      'P2 texto pendente fecha por $fechamento e atualiza imediatamente',
      (t) async {
        final f = preparacoes.F14();
        sessoesP2.add(f.f.controlador);
        await f.iniciar();
        f.preparada = true;
        final antigo = Completer<http.StreamedResponse>();
        final anterior = f.f.substituir!;
        var abortado = false, atualizacoes = 0;
        f.f.substituir = (r) {
          if (r.url.path == '/campanhas/cp1') atualizacoes++;
          if (r.url.path.endsWith('/conteudo')) {
            if (r is http.AbortableRequest) {
              r.abortTrigger?.then<void>((_) {
                abortado = true;
              }).ignore();
            }
            return antigo.future;
          }
          return anterior(r);
        };
        await f.abrir(t);
        await tocar(t, 'Ver texto');
        final atualizar = t
            .widget<IconButton>(
              find.byWidgetPredicate(
                (w) => w is IconButton && w.tooltip == 'Atualizar preparacao',
                skipOffstage: false,
              ),
            )
            .onPressed!;
        if (fechamento == 'botao') {
          await t.tap(find.text('Fechar'));
        } else if (fechamento == 'escape') {
          await t.sendKeyEvent(LogicalKeyboardKey.escape);
        } else {
          await t.tapAt(const Offset(4, 4));
        }
        // Callback no mesmo ciclo do pop, antes do dispose tardio da rota.
        atualizar();
        await t.pumpAndSettle();
        expect(atualizacoes, 2);
        expect(abortado, isTrue);
        expect(find.text('Texto antigo privado'), findsNothing);
        expect(t.takeException(), isNull);
        antigo.complete(json({'conteudo_renderizado': 'Texto antigo privado'}));
        await t.pumpAndSettle();
        expect(find.text('Texto antigo privado'), findsNothing);
        expect(atualizacoes, 2);
        expect(t.takeException(), isNull);
      },
    );
  }

  testeP2('P2 fechar texto não cancela consulta ativa de outro componente', (
    t,
  ) async {
    final f = preparacoes.F14();
    sessoesP2.add(f.f.controlador);
    await f.iniciar();
    f.preparada = true;
    final terceiro = Completer<http.StreamedResponse>();
    final anterior = f.f.substituir!;
    var abortado = false, atualizacoes = 0;
    f.f.substituir = (r) {
      if (r.url.path == '/campanhas/cp1') atualizacoes++;
      if (r.url.path == '/consulta-terceira') {
        if (r is http.AbortableRequest) {
          r.abortTrigger?.then<void>((_) {
            abortado = true;
          }).ignore();
        }
        return terceiro.future;
      }
      return anterior(r);
    };
    await f.abrir(t);
    await tocar(t, 'Ver texto');
    final consulta = f.f.controlador.negocio('GET', '/consulta-terceira');
    await t.pump();
    final atualizar = t
        .widget<IconButton>(
          find.byWidgetPredicate(
            (w) => w is IconButton && w.tooltip == 'Atualizar preparacao',
            skipOffstage: false,
          ),
        )
        .onPressed!;
    await t.tap(find.text('Fechar'));
    atualizar();
    await t.pump();
    expect(abortado, isFalse);
    expect(atualizacoes, 1);
    terceiro.complete(json({'concluida': true}));
    expect((await consulta).dados?['concluida'], isTrue);
    await t.pumpAndSettle();
    expect(atualizacoes, 2);
    expect(t.takeException(), isNull);
  });

  testeP2(
    'P2 desmontagem do texto cancela consulta sem aplicar resposta tardia',
    (t) async {
      final f = preparacoes.F14();
      sessoesP2.add(f.f.controlador);
      await f.iniciar();
      f.preparada = true;
      final antigo = Completer<http.StreamedResponse>();
      f.pendente = antigo;
      await f.abrir(t);
      await tocar(t, 'Ver texto');
      await t.pumpWidget(const SizedBox());
      await t.pump();
      final nova = await f.f.controlador.negocio('GET', '/campanhas/cp1');
      expect(nova.codigo, 200);
      antigo.complete(json({'conteudo_renderizado': 'Texto antigo privado'}));
      await t.pumpAndSettle();
      expect(find.text('Texto antigo privado'), findsNothing);
      expect(t.takeException(), isNull);
    },
  );

  testeP2('P2 cancelar consulta em espera não cancela o proprietário ativo', (
    t,
  ) async {
    final f = auth.Cenario();
    sessoesP2.add(f.controlador);
    await f.controlador.entrar('teste.local', auth.senhaFicticia);
    final respostaAtiva = Completer<http.StreamedResponse>();
    final ativo = ConsultaNegocio(), espera = ConsultaNegocio();
    var abortado = false, despachouEspera = false;
    f.substituir = (r) {
      if (r.url.path == '/ativa') {
        if (r is http.AbortableRequest) {
          r.abortTrigger?.then<void>((_) {
            abortado = true;
          }).ignore();
        }
        return respostaAtiva.future;
      }
      despachouEspera = true;
      return json({'indevida': true});
    };
    final primeira = f.controlador.negocio('GET', '/ativa', consulta: ativo);
    final segunda = f.controlador.negocio('GET', '/espera', consulta: espera);
    final rejeitada = expectLater(segunda, throwsA(isA<ErroAutenticacao>()));
    await f.controlador.cancelarNegocio(consulta: espera);
    await rejeitada;
    expect(abortado, isFalse);
    expect(despachouEspera, isFalse);
    respostaAtiva.complete(json({'ok': true}));
    expect((await primeira).dados?['ok'], isTrue);
  });

  for (final acao in ['Desativar', 'Remover conta']) {
    testeP2('P2 $acao com confirmação pendente e sessão expirada', (t) async {
      final f = AdminP2();
      await f.iniciar(expirar: true);
      await t.pumpWidget(f.tela());
      await t.pumpAndSettle();
      await tocar(t, acao);
      await t.pump(const Duration(seconds: 6));
      expect(f.f.controlador.estado, EstadoAutenticacao.login);
      await tocar(t, 'Confirmar');
      expect(f.mutacoes, isEmpty);
      expect(t.takeException(), isNull);
    });
  }

  for (final troca in ['logout e login', 'novo login sem desmontagem']) {
    testeP2('P2 confirmação não atravessa $troca', (t) async {
      final f = AdminP2();
      await f.iniciar();
      await t.pumpWidget(f.tela());
      await t.pumpAndSettle();
      await tocar(t, 'Desativar');
      if (troca == 'logout e login') await f.f.controlador.sair();
      await f.f.controlador.entrar('teste.local', auth.senhaFicticia);
      await t.pumpAndSettle();
      expect(f.f.controlador.estado, EstadoAutenticacao.autenticado);
      await tocar(t, 'Confirmar');
      expect(f.mutacoes, isEmpty);
      expect(t.takeException(), isNull);
    });
  }

  testeP2('P2 troca de conta não reutiliza confirmação antiga', (t) async {
    final f = AdminP2();
    await f.iniciar();
    await t.pumpWidget(f.tela());
    await t.pumpAndSettle();
    await tocar(t, 'Remover conta');
    f.id.value = 'conta2';
    await t.pumpAndSettle();
    expect(f.consultas, contains('/contas-envio/conta2'));
    await tocar(t, 'Confirmar');
    expect(f.mutacoes, isEmpty);
    expect(find.text('Conta conta2'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testeP2('P2 versão recarregada não substitui a versão confirmada', (t) async {
    final f = AdminP2();
    await f.iniciar();
    await t.pumpWidget(f.tela());
    await t.pumpAndSettle();
    final atualizar = t
        .widget<TextButton>(find.widgetWithText(TextButton, 'Atualizar'))
        .onPressed!;
    await tocar(t, 'Desativar');
    f.versao = 2;
    atualizar();
    await t.pumpAndSettle();
    await tocar(t, 'Confirmar');
    expect(f.mutacoes, isEmpty);
    expect(t.takeException(), isNull);
  });

  testeP2('P2 resposta de mutação antiga não recarrega outra conta', (t) async {
    final f = AdminP2();
    await f.iniciar();
    final anterior = f.f.substituir!;
    final mutacao = Completer<http.StreamedResponse>();
    f.f.substituir = (r) => r.method == 'PATCH' ? mutacao.future : anterior(r);
    await t.pumpWidget(f.tela());
    await t.pumpAndSettle();
    await tocar(t, 'Desativar');
    await t.tap(find.text('Confirmar'));
    await t.pump();
    f.id.value = 'conta2';
    await t.pumpAndSettle();
    final consultas = f.consultas.length;
    mutacao.complete(json({'id': 'conta1', 'versao': 2}));
    await t.pumpAndSettle();
    expect(f.consultas.length, consultas);
    expect(find.text('Conta conta2'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  for (final acao in ['Desativar', 'Remover conta', 'Reativar']) {
    testeP2('P2 confirmação normal preserva $acao e versão', (t) async {
      final f = AdminP2();
      if (acao == 'Reativar') f.ativo = false;
      await f.iniciar();
      await t.pumpWidget(f.tela());
      await t.pumpAndSettle();
      await tocar(t, acao);
      await tocar(t, 'Confirmar');
      expect(f.mutacoes.length, 1);
      expect(f.mutacoes.single['path'], '/contas-envio/conta1');
      expect(f.mutacoes.single['versao'], 1);
      expect(f.mutacoes.single['method'], 'PATCH');
      if (acao == 'Remover conta') {
        expect(f.mutacoes.single['remover'], isTrue);
      } else {
        expect(f.mutacoes.single['ativo'], acao == 'Reativar');
      }
      expect(t.takeException(), isNull);
    });
  }
  testeP2('P2 espera de negócio termina no prazo lógico do transporte', (
    t,
  ) async {
    final f = auth.Cenario();
    sessoesP2.add(f.controlador);
    await f.controlador.entrar('teste.local', auth.senhaFicticia);
    final antiga = Completer<http.StreamedResponse>();
    f.substituir = (r) =>
        r.url.path == '/pendente' ? antiga.future : json({'nova': true});
    final primeira = f.controlador.negocio('GET', '/pendente');
    final falha = expectLater(primeira, throwsA(isA<ErroAutenticacao>()));
    final nova = f.controlador.negocio('GET', '/nova');
    // Prazo real padrao: oito segundos, avancados pelo relogio virtual.
    await t.pump(const Duration(seconds: 8));
    await falha;
    expect((await nova).dados?['nova'], isTrue);
    antiga.complete(json({'antiga': true}));
    await t.pump();
    expect(t.takeException(), isNull);
  });

  testeP2(
    'P2 nova sessão não despacha mutação que aguardava na sessão antiga',
    (t) async {
      final pendente = Completer<http.StreamedResponse>();
      var mutacoes = 0;
      final cliente = ClienteAutenticacao(
        TransporteLocal(
          endpoint: auth.contexto().endpoint,
          token: tokenTeste,
          transporte: TransporteSimulado((_) => json(auth.sessao())),
        ),
        negocio: TransporteLocal(
          endpoint: auth.contexto().endpoint,
          token: tokenTeste,
          transporte: TransporteSimulado((r) {
            if (r.url.path == '/pendente') return pendente.future;
            mutacoes++;
            return json({'indevida': true});
          }),
        ),
      );
      final controlador = ControladorAutenticacao(criarCliente: (_) => cliente);
      controlador.conectar(auth.contexto());
      sessoesP2.add(controlador);
      await controlador.entrar('teste.local', auth.senhaFicticia);
      final anterior = controlador.geracaoSessao;
      final ativa = controlador.negocio('GET', '/pendente');
      final emEspera = controlador.negocio(
        'PATCH',
        '/mutacao-antiga',
        corpo: {'versao': 1},
      );
      final cancelada1 = expectLater(ativa, throwsA(isA<ErroAutenticacao>()));
      final cancelada2 = expectLater(
        emEspera,
        throwsA(isA<ErroAutenticacao>()),
      );
      await controlador.entrar('teste.local', auth.senhaFicticia);
      await Future.wait([cancelada1, cancelada2]);
      expect(controlador.geracaoSessao, greaterThan(anterior));
      expect(mutacoes, 0);
      pendente.complete(json({'antiga': true}));
      await t.pump();
      expect(t.takeException(), isNull);
    },
  );

  for (final expirar in [false, true]) {
    testeP2(
      'P2 excluir credencial revalida contexto administrativo (expirar=$expirar)',
      (t) async {
        final f = AdminP2()..real = true;
        await f.iniciar(expirar: expirar);
        await t.pumpWidget(f.tela());
        await t.pumpAndSettle();
        await tocar(t, 'Excluir credencial local');
        if (expirar) {
          await t.pump(const Duration(seconds: 6));
        }
        await tocar(t, 'Confirmar');
        if (expirar) {
          expect(f.mutacoes, isEmpty);
        } else {
          expect(f.mutacoes.length, 1);
          expect(f.mutacoes.single['method'], 'DELETE');
          expect(f.mutacoes.single['path'], '/contas-envio/conta1/credencial');
          expect(f.mutacoes.single['versao'], 1);
        }
        expect(t.takeException(), isNull);
      },
    );
  }
}
