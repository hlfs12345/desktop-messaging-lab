"""SQLite e a unica autoridade das intencoes; diario legado nao confirma pedidos."""

import json
from datetime import datetime
from threading import RLock

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.campanhas.execucoes_models import SuspensaoConsentimento
from app.consentimentos.models import EscopoComunicacao, EventoConsentimento
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Auditoria, Contato
from app.contatos.repository import agora, uuid
from app.persistencia.errors import DatabaseAccessError, PersistenceError
from app.persistencia.models import utc_text

CAMPOS = (
    "id",
    "escopo_id",
    "contato_id",
    "identidade_contato_id",
    "categoria",
    "tipo",
    "usuario_id",
    "requisicao_hash",
    "criado_em",
)
TIPOS = {"revogou", "bloqueio_global", "bloqueio_canal"}


class SuspensoesConsentimento:
    def __init__(self, database, path):
        self.db = database
        # Apenas localiza evidencias legadas. Nao le, grava, apaga ou confia nelas.
        self.path = path.with_suffix(".optouts")
        self.lock = RLock()
        self.pendentes = {}
        self.inseguro = False

    @staticmethod
    def _dados(row):
        return {k: utc_text(row.criado_em) if k == "criado_em" else getattr(row, k) for k in CAMPOS}

    def falhar(self, identifier):
        """Ausencia comprovada difere de commit parcial ou confirmacao perdida."""
        self.inseguro = True
        runtime = getattr(self.db, "execution_runtime", None)
        if runtime is not None:
            runtime.close_admission()
        rejeitado = False
        try:
            with self.db._sessions() as s:
                row = s.get(SuspensaoConsentimento, identifier)
                evento = s.get(EventoConsentimento, identifier)
                audit = s.scalar(
                    select(Auditoria.id)
                    .where(
                        Auditoria.entidade == "consentimento",
                        Auditoria.entidade_id == identifier,
                        Auditoria.acao.in_(("suspender_optout", "confirmar_optout")),
                    )
                    .limit(1)
                )
                rejeitado = row is None and evento is None and audit is None
        except Exception:
            pass
        if rejeitado:
            return NegocioError(
                "optout_rejeitado",
                "Solicitacao nao persistida. "
                "Atualize o estado e tente novamente. Processamento suspenso nesta abertura.",
                503,
            )
        return NegocioError(
            "optout_indeterminado",
            "Nao foi possivel confirmar o resultado. "
            "Consulte o historico antes de repetir. Processamento suspenso nesta abertura.",
            503,
        )

    def _espelhar(self, data):
        with self.db.transaction() as s:
            old = s.get(SuspensaoConsentimento, data["id"])
            if old is not None:
                if self._dados(old) != data:
                    raise conflito()
                return
            s.add(
                SuspensaoConsentimento(
                    **{**data, "criado_em": datetime.fromisoformat(data["criado_em"])}
                )
            )
            s.add(
                Auditoria(
                    id=uuid(),
                    usuario_id=data["usuario_id"],
                    acao="suspender_optout",
                    entidade="consentimento",
                    entidade_id=data["id"],
                    resumo_json="{}",
                    ocorrido_em=agora(),
                )
            )
            s.flush()

    def _verificar_intencao(self, data):
        with self.db._sessions() as s:
            row = s.get(SuspensaoConsentimento, data["id"])
            if (
                row is None
                or self._dados(row) != data
                or s.scalar(
                    select(Auditoria.id)
                    .where(
                        Auditoria.acao == "suspender_optout",
                        Auditoria.entidade == "consentimento",
                        Auditoria.entidade_id == row.id,
                        Auditoria.usuario_id == row.usuario_id,
                    )
                    .limit(1)
                )
                is None
            ):
                raise DatabaseAccessError("Intencao de descadastramento nao confirmada.")

    def recuperar(self):
        try:
            with self.lock:
                with self.db._sessions() as s:
                    pendentes = {
                        r.id: self._dados(r)
                        for r in s.scalars(
                            select(SuspensaoConsentimento).where(
                                SuspensaoConsentimento.confirmado_em.is_(None)
                            )
                        )
                    }
                for data in pendentes.values():
                    self._verificar_intencao(data)
                self.pendentes = pendentes
                for identifier in tuple(pendentes):
                    self.confirmar(identifier)
                self.inseguro = False
        except Exception:
            self.inseguro = True
            raise DatabaseAccessError(
                "Suspensoes de descadastramento indisponiveis. Preserve os dados para revisao."
            ) from None

    def afetado(self, contato, identidade, escopo, categoria):
        with self.lock:
            return self.inseguro or any(
                r["contato_id"] == contato
                and (
                    r["tipo"] == "bloqueio_global"
                    or (
                        (identidade is None or r["identidade_contato_id"] == identidade)
                        and (
                            r["tipo"] == "bloqueio_canal"
                            or (
                                r["escopo_id"] == escopo
                                and (categoria is None or r["categoria"] == categoria)
                            )
                        )
                    )
                )
                for r in self.pendentes.values()
            )

    def preparar(self, service, contato, data, token):
        try:
            self._preparar(service, contato, data, token)
        except SQLAlchemyError, PersistenceError:
            raise self.falhar(str(data.operacao_id)) from None

    def _preparar(self, service, contato, data, token):
        with service.repo.lock, self.db._sessions() as s:
            user = service.auth.current(token, session=s).usuario.id
            digest = service._digest(contato, data, user)
            old = s.get(EventoConsentimento, str(data.operacao_id))
            if old is not None:
                if old.requisicao_hash != digest:
                    raise conflito()
                return
            contact = service.repo.obter(s, Contato, contato)
            service.repo.obter(s, EscopoComunicacao, str(data.escopo_id))
            if data.identidade_contato_id is not None:
                service._identidade(s, contato, str(data.identidade_contato_id))
            if data.ocorrido_em > agora():
                raise NegocioError("validacao", "A data nao pode estar no futuro.")
            if contact.consentimento_versao != data.versao:
                raise conflito()
            value = dict(
                id=str(data.operacao_id),
                escopo_id=str(data.escopo_id),
                contato_id=contato,
                identidade_contato_id=str(data.identidade_contato_id)
                if data.identidade_contato_id
                else None,
                categoria=data.categoria,
                tipo=data.tipo,
                usuario_id=user,
                requisicao_hash=digest,
                criado_em=utc_text(agora()),
            )
            durable = s.get(SuspensaoConsentimento, value["id"])
            if durable is not None:
                previous = self._dados(durable)
                if any(previous[k] != value[k] for k in CAMPOS if k != "criado_em"):
                    raise conflito()
                value = previous
        with self.lock:
            try:
                self._espelhar(value)
                self._verificar_intencao(value)
                self.pendentes[value["id"]] = value
            except Exception:
                raise self.falhar(value["id"]) from None

    def verificar_resultado(self, identifier):
        with self.db._sessions() as s:
            event = s.get(EventoConsentimento, identifier)
            row = s.get(SuspensaoConsentimento, identifier)
            # Eventos anteriores ao protocolo de suspensao continuam idempotentes.
            if event is None or (
                row is not None and (row.confirmado_em is None or row.evento_id != event.id)
            ):
                raise DatabaseAccessError("Resultado do descadastramento nao confirmado.")

    def _verificar_confirmacoes(self, identifiers, evento_id):
        with self.db._sessions() as s:
            for identifier in identifiers:
                row = s.get(SuspensaoConsentimento, identifier)
                if row is None or row.confirmado_em is None or row.evento_id != evento_id:
                    raise DatabaseAccessError("Confirmacao do descadastramento indisponivel.")

    def bloqueia_execucao(self, session, execucao):
        from app.campanhas.execucoes_models import ItemExecucao
        from app.campanhas.models import Campanha
        from app.consentimentos.models import IdentidadeContato

        with self.lock:
            if self.inseguro:
                return True
            if not self.pendentes:
                return False
            contatos = {r["contato_id"] for r in self.pendentes.values()}
            c = session.get(Campanha, execucao.campanha_id)
            rows = session.execute(
                select(IdentidadeContato.contato_id, IdentidadeContato.id)
                .join(ItemExecucao, ItemExecucao.identidade_contato_id == IdentidadeContato.id)
                .where(
                    ItemExecucao.execucao_id == execucao.id,
                    ItemExecucao.estado.in_(("pendente", "reservado", "processando")),
                    IdentidadeContato.contato_id.in_(contatos),
                )
            )
            return any(self.afetado(cid, iid, c.escopo_id, c.categoria) for cid, iid in rows)

    def bloqueia(self, execucao_id=None, session=None, execucao=None):
        from app.campanhas.execucoes_models import ExecucaoCampanha

        if self.inseguro:
            return True
        if not self.pendentes:
            return False
        if execucao is not None:
            return self.bloqueia_execucao(session, execucao)
        if execucao_id is None:
            return True
        with self.db._sessions() as s:
            return self.bloqueia_execucao(s, s.get(ExecucaoCampanha, execucao_id))

    def confirmar(self, identifier):
        with self.lock:
            with self.db.transaction() as s:
                event = s.get(EventoConsentimento, identifier)
                if event is None or event.tipo not in TIPOS:
                    return
                # O trigger confirma a correspondencia e o bloqueio efetivo.
                candidates = s.scalars(
                    select(SuspensaoConsentimento).where(
                        SuspensaoConsentimento.confirmado_em.is_(None),
                        SuspensaoConsentimento.contato_id == event.contato_id,
                        SuspensaoConsentimento.escopo_id == event.escopo_id,
                        SuspensaoConsentimento.tipo == event.tipo,
                        SuspensaoConsentimento.identidade_contato_id == event.identidade_contato_id,
                        SuspensaoConsentimento.categoria == event.categoria,
                        SuspensaoConsentimento.criado_em <= event.registrado_em,
                    )
                ).all()
                ids = []
                for row in candidates:
                    row.confirmado_em, row.evento_id = (
                        max(agora(), row.criado_em, event.registrado_em),
                        event.id,
                    )
                    s.add(
                        Auditoria(
                            id=uuid(),
                            usuario_id=event.usuario_id,
                            acao="confirmar_optout",
                            entidade="consentimento",
                            entidade_id=row.id,
                            resumo_json=json.dumps({"evento_id": event.id}),
                            ocorrido_em=agora(),
                        )
                    )
                    ids.append(row.id)
                s.flush()
            self._verificar_confirmacoes(ids, identifier)
            for key in ids:
                self.pendentes.pop(key, None)
