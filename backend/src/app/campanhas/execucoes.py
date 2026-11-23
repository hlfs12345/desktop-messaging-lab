"""Comandos persistentes mock: nao iniciam worker nem invocam provedores."""

import hashlib
import hmac
import json
import secrets

from sqlalchemy import func, or_, select, update

from app.auth.models import Sessao
from app.auth.service import token_digest
from app.campanhas.execucoes_models import (
    ExecucaoCampanha,
    ItemExecucao,
    TentativaEnvio,
    TransicaoExecucao,
)
from app.campanhas.execucoes_schemas import IniciarExecucaoEntrada, PausarExecucaoEntrada
from app.campanhas.models import AutorizacaoEnvioTelegram, AutorizacaoSimulacao, Campanha
from app.contas_envio.models import ContaEnvio
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Auditoria
from app.contatos.repository import agora, uuid
from app.persistencia.models import utc_text

ABERTOS = ("executando", "pausando", "pausada")
ESTADOS_ITENS = (
    "pendente",
    "reservado",
    "processando",
    "aceita_provedor",
    "falhou",
    "desconhecida",
    "ignorado",
    "interrompido",
)


def _tem_tentativas(session, campanha_id):
    return (
        session.scalar(
            select(TentativaEnvio.id)
            .join(ExecucaoCampanha, ExecucaoCampanha.id == TentativaEnvio.execucao_id)
            .where(ExecucaoCampanha.campanha_id == campanha_id)
            .limit(1)
        )
        is not None
    )


def _instante(session, execucao):
    ultima = session.scalar(
        select(func.max(ItemExecucao.atualizado_em)).where(ItemExecucao.execucao_id == execucao.id)
    )
    return max(agora(), execucao.atualizado_em, ultima or execucao.criado_em)


def encerrar_para_rascunho(repo, session, campanha_id, preparacao_id, usuario):
    """Chamador mantem lock/transacao; nunca revogar ou invalidar antes de encerrar."""
    if _tem_tentativas(session, campanha_id):
        raise NegocioError(
            "retorno_indisponivel", "Campanha com tentativas exige preservar o historico.", 409
        )
    execucao = session.scalar(
        select(ExecucaoCampanha).where(
            ExecucaoCampanha.campanha_id == campanha_id,
            ExecucaoCampanha.preparacao_id == preparacao_id,
        )
    )
    if execucao is None:
        return None
    stamp = _instante(session, execucao)
    if execucao.estado not in ABERTOS:
        return stamp
    session.execute(
        update(ItemExecucao)
        .where(ItemExecucao.execucao_id == execucao.id, ItemExecucao.estado == "pendente")
        .values(
            estado="interrompido",
            motivo_codigo="encerramento",
            versao=ItemExecucao.versao + 1,
            atualizado_em=stamp,
            alterado_por_usuario_id=usuario,
        )
    )
    changed = session.execute(
        update(ExecucaoCampanha)
        .where(
            ExecucaoCampanha.id == execucao.id,
            ExecucaoCampanha.versao == execucao.versao,
            ExecucaoCampanha.estado.in_(ABERTOS),
        )
        .values(
            estado="interrompida",
            versao=ExecucaoCampanha.versao + 1,
            atualizado_em=stamp,
            alterado_por_usuario_id=usuario,
        )
    )
    if changed.rowcount != 1:
        raise conflito()
    session.flush()
    repo.auditar(
        session,
        usuario,
        "encerrar_execucao_para_rascunho",
        "campanha",
        campanha_id,
        execucao_id=execucao.id,
        preparacao_id=preparacao_id,
    )
    session.flush()
    return stamp


class ExecucoesService:
    def __init__(self, autorizacoes, telegram=None):
        self.a, self.p = autorizacoes, autorizacoes.p
        self.c, self.repo, self.auth = autorizacoes.c, autorizacoes.repo, autorizacoes.auth
        self.telegram = telegram
        self._chave_marcador = secrets.token_bytes(32)

    def _contexto(self, session, token):
        usuario = self.a._usuario(session, token)
        sessao_id = session.scalar(
            select(Sessao.id).where(Sessao.token_hash == token_digest(token))
        )
        if sessao_id is None:
            raise conflito()
        return usuario, sessao_id

    def _avaliar(self, session, campanha, prep, *, impedidos=None):
        if (
            campanha.status != "preparada"
            or prep.invalidado_em is not None
            or prep.confirmado_em is None
        ):
            raise conflito()
        autorizacao = session.scalar(
            select(AutorizacaoSimulacao).where(
                AutorizacaoSimulacao.campanha_id == campanha.id,
                AutorizacaoSimulacao.preparacao_id == prep.id,
            )
        )
        if autorizacao is None:
            raise NegocioError(
                "autorizacao_indisponivel", "Revise a autorizacao da simulacao.", 409
            )
        cfg, atual = self.a._avaliar(session, campanha, prep, impedidos=impedidos)
        if (
            autorizacao.base_id != prep.base_id
            or autorizacao.impressao != prep.impressao
            or autorizacao.resumo_configuracao != cfg.resumo_configuracao
        ):
            raise NegocioError(
                "integridade_comprometida", "Vinculos da autorizacao incompativeis.", 503
            )
        bloqueios = []
        if autorizacao.estado != "vigente":
            bloqueios.append("autorizacao_revogada")
        if not atual["condicoes_atuais_atendidas"]:
            bloqueios.append("condicoes_atuais_nao_atendidas")
        if (
            session.scalar(
                select(ExecucaoCampanha.id)
                .where(
                    or_(
                        ExecucaoCampanha.preparacao_id == prep.id,
                        ExecucaoCampanha.autorizacao_id == autorizacao.id,
                    )
                )
                .limit(1)
            )
            is not None
        ):
            bloqueios.append("execucao_existente")
        if (
            session.scalar(
                select(ExecucaoCampanha.id)
                .where(
                    ExecucaoCampanha.campanha_id == campanha.id,
                    ExecucaoCampanha.estado.in_(ABERTOS),
                )
                .limit(1)
            )
            is not None
        ):
            bloqueios.append("execucao_em_aberto")
        if (
            session.scalar(
                select(ExecucaoCampanha.id)
                .where(ExecucaoCampanha.estado.in_(("executando", "pausando")))
                .limit(1)
            )
            is not None
        ):
            bloqueios.append("execucao_despachante_existente")
        if _tem_tentativas(session, campanha.id):
            bloqueios.append("historico_tentativas")
        return autorizacao, cfg, atual, bloqueios

    def _marcador(self, session, campanha, prep, autorizacao, cfg, usuario, sessao_id):
        payload = {
            "finalidade": "inicio-simulacao-v1",
            "abertura": self.p.abertura,
            "usuario_id": usuario,
            "sessao_id": sessao_id,
            "campanha_id": campanha.id,
            "versao": campanha.versao,
            "preparacao_id": prep.id,
            "base_id": prep.base_id,
            "impressao": prep.impressao,
            "resumo_configuracao": cfg.resumo_configuracao,
            "confirmado_em": utc_text(prep.confirmado_em),
            "autorizacao_id": autorizacao.id,
            "versao_autorizacao": autorizacao.versao,
            "revisao": self.repo.revisao(session),
            "revisao_operacional": session.scalar(
                select(func.count()).select_from(TransicaoExecucao)
            ),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        return "inicio-v1." + hmac.new(self._chave_marcador, raw, hashlib.sha256).hexdigest()

    def _marcador_telegram(self, session, contexto, usuario, sessao_id):
        campanha, prep, conta = (
            contexto["campanha"],
            contexto["preparacao"],
            contexto["conta"],
        )
        payload = {
            "finalidade": "inicio-telegram-lote-v1",
            "abertura": self.p.abertura,
            "usuario_id": usuario,
            "sessao_id": sessao_id,
            "campanha_id": campanha.id,
            "versao": campanha.versao,
            "preparacao_id": prep.id,
            "impressao": prep.impressao,
            "conta_id": conta.id if conta else None,
            "conta_versao": conta.versao if conta else None,
            "bot_id": conta.telegram_bot_id if conta else None,
            "categoria": campanha.categoria,
            "quantidade": contexto["quantidade"],
            "conteudo_hash": contexto["conteudo_hash"],
            "resumo_configuracao": contexto["configuracao_hash"],
            "revisao": self.repo.revisao(session),
            "revisao_operacional": session.scalar(
                select(func.count()).select_from(TransicaoExecucao)
            ),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        return "inicio-v1." + hmac.new(self._chave_marcador, raw, hashlib.sha256).hexdigest()

    def _revisao_telegram(self, session, campanha_id, preparacao_id, usuario, sessao_id):
        if self.telegram is None:
            raise NegocioError("execucao_indisponivel", "Execucao Telegram indisponivel.", 409)
        contexto = self.telegram.avaliar_lote(session, campanha_id, preparacao_id)
        bloqueios = list(contexto["impedimentos"])
        if session.scalar(
            select(AutorizacaoEnvioTelegram.id).where(
                AutorizacaoEnvioTelegram.preparacao_id == preparacao_id
            )
        ):
            bloqueios.append("autorizacao_existente")
        if session.scalar(
            select(ExecucaoCampanha.id)
            .where(ExecucaoCampanha.estado.in_(("executando", "pausando")))
            .limit(1)
        ):
            bloqueios.append("execucao_despachante_existente")
        if _tem_tentativas(session, campanha_id):
            bloqueios.append("historico_tentativas")
        motivos = contexto["motivos_destinatarios"]
        impedidos = sum(bool(value) for value in motivos.values())
        return {
            "id": campanha_id,
            "preparacao_id": preparacao_id,
            "versao": contexto["campanha"].versao,
            "impressao": contexto["preparacao"].impressao,
            "resumo_configuracao": contexto["configuracao_hash"],
            "categoria": contexto["campanha"].categoria,
            "bot_externo_id": (
                contexto["conta"].telegram_bot_id if contexto["conta"] else None
            ),
            "marcador_inicio": self._marcador_telegram(
                session, contexto, usuario, sessao_id
            ),
            "iniciavel": not bloqueios,
            "avaliacao_atual": {
                "incluidos": contexto["quantidade"],
                "excluidos": contexto["preparacao"].excluidos,
                "elegiveis_atuais": contexto["quantidade"] - impedidos,
                "impedidos_atuais": impedidos,
                "condicoes_atuais_atendidas": not contexto["impedimentos"],
                "motivos_destinatarios": motivos,
            },
            "impedimentos_execucao": sorted(set(bloqueios)),
            "processamento_disponivel": False,
            "configuracao_execucao": contexto["configuracao"],
            "modo": "telegram",
        }

    def revisao(self, campanha_id, preparacao_id, token):
        with self.repo.transacao() as session:
            usuario, sessao_id = self._contexto(session, token)
            campanha = self.repo.obter(session, Campanha, campanha_id)
            prep = self.p._obter(session, campanha_id, preparacao_id)
            conta = session.get(ContaEnvio, campanha.conta_envio_id)
            if conta is not None and conta.provedor == "telegram_bot_api":
                resultado = self._revisao_telegram(
                    session, campanha_id, preparacao_id, usuario, sessao_id
                )
                self.a._usuario(session, token)
                return resultado
            autorizacao, cfg, atual, bloqueios = self._avaliar(session, campanha, prep)
            resultado = {
                "id": campanha.id,
                "preparacao_id": prep.id,
                "versao": campanha.versao,
                "autorizacao_id": autorizacao.id,
                "versao_autorizacao": autorizacao.versao,
                "impressao": prep.impressao,
                "resumo_configuracao": cfg.resumo_configuracao,
                "marcador_inicio": self._marcador(
                    session, campanha, prep, autorizacao, cfg, usuario, sessao_id
                ),
                "iniciavel": not bloqueios,
                "avaliacao_atual": atual,
                "impedimentos_execucao": bloqueios,
                "processamento_disponivel": False,
                "configuracao_simulacao": cfg.model_dump(),
                "configuracao_execucao": cfg.model_dump(),
                "modo": "mock",
            }
            self.a._usuario(session, token)
            return resultado

    def _recibo(self, session, data, usuario, digest, acao, campanha, execucao, **extra):
        resultado = {
            "id": campanha.id,
            "versao": campanha.versao,
            "operacao_id": str(data.operacao_id),
            "execucao_id": execucao.id,
            "preparacao_id": execucao.preparacao_id,
            "autorizacao_id": execucao.autorizacao_id or execucao.autorizacao_telegram_id,
            "modo": execucao.modo,
            "versao_execucao": execucao.versao,
            "estado_execucao": execucao.estado,
            "recibo_historico": True,
            "validade_atual": "nao_avaliada",
            "processamento_disponivel": False,
            **extra,
        }
        session.add(
            Auditoria(
                id=str(data.operacao_id),
                usuario_id=usuario,
                acao=acao,
                entidade="campanha",
                entidade_id=campanha.id,
                ocorrido_em=agora(),
                resumo_json=json.dumps({"requisicao_hash": digest, "resultado": resultado}),
            )
        )
        session.flush()
        return resultado

    def iniciar(self, campanha_id, preparacao_id, data, token):
        data = IniciarExecucaoEntrada.model_validate(data)
        if data.modo == "telegram":
            return self._iniciar_telegram(campanha_id, preparacao_id, data, token)
        with self.repo.transacao() as session:
            usuario, sessao_id = self._contexto(session, token)
            _, digest, anterior = self.c._repeticao(
                session, data, token, "iniciar_execucao:" + preparacao_id, campanha_id
            )
            if anterior is not None:
                self.a._usuario(session, token)
                return anterior
            campanha = self.repo.obter(session, Campanha, campanha_id)
            prep = self.p._obter(session, campanha_id, preparacao_id)
            impedidos = {}
            autorizacao, cfg, atual, bloqueios = self._avaliar(
                session, campanha, prep, impedidos=impedidos
            )
            if (
                campanha.versao != data.versao
                or autorizacao.id != str(data.autorizacao_id)
                or autorizacao.versao != data.versao_autorizacao
                or prep.impressao != data.impressao
                or cfg.resumo_configuracao != data.resumo_configuracao
                or not hmac.compare_digest(
                    self._marcador(session, campanha, prep, autorizacao, cfg, usuario, sessao_id),
                    data.marcador_inicio,
                )
            ):
                raise conflito()
            if bloqueios:
                raise NegocioError(
                    "inicio_indisponivel", "Revise os impedimentos atuais da simulacao.", 409
                )
            stamp = max(agora(), campanha.atualizado_em, autorizacao.autorizado_em)
            execucao = ExecucaoCampanha(
                id=uuid(),
                campanha_id=campanha.id,
                preparacao_id=prep.id,
                autorizacao_id=autorizacao.id,
                autorizacao_telegram_id=None,
                modo="mock",
                estado="executando",
                versao=1,
                proprietario_id=uuid(),
                geracao=1,
                criado_por_usuario_id=usuario,
                alterado_por_usuario_id=usuario,
                criado_em=stamp,
                atualizado_em=stamp,
            )
            session.add(execucao)
            session.flush()  # O trigger materializa a fila e o diario antes da auditoria.
            identidades = list(impedidos)
            for offset in range(0, len(identidades), 250):
                lote = identidades[offset : offset + 250]
                changed = session.execute(
                    update(ItemExecucao)
                    .where(
                        ItemExecucao.execucao_id == execucao.id,
                        ItemExecucao.identidade_contato_id.in_(lote),
                        ItemExecucao.estado == "pendente",
                        ItemExecucao.versao == 1,
                    )
                    .values(
                        estado="ignorado",
                        motivo_codigo="impedimento_atual",
                        versao=2,
                        atualizado_em=stamp,
                        alterado_por_usuario_id=usuario,
                    )
                )
                if changed.rowcount != len(lote):
                    raise conflito()
            changed = session.execute(
                update(Campanha)
                .where(
                    Campanha.id == campanha.id,
                    Campanha.versao == data.versao,
                )
                .values(versao=data.versao + 1, atualizado_em=stamp)
            )
            if changed.rowcount != 1:
                raise conflito()
            session.flush()
            resultado = self._recibo(
                session,
                data,
                usuario,
                digest,
                "iniciar_execucao",
                campanha,
                execucao,
                incluidos=atual["incluidos"],
                ignorados_inicio=len(impedidos),
                elegiveis_inicio=atual["elegiveis_atuais"],
                motivos_destinatarios=atual["motivos_destinatarios"],
            )
            self.a._usuario(session, token)
            return resultado

    def _iniciar_telegram(self, campanha_id, preparacao_id, data, token):
        with self.repo.transacao() as session:
            usuario, sessao_id = self._contexto(session, token)
            _, digest, anterior = self.c._repeticao(
                session, data, token, "iniciar_execucao:" + preparacao_id, campanha_id
            )
            if anterior is not None:
                self.a._usuario(session, token)
                return anterior
            contexto = self.telegram.avaliar_lote(session, campanha_id, preparacao_id)
            if (
                data.versao != contexto["campanha"].versao
                or data.impressao != contexto["preparacao"].impressao
                or data.resumo_configuracao != contexto["configuracao_hash"]
                or not hmac.compare_digest(
                    data.marcador_inicio,
                    self._marcador_telegram(session, contexto, usuario, sessao_id),
                )
            ):
                raise conflito()
            if contexto["impedimentos"]:
                raise NegocioError(
                    "inicio_indisponivel",
                    "Revise os impedimentos atuais do lote Telegram.",
                    409,
                )
            autorizacao = self.telegram.autorizar_lote(
                session, contexto, data, usuario, digest
            )
            stamp = max(agora(), autorizacao.autorizado_em)
            execucao = ExecucaoCampanha(
                id=uuid(),
                campanha_id=campanha_id,
                preparacao_id=preparacao_id,
                autorizacao_id=None,
                autorizacao_telegram_id=autorizacao.id,
                modo="telegram",
                estado="executando",
                versao=1,
                proprietario_id=uuid(),
                geracao=1,
                criado_por_usuario_id=usuario,
                alterado_por_usuario_id=usuario,
                criado_em=stamp,
                atualizado_em=stamp,
            )
            session.add(execucao)
            session.flush()
            campanha = contexto["campanha"]
            changed = session.execute(
                update(Campanha)
                .where(Campanha.id == campanha.id, Campanha.versao == data.versao)
                .values(versao=data.versao + 1, atualizado_em=stamp)
            )
            if changed.rowcount != 1:
                raise conflito()
            resultado = self._recibo(
                session,
                data,
                usuario,
                digest,
                "iniciar_execucao_telegram",
                campanha,
                execucao,
                incluidos=contexto["quantidade"],
                ignorados_inicio=0,
                elegiveis_inicio=contexto["quantidade"],
                motivos_destinatarios=contexto["motivos_destinatarios"],
            )
            self.a._usuario(session, token)
            return resultado

    def _obter(self, session, campanha_id, execucao_id):
        execucao = self.repo.obter(session, ExecucaoCampanha, execucao_id)
        if execucao.campanha_id != campanha_id:
            raise NegocioError("nao_encontrado", "Execucao indisponivel.", 404)
        return execucao

    def pausar(self, campanha_id, execucao_id, data, token):
        data = PausarExecucaoEntrada.model_validate(data)
        with self.repo.transacao() as session:
            usuario, _ = self._contexto(session, token)
            _, digest, anterior = self.c._repeticao(
                session, data, token, "pausar_execucao:" + execucao_id, campanha_id
            )
            if anterior is not None:
                self.a._usuario(session, token)
                return anterior
            campanha = self.repo.obter(session, Campanha, campanha_id)
            execucao = self._obter(session, campanha_id, execucao_id)
            if execucao.versao != data.versao or execucao.estado not in ABERTOS:
                raise conflito()
            ativas = (
                session.scalar(
                    select(TentativaEnvio.id)
                    .where(
                        TentativaEnvio.execucao_id == execucao.id,
                        TentativaEnvio.estado.in_(("reservada", "iniciada")),
                    )
                    .limit(1)
                )
                is not None
            )
            destino = "pausando" if ativas else "pausada"
            if execucao.estado != destino:
                changed = session.execute(
                    update(ExecucaoCampanha)
                    .where(
                        ExecucaoCampanha.id == execucao.id,
                        ExecucaoCampanha.versao == data.versao,
                    )
                    .values(
                        estado=destino,
                        versao=data.versao + 1,
                        atualizado_em=_instante(session, execucao),
                        alterado_por_usuario_id=usuario,
                    )
                )
                if changed.rowcount != 1:
                    raise conflito()
            session.flush()
            resultado = self._recibo(
                session, data, usuario, digest, "pausar_execucao", campanha, execucao
            )
            self.a._usuario(session, token)
            return resultado

    def consultar(self, campanha_id, token, *, execucao_id=None, preparacao_id=None):
        if (execucao_id is None) == (preparacao_id is None):
            raise NegocioError("validacao", "Informe somente um identificador de consulta.", 422)
        with self.repo.transacao() as session:
            self._contexto(session, token)
            self.repo.obter(session, Campanha, campanha_id)
            if execucao_id is not None:
                execucao = self._obter(session, campanha_id, execucao_id)
            else:
                self.p._obter(session, campanha_id, preparacao_id)
                execucao = session.scalar(
                    select(ExecucaoCampanha).where(
                        ExecucaoCampanha.campanha_id == campanha_id,
                        ExecucaoCampanha.preparacao_id == preparacao_id,
                    )
                )
            resultado = {
                "id": campanha_id,
                "execucao": None,
                "processamento_disponivel": False,
                "validade_atual": "nao_avaliada",
            }
            if execucao is not None:
                resultado["execucao"] = {
                    key: getattr(execucao, key)
                    for key in (
                        "id",
                        "campanha_id",
                        "preparacao_id",
                        "autorizacao_id",
                        "autorizacao_telegram_id",
                        "modo",
                        "estado",
                        "versao",
                        "geracao",
                        "criado_em",
                        "atualizado_em",
                    )
                }
                contagens = dict(
                    session.execute(
                        select(ItemExecucao.estado, func.count())
                        .where(ItemExecucao.execucao_id == execucao.id)
                        .group_by(ItemExecucao.estado)
                    ).all()
                )
                resultado["contagens"] = {
                    estado: contagens.get(estado, 0) for estado in ESTADOS_ITENS
                }
            self.a._usuario(session, token)
            return resultado
