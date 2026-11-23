"""Decisoes locais de simulacao; sem executor, provedor ou acesso a credenciais."""

import hashlib
import hmac
import json
import secrets
from collections import Counter

from sqlalchemy import func, select

from app.campanhas.configuracao_simulacao import ConfiguracaoSimulacao
from app.campanhas.crypto import GCM, indisponivel
from app.campanhas.models import AutorizacaoSimulacao, Campanha, PreparacaoItem
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Auditoria
from app.contatos.repository import agora, uuid
from app.persistencia.models import utc_text

AVISO = "Autorizacao somente para simulacao. Processamento indisponivel; sem envio externo."


def revogar_decisao(item, usuario, motivo):
    if item.estado != "vigente":
        raise conflito()
    item.estado, item.versao = "revogada", item.versao + 1
    item.revogado_por_usuario_id = usuario
    item.revogado_em = max(agora(), item.autorizado_em)
    item.motivo_codigo = motivo


def revogar_para_rascunho(repo, session, campanha_id, preparacao_id, usuario):
    item = session.scalar(
        select(AutorizacaoSimulacao).where(
            AutorizacaoSimulacao.campanha_id == campanha_id,
            AutorizacaoSimulacao.preparacao_id == preparacao_id,
            AutorizacaoSimulacao.estado == "vigente",
        )
    )
    if item is not None:
        revogar_decisao(item, usuario, "retorno_rascunho")
        session.flush()
        repo.auditar(
            session,
            usuario,
            "revogar_simulacao",
            "campanha",
            campanha_id,
            autorizacao_id=item.id,
            preparacao_id=preparacao_id,
            versao_autorizacao=item.versao,
            motivo_codigo=item.motivo_codigo,
        )
        # O trigger da preparacao exige a revogacao escrita ANTES da invalidacao.
        session.flush()


class AutorizacoesSimulacaoService:
    def __init__(self, preparacoes):
        self.p, self.c = preparacoes, preparacoes.c
        self.repo, self.auth = preparacoes.repo, preparacoes.auth
        # Marcador exclusivo desta abertura; nao altera a criptografia da fotografia.
        self._chave_marcador = secrets.token_bytes(32)

    def _usuario(self, session, token):
        user = self.auth.current(token, session=session).usuario
        # Mesmos perfis que operam campanhas; sem nova matriz de permissoes.
        if user.perfil not in {"administrador", "operador"}:
            raise NegocioError("acesso_negado", "Perfil sem permissao para operar campanhas.", 403)
        return user.id

    def _avaliar(self, session, campanha, prep, *, impedidos=None):
        try:
            ctx = self.p._ctx(session)
        except NegocioError:
            raise indisponivel() from None
        if not ctx.conferido:
            raise indisponivel()
        from app.campanhas.preparacoes import abrir

        with GCM(abrir(ctx)) as root:
            header, key, _ = self.p._autenticar_fotografia(session, prep, ctx, root)
        del key
        if (
            header["provedor"] != "mock"
            or header["plataforma"] != "whatsapp"
            or "configuracao_simulacao" not in header
        ):
            raise NegocioError(
                "configuracao_incompativel", "Somente preparacao mock/whatsapp e autorizavel.", 409
            )
        # _autenticar_fotografia ja valida semantica e vinculo do cabecalho completo.
        cfg = ConfiguracaoSimulacao.model_validate(header["configuracao_simulacao"])
        try:
            referencias_alteradas = any(
                header[field] != getattr(campanha, field)
                for field in ("conta_envio_id", "escopo_id", "template_versao_id", "categoria")
            )
            refs = self.c._referencias(session, header, header["listas"])
            if (
                refs["conta"]["id"] != cfg.conta_envio_id
                or refs["conta"]["provedor"] != cfg.provedor
                or refs["conta"]["plataforma"] != cfg.plataforma
            ):
                raise ValueError
        except ValueError, KeyError, TypeError:
            raise NegocioError(
                "integridade_comprometida", "Vinculos da fotografia incompativeis.", 503
            ) from None
        impedimentos = list(refs["problemas"])
        if referencias_alteradas:
            impedimentos.append("referencias_campanha_alteradas")
        if campanha.status != "preparada" or prep.invalidado_em is not None:
            impedimentos.append("preparacao_invalidada")
        if prep.confirmado_em is None:
            impedimentos.append("preparacao_nao_confirmada")
        motivos, elegiveis = Counter(), 0
        rows = session.scalars(
            select(PreparacaoItem)
            .where(PreparacaoItem.preparacao_id == prep.id, PreparacaoItem.incluido == 1)
            .order_by(PreparacaoItem.identidade_contato_id)
            .execution_options(yield_per=250)
        )
        for batch in rows.partitions(250):
            atuais = self.p._atuais(session, header, batch)
            for item in batch:
                reasons = atuais.get(item.identidade_contato_id, {}).get(
                    "impedimentos_atuais", ["identidade_indisponivel"]
                )
                if reasons:
                    motivos.update(set(reasons))
                    if impedidos is not None:
                        impedidos[item.identidade_contato_id] = sorted(set(reasons))
                else:
                    elegiveis += 1
        if not elegiveis:
            impedimentos.append("sem_destinatarios_elegiveis")
        return cfg, {
            "integridade": "verificada",
            "incluidos": prep.incluidos,
            "excluidos": prep.excluidos,
            "elegiveis_atuais": elegiveis,
            "impedidos_atuais": prep.incluidos - elegiveis,
            "impedimentos": sorted(set(impedimentos) | set(motivos)),
            "motivos_destinatarios": [
                {"codigo": code, "quantidade": count} for code, count in sorted(motivos.items())
            ],
            "condicoes_atuais_atendidas": not impedimentos and elegiveis > 0,
        }

    def _marcador(self, session, campanha, prep, cfg, usuario):
        payload = {
            "finalidade": "autorizacao-simulacao-v1",
            "abertura": self.p.abertura,
            "usuario_id": usuario,
            "campanha_id": campanha.id,
            "versao": campanha.versao,
            "preparacao_id": prep.id,
            "base_id": prep.base_id,
            "impressao": prep.impressao,
            "resumo_configuracao": cfg.resumo_configuracao,
            "confirmado_em": utc_text(prep.confirmado_em),
            "revisao": self.repo.revisao(session),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        return "autorizacao-v1." + hmac.new(self._chave_marcador, raw, hashlib.sha256).hexdigest()

    def revisao(self, campanha_id, preparacao_id, token):
        with self.repo.transacao() as session:
            usuario = self._usuario(session, token)
            campanha = self.repo.obter(session, Campanha, campanha_id)
            prep = self.p._obter(session, campanha_id, preparacao_id)
            cfg, atual = self._avaliar(session, campanha, prep)
            if (
                campanha.status != "preparada"
                or prep.invalidado_em is not None
                or prep.confirmado_em is None
                or session.scalar(
                    select(AutorizacaoSimulacao.id).where(
                        AutorizacaoSimulacao.preparacao_id == prep.id
                    )
                )
                is not None
            ):
                raise conflito()
            result = {
                "id": campanha.id,
                "preparacao_id": prep.id,
                "versao": campanha.versao,
                "impressao": prep.impressao,
                "resumo_configuracao": cfg.resumo_configuracao,
                "marcador_autorizacao": self._marcador(session, campanha, prep, cfg, usuario),
                "autorizavel": atual["condicoes_atuais_atendidas"],
                "avaliacao_atual": atual,
                "processamento_disponivel": False,
                "aviso": AVISO,
            }
            self._usuario(session, token)
            return result

    def _recibo(self, session, data, usuario, digest, acao, campanha, item):
        result = {
            "id": campanha.id,
            "versao": campanha.versao,
            "autorizacao_id": item.id,
            "versao_autorizacao": item.versao,
            "preparacao_id": item.preparacao_id,
            "estado_decisao": item.estado,
            "recibo_historico": True,
            "validade_atual": "nao_avaliada",
            "processamento_disponivel": False,
        }
        session.add(
            Auditoria(
                id=str(data.operacao_id),
                usuario_id=usuario,
                acao=acao,
                entidade="campanha",
                entidade_id=campanha.id,
                ocorrido_em=agora(),
                resumo_json=json.dumps({"requisicao_hash": digest, "resultado": result}),
            )
        )
        session.flush()
        return result

    def autorizar(self, campanha_id, preparacao_id, data, token):
        with self.repo.transacao() as session:
            usuario = self._usuario(session, token)
            _, digest, old = self.c._repeticao(
                session, data, token, "autorizar_simulacao:" + preparacao_id, campanha_id
            )
            if old is not None:
                return old
            campanha = self.repo.obter(session, Campanha, campanha_id)
            prep = self.p._obter(session, campanha_id, preparacao_id)
            cfg, atual = self._avaliar(session, campanha, prep)
            if (
                campanha.versao != data.versao
                or prep.confirmado_em is None
                or prep.invalidado_em is not None
                or campanha.status != "preparada"
                or prep.impressao != data.impressao
                or cfg.resumo_configuracao != data.resumo_configuracao
                or not hmac.compare_digest(
                    self._marcador(session, campanha, prep, cfg, usuario), data.marcador_autorizacao
                )
                or session.scalar(
                    select(AutorizacaoSimulacao.id).where(
                        AutorizacaoSimulacao.preparacao_id == prep.id
                    )
                )
                is not None
            ):
                raise conflito()
            if not atual["condicoes_atuais_atendidas"]:
                raise NegocioError(
                    "autorizacao_indisponivel", "Revise os impedimentos atuais da simulacao.", 409
                )
            item = AutorizacaoSimulacao(
                id=uuid(),
                campanha_id=campanha.id,
                preparacao_id=prep.id,
                base_id=prep.base_id,
                impressao=prep.impressao,
                resumo_configuracao=cfg.resumo_configuracao,
                autorizado_por_usuario_id=usuario,
                autorizado_em=max(agora(), prep.confirmado_em),
                estado="vigente",
                versao=1,
            )
            session.add(item)
            campanha.versao += 1
            campanha.atualizado_em = max(agora(), campanha.atualizado_em)
            session.flush()
            result = self._recibo(
                session, data, usuario, digest, "autorizar_simulacao", campanha, item
            )
            self._usuario(session, token)
            return result

    def _obter(self, session, campanha_id, autorizacao_id):
        item = self.repo.obter(session, AutorizacaoSimulacao, autorizacao_id)
        if item.campanha_id != campanha_id:
            raise NegocioError("nao_encontrado", "Autorizacao indisponivel.", 404)
        return item

    def revogar(self, campanha_id, autorizacao_id, data, token):
        with self.repo.transacao() as session:
            usuario = self._usuario(session, token)
            _, digest, old = self.c._repeticao(
                session, data, token, "revogar_simulacao:" + autorizacao_id, campanha_id
            )
            if old is not None:
                return old
            campanha = self.repo.obter(session, Campanha, campanha_id)
            item = self._obter(session, campanha_id, autorizacao_id)
            if item.versao != data.versao:
                raise conflito()
            revogar_decisao(item, usuario, data.motivo_codigo)
            campanha.versao += 1
            campanha.atualizado_em = max(agora(), campanha.atualizado_em)
            session.flush()
            result = self._recibo(
                session, data, usuario, digest, "revogar_simulacao", campanha, item
            )
            self._usuario(session, token)
            return result

    def _view(self, session, campanha, item):
        result = {
            field: getattr(item, field)
            for field in (
                "id",
                "campanha_id",
                "preparacao_id",
                "base_id",
                "impressao",
                "resumo_configuracao",
                "autorizado_por_usuario_id",
                "autorizado_em",
                "estado",
                "versao",
                "revogado_por_usuario_id",
                "revogado_em",
                "motivo_codigo",
            )
        }
        try:
            prep = self.p._obter(session, campanha.id, item.preparacao_id)
            cfg, atual = self._avaliar(session, campanha, prep)
            if (
                item.base_id != prep.base_id
                or item.impressao != prep.impressao
                or item.resumo_configuracao != cfg.resumo_configuracao
            ):
                atual["impedimentos"].append("vinculo_divergente")
                atual["condicoes_atuais_atendidas"] = False
            if item.estado != "vigente":
                atual["impedimentos"].append("autorizacao_revogada")
                atual["condicoes_atuais_atendidas"] = False
            atual["avaliada"] = True
        except NegocioError as error:
            atual = {
                "avaliada": False,
                "condicoes_atuais_atendidas": False,
                "integridade": "comprometida"
                if error.codigo == "integridade_comprometida"
                else "nao_verificada",
                "impedimentos": [error.codigo],
            }
        result.update(
            validade_atual={**atual, "avaliado_em": agora()},
            processamento_disponivel=False,
            aviso=AVISO,
        )
        return result

    def consultar(self, campanha_id, token, autorizacao_id=None, pagina=1, limite=20):
        if not 1 <= pagina <= 1000000 or not 1 <= limite <= 50:
            raise NegocioError("validacao", "Paginacao invalida.", 422)
        with self.repo.transacao() as session:
            self._usuario(session, token)
            campanha = self.repo.obter(session, Campanha, campanha_id)
            if autorizacao_id is not None:
                result = self._view(
                    session, campanha, self._obter(session, campanha_id, autorizacao_id)
                )
            else:
                query = select(AutorizacaoSimulacao).where(
                    AutorizacaoSimulacao.campanha_id == campanha_id
                )
                total = session.scalar(select(func.count()).select_from(query.subquery()))
                items = session.scalars(
                    query.order_by(
                        AutorizacaoSimulacao.autorizado_em.desc(), AutorizacaoSimulacao.id.desc()
                    )
                    .offset((pagina - 1) * limite)
                    .limit(limite)
                )
                result = {
                    "itens": [self._view(session, campanha, item) for item in items],
                    "pagina": pagina,
                    "limite": limite,
                    "total": total,
                    "processamento_disponivel": False,
                }
            self._usuario(session, token)
            return result
