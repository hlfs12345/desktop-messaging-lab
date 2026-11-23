"""Aplicacao HTTP: recibos B1, admissao C e historico operacional sem fotografia."""

import hmac
from contextlib import contextmanager

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.campanhas.execucoes import ESTADOS_ITENS, _instante
from app.campanhas.execucoes_models import ExecucaoCampanha, ItemExecucao, TentativaEnvio
from app.campanhas.execucoes_schemas import (
    IniciarExecucaoEntrada,
    PausarExecucaoEntrada,
    RetomarExecucaoEntrada,
)
from app.campanhas.models import Campanha
from app.contatos.errors import NegocioError, conflito
from app.persistencia.errors import PersistenceError

AVISO = "Simulacao mock, sem envio externo. Aceitacao simulada nao comprova entrega real."
AVISO_TELEGRAM = (
    "Envio real Telegram. Revise o lote; resultados desconhecidos nunca sao reenviados "
    "automaticamente."
)
PUBLICOS_EXECUCAO = (
    "id",
    "campanha_id",
    "preparacao_id",
    "autorizacao_id",
    "estado",
    "versao",
    "geracao",
    "criado_em",
    "atualizado_em",
)
PUBLICOS_ITEM = (
    "id",
    "execucao_id",
    "ordem",
    "estado",
    "versao",
    "motivo_codigo",
    "atualizado_em",
)
PUBLICOS_TENTATIVA = (
    "id",
    "execucao_id",
    "item_id",
    "numero",
    "estado",
    "versao",
    "geracao",
    "reservada_em",
    "invocacao_em",
    "finalizada_em",
    "atualizado_em",
    "codigo_resultado",
    "message_id",
    "retry_after",
)
VINCULOS_RETOMADA = (
    "versao",
    "preparacao_id",
    "autorizacao_id",
    "versao_autorizacao",
    "geracao",
    "impressao",
    "resumo_configuracao",
    "modo",
)


def _view(item, campos):
    return {campo: getattr(item, campo) for campo in campos}


class ExecucoesHTTPService:
    def __init__(self, svc, runtime):
        self.svc, self.rt, self.repo = svc, runtime, svc.repo

    @contextmanager
    def _comando(self):
        # Nao fica indefinidamente esperando uma ativacao ou um passo em andamento.
        if not self.repo.lock.acquire(timeout=2):
            raise NegocioError("operacao_ocupada", "Operacao em andamento. Tente novamente.", 503)
        try:
            yield
        finally:
            self.repo.lock.release()

    @contextmanager
    def _leitura(self, token):
        # Snapshot curto e limitado, sem lock Python do executor e sem DPAPI.
        try:
            with self.repo.database.transaction() as s:
                self.svc._contexto(s, token)
                yield s
                self.svc._contexto(s, token)
        except SQLAlchemyError, PersistenceError:
            raise NegocioError(
                "persistencia_indisponivel", "Nao foi possivel acessar os dados.", 503
            ) from None

    def _repeticao(self, cid, data, token, acao):
        with self.repo.transacao() as s:
            self.svc._contexto(s, token)
            _, digest, anterior = self.svc.c._repeticao(s, data, token, acao, cid)
            return digest, anterior

    def revisao_inicio(self, cid, pid, token):
        with self._comando():
            r = self.svc.revisao(cid, pid, token)
        mock = r.get("modo") == "mock"
        return {
            **r,
            **self.rt.snapshot(),
            "simulacao": mock,
            "aviso": AVISO if mock else AVISO_TELEGRAM,
        }

    def _suspender_nao_ativada(self, cid, eid):
        # Falha depois do commit nao deixa execucao despachante esperando polling/replay.
        # Nao recupera tentativas e nao toca num worker ja publicado.
        if self.rt.snapshot(eid)["worker_ativo"]:
            return
        with self.repo.transacao() as s:
            e = self.svc._obter(s, cid, eid)
            ativa = s.scalar(
                select(TentativaEnvio.id)
                .where(
                    TentativaEnvio.execucao_id == eid,
                    TentativaEnvio.estado.in_(("reservada", "iniciada")),
                )
                .limit(1)
            )
            if e.estado == "executando" and ativa is None:
                changed = s.execute(
                    update(ExecucaoCampanha)
                    .where(
                        ExecucaoCampanha.id == eid,
                        ExecucaoCampanha.versao == e.versao,
                    )
                    .values(
                        estado="pausada",
                        versao=e.versao + 1,
                        atualizado_em=_instante(s, e),
                        alterado_por_usuario_id=None,
                    )
                )
                if changed.rowcount != 1:
                    raise conflito()
                self.repo.auditar(
                    s, None, "suspender_ativacao_indisponivel", "campanha", cid, execucao_id=eid
                )

    def _resposta(self, cid, recibo, token, *, replay, solicitada=False, admitida=False, erro=None):
        atual = self.obter(cid, recibo["execucao_id"], token)
        return {
            "recibo": recibo,
            "replay": replay,
            "ativacao": {
                "solicitada": solicitada,
                "admissao_confirmada": admitida,
                "erro_codigo": erro,
            },
            "atual": atual,
            "modo": atual["modo"],
            "simulacao": atual["simulacao"],
            "aviso": atual["aviso"],
        }

    def iniciar(self, cid, pid, data, token):
        data = IniciarExecucaoEntrada.model_validate(data)
        with self._comando():
            _, anterior = self._repeticao(cid, data, token, "iniciar_execucao:" + pid)
            if anterior is not None:
                return self._resposta(cid, anterior, token, replay=True)
            recibo = self.svc.iniciar(cid, pid, data, token)
            admitida, erro = False, None
            try:
                review = self.rt.review(cid, recibo["execucao_id"], token)
                self.rt.activate(review, token, confirmo=True)
                admitida = True
            except Exception:
                erro = "ativacao_indisponivel"
                self._suspender_nao_ativada(cid, recibo["execucao_id"])
            return self._resposta(
                cid, recibo, token, replay=False, solicitada=True, admitida=admitida, erro=erro
            )

    def pausar(self, cid, eid, data, token):
        data = PausarExecucaoEntrada.model_validate(data)
        with self._comando():
            _, anterior = self._repeticao(cid, data, token, "pausar_execucao:" + eid)
            if anterior is not None:
                return self._resposta(cid, anterior, token, replay=True)
            recibo = self.svc.pausar(cid, eid, data, token)
            self.rt.suspend(eid)
            return self._resposta(cid, recibo, token, replay=False)

    def revisao_retomada(self, cid, eid, token):
        with self._comando():
            with self.repo.transacao() as s:
                self.svc._contexto(s, token)
            r = self.rt.review(cid, eid, token)
            if r["estado"] != "pausada":
                raise conflito()
            return {
                **{k: r[k] for k in VINCULOS_RETOMADA},
                "campanha_id": cid,
                "execucao_id": eid,
                "retomavel": r["permitida"],
                "marcador_retomada": r["marcador"],
                "configuracao_execucao": r["configuracao_execucao"],
                **(
                    {"configuracao_simulacao": r["configuracao_execucao"]}
                    if r["modo"] == "mock"
                    else {}
                ),
                "avaliacao_atual": r["avaliacao_atual"],
                "pendentes": r["pendentes"],
                "elegiveis_pendentes": r["elegiveis_pendentes"],
                "modo": r["modo"],
                "impedimento_codigo": r.get("impedimento_codigo"),
                "impedido_ate": r.get("impedido_ate"),
                **(
                    {
                        "categoria": r["categoria"],
                        "bot_externo_id": r["bot_externo_id"],
                    }
                    if r["modo"] == "telegram"
                    else {}
                ),
                "simulacao": r["modo"] == "mock",
                "aviso": AVISO if r["modo"] == "mock" else AVISO_TELEGRAM,
            }

    def retomar(self, cid, eid, data, token):
        data = RetomarExecucaoEntrada.model_validate(data)
        acao = "retomar_execucao:" + eid
        with self._comando():
            digest, anterior = self._repeticao(cid, data, token, acao)
            if anterior is not None:
                return self._resposta(cid, anterior, token, replay=True)
            review = self.rt.review(cid, eid, token)
            body = data.model_dump(mode="json")
            if (
                review["estado"] != "pausada"
                or any(body[k] != review[k] for k in VINCULOS_RETOMADA)
                or not hmac.compare_digest(data.marcador_retomada, review["marcador"])
            ):
                raise conflito()
            persistidos = []

            def registrar(s, e, usuario):
                c = self.repo.obter(s, Campanha, cid)
                persistidos.append(
                    self.svc._recibo(s, data, usuario, digest, "retomar_execucao", c, e)
                )

            erro = None
            try:
                self.rt.activate(review, token, confirmo=True, registrar=registrar)
            except Exception:
                # A lista local nao prova commit; consultar o recibo transacional B1.
                _, recibo = self._repeticao(cid, data, token, acao)
                if recibo is None:
                    raise
                erro = "ativacao_indisponivel"
            else:
                recibo = persistidos[0]
            return self._resposta(
                cid, recibo, token, replay=False, solicitada=True, admitida=erro is None, erro=erro
            )

    def _execucao(self, e, contagens):
        mock = e.modo == "mock"
        result = {
            **_view(e, PUBLICOS_EXECUCAO),
            "autorizacao_id": e.autorizacao_id or e.autorizacao_telegram_id,
            "contagens": {estado: contagens.get(estado, 0) for estado in ESTADOS_ITENS},
            "operacional": self.rt.snapshot(e.id),
            "pausa_solicitada": e.estado == "pausando",
            "pausa_efetivada": e.estado == "pausada",
            "modo": e.modo,
            "simulacao": mock,
            "aviso": AVISO if mock else AVISO_TELEGRAM,
            "validade_atual": "nao_avaliada",
        }
        return result

    def obter(self, cid, eid, token):
        with self._leitura(token) as s:
            e = self.svc._obter(s, cid, eid)
            counts = dict(
                s.execute(
                    select(ItemExecucao.estado, func.count())
                    .where(ItemExecucao.execucao_id == eid)
                    .group_by(ItemExecucao.estado)
                ).all()
            )
            return self._execucao(e, counts)

    def listar(self, cid, token, pagina=1, limite=20, *, eid=None, iid=None, tipo="execucoes"):
        if not 1 <= pagina <= 1000000 or not 1 <= limite <= 50:
            raise NegocioError("validacao", "Paginacao invalida.", 422)
        with self._leitura(token) as s:
            self.repo.obter(s, Campanha, cid)
            execucao = None
            if tipo == "execucoes":
                model, filtro, ordem = (
                    ExecucaoCampanha,
                    ExecucaoCampanha.campanha_id == cid,
                    (ExecucaoCampanha.criado_em, ExecucaoCampanha.id),
                )
            else:
                execucao = self.svc._obter(s, cid, eid)
                if tipo == "itens":
                    model, filtro, ordem = (
                        ItemExecucao,
                        ItemExecucao.execucao_id == eid,
                        (ItemExecucao.ordem, ItemExecucao.id),
                    )
                else:
                    item = self.repo.obter(s, ItemExecucao, iid)
                    if item.execucao_id != eid:
                        raise NegocioError("nao_encontrado", "Item indisponivel.", 404)
                    model, filtro, ordem = (
                        TentativaEnvio,
                        TentativaEnvio.item_id == iid,
                        (TentativaEnvio.numero, TentativaEnvio.id),
                    )
            total = s.scalar(select(func.count()).select_from(model).where(filtro))
            rows = s.scalars(
                select(model)
                .where(filtro)
                .order_by(*ordem)
                .offset((pagina - 1) * limite)
                .limit(limite)
            ).all()
            if tipo == "execucoes":
                counts = {}
                if rows:
                    for eid_, estado, count in s.execute(
                        select(ItemExecucao.execucao_id, ItemExecucao.estado, func.count())
                        .where(ItemExecucao.execucao_id.in_([e.id for e in rows]))
                        .group_by(ItemExecucao.execucao_id, ItemExecucao.estado)
                    ):
                        counts.setdefault(eid_, {})[estado] = count
                items = [self._execucao(e, counts.get(e.id, {})) for e in rows]
            else:
                campos = PUBLICOS_ITEM if tipo == "itens" else PUBLICOS_TENTATIVA
                items = [_view(item, campos) for item in rows]
            mock = (
                all(row.modo == "mock" for row in rows)
                if tipo == "execucoes"
                else execucao.modo == "mock"
            )
            return {
                "itens": items,
                "total": total,
                "pagina": pagina,
                "limite": limite,
                "simulacao": mock,
                "aviso": AVISO if mock else AVISO_TELEGRAM,
            }
