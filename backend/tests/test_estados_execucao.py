"""Matrizes puras do A2; sem acesso a banco, mock ou I/O."""

from dataclasses import FrozenInstanceError

import pytest

from app.campanhas.estados_execucao import (
    EstadoExecucao as E,
)
from app.campanhas.estados_execucao import (
    EstadoItem as I,
)
from app.campanhas.estados_execucao import (
    EstadoTentativa as T,
)
from app.campanhas.estados_execucao import (
    ResumoItens,
    TransicaoInvalida,
    reservar_tentativa,
    transicionar_execucao,
    transicionar_item,
    transicionar_tentativa,
)


@pytest.mark.parametrize("origem", list(I))
@pytest.mark.parametrize("destino", list(I))
def test_matriz_item(origem, destino):
    permitidas = {
        ("pendente", "reservado"),
        ("pendente", "ignorado"),
        ("pendente", "interrompido"),
        ("reservado", "processando"),
        ("reservado", "pendente"),
        ("processando", "aceita_provedor"),
        ("processando", "falhou"),
        ("processando", "desconhecida"),
    }
    if (origem.value, destino.value) in permitidas:
        assert transicionar_item(origem, destino, E.EXECUTANDO) == destino
    else:
        with pytest.raises(TransicaoInvalida):
            transicionar_item(origem, destino, E.EXECUTANDO)


@pytest.mark.parametrize("origem", list(T))
@pytest.mark.parametrize("destino", list(T))
def test_matriz_tentativa(origem, destino):
    permitidas = {
        ("reservada", "iniciada"),
        ("reservada", "interrompida"),
        ("iniciada", "aceita_provedor"),
        ("iniciada", "falhou"),
        ("iniciada", "desconhecida"),
    }
    if (origem.value, destino.value) in permitidas:
        assert transicionar_tentativa(origem, destino, E.EXECUTANDO) == destino
    else:
        with pytest.raises(TransicaoInvalida):
            transicionar_tentativa(origem, destino, E.EXECUTANDO)


@pytest.mark.parametrize("origem", list(E))
@pytest.mark.parametrize("destino", list(E))
def test_matriz_execucao_com_pendencia(origem, destino):
    permitidas = {
        ("executando", "pausando"),
        ("executando", "pausada"),
        ("pausando", "pausada"),
        ("pausada", "executando"),
    }
    if (origem.value, destino.value) in permitidas:
        assert transicionar_execucao(origem, destino, ResumoItens(pendentes=1)) == destino
    else:
        with pytest.raises(TransicaoInvalida):
            transicionar_execucao(origem, destino, ResumoItens(pendentes=1))


@pytest.mark.parametrize(
    "resumo,esperado",
    [
        (ResumoItens(aceitas_provedor=1), E.CONCLUIDA),
        (ResumoItens(ignorados=1), E.CONCLUIDA),
        (ResumoItens(falhas=1), E.CONCLUIDA_COM_FALHAS),
        (ResumoItens(desconhecidas=1), E.CONCLUIDA_COM_FALHAS),
        (ResumoItens(interrompidos=1), E.CONCLUIDA_COM_FALHAS),
    ],
)
def test_conclusao_preserva_significado(resumo, esperado):
    assert transicionar_execucao(E.EXECUTANDO, esperado, resumo) == esperado
    outro = E.CONCLUIDA if esperado == E.CONCLUIDA_COM_FALHAS else E.CONCLUIDA_COM_FALHAS
    with pytest.raises(TransicaoInvalida):
        transicionar_execucao(E.EXECUTANDO, outro, resumo)


@pytest.mark.parametrize(
    "resumo", [ResumoItens(), ResumoItens(reservados=1), ResumoItens(processando=1)]
)
def test_pausa_ou_conclusao_nao_oculta_trabalho(resumo):
    for estado in (E.PAUSADA, E.CONCLUIDA, E.CONCLUIDA_COM_FALHAS, E.INTERROMPIDA):
        with pytest.raises(TransicaoInvalida):
            transicionar_execucao(E.EXECUTANDO, estado, resumo)


def test_reserva_nao_e_invocacao_e_pausa_bloqueia_inicios():
    assert reservar_tentativa(E.EXECUTANDO, I.PENDENTE) == (I.RESERVADO, T.RESERVADA)
    for estado in (E.PAUSANDO, E.PAUSADA, E.CONCLUIDA, E.CONCLUIDA_COM_FALHAS, E.INTERROMPIDA):
        with pytest.raises(TransicaoInvalida):
            reservar_tentativa(estado, I.PENDENTE)
        with pytest.raises(TransicaoInvalida):
            transicionar_tentativa(T.RESERVADA, T.INICIADA, estado)
    assert transicionar_tentativa(T.INICIADA, T.DESCONHECIDA, E.PAUSANDO) == T.DESCONHECIDA
    assert transicionar_item(I.RESERVADO, I.PENDENTE, E.PAUSANDO) == I.PENDENTE


@pytest.mark.parametrize("valor", [-1, True, 1.5, "privado", None])
def test_tipos_estritos_e_imutabilidade(valor):
    with pytest.raises(ValueError):
        ResumoItens(reservados=valor)
    with pytest.raises(TransicaoInvalida):
        transicionar_execucao(valor, E.EXECUTANDO, ResumoItens(pendentes=1))
    with pytest.raises(TransicaoInvalida):
        transicionar_item(I.PENDENTE, valor, E.EXECUTANDO)
    with pytest.raises(TransicaoInvalida):
        transicionar_tentativa(T.INICIADA, valor, E.EXECUTANDO)
    with pytest.raises(FrozenInstanceError):
        ResumoItens().pendentes = 1
