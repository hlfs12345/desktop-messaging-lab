"""Contrato isolado: entradas fictícias; nenhuma campanha ou integração operacional."""

import ctypes
import gc
import os
import socket
import sqlite3
import subprocess
import weakref
from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest
import sqlalchemy

from app.contas_envio import segredos
from app.persistencia import database
from app.provedores.contratos import (
    CapacidadesProvedor,
    CodigoResultado,
    ContextoProvedor,
    EntradaTentativa,
    EstadoResultado,
    Plataforma,
    Provedor,
    ProvedorTexto,
    ResultadoTentativa,
)
from app.provedores.mock import (
    CenarioMock,
    ExcecaoSimulada,
    InterrupcaoSimulada,
    MockProvedor,
)

DESTINO = "destino-ficticio-privado"
TEXTO = "Texto fictício confidencial da tentativa."


MARCADOR_DESTINO_INVALIDO = "destino-invalido-ficticio"
MARCADOR_TEXTO_EXCESSIVO = "texto-excessivo-ficticio"


def entrada(**changes):
    return EntradaTentativa(
        **{
            "tentativa_id": UUID(int=1),
            "contexto": ContextoProvedor(Provedor.MOCK, Plataforma.WHATSAPP),
            "destino": DESTINO,
            "texto": TEXTO,
            **changes,
        }
    )


def verificar_erro_sem_payload(error, caplog, *rejeitados):
    saidas = (str(error), repr(error), repr(error.args), caplog.text)
    for valor in (TEXTO, DESTINO, *rejeitados):
        formas = (valor, repr(valor)[1:-1]) if isinstance(valor, str) else (str(valor),)
        for forma in formas:
            # A string vazia está em qualquer saída e não identifica um vazamento.
            if forma:
                for saida in saidas:
                    assert forma not in saida


def test_contrato_imutavel_e_capacidades_honestas():
    mock: ProvedorTexto = MockProvedor()
    request = entrada()
    result = mock.executar(request)
    assert result.contexto == request.contexto
    assert result.tentativa_id == request.tentativa_id
    assert mock.capacidades == CapacidadesProvedor(texto=True, simulacao=True)
    assert not any(
        (
            mock.capacidades.entrega,
            mock.capacidades.leitura,
            mock.capacidades.botoes,
            mock.capacidades.anexos,
            mock.capacidades.conexao_externa_verificada,
        )
    )
    for obj, attr, value in (
        (request, "texto", "outro"),
        (request.contexto, "provedor", Provedor.META_CLOUD),
        (result, "estado", EstadoResultado.FALHOU),
        (mock.capacidades, "entrega", True),
        (mock, "padrao", CenarioMock.FALHOU),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, attr, value)


@pytest.mark.parametrize(
    "changes",
    [
        {"tentativa_id": UUID(int=0)},
        {"tentativa_id": TEXTO},
        {"contexto": TEXTO},
        {"destino": ""},
        {"destino": " teste"},
        {"destino": "teste\n"},
        {"destino": "teste\x00"},
        {"destino": "teste\x7f"},
        {"destino": "a" * 257},
        {"destino": "á" * 129},
        {"destino": "\ud800"},
        {"destino": None},
        {"texto": ""},
        {"texto": " \n\t"},
        {"texto": None},
        {"texto": "\ud800"},
        {"texto": "á" * 32769},
        pytest.param(
            {"destino": MARCADOR_DESTINO_INVALIDO + " com espaco"},
            id="destino-invalido-marcado",
        ),
        pytest.param(
            {"texto": MARCADOR_TEXTO_EXCESSIVO + "x" * 65536},
            id="texto-excessivo-marcado",
        ),
    ],
)
def test_entrada_invalida_tem_erro_sanitizado(changes, caplog):
    caplog.set_level("DEBUG")
    with pytest.raises(ValueError) as error:
        entrada(**changes)
    verificar_erro_sem_payload(
        error.value,
        caplog,
        *changes.values(),
        MARCADOR_DESTINO_INVALIDO,
        MARCADOR_TEXTO_EXCESSIVO,
    )
    assert error.value.__cause__ is None


def test_limites_utf8_e_texto_multilinha():
    request = entrada(destino="á" * 128, texto="á" * 32768)
    assert MockProvedor().executar(request).estado == EstadoResultado.ACEITA_PROVEDOR
    assert MockProvedor().executar(entrada(texto="Linha 1\nLinha 2\t."))


@pytest.mark.parametrize(
    "provider,platform",
    [
        ("mock", Plataforma.WHATSAPP),
        (Provedor.MOCK, TEXTO),
        (Provedor.TELEGRAM_BOT_API, Plataforma.WHATSAPP),
        (Provedor.META_CLOUD, Plataforma.TELEGRAM),
        (Provedor.QR_CANDIDATO, Plataforma.TELEGRAM),
    ],
)
def test_contexto_estrito_e_compativel(provider, platform, caplog):
    caplog.set_level("DEBUG")
    with pytest.raises(ValueError) as error:
        ContextoProvedor(provider, platform)
    verificar_erro_sem_payload(error.value, caplog, provider, platform)


@pytest.mark.parametrize(
    "provider,platform",
    [
        (Provedor.QR_CANDIDATO, Plataforma.WHATSAPP),
        (Provedor.META_CLOUD, Plataforma.WHATSAPP),
        (Provedor.TELEGRAM_BOT_API, Plataforma.TELEGRAM),
    ],
)
def test_mock_rejeita_provedor_real(provider, platform):
    with pytest.raises(ValueError, match="exige contexto de provedor mock"):
        MockProvedor().executar(entrada(contexto=ContextoProvedor(provider, platform)))


@pytest.mark.parametrize("platform", list(Plataforma))
@pytest.mark.parametrize(
    "scenario,state,code",
    [
        (CenarioMock.ACEITA_PROVEDOR, EstadoResultado.ACEITA_PROVEDOR, CodigoResultado.ACEITA),
        (CenarioMock.FALHOU, EstadoResultado.FALHOU, CodigoResultado.FALHA),
        (CenarioMock.DESCONHECIDA, EstadoResultado.DESCONHECIDA, CodigoResultado.SEM_CONFIRMACAO),
    ],
)
def test_cenarios_por_tentativa_deterministicos(platform, scenario, state, code):
    request = entrada(contexto=ContextoProvedor(Provedor.MOCK, platform))
    mock = MockProvedor({request.tentativa_id: scenario})
    first = mock.executar(request)
    assert first == mock.executar(request)
    assert (first.estado, first.codigo) == (state, code)
    assert mock.executar(replace(request, tentativa_id=UUID(int=2))).estado == (
        EstadoResultado.ACEITA_PROVEDOR
    )


@pytest.mark.parametrize(
    "scenario,exception",
    [
        (CenarioMock.INTERRUPCAO, InterrupcaoSimulada),
        (CenarioMock.EXCECAO, ExcecaoSimulada),
    ],
)
def test_interrupcao_e_excecao_controladas_sem_payload(scenario, exception, caplog):
    caplog.set_level("DEBUG")
    mock = MockProvedor(padrao=scenario)
    for _ in range(2):
        with pytest.raises(exception) as error:
            mock.executar(entrada())
        assert "resultado não confirmado" in str(error.value)
        for sensitive in (TEXTO, DESTINO):
            assert sensitive not in str(error.value)
            assert sensitive not in repr(error.value)
            assert sensitive not in repr(error.value.args)
            assert sensitive not in caplog.text


def test_copia_de_cenarios_e_isolamento_entre_instancias():
    plan = {UUID(int=1): CenarioMock.FALHOU}
    first = MockProvedor(plan)
    second = MockProvedor()
    plan[UUID(int=1)] = CenarioMock.DESCONHECIDA
    assert first.executar(entrada()).estado == EstadoResultado.FALHOU
    assert second.executar(entrada()).estado == EstadoResultado.ACEITA_PROVEDOR
    assert MockProvedor(padrao=CenarioMock.DESCONHECIDA).executar(entrada()).estado == (
        EstadoResultado.DESCONHECIDA
    )
    with pytest.raises(TypeError):
        first.cenarios[UUID(int=1)] = CenarioMock.ACEITA_PROVEDOR


@pytest.mark.parametrize(
    "changes",
    [
        {"padrao": TEXTO},
        {"cenarios": TEXTO},
        {"cenarios": {TEXTO: CenarioMock.FALHOU}},
        {"cenarios": {UUID(int=0): CenarioMock.FALHOU}},
        {"cenarios": {UUID(int=1): TEXTO}},
    ],
)
def test_configuracao_nao_aceita_payload_ou_cenario_livre(changes, caplog):
    caplog.set_level("DEBUG")
    with pytest.raises(ValueError) as error:
        MockProvedor(**changes)
    verificar_erro_sem_payload(error.value, caplog, *changes.values())


@pytest.mark.parametrize(
    "changes",
    [
        {"tentativa_id": TEXTO},
        {"contexto": TEXTO},
        {"estado": TEXTO},
        {"codigo": TEXTO},
        {"codigo": CodigoResultado.FALHA},
    ],
)
def test_resultado_rejeita_codigo_livre_ou_incoerente(changes, caplog):
    caplog.set_level("DEBUG")
    with pytest.raises(ValueError) as error:
        replace(MockProvedor().executar(entrada()), **changes)
    verificar_erro_sem_payload(error.value, caplog, *changes.values())


def test_capacidades_e_entrada_exigem_tipos_explicitos(caplog):
    caplog.set_level("DEBUG")
    with pytest.raises(ValueError) as error:
        CapacidadesProvedor(texto=1, simulacao=True)
    verificar_erro_sem_payload(error.value, caplog, 1)
    with pytest.raises(ValueError) as error:
        MockProvedor().executar(TEXTO)
    verificar_erro_sem_payload(error.value, caplog, TEXTO)


def test_representacoes_resultados_e_logs_nao_expoem_payload(caplog):
    caplog.set_level("DEBUG")
    request = entrada()
    for scenario in (
        CenarioMock.ACEITA_PROVEDOR,
        CenarioMock.FALHOU,
        CenarioMock.DESCONHECIDA,
    ):
        mock = MockProvedor(padrao=scenario)
        result = mock.executar(request)
        assert not hasattr(result, "destino") and not hasattr(result, "texto")
        for obj in (request, request.contexto, result, mock, mock.cenarios):
            for sensitive in (DESTINO, TEXTO):
                assert sensitive not in repr(obj)
                assert sensitive not in str(obj)
    assert not caplog.records


def test_mock_sem_rede_dns_subprocesso_banco_ou_credenciais(monkeypatch):
    def proibido(*args, **kwargs):
        pytest.fail("O mock tentou acessar I/O ou credenciais.")

    # As guardas abrangem construção, sucesso, falha, desconhecido e exceções.
    with monkeypatch.context() as patch:
        for name in (
            "socket",
            "create_connection",
            "getaddrinfo",
            "gethostbyname",
            "gethostbyname_ex",
            "gethostbyaddr",
        ):
            patch.setattr(socket, name, proibido)
        patch.setattr(subprocess, "Popen", proibido)
        patch.setattr(os, "system", proibido)
        patch.setattr(os, "startfile", proibido)
        patch.setattr(ctypes, "WinDLL", proibido)
        patch.setattr(sqlite3, "connect", proibido)
        patch.setattr(sqlalchemy, "create_engine", proibido)
        patch.setattr(database, "make_engine", proibido)
        patch.setattr(database.Database, "__init__", proibido)
        patch.setattr(segredos, "dpapi", proibido)
        patch.setattr(segredos.SegredosWindows, "__init__", proibido)
        for scenario in CenarioMock:
            mock = MockProvedor(padrao=scenario)
            for platform in Plataforma:
                request = entrada(contexto=ContextoProvedor(Provedor.MOCK, platform))
                if scenario == CenarioMock.INTERRUPCAO:
                    with pytest.raises(InterrupcaoSimulada):
                        mock.executar(request)
                elif scenario == CenarioMock.EXCECAO:
                    with pytest.raises(ExcecaoSimulada):
                        mock.executar(request)
                else:
                    assert isinstance(mock.executar(request), ResultadoTentativa)


def test_volume_8000_sem_reter_entradas_ou_payloads():
    class TextoRastreavel(str):
        pass

    mock = MockProvedor()
    references = []
    for number in range(1, 8001):
        request = entrada(
            tentativa_id=UUID(int=number),
            destino=TextoRastreavel(f"destino-ficticio-{number}"),
            texto=TextoRastreavel(f"Conteúdo fictício {number}."),
        )
        references.extend(
            (weakref.ref(request), weakref.ref(request.destino), weakref.ref(request.texto))
        )
        result = mock.executar(request)
        assert result.estado == EstadoResultado.ACEITA_PROVEDOR
        del request, result
    gc.collect()
    assert all(reference() is None for reference in references)
    assert not mock.cenarios
