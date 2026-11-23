"""Worker desarmado, ativacao interna revisada e drenagem antes de fechar o banco."""

import hashlib
import hmac
import json
import logging
import secrets
from threading import Condition, Event, Thread
from time import monotonic

from sqlalchemy import func, select

from app.auth.errors import AuthError
from app.campanhas.execucoes_models import (
    ControleEnvioTelegram,
    ExecucaoCampanha,
    ItemExecucao,
    TransicaoExecucao,
)
from app.campanhas.executor_mock import ContextoOperacionalMock, ExecutorMock
from app.campanhas.executor_telegram import ExecutorTelegram
from app.campanhas.models import AutorizacaoEnvioTelegram, AutorizacaoSimulacao, Campanha
from app.contatos.errors import NegocioError, conflito
from app.contatos.repository import agora


class ExecutionLifecycle:
    def __init__(self, execucoes, persistence, lifetime=None):
        anterior = getattr(persistence.database, "execution_runtime", None)
        if anterior is not None and (
            not anterior._closed.is_set()
            or anterior._activating
            or (anterior._thread is not None and anterior._thread.is_alive())
        ):
            raise conflito()
        self.svc, self.persistence, self.lifetime = execucoes, persistence, lifetime
        self._condition, self._closed = Condition(), Event()
        self._active = None
        self._context = None
        self._activating = False
        self._reconciling = True
        self._activation_token = None
        self._session_generation = 0
        self._key = secrets.token_bytes(32)
        self.recovery_ok = False
        self.failure = None
        self._thread = None
        self._started = False
        persistence.database.execution_runtime = self
        if lifetime is not None:
            lifetime.subscribe(self.close_admission)

    def _require(self):
        if (
            self._closed.is_set()
            or not self.recovery_ok
            or self.lifetime is None
            or self._thread is None
            or not self._thread.is_alive()
        ):
            raise NegocioError(
                "execucao_indisponivel", "Processamento integrado indisponivel.", 409
            )
        self.lifetime.require_open()

    def _recover(self):
        anterior = getattr(self.persistence.database, "controle_mock", None)
        if anterior is not None:
            anterior.fechar()
        for telegram in (False, True):
            ctx = ContextoOperacionalMock(self.svc, self.persistence, self.svc.p.abertura)
            try:
                if telegram:
                    ExecutorTelegram(
                        self.svc,
                        self.svc.telegram,
                        self.svc.telegram.recepcao,
                        ctx,
                    ).recuperar()
                else:
                    ExecutorMock(self.svc, ctx).recuperar()
            finally:
                ctx.fechar()

    def start(self):
        with self._condition:
            if self._started:
                raise conflito()
            self._started = True
        try:
            self._recover()
            self.recovery_ok = True
        except Exception:
            self.failure = "recuperacao_indisponivel"
            logging.getLogger("uvicorn.error").error("Recuperacao operacional indisponivel.")
        self._reconciling = False
        if self._closed.is_set():
            return
        # O worker nao recebe execucao, sessao ou certificados no startup.
        self._thread = Thread(target=self._run, daemon=True, name="edisk-mock-worker")
        self._thread.start()

    def _signature(self, payload):
        data = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        return "ativar-v1." + hmac.new(self._key, data, hashlib.sha256).hexdigest()

    def review(self, campanha_id, execucao_id, token):
        self._require()
        with self.svc.repo.transacao() as s:
            usuario, sid = self.svc._contexto(s, token)
            e = self.svc._obter(s, campanha_id, execucao_id)
            if e.estado not in {"executando", "pausada"}:
                raise conflito()
            if e.modo == "telegram":
                return self._review_telegram(s, e, token, usuario, sid)
            c = self.svc.repo.obter(s, Campanha, campanha_id)
            p = self.svc.p._obter(s, campanha_id, e.preparacao_id)
            a = self.svc.repo.obter(s, AutorizacaoSimulacao, e.autorizacao_id)
            impedidos = {}
            cfg, atual = self.svc.a._avaliar(s, c, p, impedidos=impedidos)
            if (
                a.preparacao_id != p.id
                or a.campanha_id != c.id
                or a.base_id != p.base_id
                or a.impressao != p.impressao
                or a.resumo_configuracao != cfg.resumo_configuracao
            ):
                raise conflito()
            pendentes = s.scalars(
                select(ItemExecucao.identidade_contato_id).where(
                    ItemExecucao.execucao_id == e.id, ItemExecucao.estado == "pendente"
                )
            ).all()
            conflito_global = s.scalar(
                select(ExecucaoCampanha.id)
                .where(
                    ExecucaoCampanha.id != e.id,
                    ExecucaoCampanha.estado.in_(("executando", "pausando")),
                )
                .limit(1)
            )
            payload = dict(
                finalidade="ativacao-integrada-mock-v1",
                campanha_id=c.id,
                execucao_id=e.id,
                versao=e.versao,
                estado=e.estado,
                proprietario_id=e.proprietario_id,
                geracao=e.geracao,
                preparacao_id=p.id,
                autorizacao_id=a.id,
                versao_autorizacao=a.versao,
                impressao=p.impressao,
                resumo_configuracao=cfg.resumo_configuracao,
                configuracao_simulacao=cfg.model_dump(),
                configuracao_execucao=cfg.model_dump(),
                avaliacao_atual=atual,
                pendentes=len(pendentes),
                elegiveis_pendentes=sum(i not in impedidos for i in pendentes),
                usuario_id=usuario,
                sessao_id=sid,
                abertura=self.svc.p.abertura,
                instancia=self.lifetime.execution_id.hex(),
                sessao_geracao=self._session_generation,
                revisao=self.svc.repo.revisao(s),
                transicoes=s.scalar(select(func.count()).select_from(TransicaoExecucao)),
                permitida=(
                    a.estado == "vigente"
                    and atual["condicoes_atuais_atendidas"]
                    and any(i not in impedidos for i in pendentes)
                    and conflito_global is None
                ),
                modo="mock",
            )
            self.svc._contexto(s, token)
            return {**payload, "marcador": self._signature(payload)}

    def _review_telegram(self, s, e, token, usuario, sid):
        c = self.svc.repo.obter(s, Campanha, e.campanha_id)
        p = self.svc.p._obter(s, e.campanha_id, e.preparacao_id)
        a = self.svc.repo.obter(s, AutorizacaoEnvioTelegram, e.autorizacao_telegram_id)
        contexto = self.svc.telegram.avaliar_lote(s, c.id, p.id)
        pendentes = s.scalars(
            select(ItemExecucao.identidade_telegram_id).where(
                ItemExecucao.execucao_id == e.id,
                ItemExecucao.estado == "pendente",
            )
        ).all()
        motivos = contexto["motivos_destinatarios"]
        elegiveis = sum(
            not motivos.get(identifier, ["identidade_indisponivel"])
            for identifier in pendentes
        )
        conflito_global = s.scalar(
            select(ExecucaoCampanha.id)
            .where(
                ExecucaoCampanha.id != e.id,
                ExecucaoCampanha.estado.in_(("executando", "pausando")),
            )
            .limit(1)
        )
        controle = s.get(ControleEnvioTelegram, a.conta_envio_id)
        limite = bool(
            controle
            and controle.impedimento_codigo == "limite_telegram"
            and (controle.impedido_ate is None or controle.impedido_ate > agora())
        )
        sincronizacao = bool(
            controle and controle.impedimento_codigo == "sincronizacao"
        )
        payload = dict(
            finalidade="ativacao-integrada-telegram-v1",
            campanha_id=c.id,
            execucao_id=e.id,
            versao=e.versao,
            estado=e.estado,
            proprietario_id=e.proprietario_id,
            geracao=e.geracao,
            preparacao_id=p.id,
            autorizacao_id=a.id,
            versao_autorizacao=a.versao,
            impressao=p.impressao,
            resumo_configuracao=a.configuracao_hash,
            categoria=a.categoria,
            bot_externo_id=a.bot_externo_id,
            configuracao_execucao=contexto["configuracao"],
            avaliacao_atual={
                "incluidos": contexto["quantidade"],
                "excluidos": p.excluidos,
                "elegiveis_atuais": elegiveis,
                "impedidos_atuais": len(pendentes) - elegiveis,
                "condicoes_atuais_atendidas": not contexto["impedimentos"],
                "motivos_destinatarios": motivos,
            },
            pendentes=len(pendentes),
            elegiveis_pendentes=elegiveis,
            usuario_id=usuario,
            sessao_id=sid,
            abertura=self.svc.p.abertura,
            instancia=self.lifetime.execution_id.hex(),
            sessao_geracao=self._session_generation,
            revisao=self.svc.repo.revisao(s),
            transicoes=s.scalar(select(func.count()).select_from(TransicaoExecucao)),
            impedimento_codigo=(
                "limite_telegram" if limite else "sincronizacao" if sincronizacao else None
            ),
            impedido_ate=controle.impedido_ate if limite else None,
            permitida=(
                a.modo == "lote"
                and a.impressao == p.impressao
                and a.quantidade == contexto["quantidade"]
                and a.configuracao_hash == contexto["configuracao_hash"]
                and not contexto["impedimentos"]
                and elegiveis > 0
                and conflito_global is None
                and not limite
                and not sincronizacao
            ),
            modo="telegram",
        )
        self.svc._contexto(s, token)
        return {**payload, "marcador": self._signature(payload)}

    def activate(self, review, token, *, confirmo=False, registrar=None):
        if confirmo is not True or not isinstance(review, dict):
            raise conflito()
        self._require()
        try:
            marcador = review.get("marcador")
            if (
                not isinstance(marcador, str)
                or len(marcador) != 74
                or not hmac.compare_digest(
                    self._signature({k: v for k, v in review.items() if k != "marcador"}), marcador
                )
            ):
                raise conflito()
        except TypeError, ValueError:
            raise conflito() from None
        with self._condition:
            if self._active is not None or self._activating or self._reconciling:
                raise conflito()
            self._activating = True
            self._activation_token = token
            generation = self._session_generation
        ctx = None
        try:
            with self.svc.repo.lock:
                atual = self.review(review.get("campanha_id"), review.get("execucao_id"), token)
                if (
                    not atual["permitida"]
                    or not isinstance(review.get("marcador"), str)
                    or {k: v for k, v in atual.items() if k != "marcador"}
                    != {k: v for k, v in review.items() if k != "marcador"}
                    or not hmac.compare_digest(atual["marcador"], review["marcador"])
                ):
                    raise conflito()
                ctx = ContextoOperacionalMock(
                    self.svc, self.persistence, self.svc.p.abertura, lifetime=self.lifetime
                )
                with self._condition:
                    self._context = ctx
                executor = (
                    ExecutorTelegram(
                        self.svc,
                        self.svc.telegram,
                        self.svc.telegram.recepcao,
                        ctx,
                    )
                    if atual.get("modo") == "telegram"
                    else ExecutorMock(self.svc, ctx)
                )
                adm = executor.admitir(
                    atual["execucao_id"],
                    token,
                    atual["versao"],
                    retomar=atual["estado"] == "pausada",
                    registrar=registrar,
                )
                self._require()
                with self._condition:
                    if generation != self._session_generation:
                        raise conflito()
                    ctx.validar()
                    self._active = (executor, adm)
                    self._condition.notify_all()
            return {"execucao_id": adm.execucao_id, "geracao": adm.geracao}
        except BaseException:
            if ctx is not None:
                ctx.fechar()
                try:
                    self._recover()
                except Exception:
                    self.recovery_ok = False
                    self.failure = "recuperacao_indisponivel"
            raise
        finally:
            with self._condition:
                self._activation_token = None
                self._activating = False
                self._condition.notify_all()

    def snapshot(self, execucao_id=None):
        """Observacao efemera: nao valida, reconcilia, admite nem acorda o worker."""
        with self._condition:
            active = self._active
            corresponde = active is not None and active[1].execucao_id == execucao_id
            ctx = active[0].contexto if corresponde else None
            aberta = (
                self.lifetime is not None and self.lifetime.is_open() and not self._closed.is_set()
            )
            vivo = self._thread is not None and self._thread.is_alive()
            return {
                "processamento_disponivel": bool(aberta and vivo and self.recovery_ok),
                "admitida": bool(
                    ctx is not None and not ctx.fechado.is_set() and not ctx.suspenso.is_set()
                ),
                "worker_ativo": bool(corresponde and vivo),
                "recuperacao_disponivel": self.recovery_ok,
                "encerrando": self._closed.is_set(),
            }

    def suspend(self, execucao_id):
        """Depois do commit da pausa; resultado admitido continua podendo persistir."""
        with self._condition:
            if self._active is not None and self._active[1].execucao_id == execucao_id:
                self._active[0].contexto.fechar()
            self._condition.notify_all()

    def session_changed(self, token=None):
        with self._condition:
            current = self._active[1].token if self._active else self._activation_token
            ctx = self._context or getattr(self.persistence.database, "controle_mock", None)
            corresponde = ctx is not None and any(a.token == token for a in ctx._admissoes.values())
            if token is not None and token != current and not corresponde:
                return
            self._session_generation += 1
            if ctx is not None:
                ctx.fechar()
            self._condition.notify_all()

    def close_admission(self):
        self._closed.set()
        with self._condition:
            if self._context is not None:
                self._context.fechar()
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed.is_set() or self._active is not None)
                if self._closed.is_set() and self._active is None:
                    return
                executor, adm = self._active
            try:
                self._require()
                executor.passo(adm)
                with self.svc.repo.transacao() as s:
                    e = s.get(ExecucaoCampanha, adm.execucao_id)
                    continuar = e is not None and e.estado == "executando"
                self._require()
                if continuar and not executor.contexto.fechado.is_set():
                    continue
            except NegocioError, AuthError:
                self.failure = "processamento_suspenso"
            except Exception:
                self.failure = "processamento_suspenso"
            with self._condition:
                executor.contexto.fechar()
                self._context = None
                self._active = None
                self._reconciling = True
                self._condition.notify_all()
            # Reconcilia sem rearmar. Uma falha preserva HTTP/historico seguro.
            try:
                self._recover()
            except Exception:
                self.recovery_ok = False
                self.failure = "recuperacao_indisponivel"
            finally:
                with self._condition:
                    self._reconciling = False
                    self._condition.notify_all()

    def drain(self, timeout=5.0):
        self.close_admission()
        deadline = monotonic() + timeout
        with self._condition:
            if not self._condition.wait_for(
                lambda: not self._activating, timeout=max(0, deadline - monotonic())
            ):
                return False
        if self._thread is not None:
            self._thread.join(timeout=max(0, deadline - monotonic()))
            if self._thread.is_alive():
                return False
        try:
            self._recover()
        except Exception:
            self.recovery_ok = False
            self.failure = "recuperacao_indisponivel"
        return True
