"""Certificados efemeros derivados SOMENTE de fotografia integralmente autenticada.

Nenhum texto ou chave de conteudo e armazenado. HMAC usa chave aleatoria do
contexto e vincula a admissao e os bytes historicos; hashes isolados nao autorizam.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from sqlalchemy import select

from app.campanhas.crypto import indisponivel
from app.campanhas.execucoes_models import DestinatarioCampanha, ItemExecucao
from app.campanhas.models import PreparacaoItem, PreparacaoItemTelegram
from app.campanhas.preparacoes import PreparacoesService, canon
from app.persistencia.models import utc_text


@dataclass(frozen=True, slots=True)
class SeloFotografia:
    cabecalho: bytes = field(repr=False)
    itens: MappingProxyType = field(repr=False)


def _valor(value):
    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _mac(chave, finalidade, payload):
    return hmac.digest(chave, finalidade + b"\0" + canon(payload), "sha256")


def _cabecalho(s, chave, adm, e, prep, autorizacao, header):
    # Estado operacional e campos variaveis da fotografia sao revalidados a cada
    # passo; inclui-los impede continuar com um certificado de outra decisao.
    return _mac(
        chave,
        b"edisk-fotografia-operacional-v1",
        [
            s.connection().exec_driver_sql("PRAGMA schema_version").scalar_one(),
            [adm.admissao_id, adm.usuario_id, adm.sessao_id, adm.abertura],
            [
                e.id,
                e.campanha_id,
                e.preparacao_id,
                e.autorizacao_id,
                e.autorizacao_telegram_id,
                e.modo,
                e.proprietario_id,
                e.geracao,
                e.versao,
            ],
            [[c.name, _valor(getattr(prep, c.name))] for c in prep.__table__.columns],
            [[c.name, _valor(getattr(autorizacao, c.name))] for c in autorizacao.__table__.columns],
            header,
        ],
    )


def _item(chave, cabecalho, fila, destinatario, foto):
    digest = hashlib.sha256()
    if isinstance(foto, PreparacaoItemTelegram):
        PreparacoesService._hash_item_telegram(digest, foto)
    else:
        PreparacoesService._hash_item(digest, foto)
    return _mac(
        chave,
        b"edisk-item-operacional-v1",
        [
            cabecalho.hex(),
            [
                fila.id,
                fila.execucao_id,
                fila.campanha_id,
                fila.preparacao_id,
                fila.destinatario_id,
                fila.identidade_contato_id,
                fila.identidade_telegram_id,
                fila.ordem,
            ],
            [
                destinatario.id,
                destinatario.campanha_id,
                destinatario.identidade_contato_id,
                destinatario.identidade_telegram_id,
            ],
            foto.preparacao_id,
            digest.hexdigest(),
        ],
    )


def gerar_selo(s, chave, adm, e, prep, autorizacao, header):
    """Chamador autenticou manifesto na MESMA transacao, antes desta leitura."""
    cabecalho = _cabecalho(s, chave, adm, e, prep, autorizacao, header)
    filas = s.execute(
        select(ItemExecucao, DestinatarioCampanha)
        .join(DestinatarioCampanha, DestinatarioCampanha.id == ItemExecucao.destinatario_id)
        .where(ItemExecucao.execucao_id == e.id)
        .order_by(ItemExecucao.ordem)
    ).all()
    if e.modo == "telegram":
        fotos = s.scalars(
            select(PreparacaoItemTelegram)
            .where(
                PreparacaoItemTelegram.preparacao_id == prep.id,
                PreparacaoItemTelegram.incluido == 1,
            )
            .order_by(PreparacaoItemTelegram.identidade_telegram_id)
        ).all()
    else:
        fotos = s.scalars(
            select(PreparacaoItem)
            .where(PreparacaoItem.preparacao_id == prep.id, PreparacaoItem.incluido == 1)
            .order_by(PreparacaoItem.identidade_contato_id)
        ).all()
    if len(filas) != prep.incluidos or len(fotos) != prep.incluidos:
        raise indisponivel()
    certificados = {}
    for ordem, ((fila, destinatario), foto) in enumerate(zip(filas, fotos, strict=True), 1):
        if (
            fila.ordem != ordem
            or (
                fila.identidade_telegram_id
                if e.modo == "telegram"
                else fila.identidade_contato_id
            )
            != (
                foto.identidade_telegram_id
                if e.modo == "telegram"
                else foto.identidade_contato_id
            )
            or fila.preparacao_id != prep.id
            or fila.campanha_id != e.campanha_id
            or destinatario.campanha_id != e.campanha_id
            or (
                destinatario.identidade_telegram_id
                if e.modo == "telegram"
                else destinatario.identidade_contato_id
            )
            != (
                foto.identidade_telegram_id
                if e.modo == "telegram"
                else foto.identidade_contato_id
            )
        ):
            raise indisponivel()
        certificados[fila.id] = _item(chave, cabecalho, fila, destinatario, foto)
    return SeloFotografia(cabecalho, MappingProxyType(certificados))


def verificar_cabecalho(s, selo, chave, adm, e, prep, autorizacao, header):
    if (
        not isinstance(selo, SeloFotografia)
        or not isinstance(selo.cabecalho, bytes)
        or not hmac.compare_digest(
            selo.cabecalho, _cabecalho(s, chave, adm, e, prep, autorizacao, header)
        )
    ):
        raise indisponivel()


def verificar_item(s, selo, chave, fila, foto):
    if not isinstance(selo, SeloFotografia) or not isinstance(selo.itens, MappingProxyType):
        raise indisponivel()
    certificado = selo.itens.get(fila.id)
    destinatario = s.get(DestinatarioCampanha, fila.destinatario_id)
    if (
        not isinstance(certificado, bytes)
        or destinatario is None
        or foto.incluido != 1
        or not isinstance(foto.nonce, bytes)
        or not isinstance(foto.conteudo_cifrado, bytes)
        or not hmac.compare_digest(
            certificado, _item(chave, selo.cabecalho, fila, destinatario, foto)
        )
    ):
        raise indisponivel()
