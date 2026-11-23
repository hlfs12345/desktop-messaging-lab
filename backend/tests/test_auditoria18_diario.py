"""VM SQLite real em inicio/meio/fim; nao confundir comandos com trabalho."""

import json
from time import perf_counter

import pytest
import test_execucoes_migration as a2
import test_executor_mock as b2
import test_executor_mock_desempenho as volume

cadastro = b2.cadastro
consentimento = b2.consentimento
rascunho = b2.rascunho
pronta = b2.pronta
simulavel = b2.simulavel


def medir(conn, action):
    count = 0

    def tick():
        nonlocal count
        count += 1
        return 0

    raw = conn.connection.driver_connection
    raw.set_progress_handler(tick, 1)
    start = perf_counter()
    try:
        action()
    finally:
        seconds = perf_counter() - start
        raw.set_progress_handler(None, 0)
    return dict(instrucoes=count, segundos=seconds)


@pytest.mark.parametrize("total", [250, 1000, 8000])
def test_vm_transicoes_inicio_meio_fim_e_plano_composto(simulavel, total):
    volume.popular(simulavel, total)
    f, receipt, _, _, _ = b2.construir(simulavel)
    index = a2.a1.modulo().INDICES_OPERACIONAIS["ix_transicoes_entidade_versao"][1]
    samples = []
    with f["db"].engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        e = dict(conn.exec_driver_sql("SELECT * FROM campanha_execucoes").mappings().one())
        items = (
            conn.exec_driver_sql("SELECT id FROM campanha_execucao_itens ORDER BY ordem")
            .scalars()
            .all()
        )
        chosen = [items[0], items[total // 2], items[-1]]
        for phase, upto, item in zip(
            ("inicio", "meio", "fim"), (0, total // 2, total), chosen, strict=True
        ):
            # Crescer historico com transicoes reais de itens ignorados. Nao sao
            # 8.000 invocacoes de provedor e nenhum trigger e desabilitado.
            conn.exec_driver_sql(
                "UPDATE campanha_execucao_itens SET estado='ignorado',"
                "motivo_codigo='impedimento_atual',versao=versao+1,atualizado_em=? "
                "WHERE estado='pendente' AND ordem<=? AND id NOT IN (?,?,?)",
                (a2.T1, upto, *chosen),
            )
            for entity in ("execucao", "item", "tentativa"):
                save = conn.begin_nested()
                try:
                    attempt = a2.reservar(conn, e, item_id=item) if entity == "tentativa" else None

                    def action(entity=entity, item=item, attempt=attempt):
                        if entity == "execucao":
                            a2.alterar(
                                conn,
                                "campanha_execucoes",
                                e["id"],
                                1,
                                estado="pausando",
                                atualizado_em=a2.T4,
                            )
                        elif entity == "item":
                            a2.alterar(
                                conn,
                                "campanha_execucao_itens",
                                item,
                                1,
                                estado="ignorado",
                                motivo_codigo="impedimento_atual",
                                atualizado_em=a2.T4,
                            )
                        else:
                            a2.iniciar(conn, attempt)

                    conn.exec_driver_sql("DROP INDEX ix_transicoes_entidade_versao")
                    before = conn.begin_nested()
                    old = medir(conn, action)
                    before.rollback()
                    conn.exec_driver_sql(index)
                    after = conn.begin_nested()
                    new = medir(conn, action)
                    after.rollback()
                    # A execucao ainda verifica max(atualizado_em) de todos os
                    # itens ao pausar/concluir, uma vez por decisao global.
                    # As transicoes por item/tentativa nao podem varrer o diario.
                    limit = 7 * total + 3000 if entity == "execucao" else 15000
                    assert new["instrucoes"] < limit, (total, phase, entity, new)
                    samples.append(dict(fase=phase, entidade=entity, antes=old, depois=new))
                finally:
                    save.rollback()
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    print("BENCH_DIARIO " + json.dumps(dict(total=total, amostras=samples)))
    if total == 8000:
        per_item = [s for s in samples if s["entidade"] != "execucao"]
        assert max(s["antes"]["instrucoes"] for s in per_item) > 5 * max(
            s["depois"]["instrucoes"] for s in per_item
        )
