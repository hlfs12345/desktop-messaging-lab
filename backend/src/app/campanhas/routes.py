"""Rotas privadas: somente rascunhos, referências públicas e revisão."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependencies import authenticated_user, session_token
from app.campanhas.configuracao_simulacao import CenarioSimulacao
from app.campanhas.schemas import (
    AutorizarSimulacaoEntrada,
    CodigoEntrada,
    ConfirmarEnvioTelegramEntrada,
    ConfirmarEnvioWhatsappEntrada,
    PrepararEntrada,
    PreviaEntrada,
    RascunhoEdicao,
    RascunhoEntrada,
    RevogarSimulacaoEntrada,
    SelecaoElegiveisEntrada,
    SelecaoEntrada,
    TransicaoEntrada,
)
from app.campanhas.schemas import ChaveEntrada as OperacaoEntrada

router = APIRouter(prefix="/campanhas", dependencies=[Depends(authenticated_user)])
Token = Annotated[str, Depends(session_token)]
Pagina = Annotated[int, Query(ge=1, le=1000000)]
Limite = Annotated[int, Query(ge=1, le=50)]


@router.get("")
def listar(
    request: Request, pagina: Pagina = 1, limite: Limite = 20, q: str = Query("", max_length=120)
):
    return request.app.state.campanhas.listar(pagina, limite, q)


@router.post("", status_code=201)
def criar(request: Request, data: RascunhoEntrada, token: Token):
    return request.app.state.campanhas.criar(data, token)


@router.get("/opcoes")
def opcoes(
    request: Request,
    tipo: Literal["contas", "listas", "versoes", "escopos"],
    pagina: Pagina = 1,
    limite: Limite = 20,
    q: str = Query("", max_length=120),
):
    return request.app.state.campanhas.opcoes(tipo, pagina, limite, q)


# Rotas estaticas de chaves antes da rota UUID.
@router.get("/chaves/estado")
def estado_chave(request: Request, token: Token):
    return request.app.state.preparacoes.estado_chave(token)


@router.post("/chaves/inicializar")
def inicializar_chave(request: Request, data: OperacaoEntrada, token: Token):
    return request.app.state.preparacoes.chave("inicializar", data, token)


@router.post("/chaves/reemitir")
def reemitir_chave(request: Request, data: OperacaoEntrada, token: Token):
    return request.app.state.preparacoes.chave("reemitir", data, token)


@router.post("/chaves/{acao}")
def conferir_chave(
    request: Request, acao: Literal["conferir", "recuperar"], data: CodigoEntrada, token: Token
):
    return request.app.state.preparacoes.chave(acao, data, token)


@router.get("/{campanha_id}")
def obter(request: Request, campanha_id: UUID):
    return request.app.state.campanhas.obter(str(campanha_id))


@router.patch("/{campanha_id}")
def editar(request: Request, campanha_id: UUID, data: RascunhoEdicao, token: Token):
    return request.app.state.campanhas.editar(str(campanha_id), data, token)


@router.put("/{campanha_id}/selecoes")
def selecionar(request: Request, campanha_id: UUID, data: SelecaoEntrada, token: Token):
    return request.app.state.campanhas.selecionar(str(campanha_id), data, token)


@router.put("/{campanha_id}/selecoes-elegiveis")
def selecionar_elegiveis(
    request: Request, campanha_id: UUID, data: SelecaoElegiveisEntrada, token: Token
):
    return request.app.state.campanhas.selecionar_elegiveis(str(campanha_id), data, token)


@router.get("/{campanha_id}/revisao")
def revisao(
    request: Request,
    campanha_id: UUID,
    pagina: Pagina = 1,
    limite: Limite = 20,
    somente_selecionadas: bool = False,
):
    return request.app.state.campanhas.revisao(
        str(campanha_id), pagina, limite, somente_selecionadas
    )


@router.post("/{campanha_id}/previa")
def previa(request: Request, campanha_id: UUID, data: PreviaEntrada):
    return request.app.state.campanhas.previa(str(campanha_id), data)


@router.get("/{campanha_id}/revisao-preparacao")
def revisao_preparacao(
    request: Request,
    campanha_id: UUID,
    token: Token,
    pagina: Pagina = 1,
    limite: Limite = 20,
    marcador: str | None = Query(None, max_length=180),
    cenario: CenarioSimulacao | None = None,
):
    return request.app.state.preparacoes.revisao(
        str(campanha_id), token, pagina, limite, marcador, cenario
    )


@router.post("/{campanha_id}/preparacoes", status_code=201)
def preparar(request: Request, campanha_id: UUID, data: PrepararEntrada, token: Token):
    return request.app.state.preparacoes.preparar(str(campanha_id), data, token)


@router.get("/{campanha_id}/preparacoes")
def preparacoes(
    request: Request, campanha_id: UUID, token: Token, pagina: Pagina = 1, limite: Limite = 20
):
    return request.app.state.preparacoes.consultar(
        str(campanha_id), token, pagina=pagina, limite=limite
    )


@router.get("/{campanha_id}/preparacoes/{preparacao_id}")
@router.get("/{campanha_id}/preparacoes/{preparacao_id}/itens")
def preparacao(
    request: Request,
    campanha_id: UUID,
    preparacao_id: UUID,
    token: Token,
    pagina: Pagina = 1,
    limite: Limite = 20,
):
    return request.app.state.preparacoes.consultar(
        str(campanha_id), token, str(preparacao_id), pagina, limite
    )


@router.get("/{campanha_id}/preparacoes/{preparacao_id}/itens/{identidade_id}/conteudo")
def conteudo(
    request: Request, campanha_id: UUID, preparacao_id: UUID, identidade_id: UUID, token: Token
):
    return request.app.state.preparacoes.conteudo(
        str(campanha_id), str(preparacao_id), str(identidade_id), token
    )


@router.post("/{campanha_id}/preparacoes/{preparacao_id}/confirmacao")
def confirmar_preparacao(
    request: Request, campanha_id: UUID, preparacao_id: UUID, data: TransicaoEntrada, token: Token
):
    if str(data.preparacao_id) != str(preparacao_id):
        from app.contatos.errors import conflito

        raise conflito()
    return request.app.state.preparacoes.transicao(str(campanha_id), data, token, True)


@router.post("/{campanha_id}/retorno-rascunho")
def retornar_rascunho(request: Request, campanha_id: UUID, data: TransicaoEntrada, token: Token):
    return request.app.state.preparacoes.transicao(str(campanha_id), data, token)


@router.get("/{campanha_id}/preparacoes/{preparacao_id}/revisao-envio-telegram")
def revisao_envio_telegram(
    request: Request, campanha_id: UUID, preparacao_id: UUID, token: Token
):
    return request.app.state.envios_telegram.revisao(
        str(campanha_id), str(preparacao_id), token
    )


@router.post("/{campanha_id}/preparacoes/{preparacao_id}/envio-telegram", status_code=201)
def confirmar_envio_telegram(
    request: Request,
    campanha_id: UUID,
    preparacao_id: UUID,
    data: ConfirmarEnvioTelegramEntrada,
    token: Token,
):
    return request.app.state.envios_telegram.confirmar(
        str(campanha_id), str(preparacao_id), data, token
    )


@router.get("/{campanha_id}/preparacoes/{preparacao_id}/revisao-envio-whatsapp")
def revisao_envio_whatsapp(request: Request, campanha_id: UUID, preparacao_id: UUID, token: Token):
    return request.app.state.envios_whatsapp.revisao(str(campanha_id), str(preparacao_id), token)


@router.post("/{campanha_id}/preparacoes/{preparacao_id}/envio-whatsapp", status_code=201)
def confirmar_envio_whatsapp(
    request: Request,
    campanha_id: UUID,
    preparacao_id: UUID,
    data: ConfirmarEnvioWhatsappEntrada,
    token: Token,
):
    return request.app.state.envios_whatsapp.confirmar(
        str(campanha_id), str(preparacao_id), data, token
    )


@router.get("/{campanha_id}/preparacoes/{preparacao_id}/revisao-autorizacao")
def revisao_autorizacao(request: Request, campanha_id: UUID, preparacao_id: UUID, token: Token):
    return request.app.state.autorizacoes_simulacao.revisao(
        str(campanha_id), str(preparacao_id), token
    )


@router.post("/{campanha_id}/preparacoes/{preparacao_id}/autorizacoes-simulacao", status_code=201)
def autorizar_simulacao(
    request: Request,
    campanha_id: UUID,
    preparacao_id: UUID,
    data: AutorizarSimulacaoEntrada,
    token: Token,
):
    return request.app.state.autorizacoes_simulacao.autorizar(
        str(campanha_id), str(preparacao_id), data, token
    )


@router.get("/{campanha_id}/autorizacoes-simulacao")
def autorizacoes_simulacao(
    request: Request, campanha_id: UUID, token: Token, pagina: Pagina = 1, limite: Limite = 20
):
    return request.app.state.autorizacoes_simulacao.consultar(
        str(campanha_id), token, pagina=pagina, limite=limite
    )


@router.get("/{campanha_id}/autorizacoes-simulacao/{autorizacao_id}")
def autorizacao_simulacao(request: Request, campanha_id: UUID, autorizacao_id: UUID, token: Token):
    return request.app.state.autorizacoes_simulacao.consultar(
        str(campanha_id), token, str(autorizacao_id)
    )


@router.post("/{campanha_id}/autorizacoes-simulacao/{autorizacao_id}/revogacao")
def revogar_simulacao(
    request: Request,
    campanha_id: UUID,
    autorizacao_id: UUID,
    data: RevogarSimulacaoEntrada,
    token: Token,
):
    return request.app.state.autorizacoes_simulacao.revogar(
        str(campanha_id), str(autorizacao_id), data, token
    )
