"""Um receptor Telegram por aplicativo, ativo apenas na sessão autenticada."""

import logging
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Lock, RLock, Thread
from time import monotonic

from sqlalchemy import func, select

from app.auth.errors import AuthError
from app.campanhas.execucoes_models import ControleEnvioTelegram
from app.consentimentos.models import ReciboTelegram
from app.consentimentos.service import AVISO_TELEGRAM_TEXTO
from app.contas_envio.models import ContaEnvio
from app.contatos.errors import NegocioError, conflito
from app.contatos.repository import agora
from app.provedores.telegram import Telegram


class RecepcaoTelegram:
    def __init__(self, consentimentos, contas, lifetime=None):
        self.svc, self.contas = consentimentos, contas
        self.repo, self.auth = consentimentos.repo, consentimentos.auth
        self._controle = RLock()
        self._closed, self._stop = Event(), Event()
        self._thread = self._client = self._token = None
        self._contas_lock, self._contas = RLock(), {}
        self.estados = {}
        old = getattr(self.repo.database, "telegram_recepcao", None)
        if old is not None and (
            not old._closed.is_set() or (old._thread is not None and old._thread.is_alive())
        ):
            raise conflito()
        self.repo.database.telegram_recepcao = self
        if lifetime is not None:
            lifetime.subscribe(self.fechar)

    def iniciar(self, token):
        with self._controle:
            if self._closed.is_set() or not self.parar():
                return
            self.auth.current(token)
            if self._closed.is_set():
                return
            self._token, self._stop, self._client = token, Event(), Telegram()
            self.estados = {}
            self._thread = Thread(
                target=self._run,
                args=(token, self._stop, self._client),
                name="edisk-telegram",
                daemon=True,
            )
            self._thread.start()

    @contextmanager
    def exclusivo(self, conta_id):
        with self._contas_lock:
            lock = self._contas.setdefault(conta_id, Lock())
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def parar(self, token=None, *, timeout=6):
        deadline = monotonic() + timeout
        if not self._controle.acquire(timeout=max(0, deadline - monotonic())):
            return False
        try:
            if token is not None and token != self._token:
                return True
            self._stop.set()
            if self._client is not None:
                self._client.cancelar()
            self._token = None
            thread = self._thread
        finally:
            self._controle.release()
        if thread is not None:
            thread.join(timeout=max(0, deadline - monotonic()))
        return thread is None or not thread.is_alive()

    def fechar(self):
        # Callback do pipe/event loop: nunca aguardar banco, repositório ou rede.
        self._closed.set()
        self._stop.set()
        if self._client is not None:
            self._client.cancelar()

    def drenar(self, *, timeout=6):
        self.fechar()
        return self.parar(timeout=timeout)

    def _contextos(self, token):
        with self.repo.transacao(espera=0.25) as s:
            self.auth.current(token, session=s)
            return [
                (a.id, a.segredo_ref, a.versao)
                for a in s.scalars(
                    select(ContaEnvio)
                    .where(
                        ContaEnvio.provedor == "telegram_bot_api",
                        ContaEnvio.ativo == 1,
                        ContaEnvio.removido_em.is_(None),
                        ContaEnvio.segredo_ref.is_not(None),
                    )
                    .order_by(ContaEnvio.id)
                )
            ]

    def _conta(self, s, contexto, token, ativo):
        self.auth.current(token, session=s)
        account = self.repo.obter(s, ContaEnvio, contexto[0])
        if (
            not ativo()
            or not account.ativo
            or account.removido_em
            or account.segredo_ref != contexto[1]
            or account.versao != contexto[2]
        ):
            raise conflito()
        return account

    def _passo(self, contexto, token, client, ativo, verificados):
        with self.repo.transacao(espera=0.25) as s:
            account = self._conta(s, contexto, token, ativo)
            secret = self.contas.segredos.recuperar(
                account.id, account.provedor, account.segredo_ref
            )
        bot = verificados.get(contexto)
        if bot is None:
            bot = client.verificar(secret, recepcao=True)
            with self.repo.transacao(espera=0.25) as s:
                account = self._conta(s, contexto, token, ativo)
                if account.telegram_bot_id not in (None, str(bot["id"])):
                    raise NegocioError(
                        "telegram_bot_divergente", "O bot da conta foi alterado.", 409
                    )
                # UNIQUE persiste a exclusividade mesmo entre contas com tokens equivalentes.
                account.telegram_bot_id = str(bot["id"])
                s.flush()
            verificados[contexto] = {"id": bot["id"], "username": bot.get("username")}
        with self.repo.transacao(espera=0.25) as s:
            account = self._conta(s, contexto, token, ativo)
            offset = account.telegram_proximo_offset
            last = s.scalar(
                select(func.max(ReciboTelegram.registrado_em)).where(
                    ReciboTelegram.conta_envio_id == account.id,
                )
            )
            # IDs podem reiniciar aleatoriamente após uma semana sem atualizações.
            if last is not None and last <= agora() - timedelta(days=7):
                offset = None
        updates = client.atualizacoes(secret, offset, bot.get("username"))
        sincronizacao = agora()
        anterior = account.telegram_sincronizado_em or last
        lacuna = anterior is None or anterior <= sincronizacao - timedelta(hours=24)
        for update in updates:
            if update["resultado"] == "iniciou" and (
                not update.get("reativacao")
                or self.svc.pode_apresentar_reativacao(
                    contexto, bot, update, token, ativo
                )
            ):
                try:
                    client.responder(secret, update["chat_externo_id"], AVISO_TELEGRAM_TEXTO)
                    update["aviso_apresentado"] = True
                except NegocioError:
                    update["aviso_apresentado"] = False
                    logging.getLogger("uvicorn.error").warning(
                        "Resposta Telegram: aviso_indisponivel."
                    )
            with self.repo.lock:
                resposta = self.svc.receber_telegram(
                    contexto, bot, update, token, ativo, sincronizacao
                )
            if resposta:
                try:
                    client.responder(secret, update["chat_externo_id"], resposta)
                except NegocioError:
                    logging.getLogger("uvicorn.error").warning(
                        "Resposta Telegram: confirmacao_indisponivel."
                    )
        with self.repo.lock, self.repo.transacao(espera=0.25) as s:
            account = self._conta(s, contexto, token, ativo)
            if lacuna:
                account.telegram_lacuna_desde = (
                    account.telegram_lacuna_desde or sincronizacao
                )
            account.telegram_sincronizado_em = sincronizacao
            controle = s.get(ControleEnvioTelegram, account.id)
            if controle is not None and controle.impedimento_codigo == "sincronizacao":
                controle.impedimento_codigo = None
                controle.impedido_ate = None
                controle.atualizado_em = max(sincronizacao, controle.atualizado_em)
            s.flush()
        # A próxima chamada relê o cursor do banco, inclusive após commit de resultado incerto.

    def sincronizar(self, conta_id, token):
        """Uma leitura confiavel. Chamador mantem exclusivo(conta_id)."""
        contextos = [c for c in self._contextos(token) if c[0] == conta_id]
        if len(contextos) != 1:
            raise NegocioError("conta_inativa", "Conta Telegram indisponivel.", 409)
        client = Telegram()
        try:
            self._passo(contextos[0], token, client, lambda: not self._closed.is_set(), {})
        finally:
            client.cancelar()

    def _run(self, token, stop, client):
        verificados, suspensos, tentativas = {}, set(), {}

        def ativo():
            return not stop.is_set() and not self._closed.is_set()

        try:
            while ativo():
                try:
                    contextos = self._contextos(token)
                except AuthError:
                    return
                except NegocioError:
                    stop.wait(1)
                    continue
                for contexto in contextos:
                    if not ativo():
                        break
                    if contexto in suspensos or monotonic() < tentativas.get(contexto, (0, 0))[0]:
                        continue
                    try:
                        with self.exclusivo(contexto[0]):
                            self._passo(contexto, token, client, ativo, verificados)
                        self.estados[contexto[0]] = "recebendo"
                        tentativas.pop(contexto, None)
                    except AuthError:
                        return
                    except Exception as error:
                        code = getattr(error, "codigo", "")
                        permanent = code in {
                            "telegram_conflito",
                            "telegram_bot_divergente",
                            "telegram_credencial_invalida",
                            "duplicidade",
                        }
                        if permanent:
                            suspensos.add(contexto)
                        failures = tentativas.get(contexto, (0, 0))[1] + 1
                        tentativas[contexto] = (
                            monotonic() + min(30, 2 ** min(failures, 5)),
                            failures,
                        )
                        state = "conflito_ou_credencial" if permanent else "recepcao_indisponivel"
                        if self.estados.get(contexto[0]) != state:
                            logging.getLogger("uvicorn.error").warning(
                                "Recepção Telegram: %s (%s).",
                                state,
                                code or type(error).__name__,
                            )
                        self.estados[contexto[0]] = state
                stop.wait(0.25)
        except Exception:
            # Não registrar exceções, corpos, referências externas ou tokens.
            logging.getLogger("uvicorn.error").warning("Recepção Telegram encerrada.")
        finally:
            client.cancelar()
