"""Memoria Python observada; nao mede RSS nativo nem 8.000 despachos completos."""

import gc
import json
import tracemalloc

import pytest
import test_executor_mock as b2
import test_executor_mock_desempenho as volume

cadastro = volume.cadastro
consentimento = volume.consentimento
rascunho = volume.rascunho
pronta = volume.pronta
simulavel = volume.simulavel


@pytest.mark.parametrize("total", [250, 8000])
def test_memoria_admissao_tres_passos_e_descarte(simulavel, total):
    volume.popular(simulavel, total)
    tracemalloc.start()
    try:
        f, receipt, ctx, executor, adm = b2.construir(simulavel)
        gc.collect()
        inicial, pico = tracemalloc.get_traced_memory()
        for _ in range(3):
            assert executor.passo(adm)["estado"] == "aceita_provedor"
        apos_passos, pico_passos = tracemalloc.get_traced_memory()
        ctx.fechar()
        gc.collect()
        apos_descarte, _ = tracemalloc.get_traced_memory()
        assert len(b2.linhas(f, "tentativas_envio")) == 3
        assert not ctx._selos and not ctx._admissoes
        print(
            "BENCH_MEM "
            + json.dumps(
                dict(
                    destinatarios=total,
                    chamadas_reais=3,
                    python_retido_bytes=inicial,
                    python_pico_bytes=pico,
                    python_apos_passos_bytes=apos_passos,
                    python_pico_passos_bytes=pico_passos,
                    python_apos_descarte_bytes=apos_descarte,
                ),
                sort_keys=True,
            )
        )
    finally:
        tracemalloc.stop()
