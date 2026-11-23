"""Uma tentativa Telegram confirmada, sem fila ou repeticao automatica."""

import hashlib
import hmac
import json
import secrets
from threading import Event, Lock

from sqlalchemy import func, select, update

from app.auth.errors import AuthError
from app.campanhas.crypto import GCM, indisponivel
from app.campanhas.models import (
    AutorizacaoEnvioTelegram,
    Campanha,
    PreparacaoItemTelegram,
    TentativaEnvioTelegram,
)
from app.campanhas.preparacoes import aad, abrir, canon
from app.consentimentos.models import IdentidadeTelegram, InscricaoConta
from app.contas_envio.models import ContaEnvio
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Contato
from app.contatos.repository import agora, uuid
from app.persistencia.models import utc_text
from app.provedores.telegram import ResultadoEnvioTelegram, Telegram


class EnviosTelegramService:
    def __init__(self, preparacoes, contas, provedor_factory=Telegram):
        self.p, self.c = preparacoes, preparacoes.c
        self.repo, self.auth = preparacoes.repo, preparacoes.auth
        self.contas, self.provedor_factory = contas, provedor_factory
        self._chave_marcador = secrets.token_bytes(32)
        self._ativo_lock, self._terminou = Lock(), Event()
        self._ativo = None
        self._terminou.set()
        self.recepcao = None

    @staticmethod
    def configuracao_lote(conta_id):
        value = {
            "modo": "envio_real",
            "provedor": "telegram_bot_api",
            "plataforma": "telegram",
            "conta_envio_id": conta_id,
            "sequencial": True,
            "intervalo_minimo_segundos": 5,
        }
        return value, hashlib.sha256(canon(value)).hexdigest()

    def _usuario(self, session, token):
        user = self.auth.current(token, session=session).usuario
        if user.perfil not in {"administrador", "operador"}:
            raise NegocioError("acesso_negado", "Perfil sem permissao para operar campanhas.", 403)
        return user.id

    def _avaliar(self, session, campanha_id, preparacao_id):
        campanha = self.repo.obter(session, Campanha, campanha_id)
        prep = self.p._obter(session, campanha_id, preparacao_id)
        quantidade = session.scalar(
            select(func.count())
            .select_from(PreparacaoItemTelegram)
            .where(PreparacaoItemTelegram.preparacao_id == prep.id)
        )
        if quantidade != 1:
            raise NegocioError(
                "envio_telegram_lote_indisponivel",
                "Envio real Telegram em lote ainda nao esta disponivel.",
                409,
            )
        try:
            ctx = self.p._ctx(session, True)
            with GCM(abrir(ctx)) as root:
                header, key, _ = self.p._autenticar_fotografia(session, prep, ctx, root)
        except NegocioError:
            raise indisponivel() from None
        item = session.scalar(
            select(PreparacaoItemTelegram).where(
                PreparacaoItemTelegram.preparacao_id == prep.id
            )
        )
        if item is None or header.get("provedor") != "telegram_bot_api":
            raise NegocioError("configuracao_incompativel", "Preparacao Telegram invalida.", 409)
        account = session.get(ContaEnvio, item.conta_envio_id)
        identity = session.get(IdentidadeTelegram, item.identidade_telegram_id)
        subscription = session.get(InscricaoConta, item.inscricao_id)
        contact = session.get(Contato, identity.contato_id) if identity else None
        impedimentos = []
        if (
            campanha.status != "preparada"
            or prep.confirmado_em is None
            or prep.invalidado_em is not None
            or prep.incluidos != 1
            or prep.excluidos != 0
        ):
            impedimentos.append("preparacao_invalida")
        if not all((account, identity, subscription, contact)):
            impedimentos.append("vinculo_telegram_indisponivel")
            eligibility = {"motivos": ["vinculo_telegram_indisponivel"], "evento_telegram_id": None}
        else:
            eligibility = self.c.consentimentos._avaliar_telegram(
                session,
                account,
                identity,
                contact,
                subscription,
                campanha.escopo_id,
                campanha.categoria,
            )
            impedimentos.extend(eligibility["motivos"])
        if (
            account
            and (campanha.conta_envio_id != account.id or item.conta_envio_id != account.id)
        ):
            impedimentos.append("conta_telegram_incompativel")
        atuais = self.p._atuais(session, header, [item])
        impedimentos.extend(
            atuais.get(item.identidade_telegram_id, {}).get(
                "impedimentos_atuais", ["identidade_indisponivel"]
            )
        )
        try:
            with GCM(key) as content:
                texto = content.decrypt(
                    item.nonce,
                    item.conteudo_cifrado,
                    aad(header, "texto", item.identidade_telegram_id),
                ).decode("utf-8")
        except (NegocioError, UnicodeError, TypeError):
            raise indisponivel() from None
        if not texto.strip() or len(texto) > 4096:
            impedimentos.append("conteudo_telegram_invalido")
        if impedimentos:
            raise NegocioError(
                "envio_telegram_impedido",
                "As condicoes atuais nao permitem o envio Telegram.",
                409,
            )
        return {
            "campanha": campanha,
            "preparacao": prep,
            "item": item,
            "conta": account,
            "identidade": identity,
            "contato": contact,
            "texto": texto,
            "evento_id": eligibility["evento_telegram_id"],
            "conteudo_hash": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
        }

    def avaliar_lote(self, session, campanha_id, preparacao_id):
        campanha = self.repo.obter(session, Campanha, campanha_id)
        prep = self.p._obter(session, campanha_id, preparacao_id)
        try:
            ctx = self.p._ctx(session, True)
            with GCM(abrir(ctx)) as root:
                header, key, _ = self.p._autenticar_fotografia(session, prep, ctx, root)
            del key
        except NegocioError:
            raise indisponivel() from None
        if header.get("provedor") != "telegram_bot_api":
            raise NegocioError("configuracao_incompativel", "Preparacao Telegram invalida.", 409)
        itens = session.scalars(
            select(PreparacaoItemTelegram)
            .where(
                PreparacaoItemTelegram.preparacao_id == prep.id,
                PreparacaoItemTelegram.incluido == 1,
            )
            .order_by(PreparacaoItemTelegram.identidade_telegram_id)
        ).all()
        conta = session.get(ContaEnvio, campanha.conta_envio_id)
        impedimentos = []
        if (
            campanha.status != "preparada"
            or prep.confirmado_em is None
            or prep.invalidado_em is not None
            or not itens
            or len(itens) != prep.incluidos
        ):
            impedimentos.append("preparacao_invalida")
        if conta is None or conta.provedor != "telegram_bot_api":
            impedimentos.append("conta_telegram_incompativel")
        atuais = self.p._atuais(session, header, itens)
        motivos = {
            item.identidade_telegram_id: atuais.get(
                item.identidade_telegram_id,
                {"impedimentos_atuais": ["identidade_indisponivel"]},
            )["impedimentos_atuais"]
            for item in itens
        }
        if any(motivos.values()):
            impedimentos.append("condicoes_atuais_nao_atendidas")
        configuracao, configuracao_hash = self.configuracao_lote(campanha.conta_envio_id)
        return {
            "campanha": campanha,
            "preparacao": prep,
            "itens": itens,
            "conta": conta,
            "header": header,
            "quantidade": len(itens),
            "conteudo_hash": prep.impressao,
            "configuracao": configuracao,
            "configuracao_hash": configuracao_hash,
            "motivos_destinatarios": motivos,
            "impedimentos": sorted(set(impedimentos)),
        }

    def autorizar_lote(self, session, contexto, data, usuario, requisicao_hash):
        autorizacao = AutorizacaoEnvioTelegram(
            id=uuid(),
            operacao_id=str(data.operacao_id),
            requisicao_hash=requisicao_hash,
            modo="lote",
            versao=1,
            campanha_id=contexto["campanha"].id,
            preparacao_id=contexto["preparacao"].id,
            conta_envio_id=contexto["conta"].id,
            identidade_telegram_id=None,
            inscricao_id=None,
            evento_telegram_id=None,
            bot_externo_id=contexto["conta"].telegram_bot_id,
            chat_externo_id=None,
            conta_versao=contexto["conta"].versao,
            impressao=contexto["preparacao"].impressao,
            conteudo_hash=contexto["conteudo_hash"],
            categoria=contexto["campanha"].categoria,
            quantidade=contexto["quantidade"],
            configuracao_hash=contexto["configuracao_hash"],
            intervalo_segundos=5,
            autorizado_por_usuario_id=usuario,
            autorizado_em=agora(),
        )
        session.add(autorizacao)
        session.flush()
        return autorizacao

    def _marcador(self, session, usuario, contexto):
        payload = {
            "finalidade": "envio-telegram-v1",
            "abertura": self.p.abertura,
            "usuario_id": usuario,
            "campanha_id": contexto["campanha"].id,
            "versao": contexto["campanha"].versao,
            "preparacao_id": contexto["preparacao"].id,
            "impressao": contexto["preparacao"].impressao,
            "conta_id": contexto["conta"].id,
            "conta_versao": contexto["conta"].versao,
            "bot_id": contexto["conta"].telegram_bot_id,
            "chat_id": contexto["identidade"].chat_externo_id,
            "evento_id": contexto["evento_id"],
            "conteudo_hash": contexto["conteudo_hash"],
            "confirmado_em": utc_text(contexto["preparacao"].confirmado_em),
            "revisao": self.repo.revisao(session),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        return "envio-telegram-v1." + hmac.new(
            self._chave_marcador, raw, hashlib.sha256
        ).hexdigest()

    def revisao(self, campanha_id, preparacao_id, token):
        with self.repo.transacao() as session:
            usuario = self._usuario(session, token)
            contexto = self._avaliar(session, campanha_id, preparacao_id)
            if session.scalar(
                select(AutorizacaoEnvioTelegram.id).where(
                    AutorizacaoEnvioTelegram.preparacao_id == preparacao_id
                )
            ):
                raise conflito()
            return {
                "campanha_id": campanha_id,
                "preparacao_id": preparacao_id,
                "versao": contexto["campanha"].versao,
                "impressao": contexto["preparacao"].impressao,
                "conta": {
                    "id": contexto["conta"].id,
                    "nome": contexto["conta"].nome,
                    "bot_id": contexto["conta"].telegram_bot_id,
                },
                "destinatario": {
                    "identidade_telegram_id": contexto["identidade"].id,
                    "nome": contexto["contato"].nome,
                    "destino_mascarado": "***" + contexto["identidade"].chat_externo_id[-4:],
                },
                "categoria": contexto["campanha"].categoria,
                "conteudo": contexto["texto"],
                "marcador_envio": self._marcador(session, usuario, contexto),
                "aviso": "Envio real unitario. Confirme explicitamente antes de continuar.",
            }

    @staticmethod
    def _recibo(tentativa):
        return {
            "tentativa_id": tentativa.id,
            "estado": tentativa.estado,
            "codigo_resultado": tentativa.codigo_resultado,
            "message_id": tentativa.message_id,
            "retry_after": tentativa.retry_after,
            "reenvio_automatico": False,
        }

    def _hash_requisicao(self, data, usuario, campanha_id, preparacao_id):
        payload = {
            "acao": "enviar_telegram_real",
            "usuario_id": usuario,
            "campanha_id": campanha_id,
            "preparacao_id": preparacao_id,
            "entrada": data.model_dump(mode="json"),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        ).hexdigest()

    def _persistir_resultado(self, tentativa_id, resultado):
        with self.repo.transacao() as session:
            tentativa = self.repo.obter(session, TentativaEnvioTelegram, tentativa_id)
            if tentativa.estado != "iniciada":
                return self._recibo(tentativa)
            now = agora()
            tentativa.estado = resultado.estado
            tentativa.codigo_resultado = resultado.codigo
            tentativa.message_id = resultado.message_id
            tentativa.retry_after = resultado.retry_after
            tentativa.finalizada_em = now
            tentativa.atualizado_em = now
            session.flush()
            return self._recibo(tentativa)

    def confirmar(self, campanha_id, preparacao_id, data, token):
        with self.repo.lock:
            with self.repo.transacao() as session:
                usuario = self._usuario(session, token)
                digest = self._hash_requisicao(data, usuario, campanha_id, preparacao_id)
                anterior = session.scalar(
                    select(AutorizacaoEnvioTelegram).where(
                        AutorizacaoEnvioTelegram.operacao_id == str(data.operacao_id)
                    )
                )
                if anterior is not None:
                    if not hmac.compare_digest(anterior.requisicao_hash, digest):
                        raise conflito()
                    tentativa = session.scalar(
                        select(TentativaEnvioTelegram).where(
                            TentativaEnvioTelegram.autorizacao_id == anterior.id
                        )
                    )
                    return self._recibo(tentativa)
                contexto = self._avaliar(session, campanha_id, preparacao_id)
                if (
                    contexto["campanha"].versao != data.versao
                    or contexto["preparacao"].impressao != data.impressao
                    or not hmac.compare_digest(
                        self._marcador(session, usuario, contexto), data.marcador_envio
                    )
                ):
                    raise conflito()
                now, auth_id, tentativa_id = agora(), uuid(), uuid()
                autorizacao = AutorizacaoEnvioTelegram(
                    id=auth_id,
                    operacao_id=str(data.operacao_id),
                    requisicao_hash=digest,
                    modo="unitario",
                    versao=1,
                    campanha_id=campanha_id,
                    preparacao_id=preparacao_id,
                    conta_envio_id=contexto["conta"].id,
                    identidade_telegram_id=contexto["identidade"].id,
                    inscricao_id=contexto["item"].inscricao_id,
                    evento_telegram_id=contexto["evento_id"],
                    bot_externo_id=contexto["conta"].telegram_bot_id,
                    chat_externo_id=contexto["identidade"].chat_externo_id,
                    conta_versao=contexto["conta"].versao,
                    impressao=contexto["preparacao"].impressao,
                    conteudo_hash=contexto["conteudo_hash"],
                    categoria=None,
                    quantidade=None,
                    configuracao_hash=None,
                    intervalo_segundos=None,
                    autorizado_por_usuario_id=usuario,
                    autorizado_em=now,
                )
                tentativa = TentativaEnvioTelegram(
                    id=tentativa_id,
                    autorizacao_id=auth_id,
                    conta_envio_id=contexto["conta"].id,
                    chat_externo_id=contexto["identidade"].chat_externo_id,
                    estado="iniciada",
                    codigo_resultado=None,
                    message_id=None,
                    retry_after=None,
                    iniciada_em=now,
                    finalizada_em=None,
                    atualizado_em=now,
                )
                session.add_all((autorizacao, tentativa))
                self.repo.auditar(
                    session,
                    usuario,
                    "autorizar_envio_telegram",
                    "campanha",
                    campanha_id,
                    autorizacao_id=auth_id,
                    tentativa_id=tentativa_id,
                )
                session.flush()
                conta_id, segredo_ref = contexto["conta"].id, contexto["conta"].segredo_ref
                chat_id, texto = contexto["identidade"].chat_externo_id, contexto["texto"]

            # O lock global continua retido: revogacao confirmada nao pode ser ultrapassada.
            try:
                if not segredo_ref:
                    raise NegocioError("segredo_indisponivel", "Credencial indisponivel.", 503)
                token_bot = self.contas.segredos.recuperar(
                    conta_id, "telegram_bot_api", segredo_ref
                )
            except NegocioError:
                return self._persistir_resultado(
                    tentativa_id, ResultadoEnvioTelegram("falhou", "credencial_indisponivel")
                )
            try:
                provedor = self.provedor_factory()
            except Exception:
                return self._persistir_resultado(
                    tentativa_id,
                    ResultadoEnvioTelegram("desconhecida", "sem_confirmacao"),
                )
            with self._ativo_lock:
                self._ativo, self._terminou = provedor, Event()
            try:
                try:
                    # Provedor ja esta cancelavel: logout concorrente fecha a tentativa.
                    self.auth.current(token)
                except AuthError:
                    resultado = ResultadoEnvioTelegram("falhou", "sessao_encerrada")
                else:
                    try:
                        resultado = provedor.enviar(token_bot, chat_id, texto)
                    except Exception:
                        resultado = ResultadoEnvioTelegram(
                            "desconhecida", "sem_confirmacao"
                        )
            finally:
                token_bot = None
                with self._ativo_lock:
                    self._ativo = None
                    self._terminou.set()
            try:
                return self._persistir_resultado(tentativa_id, resultado)
            except NegocioError:
                try:
                    return self._persistir_resultado(
                        tentativa_id,
                        ResultadoEnvioTelegram("desconhecida", "sem_confirmacao"),
                    )
                except NegocioError:
                    return {
                        "tentativa_id": tentativa_id,
                        "estado": "desconhecida",
                        "codigo_resultado": "sem_confirmacao",
                        "message_id": None,
                        "retry_after": None,
                        "reenvio_automatico": False,
                    }

    def recuperar(self):
        with self.repo.transacao() as session:
            now = agora()
            session.execute(
                update(TentativaEnvioTelegram)
                .where(TentativaEnvioTelegram.estado == "iniciada")
                .values(
                    estado="desconhecida",
                    codigo_resultado="sem_confirmacao",
                    finalizada_em=now,
                    atualizado_em=now,
                )
            )

    def drenar(self, espera=5):
        self.parar()
        with self._ativo_lock:
            terminou = self._terminou
        return terminou.wait(espera)

    def parar(self):
        with self._ativo_lock:
            ativo = self._ativo
        if ativo is not None:
            ativo.cancelar()
