"""Regras transacionais; nenhuma integração externa ou capacidade de envio."""

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, or_, select, update

from app.consentimentos.models import (
    CATEGORIAS,
    EscopoComunicacao,
    EventoConsentimento,
    EventoTelegram,
    IdentidadeContato,
    IdentidadeTelegram,
    InscricaoConta,
    PreferenciaContato,
    ReciboTelegram,
)
from app.consentimentos.schemas import (
    EventoEntrada,
    IdentidadeEntrada,
    IdentidadeTelegramEntrada,
    TelegramAceiteEntrada,
    TelegramEventoEntrada,
)
from app.contas_envio.models import ContaEnvio
from app.contatos.errors import NegocioError, ausente, conflito
from app.contatos.models import Auditoria, Contato, Lista, ListaContato, TelefoneContato
from app.contatos.repository import agora, uuid

AVISO_TELEGRAM_CATEGORIA = "promocoes"
AVISO_TELEGRAM_VERSAO = "2"
AVISO_TELEGRAM_TEXTO = (
    "Consentimento promocional, aviso v2. Categoria disponível: promocoes. "
    "Autorize neste bot com /aceitar promocoes. /start e /reativar não autorizam "
    "campanhas. Use /stop para revogar."
)


def escopo_view(item):
    return {"id": item.id, "nome": item.nome, "criado_em": item.criado_em}


def identidade_view(item):
    return {
        "id": item.id,
        "contato_id": item.contato_id,
        "telefone_id": item.telefone_id,
        "canal": item.canal,
        "bloqueio_canal_em": item.bloqueio_canal_em,
        "existencia_canal": "nao_verificada",
    }


def evento_view(item):
    fields = (
        "id",
        "escopo_id",
        "contato_id",
        "identidade_contato_id",
        "categoria",
        "tipo",
        "origem",
        "ocorrido_em",
        "registrado_em",
        "metodo_captura",
        "evidencia_referencia",
        "justificativa",
        "usuario_id",
        "versao_resultante",
    )
    return {k: getattr(item, k) for k in fields}


class ConsentimentosService:
    def __init__(self, repository, auth):
        self.repo = repository
        self.auth = auth

    def criar_identidade_telegram(self, contato_id, data, token, *, session=None):
        data = IdentidadeTelegramEntrada.model_validate(data.model_dump())
        identifier = str(data.operacao_id)
        digest = hashlib.sha256(
            json.dumps([contato_id, data.model_dump(mode="json")], sort_keys=True).encode()
        ).hexdigest()
        with self.repo.transacao() if session is None else nullcontext(session) as s:
            usuario = self.auth.current(token, session=s).usuario.id
            old = s.get(IdentidadeTelegram, identifier)
            if old:
                audit = s.scalar(
                    select(Auditoria).where(
                        Auditoria.entidade == "identidade_telegram",
                        Auditoria.entidade_id == identifier,
                        Auditoria.acao == "criar",
                    )
                )
                if audit is None or json.loads(audit.resumo_json).get("requisicao_hash") != digest:
                    raise conflito()
                return {"id": old.id, "contato_id": old.contato_id}
            contact = self.repo.obter(s, Contato, contato_id)
            if not contact.ativo:
                raise NegocioError("arquivado", "O cadastro está arquivado.", 409)
            old = s.scalar(
                select(IdentidadeTelegram).where(
                    IdentidadeTelegram.usuario_externo_id == data.usuario_externo_id
                )
            )
            if old:
                # Nunca unir pessoas ou transferir uma identidade implicitamente.
                raise conflito()
            item = IdentidadeTelegram(
                id=identifier,
                contato_id=contato_id,
                usuario_externo_id=data.usuario_externo_id,
                chat_externo_id=data.chat_externo_id,
                criado_por_usuario_id=usuario,
                criado_em=agora(),
            )
            s.add(item)
            self._avancar(s, contact, contact.consentimento_versao)
            self.repo.auditar(
                s, usuario, "criar", "identidade_telegram", identifier, requisicao_hash=digest
            )
            s.flush()
            return {"id": item.id, "contato_id": item.contato_id}

    def inscrever_telegram(self, conta_id, identidade_id, data, token, *, session=None):
        """Persiste interação privada; não autoriza nenhuma finalidade."""
        return self._registrar_telegram(
            conta_id, identidade_id, data, token, "iniciou", session=session
        )

    def aceitar_telegram(self, conta_id, identidade_id, data, token):
        return self._registrar_telegram(conta_id, identidade_id, data, token, "autorizou")

    def reativar_telegram(self, conta_id, identidade_id, data, token, *, session=None):
        """Registra novo aviso após /stop, sem remover o bloqueio."""
        data = TelegramEventoEntrada.model_validate(data.model_dump())
        marker = (
            f":reativacao:aviso:{AVISO_TELEGRAM_CATEGORIA}:{AVISO_TELEGRAM_VERSAO}"
        )
        data = data.model_copy(update={"evento_externo_id": data.evento_externo_id + marker})
        return self._registrar_telegram(
            conta_id,
            identidade_id,
            data,
            token,
            "iniciou",
            session=session,
            reativacao=True,
        )

    def revogar_telegram(self, conta_id, identidade_id, data, token, *, session=None):
        """Revogação integral naquela conta; nova interação não reativa a inscrição."""
        return self._registrar_telegram(
            conta_id, identidade_id, data, token, "revogou", session=session
        )

    def _registrar_telegram(
        self,
        conta_id,
        identidade_id,
        data,
        token,
        tipo,
        *,
        session=None,
        reativacao=False,
    ):
        schema = TelegramAceiteEntrada if tipo == "autorizou" else TelegramEventoEntrada
        data = schema.model_validate(data.model_dump())
        payload = data.model_dump(mode="json", exclude={"operacao_id"})
        payload["ocorrido_em"] = data.ocorrido_em.astimezone(UTC).isoformat()
        digest = hashlib.sha256(
            json.dumps([conta_id, identidade_id, tipo, payload], sort_keys=True).encode()
        ).hexdigest()
        with self.repo.transacao() if session is None else nullcontext(session) as s:
            usuario = self.auth.current(token, session=s).usuario.id
            old = list(
                s.scalars(
                    select(EventoTelegram).where(
                        or_(
                            EventoTelegram.id == str(data.operacao_id),
                            (EventoTelegram.conta_envio_id == conta_id)
                            & (EventoTelegram.evento_externo_id == data.evento_externo_id),
                        )
                    )
                )
            )
            if old:
                if len(old) != 1 or old[0].requisicao_hash != digest:
                    raise conflito()
                return self._evento_telegram_view(old[0])
            account = self.repo.obter(s, ContaEnvio, conta_id)
            if account.provedor != "telegram_bot_api":
                raise NegocioError("conta_invalida", "Selecione uma conta Telegram.", 409)
            # Identidade do bot deve vir de getMe validado pelo futuro receptor.
            if account.telegram_bot_id not in (None, data.bot_externo_id):
                raise conflito()
            if account.telegram_bot_id is None:
                account.telegram_bot_id = data.bot_externo_id
                s.flush()
            identity = self.repo.obter(s, IdentidadeTelegram, identidade_id)
            contact = self.repo.obter(s, Contato, identity.contato_id)
            self.repo.obter(s, EscopoComunicacao, str(data.escopo_id))
            now = agora()
            if data.ocorrido_em > now:
                raise NegocioError("validacao", "A data não pode estar no futuro.")
            if tipo != "revogou" and (
                not contact.ativo or not account.ativo or account.removido_em
            ):
                raise NegocioError("arquivado", "Conta ou contato está arquivado.", 409)
            subscription = s.scalar(
                select(InscricaoConta).where(
                    InscricaoConta.conta_envio_id == conta_id,
                    InscricaoConta.identidade_telegram_id == identidade_id,
                )
            )
            if subscription is None:
                if reativacao:
                    raise NegocioError("sem_inscricao", "Inicie primeiro a interação.", 409)
                if tipo == "autorizou":
                    raise NegocioError("sem_inscricao", "Registre primeiro a interação.", 409)
                subscription = InscricaoConta(
                    id=uuid(),
                    conta_envio_id=conta_id,
                    identidade_telegram_id=identidade_id,
                    estado="iniciada",
                    registrado_em=now,
                    atualizado_em=now,
                )
                s.add(subscription)
                s.flush()
            if reativacao:
                revogacao = s.scalar(
                    select(EventoTelegram)
                    .where(
                        EventoTelegram.inscricao_id == subscription.id,
                        EventoTelegram.tipo == "revogou",
                    )
                    .order_by(EventoTelegram.registrado_em.desc(), EventoTelegram.id.desc())
                )
                if (
                    subscription.estado != "revogada"
                    or revogacao is None
                    or data.ocorrido_em < revogacao.ocorrido_em
                ):
                    raise NegocioError("bloqueado", "A inscrição não admite reativação.", 409)
            if tipo == "autorizou":
                barrier = self._barreira_global_telegram(s, contact.id, str(data.escopo_id))
                reativada = subscription.estado == "revogada" and self._reativacao_apresentada(
                    s, subscription.id, str(data.escopo_id), data, barrier
                )
                if (
                    subscription.estado not in {"iniciada", "revogada"}
                    or (subscription.estado == "revogada" and not reativada)
                    or contact.bloqueio_global_em
                ):
                    raise NegocioError("bloqueado", "A inscrição está bloqueada.", 409)
                interaction = s.scalar(
                    select(func.min(EventoTelegram.ocorrido_em)).where(
                        EventoTelegram.inscricao_id == subscription.id,
                        EventoTelegram.tipo == "iniciou",
                    )
                )
                if (
                    interaction is None
                    or data.ocorrido_em < interaction
                    or (barrier is not None and data.ocorrido_em <= barrier)
                ):
                    raise NegocioError("evidencia_anterior", "Registre uma nova confirmação.", 409)
                notice = s.scalar(
                    select(EventoTelegram.aviso_texto)
                    .where(
                        EventoTelegram.conta_envio_id == conta_id,
                        EventoTelegram.categoria == data.categoria,
                        EventoTelegram.aviso_versao == data.aviso_versao,
                    )
                    .limit(1)
                )
                if notice is not None and notice != data.aviso_texto:
                    raise conflito()
            if tipo == "revogou":
                subscription.estado = "revogada"
            subscription.atualizado_em = now
            item = EventoTelegram(
                id=str(data.operacao_id),
                inscricao_id=subscription.id,
                conta_envio_id=conta_id,
                escopo_id=str(data.escopo_id),
                evento_externo_id=data.evento_externo_id,
                tipo=tipo,
                categoria=getattr(data, "categoria", None),
                aviso_versao=getattr(data, "aviso_versao", None),
                aviso_texto=getattr(data, "aviso_texto", None),
                confirmado=int(tipo == "autorizou"),
                ocorrido_em=data.ocorrido_em,
                registrado_em=now,
                usuario_id=usuario,
                requisicao_hash=digest,
            )
            s.add(item)
            self._avancar(s, contact, contact.consentimento_versao)
            self.repo.auditar(s, usuario, tipo, "evento_telegram", item.id)
            s.flush()
            return self._evento_telegram_view(item)

    def pode_apresentar_reativacao(self, contexto, bot, update, token, ativo):
        """Confirma identidade e bot antes de expor o aviso de reativação."""
        conta_id, referencia, versao = contexto
        with self.repo.transacao(espera=0.25) as s:
            self.auth.current(token, session=s)
            account = self.repo.obter(s, ContaEnvio, conta_id)
            if (
                not ativo()
                or not account.ativo
                or account.removido_em
                or account.segredo_ref != referencia
                or account.versao != versao
                or account.provedor != "telegram_bot_api"
                or account.telegram_bot_id != str(bot["id"])
            ):
                return False
            identity = s.scalar(
                select(IdentidadeTelegram).where(
                    IdentidadeTelegram.usuario_externo_id == update["usuario_externo_id"],
                    IdentidadeTelegram.chat_externo_id == update["chat_externo_id"],
                )
            )
            if identity is None:
                return False
            subscription = s.scalar(
                select(InscricaoConta).where(
                    InscricaoConta.conta_envio_id == conta_id,
                    InscricaoConta.identidade_telegram_id == identity.id,
                )
            )
            return subscription is not None and subscription.estado == "revogada"

    def receber_telegram(
        self, contexto, bot, update, token, ativo, sincronizacao_em=None
    ):
        """Receptor interno: o chamador verifica bot e normaliza o evento no adaptador."""
        conta_id, referencia, versao = contexto
        identifier = update["update_id"]
        if type(identifier) is not int or not 0 <= identifier < 2**63 - 1:
            raise NegocioError("telegram_resposta", "Atualização inválida.")
        resultado = update["resultado"]
        if resultado not in {"iniciou", "autorizou", "revogou", "ignorada"}:
            raise NegocioError("telegram_resposta", "Atualização inválida.")
        reativacao = update.get("reativacao", False)
        if type(reativacao) is not bool or (reativacao and resultado != "iniciou"):
            raise NegocioError("telegram_resposta", "Atualização inválida.")
        resposta = None
        with self.repo.transacao(espera=0.25) as s:
            usuario = self.auth.current(token, session=s).usuario.id
            account = self.repo.obter(s, ContaEnvio, conta_id)
            if (
                not ativo()
                or not account.ativo
                or account.removido_em
                or account.segredo_ref != referencia
                or account.versao != versao
                or account.provedor != "telegram_bot_api"
                or account.telegram_bot_id != str(bot["id"])
            ):
                raise conflito()
            old = s.scalar(
                select(ReciboTelegram).where(
                    ReciboTelegram.conta_envio_id == conta_id,
                    ReciboTelegram.update_id == identifier,
                )
            )
            if old:
                return None
            last = s.scalar(
                select(func.max(ReciboTelegram.registrado_em)).where(
                    ReciboTelegram.conta_envio_id == conta_id,
                )
            )
            anterior = account.telegram_sincronizado_em or last
            if sincronizacao_em is not None and (
                anterior is None or anterior <= sincronizacao_em - timedelta(hours=24)
            ):
                account.telegram_lacuna_desde = (
                    account.telegram_lacuna_desde or sincronizacao_em
                )
            if (
                account.telegram_proximo_offset is not None
                and identifier < account.telegram_proximo_offset
                and (last is None or last > agora() - timedelta(days=7))
            ):
                raise NegocioError("telegram_ordem", "Atualizações fora de ordem.", 409)
            if resultado != "ignorada":
                scope = s.scalar(select(EscopoComunicacao))
                if scope is None:
                    raise NegocioError("telegram_escopo_pendente", "Configure o escopo local.", 409)
                identity = s.scalar(
                    select(IdentidadeTelegram).where(
                        IdentidadeTelegram.usuario_externo_id == update["usuario_externo_id"],
                    )
                )
                if reativacao and (
                    identity is None
                    or identity.chat_externo_id != update["chat_externo_id"]
                    or update.get("aviso_apresentado") is not True
                ):
                    resultado = "ignorada"
                if identity is None:
                    if resultado != "ignorada":
                        contact = Contato(id=uuid(), criado_em=agora(), atualizado_em=agora())
                        s.add(contact)
                        s.flush()
                        self.repo.auditar(s, usuario, "criar", "contato", contact.id)
                        created = self.criar_identidade_telegram(
                            contact.id,
                            IdentidadeTelegramEntrada(
                                operacao_id=uuid5(
                                    NAMESPACE_URL,
                                    "edisk:telegram:" + update["usuario_externo_id"],
                                ),
                                usuario_externo_id=update["usuario_externo_id"],
                                chat_externo_id=update["chat_externo_id"],
                            ),
                            token,
                            session=s,
                        )
                        identity = s.get(IdentidadeTelegram, created["id"])
                if identity is not None:
                    contact = self.repo.obter(s, Contato, identity.contato_id)
                    if resultado == "iniciou" and not contact.ativo:
                        resultado = "ignorada"
                if resultado != "ignorada":
                    referencia = f"telegram-update:{identifier}"
                    if resultado == "iniciou" and update.get("aviso_apresentado") is True:
                        referencia += f":aviso:{AVISO_TELEGRAM_CATEGORIA}:{AVISO_TELEGRAM_VERSAO}"
                    campos = {
                        "operacao_id": uuid5(
                            NAMESPACE_URL, f"edisk:telegram:{conta_id}:{identifier}"
                        ),
                        "escopo_id": UUID(scope.id),
                        "bot_externo_id": str(bot["id"]),
                        "evento_externo_id": referencia,
                        "ocorrido_em": update["ocorrido_em"],
                    }
                    if resultado == "autorizou":
                        data = TelegramAceiteEntrada(
                            **campos,
                            categoria=AVISO_TELEGRAM_CATEGORIA,
                            aviso_versao=AVISO_TELEGRAM_VERSAO,
                            aviso_texto=AVISO_TELEGRAM_TEXTO,
                            confirmado=True,
                        )
                        subscription = s.scalar(
                            select(InscricaoConta).where(
                                InscricaoConta.conta_envio_id == conta_id,
                                InscricaoConta.identidade_telegram_id == identity.id,
                            )
                        )
                        barrier = self._barreira_global_telegram(s, contact.id, scope.id)
                        apresentada = False
                        if subscription is not None:
                            if subscription.estado == "revogada":
                                apresentada = self._reativacao_apresentada(
                                    s, subscription.id, scope.id, data, barrier
                                )
                            else:
                                apresentada = s.scalar(
                                    select(EventoTelegram.id)
                                    .where(
                                        EventoTelegram.inscricao_id == subscription.id,
                                        EventoTelegram.tipo == "iniciou",
                                        EventoTelegram.evento_externo_id.like(
                                            f"%:aviso:{AVISO_TELEGRAM_CATEGORIA}:"
                                            f"{AVISO_TELEGRAM_VERSAO}"
                                        ),
                                        EventoTelegram.ocorrido_em <= update["ocorrido_em"],
                                        (
                                            EventoTelegram.registrado_em > barrier
                                            if barrier is not None
                                            else True
                                        ),
                                    )
                                    .limit(1)
                                )
                        if (
                            not apresentada
                            or subscription.estado not in {"iniciada", "revogada"}
                            or not contact.ativo
                            or contact.bloqueio_global_em
                        ):
                            resultado = "ignorada"
                            resposta = (
                                "Consentimento não registrado. Envie /start ou /reativar para "
                                "receber o aviso atual. Bloqueios e revogações permanecem ativos."
                            )
                        else:
                            self._registrar_telegram(
                                conta_id, identity.id, data, token, resultado, session=s
                            )
                            resposta = "Consentimento registrado somente para promoções neste bot."
                    elif reativacao:
                        data = TelegramEventoEntrada(**campos)
                        self.reativar_telegram(
                            conta_id, identity.id, data, token, session=s
                        )
                    else:
                        data = TelegramEventoEntrada(**campos)
                        self._registrar_telegram(
                            conta_id, identity.id, data, token, resultado, session=s
                        )
            s.add(
                ReciboTelegram(
                    id=uuid(),
                    conta_envio_id=conta_id,
                    update_id=identifier,
                    resultado=resultado,
                    registrado_em=agora(),
                )
            )
            account.telegram_proximo_offset = identifier + 1
            self.auth.current(token, session=s)
            if not ativo():
                raise conflito()
            s.flush()
            return resposta

    @staticmethod
    def _evento_telegram_view(item):
        return {
            key: getattr(item, key)
            for key in (
                "id",
                "inscricao_id",
                "conta_envio_id",
                "escopo_id",
                "tipo",
                "categoria",
                "aviso_versao",
                "ocorrido_em",
                "registrado_em",
            )
        }

    def _avaliar_telegram(self, s, account, identity, contact, subscription, escopo_id, categoria):
        barrier = self._barreira_global_telegram(s, contact.id, escopo_id)
        event = None
        if subscription is not None:
            revocation = self._barreira_revogacao_telegram(s, subscription.id)
            query = select(EventoTelegram).where(
                EventoTelegram.inscricao_id == subscription.id,
                EventoTelegram.escopo_id == escopo_id,
                EventoTelegram.categoria == categoria,
                EventoTelegram.tipo == "autorizou",
            )
            if barrier is not None:
                query = query.where(EventoTelegram.registrado_em > barrier)
            if revocation is not None:
                query = query.where(EventoTelegram.registrado_em > revocation)
            event = s.scalar(query.order_by(EventoTelegram.ocorrido_em.desc(), EventoTelegram.id))
        blocked = bool(
            contact.bloqueio_global_em or (subscription and subscription.estado == "revogada")
        )
        reasons = []
        if subscription is None:
            reasons.append("inscricao_telegram_ausente")
        elif subscription.estado != "iniciada":
            reasons.append("inscricao_telegram_revogada")
        if event is None:
            reasons.append("consentimento_telegram_ausente")
        if (
            account.telegram_lacuna_desde is not None
            and (event is None or event.registrado_em <= account.telegram_lacuna_desde)
        ):
            reasons.append("sincronizacao_telegram_lacuna")
        if contact.bloqueio_global_em:
            reasons.append("bloqueio_global")
        if not contact.ativo:
            reasons.append("contato_inativo")
        if not account.ativo or account.removido_em:
            reasons.append("conta_inativa")
        if account.plataforma != "telegram" or account.provedor != "telegram_bot_api":
            reasons.append("conta_telegram_incompativel")
        if not account.telegram_bot_id:
            reasons.append("bot_telegram_nao_verificado")
        return {
            "estado": "bloqueado" if blocked else "autorizado" if event else "desconhecido",
            "elegivel_consentimento": not reasons,
            "avaliacao": "somente_consentimento_e_supressao",
            "capacidade_envio": "nao_avaliada",
            "evento_telegram_id": event.id if event else None,
            "motivos": reasons,
        }

    def elegibilidade_telegram(self, conta_id, identidade_id, escopo_id, categoria, token):
        if categoria not in CATEGORIAS:
            raise NegocioError("validacao", "Finalidade inválida.")
        with self.repo.transacao() as s:
            self.auth.current(token, session=s)
            self.repo.obter(s, EscopoComunicacao, escopo_id)
            account = self.repo.obter(s, ContaEnvio, conta_id)
            identity = self.repo.obter(s, IdentidadeTelegram, identidade_id)
            contact = self.repo.obter(s, Contato, identity.contato_id)
            subscription = s.scalar(
                select(InscricaoConta).where(
                    InscricaoConta.conta_envio_id == conta_id,
                    InscricaoConta.identidade_telegram_id == identidade_id,
                )
            )
            result = self._avaliar_telegram(
                s, account, identity, contact, subscription, escopo_id, categoria
            )
            return {
                key: result[key]
                for key in (
                    "estado",
                    "elegivel_consentimento",
                    "avaliacao",
                    "capacidade_envio",
                )
            }

    @staticmethod
    def _barreira_global_telegram(s, contato_id, escopo_id):
        return s.scalar(
            select(func.max(EventoConsentimento.registrado_em)).where(
                EventoConsentimento.contato_id == contato_id,
                EventoConsentimento.escopo_id == escopo_id,
                EventoConsentimento.tipo.in_(("bloqueio_global", "desbloqueio_global")),
            )
        )

    @staticmethod
    def _barreira_revogacao_telegram(s, inscricao_id):
        return s.scalar(
            select(func.max(EventoTelegram.registrado_em)).where(
                EventoTelegram.inscricao_id == inscricao_id,
                EventoTelegram.tipo == "revogou",
            )
        )

    def _reativacao_apresentada(self, s, inscricao_id, escopo_id, data, barrier=None):
        if (
            data.aviso_versao != AVISO_TELEGRAM_VERSAO
            or data.aviso_texto != AVISO_TELEGRAM_TEXTO
        ):
            return False
        revogacao = self._barreira_revogacao_telegram(s, inscricao_id)
        if revogacao is None:
            return False
        marker = f":reativacao:aviso:{data.categoria}:{data.aviso_versao}"
        return bool(
            s.scalar(
                select(EventoTelegram.id)
                .where(
                    EventoTelegram.inscricao_id == inscricao_id,
                    EventoTelegram.escopo_id == escopo_id,
                    EventoTelegram.tipo == "iniciou",
                    EventoTelegram.evento_externo_id.like("%" + marker),
                    EventoTelegram.registrado_em > revogacao,
                    EventoTelegram.ocorrido_em <= data.ocorrido_em,
                    (
                        EventoTelegram.registrado_em > barrier
                        if barrier is not None
                        else True
                    ),
                )
                .limit(1)
            )
        )

    def escopos(self):
        with self.repo.transacao() as s:
            return {"itens": [escopo_view(i) for i in s.scalars(select(EscopoComunicacao))]}

    def criar_escopo(self, data, token):
        identifier = str(data.operacao_id)
        with self.repo.transacao() as s:
            usuario = self.auth.current(token, session=s).usuario.id
            old = s.scalar(select(EscopoComunicacao))
            if old:
                if (old.id, old.nome, old.criado_por_usuario_id) != (
                    identifier,
                    data.nome,
                    usuario,
                ):
                    raise conflito()
                return escopo_view(old)
            item = EscopoComunicacao(
                id=identifier,
                nome=data.nome,
                criado_por_usuario_id=usuario,
                criado_em=agora(),
            )
            s.add(item)
            self.repo.auditar(s, usuario, "criar", "escopo_comunicacao", identifier)
            s.flush()
            return escopo_view(item)

    def _avancar(self, s, contact, version):
        result = s.execute(
            update(Contato)
            .where(
                Contato.id == contact.id,
                Contato.consentimento_versao == version,
            )
            .values(consentimento_versao=version + 1)
        )
        if result.rowcount != 1:
            raise conflito()

    def criar_identidade(self, contato_id, data, token):
        identifier = str(data.operacao_id)
        phone_id = str(data.telefone_id)
        with self.repo.transacao() as s:
            usuario = self.auth.current(token, session=s).usuario.id
            digest = hashlib.sha256(
                json.dumps(
                    [contato_id, usuario, data.model_dump(mode="json")],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            old = s.get(IdentidadeContato, identifier)
            if old:
                audit = s.scalar(
                    select(Auditoria).where(
                        Auditoria.entidade == "identidade_contato",
                        Auditoria.entidade_id == identifier,
                        Auditoria.acao == "criar",
                    )
                )
                if audit is None or json.loads(audit.resumo_json).get("requisicao_hash") != digest:
                    raise conflito()
                # A resposta original de criação nunca continha bloqueio de canal.
                return {**identidade_view(old), "bloqueio_canal_em": None}
            contact = self.repo.obter(s, Contato, contato_id)
            phone = self.repo.obter(s, TelefoneContato, phone_id)
            if phone.contato_id != contato_id:
                raise ausente()
            if not contact.ativo or not phone.ativo:
                raise NegocioError("arquivado", "O cadastro está arquivado.", 409)
            self._avancar(s, contact, data.versao)
            item = IdentidadeContato(
                id=identifier,
                contato_id=contato_id,
                telefone_id=phone_id,
                canal=data.canal,
                criado_por_usuario_id=usuario,
                criado_em=agora(),
            )
            s.add(item)
            self.repo.auditar(
                s,
                usuario,
                "criar",
                "identidade_contato",
                identifier,
                requisicao_hash=digest,
            )
            s.flush()
            return identidade_view(item)

    def autorizar_lista(self, lista_id, data, token):
        """Registra autorização externa nas identidades ativas da lista."""
        scope_id = str(data.escopo_id)
        with self.repo.lock, self.repo.transacao() as s:
            usuario = self.auth.current(token, session=s).usuario.id
            self.repo.obter(s, Lista, lista_id)
            self.repo.obter(s, EscopoComunicacao, scope_id)
            now = agora()
            if data.ocorrido_em > now:
                raise NegocioError("validacao", "A data não pode estar no futuro.")
            rows = s.execute(
                select(Contato, TelefoneContato, IdentidadeContato)
                .join(ListaContato, ListaContato.contato_id == Contato.id)
                .join(TelefoneContato, TelefoneContato.contato_id == Contato.id)
                .outerjoin(
                    IdentidadeContato,
                    (IdentidadeContato.telefone_id == TelefoneContato.id)
                    & (IdentidadeContato.canal == "whatsapp"),
                )
                .where(
                    ListaContato.lista_id == lista_id,
                    Contato.ativo == 1,
                    TelefoneContato.ativo == 1,
                )
                .order_by(Contato.id, TelefoneContato.id)
            ).all()
            resultado = {"autorizados": 0, "ja_autorizados": 0, "bloqueados": 0}
            guard = getattr(self.repo.database, "suspensoes_consentimento", None)
            for contact, phone, identity in rows:
                pref = (
                    s.get(PreferenciaContato, (scope_id, identity.id, data.categoria))
                    if identity
                    else None
                )
                barreiras = [
                    EventoConsentimento.tipo.in_(("bloqueio_global", "desbloqueio_global"))
                ]
                if identity:
                    barreiras.append(
                        (EventoConsentimento.identidade_contato_id == identity.id)
                        & EventoConsentimento.tipo.in_(
                            ("bloqueio_canal", "desbloqueio_canal")
                        )
                    )
                barreira = s.scalar(
                    select(func.max(EventoConsentimento.registrado_em)).where(
                        EventoConsentimento.contato_id == contact.id,
                        EventoConsentimento.escopo_id == scope_id,
                        or_(*barreiras),
                    )
                )
                if (
                    contact.bloqueio_global_em
                    or (identity and identity.bloqueio_canal_em)
                    or (pref and pref.estado == "bloqueado")
                    or (barreira and data.ocorrido_em <= barreira)
                    or (
                        guard is not None
                        and guard.afetado(
                            contact.id,
                            identity.id if identity else None,
                            scope_id,
                            data.categoria,
                        )
                    )
                ):
                    resultado["bloqueados"] += 1
                    continue
                if pref and pref.estado == "autorizado":
                    resultado["ja_autorizados"] += 1
                    continue
                version = contact.consentimento_versao
                if identity is None:
                    identity_data = IdentidadeEntrada(
                        operacao_id=uuid5(data.operacao_id, f"identidade:{contact.id}:{phone.id}"),
                        telefone_id=UUID(phone.id),
                        canal="whatsapp",
                        versao=version,
                    )
                    identity = IdentidadeContato(
                        id=str(identity_data.operacao_id),
                        contato_id=contact.id,
                        telefone_id=phone.id,
                        canal="whatsapp",
                        criado_por_usuario_id=usuario,
                        criado_em=now,
                    )
                    self._avancar(s, contact, version)
                    version += 1
                    contact.consentimento_versao = version
                    s.add(identity)
                    digest = hashlib.sha256(
                        json.dumps(
                            [contact.id, usuario, identity_data.model_dump(mode="json")],
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                    self.repo.auditar(
                        s,
                        usuario,
                        "criar",
                        "identidade_contato",
                        identity.id,
                        requisicao_hash=digest,
                    )
                    s.flush()
                event = EventoEntrada(
                    operacao_id=uuid5(data.operacao_id, f"autorizacao:{contact.id}:{phone.id}"),
                    escopo_id=data.escopo_id,
                    versao=version,
                    tipo="autorizou",
                    identidade_contato_id=UUID(identity.id),
                    categoria=data.categoria,
                    origem="operador",
                    ocorrido_em=data.ocorrido_em,
                    metodo_captura=data.metodo_captura,
                    evidencia_referencia=data.evidencia_referencia,
                )
                self._avancar(s, contact, version)
                contact.consentimento_versao = version + 1
                item = EventoConsentimento(
                    id=str(event.operacao_id),
                    escopo_id=scope_id,
                    contato_id=contact.id,
                    identidade_contato_id=identity.id,
                    categoria=data.categoria,
                    tipo="autorizou",
                    origem="operador",
                    ocorrido_em=data.ocorrido_em,
                    registrado_em=now,
                    metodo_captura=data.metodo_captura,
                    evidencia_referencia=data.evidencia_referencia,
                    justificativa=None,
                    usuario_id=usuario,
                    requisicao_hash=self._digest(contact.id, event, usuario),
                    versao_resultante=version + 1,
                )
                s.add(item)
                s.flush()
                if pref is None:
                    pref = PreferenciaContato(
                        escopo_id=scope_id,
                        identidade_contato_id=identity.id,
                        categoria=data.categoria,
                    )
                    s.add(pref)
                pref.estado = "autorizado"
                pref.evento_origem_id = item.id
                pref.atualizado_em = now
                self.repo.auditar(
                    s, usuario, "autorizou", "consentimento", item.id, escopo_id=scope_id
                )
                resultado["autorizados"] += 1
            resultado["total"] = len(rows)
            s.flush()
            return resultado

    def identidades(self, contato_id):
        with self.repo.transacao() as s:
            self.repo.obter(s, Contato, contato_id)
            return {
                "itens": [
                    identidade_view(i)
                    for i in s.scalars(
                        select(IdentidadeContato)
                        .where(IdentidadeContato.contato_id == contato_id)
                        .order_by(IdentidadeContato.criado_em, IdentidadeContato.id)
                    )
                ]
            }

    def _identidade(self, s, contato_id, identifier):
        item = self.repo.obter(s, IdentidadeContato, identifier)
        if item.contato_id != contato_id:
            raise ausente()
        return item

    def avaliar_dados(self, contact, identity, phone, preference):
        """Avaliação pura, também usada pelas revisões em lote do núcleo."""
        estado = preference.estado if preference else "desconhecido"
        motivos = []
        if contact.bloqueio_global_em:
            estado = "bloqueado"
            motivos.append("bloqueio_global")
        if identity and identity.bloqueio_canal_em:
            estado = "bloqueado"
            motivos.append("bloqueio_canal")
        if preference and preference.estado == "bloqueado":
            motivos.append("categoria_revogada")
        if not identity:
            motivos.append("identidade_nao_cadastrada")
        if not contact.ativo:
            motivos.append("contato_arquivado")
        if phone and not phone.ativo:
            motivos.append("telefone_arquivado")
        if estado == "desconhecido":
            motivos.append("sem_autorizacao")
        return {
            "estado": estado,
            "elegivel_consentimento": estado == "autorizado" and not motivos,
            "motivos": motivos,
            "avaliacao": "somente_consentimento_e_supressao",
            "existencia_canal": "nao_verificada",
            "capacidade_envio": "nao_avaliada",
        }

    def estado(self, contato_id, escopo_id):
        with self.repo.transacao() as s:
            contact = self.repo.obter(s, Contato, contato_id)
            scope = self.repo.obter(s, EscopoComunicacao, escopo_id)
            pairs = s.execute(
                select(IdentidadeContato, TelefoneContato)
                .join(
                    TelefoneContato,
                    TelefoneContato.id == IdentidadeContato.telefone_id,
                )
                .where(IdentidadeContato.contato_id == contato_id)
                .order_by(IdentidadeContato.criado_em, IdentidadeContato.id)
            ).all()
            prefs = {
                (p.identidade_contato_id, p.categoria): p
                for p in s.scalars(
                    select(PreferenciaContato).where(
                        PreferenciaContato.escopo_id == escopo_id,
                        PreferenciaContato.identidade_contato_id.in_([i.id for i, _ in pairs]),
                    )
                )
            }
            return {
                "escopo": escopo_view(scope),
                "versao": contact.consentimento_versao,
                "bloqueio_global_em": contact.bloqueio_global_em,
                "identidades": [
                    {
                        **identidade_view(i),
                        "telefone_ativo": bool(p.ativo),
                        "preferencias": [
                            {
                                "categoria": cat,
                                **self.avaliar_dados(
                                    contact,
                                    i,
                                    p,
                                    prefs.get((i.id, cat)),
                                ),
                            }
                            for cat in CATEGORIAS
                        ],
                    }
                    for i, p in pairs
                ],
            }

    def elegibilidade(self, contato_id, escopo_id, identidade_id, categoria):
        with self.repo.transacao() as s:
            contact = self.repo.obter(s, Contato, contato_id)
            self.repo.obter(s, EscopoComunicacao, escopo_id)
            identity = self._identidade(s, contato_id, identidade_id)
            phone = self.repo.obter(s, TelefoneContato, identity.telefone_id)
            pref = s.get(PreferenciaContato, (escopo_id, identidade_id, categoria))
            return {**self.avaliar_dados(contact, identity, phone, pref), "avaliado_em": agora()}

    def registrar(self, contato_id, data, token):
        from contextlib import nullcontext

        controle = getattr(self.repo.database, "controle_mock", None)
        bloqueio = (
            controle.proteger_optout(str(data.operacao_id))
            if controle is not None
            and data.tipo in {"revogou", "bloqueio_global", "bloqueio_canal"}
            else nullcontext()
        )
        with bloqueio, self.repo.lock:
            guard = getattr(self.repo.database, "suspensoes_consentimento", None)
            if guard is not None and data.tipo in {"revogou", "bloqueio_global", "bloqueio_canal"}:
                guard.preparar(self, contato_id, data, token)
                try:
                    result = self._registrar(contato_id, data, token)
                    guard.confirmar(result["id"])
                    guard.verificar_resultado(result["id"])
                except Exception:
                    # Depois da intencao confirmada, falha nunca prova rejeicao.
                    raise guard.falhar(str(data.operacao_id)) from None
                return result
            elif guard is not None and guard.afetado(
                contato_id,
                str(data.identidade_contato_id) if data.identidade_contato_id else None,
                str(data.escopo_id),
                data.categoria,
            ):
                raise NegocioError(
                    "descadastramento_pendente",
                    "Confirme o descadastramento pendente antes de alterar a autorizacao.",
                    409,
                )
            return self._registrar(contato_id, data, token)

    @staticmethod
    def _digest(contato_id, data, usuario):
        payload = data.model_dump(mode="json")
        payload["ocorrido_em"] = data.ocorrido_em.astimezone(UTC).isoformat()
        return hashlib.sha256(
            json.dumps(
                [contato_id, usuario, payload], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def _registrar(self, contato_id, data, token):
        identifier, scope_id = str(data.operacao_id), str(data.escopo_id)
        identity_id = str(data.identidade_contato_id) if data.identidade_contato_id else None
        with self.repo.transacao() as s:
            # A autorização é validada nesta leitura, na própria transação SQLite.
            usuario = self.auth.current(token, session=s).usuario.id
            digest = self._digest(contato_id, data, usuario)
            old = s.get(EventoConsentimento, identifier)
            if old:
                if old.requisicao_hash != digest:
                    raise conflito()
                return evento_view(old)
            now = agora()
            if data.ocorrido_em > now:
                raise NegocioError("validacao", "A data não pode estar no futuro.")
            contact = self.repo.obter(s, Contato, contato_id)
            self.repo.obter(s, EscopoComunicacao, scope_id)
            identity = self._identidade(s, contato_id, identity_id) if identity_id else None
            if data.tipo == "autorizou":
                phone = self.repo.obter(s, TelefoneContato, identity.telefone_id)
                if not contact.ativo or not phone.ativo:
                    raise NegocioError("arquivado", "O cadastro está arquivado.", 409)
                if contact.bloqueio_global_em or identity.bloqueio_canal_em:
                    raise NegocioError(
                        "bloqueado",
                        "Remova o bloqueio antes de registrar nova autorização.",
                        409,
                    )
            if data.tipo == "autorizou":
                barrier = s.scalar(
                    select(func.max(EventoConsentimento.registrado_em)).where(
                        EventoConsentimento.contato_id == contato_id,
                        EventoConsentimento.escopo_id == scope_id,
                        or_(
                            EventoConsentimento.tipo.in_(("bloqueio_global", "desbloqueio_global")),
                            (EventoConsentimento.identidade_contato_id == identity_id)
                            & EventoConsentimento.tipo.in_(("bloqueio_canal", "desbloqueio_canal")),
                            (EventoConsentimento.identidade_contato_id == identity_id)
                            & (EventoConsentimento.categoria == data.categoria)
                            & (EventoConsentimento.tipo == "revogou"),
                        ),
                    )
                )
                if barrier and data.ocorrido_em <= barrier:
                    raise NegocioError(
                        "evidencia_anterior",
                        "A nova autorização deve ser posterior à restrição.",
                        409,
                    )
            self._avancar(s, contact, data.versao)
            item = EventoConsentimento(
                id=identifier,
                escopo_id=scope_id,
                contato_id=contato_id,
                identidade_contato_id=identity_id,
                categoria=data.categoria,
                tipo=data.tipo,
                origem=data.origem,
                ocorrido_em=data.ocorrido_em,
                registrado_em=now,
                metodo_captura=data.metodo_captura,
                evidencia_referencia=data.evidencia_referencia,
                justificativa=data.justificativa,
                usuario_id=usuario,
                requisicao_hash=digest,
                versao_resultante=data.versao + 1,
            )
            s.add(item)
            s.flush()
            if data.tipo in {"autorizou", "revogou"}:
                key = (scope_id, identity_id, data.categoria)
                pref = s.get(PreferenciaContato, key)
                if pref is None:
                    pref = PreferenciaContato(
                        escopo_id=scope_id,
                        identidade_contato_id=identity_id,
                        categoria=data.categoria,
                    )
                    s.add(pref)
                pref.estado = "autorizado" if data.tipo == "autorizou" else "bloqueado"
                pref.evento_origem_id, pref.atualizado_em = identifier, now
            else:
                global_ = data.tipo.endswith("_global")
                target = contact if global_ else identity
                field = "bloqueio_global_em" if global_ else "bloqueio_canal_em"
                unblock = data.tipo.startswith("desbloqueio")
                if unblock and getattr(target, field) is None:
                    raise NegocioError("conflito", "O bloqueio já está removido.", 409)
                setattr(target, field, None if unblock else now)
                if unblock:
                    ids = select(IdentidadeContato.id).where(
                        IdentidadeContato.contato_id == contato_id,
                    )
                    if not global_:
                        ids = ids.where(IdentidadeContato.id == identity_id)
                    # Apenas o escopo local existe; a futura expansão deve revisar este contrato.
                    s.execute(
                        update(PreferenciaContato)
                        .where(
                            PreferenciaContato.escopo_id == scope_id,
                            PreferenciaContato.identidade_contato_id.in_(ids),
                            PreferenciaContato.estado == "autorizado",
                        )
                        .values(
                            estado="desconhecido",
                            evento_origem_id=identifier,
                            atualizado_em=now,
                        )
                    )
            self.repo.auditar(
                s,
                usuario,
                data.tipo,
                "consentimento",
                identifier,
                escopo_id=scope_id,
            )
            s.flush()
            return evento_view(item)

    def historico(self, contato_id, escopo_id, pagina=1, limite=20):
        with self.repo.transacao() as s:
            self.repo.obter(s, Contato, contato_id)
            self.repo.obter(s, EscopoComunicacao, escopo_id)
            condition = (
                EventoConsentimento.contato_id == contato_id,
                EventoConsentimento.escopo_id == escopo_id,
            )
            total = s.scalar(
                select(func.count()).select_from(EventoConsentimento).where(*condition)
            )
            events = s.scalars(
                select(EventoConsentimento)
                .where(*condition)
                .order_by(
                    EventoConsentimento.registrado_em.desc(),
                    EventoConsentimento.id.desc(),
                )
                .offset((pagina - 1) * limite)
                .limit(limite)
            )
            return {
                "itens": [evento_view(i) for i in events],
                "total": total,
                "pagina": pagina,
                "limite": limite,
            }
