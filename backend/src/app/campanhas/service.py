"""Rascunhos transacionais e revisão atual, sem autorização de despacho."""

import hashlib
import json

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import aliased

from app.campanhas.models import (
    Campanha,
    CampanhaLista,
    CampanhaSelecao,
    CampanhaSelecaoTelegram,
)
from app.consentimentos.models import (
    EscopoComunicacao,
    IdentidadeContato,
    IdentidadeTelegram,
    InscricaoConta,
    PreferenciaContato,
)
from app.contas_envio.models import ContaEnvio
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Auditoria, Contato, Lista, ListaContato, TelefoneContato
from app.contatos.normalizacao import busca
from app.contatos.repository import agora, uuid
from app.templates.models import Template, TemplateVersao
from app.templates.schemas import PreviaEntrada as TemplatePrevia

AVISO = "Revisão preliminar - esta campanha não está pronta para envio."
NAO_AVALIADAS = [
    "frequencia",
    "conexao",
    "capacidade_provedor",
    "existencia_canal",
    "inscricao_telegram",
    "alcance_autorizacao_na_conta",
]


def campanha_view(item):
    return {
        key: getattr(item, key)
        for key in (
            "id",
            "nome",
            "conta_envio_id",
            "escopo_id",
            "template_versao_id",
            "categoria",
            "status",
            "versao",
            "criado_em",
            "atualizado_em",
        )
    }


class CampanhasService:
    def __init__(self, repository, auth, consentimentos, templates):
        self.repo, self.auth = repository, auth
        self.consentimentos, self.templates = consentimentos, templates

    def _referencias(self, s, values, listas):
        # Projeção pública: nunca consulta segredo_ref nem o armazenamento DPAPI.
        conta = (
            s.execute(
                select(
                    ContaEnvio.id,
                    ContaEnvio.nome,
                    ContaEnvio.plataforma,
                    ContaEnvio.provedor,
                    ContaEnvio.ativo,
                    ContaEnvio.removido_em,
                ).where(ContaEnvio.id == values["conta_envio_id"])
            )
            .mappings()
            .one_or_none()
        )
        version = (
            s.execute(
                select(
                    TemplateVersao.id,
                    TemplateVersao.plataforma,
                    TemplateVersao.numero_versao,
                    Template.nome,
                    Template.categoria,
                    Template.ativo,
                )
                .join(Template, Template.id == TemplateVersao.template_id)
                .where(TemplateVersao.id == values["template_versao_id"])
            )
            .mappings()
            .one_or_none()
        )
        scope = s.get(EscopoComunicacao, values["escopo_id"])
        rows = s.scalars(select(Lista).where(Lista.id.in_(listas)).order_by(Lista.id)).all()
        if conta is None or version is None or scope is None or len(rows) != len(listas):
            raise NegocioError("referencia_invalida", "Uma referência não está disponível.", 409)
        problemas = []
        if not conta["ativo"] or conta["removido_em"]:
            problemas.append("conta_inativa")
        if not version["ativo"]:
            problemas.append("template_arquivado")
        if (
            conta["plataforma"] != version["plataforma"]
            or values["categoria"] != version["categoria"]
        ):
            problemas.append("plataforma_ou_categoria_incompativel")
        if any(not row.ativo for row in rows):
            problemas.append("lista_arquivada")
        return {
            "conta": {k: conta[k] for k in ("id", "nome", "plataforma", "provedor", "ativo")},
            "template": dict(version),
            "escopo": {"id": scope.id, "nome": scope.nome},
            "listas": [{"id": row.id, "nome": row.nome, "ativo": bool(row.ativo)} for row in rows],
            "problemas": problemas,
        }

    def _listas(self, s, identifier):
        return list(
            s.scalars(
                select(CampanhaLista.lista_id)
                .where(CampanhaLista.campanha_id == identifier)
                .order_by(CampanhaLista.lista_id)
            )
        )

    def _validar(self, s, values, listas):
        refs = self._referencias(s, values, listas)
        if refs["problemas"]:
            raise NegocioError(
                "referencia_invalida", "Revise as referências ativas e compatíveis.", 409
            )
        return refs

    def listar(self, pagina=1, limite=20, q=""):
        with self.repo.transacao() as s:
            filters = [Campanha.nome_busca.contains(busca(q.strip()), autoescape=True)] if q else []
            query = select(Campanha).where(*filters)
            total = s.scalar(select(func.count()).select_from(query.subquery()))
            rows = s.scalars(
                query.order_by(Campanha.nome_busca, Campanha.id)
                .offset((pagina - 1) * limite)
                .limit(limite)
            )
            return {"itens": [campanha_view(i) for i in rows], "total": total}

    def obter(self, identifier):
        with self.repo.transacao() as s:
            item = self.repo.obter(s, Campanha, identifier)
            values = campanha_view(item)
            listas = self._listas(s, identifier)
            return {
                **values,
                "listas": listas,
                "referencias": self._referencias(s, values, listas),
                "aviso": AVISO,
                "condicoes_nao_avaliadas": NAO_AVALIADAS,
            }

    def opcoes(self, tipo, pagina=1, limite=20, q=""):
        # Catálogo mínimo para operador escolher referências sem administrar contas.
        if tipo == "contas":
            query = select(
                ContaEnvio.id, ContaEnvio.nome, ContaEnvio.plataforma, ContaEnvio.provedor
            ).where(ContaEnvio.ativo == 1, ContaEnvio.removido_em.is_(None))
            name = ContaEnvio.nome_busca
        elif tipo == "listas":
            query = select(Lista.id, Lista.nome).where(Lista.ativo == 1)
            name = Lista.nome_normalizado
        elif tipo == "escopos":
            query = select(EscopoComunicacao.id, EscopoComunicacao.nome)
            name = EscopoComunicacao.nome
        else:
            query = (
                select(
                    TemplateVersao.id,
                    Template.nome,
                    Template.categoria,
                    TemplateVersao.plataforma,
                    TemplateVersao.numero_versao,
                )
                .join(Template, Template.id == TemplateVersao.template_id)
                .where(Template.ativo == 1)
            )
            name = Template.nome_busca
        if q:
            query = query.where(
                name.contains(q.strip() if tipo == "escopos" else busca(q.strip()), autoescape=True)
            )
        with self.repo.transacao() as s:
            total = s.scalar(select(func.count()).select_from(query.subquery()))
            rows = s.execute(
                query.order_by(name, query.selected_columns.id)
                .offset((pagina - 1) * limite)
                .limit(limite)
            ).mappings()
            return {"itens": [dict(i) for i in rows], "total": total}

    def _repeticao(self, s, data, token, acao, identifier):
        usuario = self.auth.current(token, session=s).usuario.id
        payload = {
            "acao": acao,
            "campanha_id": identifier,
            "usuario_id": usuario,
            "entrada": data.model_dump(mode="json", exclude_unset=True),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        ).hexdigest()
        old = s.get(Auditoria, str(data.operacao_id))
        if old:
            summary = json.loads(old.resumo_json)
            if old.entidade != "campanha" or summary.get("requisicao_hash") != digest:
                raise conflito()
            return usuario, digest, summary["resultado"]
        return usuario, digest, None

    def _registrar(self, s, data, usuario, digest, acao, identifier, versao, **detalhes):
        result = {"id": identifier, "versao": versao, **detalhes}
        summary = {"requisicao_hash": digest, "resultado": result}
        if acao == "selecionar":
            summary.update(
                identidade_contato_id=str(data.identidade_contato_id), selecionada=data.selecionada
            )
        s.add(
            Auditoria(
                id=str(data.operacao_id),
                usuario_id=usuario,
                acao=acao,
                entidade="campanha",
                entidade_id=identifier,
                ocorrido_em=agora(),
                resumo_json=json.dumps(summary),
            )
        )
        s.flush()
        return result

    def _trocar_listas(self, s, identifier, listas):
        s.execute(delete(CampanhaLista).where(CampanhaLista.campanha_id == identifier))
        s.add_all(CampanhaLista(campanha_id=identifier, lista_id=i) for i in listas)

    def criar(self, data, token):
        with self.repo.transacao() as s:
            user, digest, old = self._repeticao(s, data, token, "criar", None)
            if old is not None:
                return old
            values = data.model_dump(mode="json", exclude={"operacao_id", "listas"})
            listas = [str(i) for i in data.listas]
            self._validar(s, values, listas)
            stamp = agora()
            item = Campanha(
                **values,
                id=uuid(),
                nome_busca=busca(values["nome"]),
                status="rascunho",
                versao=1,
                criado_por_usuario_id=user,
                criado_em=stamp,
                atualizado_em=stamp,
            )
            s.add(item)
            s.flush()
            self._trocar_listas(s, item.id, listas)
            return self._registrar(s, data, user, digest, "criar", item.id, 1)

    def editar(self, identifier, data, token):
        with self.repo.transacao() as s:
            user, digest, old = self._repeticao(s, data, token, "editar", identifier)
            if old is not None:
                return old
            item = self.repo.obter(s, Campanha, identifier)
            if item.status != "rascunho":
                raise conflito()
            fields = data.model_dump(
                mode="json", exclude_unset=True, exclude={"operacao_id", "versao", "listas"}
            )
            values = {**campanha_view(item), **fields}
            listas = (
                [str(i) for i in data.listas]
                if data.listas is not None
                else self._listas(s, identifier)
            )
            self._validar(s, values, listas)
            if "nome" in fields:
                fields["nome_busca"] = busca(fields["nome"])
            changed = s.execute(
                update(Campanha)
                .where(Campanha.id == identifier, Campanha.versao == data.versao)
                .values(**fields, versao=data.versao + 1, atualizado_em=agora())
            )
            if changed.rowcount != 1:
                raise conflito()
            if data.listas is not None:
                self._trocar_listas(s, identifier, listas)
            return self._registrar(s, data, user, digest, "editar", identifier, data.versao + 1)

    def _pertence(self, identifier, ativa=True):
        query = select(ListaContato.contato_id).join(
            CampanhaLista, CampanhaLista.lista_id == ListaContato.lista_id
        )
        if ativa:
            query = query.join(Lista, Lista.id == ListaContato.lista_id).where(Lista.ativo == 1)
        return query.where(
            CampanhaLista.campanha_id == identifier,
            ListaContato.contato_id == IdentidadeContato.contato_id,
        ).exists()

    def _pertence_telegram(self, identifier, ativa=True):
        query = select(ListaContato.contato_id).join(
            CampanhaLista, CampanhaLista.lista_id == ListaContato.lista_id
        )
        if ativa:
            query = query.join(Lista, Lista.id == ListaContato.lista_id).where(Lista.ativo == 1)
        return query.where(
            CampanhaLista.campanha_id == identifier,
            ListaContato.contato_id == IdentidadeTelegram.contato_id,
        ).exists()

    def selecionar(self, identifier, data, token):
        with self.repo.transacao() as s:
            user, digest, old = self._repeticao(s, data, token, "selecionar", identifier)
            if old is not None:
                return old
            item = self.repo.obter(s, Campanha, identifier)
            if item.status != "rascunho":
                raise conflito()
            telegram = data.identidade_telegram_id is not None
            identity_id = str(
                data.identidade_telegram_id if telegram else data.identidade_contato_id
            )
            model = IdentidadeTelegram if telegram else IdentidadeContato
            identity = self.repo.obter(s, model, identity_id)
            subscription = None
            if data.selecionada:
                refs = self._validar(s, campanha_view(item), self._listas(s, identifier))
                if telegram:
                    if s.scalar(
                        select(CampanhaSelecao.identidade_contato_id)
                        .where(CampanhaSelecao.campanha_id == identifier)
                        .limit(1)
                    ):
                        raise NegocioError(
                            "selecao_incompativel",
                            "Remova identidades de outro canal antes de escolher "
                            "o destino Telegram.",
                            409,
                        )
                    subscription = s.scalar(
                        select(InscricaoConta).where(
                            InscricaoConta.conta_envio_id == item.conta_envio_id,
                            InscricaoConta.identidade_telegram_id == identity_id,
                            InscricaoConta.estado == "iniciada",
                        )
                    )
                    member = s.scalar(
                        select(ListaContato.contato_id)
                        .join(Lista, Lista.id == ListaContato.lista_id)
                        .join(CampanhaLista, CampanhaLista.lista_id == ListaContato.lista_id)
                        .where(
                            CampanhaLista.campanha_id == identifier,
                            ListaContato.contato_id == identity.contato_id,
                            Lista.ativo == 1,
                        )
                        .limit(1)
                    )
                    if (
                        (
                            refs["conta"]["plataforma"],
                            refs["conta"]["provedor"],
                        )
                        != ("telegram", "telegram_bot_api")
                        or subscription is None
                        or member is None
                    ):
                        raise NegocioError(
                            "inscricao_telegram_invalida",
                            "Destino sem inscrição vigente na conta Telegram da campanha.",
                            409,
                        )
                else:
                    candidate = s.scalar(
                        select(IdentidadeContato.id).where(
                            IdentidadeContato.id == identity_id,
                            self._pertence(identifier),
                            IdentidadeContato.canal == refs["conta"]["plataforma"],
                        )
                    )
                    if candidate is None:
                        raise NegocioError(
                            "fora_das_listas", "Identidade fora dos candidatos atuais.", 409
                        )
            changed = s.execute(
                update(Campanha)
                .where(Campanha.id == identifier, Campanha.versao == data.versao)
                .values(versao=data.versao + 1, atualizado_em=agora())
            )
            if changed.rowcount != 1:
                raise conflito()
            if telegram:
                selection = s.get(CampanhaSelecaoTelegram, (identifier, identity_id))
                if data.selecionada and selection is None:
                    s.add(
                        CampanhaSelecaoTelegram(
                            campanha_id=identifier,
                            conta_envio_id=item.conta_envio_id,
                            identidade_telegram_id=identity_id,
                            inscricao_id=subscription.id,
                            selecionado_em=agora(),
                        )
                    )
                elif not data.selecionada and selection is not None:
                    s.delete(selection)
            else:
                selection = s.get(CampanhaSelecao, (identifier, identity_id))
                if data.selecionada and selection is None:
                    s.add(
                        CampanhaSelecao(
                            campanha_id=identifier,
                            identidade_contato_id=identity_id,
                            selecionado_em=agora(),
                        )
                    )
                elif not data.selecionada and selection is not None:
                    s.delete(selection)
            return self._registrar(s, data, user, digest, "selecionar", identifier, data.versao + 1)

    def selecionar_elegiveis(self, identifier, data, token):
        with self.repo.transacao() as s:
            user, digest, old = self._repeticao(s, data, token, "selecionar_elegiveis", identifier)
            if old is not None:
                return old
            item = self.repo.obter(s, Campanha, identifier)
            if item.status != "rascunho":
                raise conflito()
            refs = self._validar(s, campanha_view(item), self._listas(s, identifier))
            stamp = agora()
            selecionados = []
            telegram = refs["conta"]["plataforma"] == "telegram"
            if telegram:
                account = self.repo.obter(s, ContaEnvio, item.conta_envio_id)
                rows = s.execute(
                    select(IdentidadeTelegram, Contato, InscricaoConta)
                    .join(Contato, Contato.id == IdentidadeTelegram.contato_id)
                    .join(
                        InscricaoConta,
                        and_(
                            InscricaoConta.identidade_telegram_id == IdentidadeTelegram.id,
                            InscricaoConta.conta_envio_id == item.conta_envio_id,
                            InscricaoConta.estado == "iniciada",
                        ),
                    )
                    .where(self._pertence_telegram(identifier))
                    .order_by(IdentidadeTelegram.id)
                    .limit(10001)
                ).all()
                if len(rows) > 10000:
                    raise NegocioError("volume_excessivo", "A campanha excede 10.000 identidades.")
                for identity, contact, subscription in rows:
                    avaliacao = self.consentimentos._avaliar_telegram(
                        s,
                        account,
                        identity,
                        contact,
                        subscription,
                        item.escopo_id,
                        item.categoria,
                    )
                    if avaliacao["elegivel_consentimento"]:
                        selecionados.append(
                            CampanhaSelecaoTelegram(
                                campanha_id=identifier,
                                conta_envio_id=item.conta_envio_id,
                                identidade_telegram_id=identity.id,
                                inscricao_id=subscription.id,
                                selecionado_em=stamp,
                            )
                        )
                s.execute(
                    delete(CampanhaSelecaoTelegram).where(
                        CampanhaSelecaoTelegram.campanha_id == identifier
                    )
                )
                s.execute(delete(CampanhaSelecao).where(CampanhaSelecao.campanha_id == identifier))
            else:
                rows = s.execute(
                    select(
                        IdentidadeContato,
                        Contato,
                        TelefoneContato,
                        PreferenciaContato,
                    )
                    .join(Contato, Contato.id == IdentidadeContato.contato_id)
                    .join(TelefoneContato, TelefoneContato.id == IdentidadeContato.telefone_id)
                    .outerjoin(
                        PreferenciaContato,
                        and_(
                            PreferenciaContato.identidade_contato_id == IdentidadeContato.id,
                            PreferenciaContato.escopo_id == item.escopo_id,
                            PreferenciaContato.categoria == item.categoria,
                        ),
                    )
                    .where(
                        self._pertence(identifier),
                        IdentidadeContato.canal == refs["conta"]["plataforma"],
                    )
                    .order_by(IdentidadeContato.id)
                    .limit(10001)
                ).all()
                if len(rows) > 10000:
                    raise NegocioError("volume_excessivo", "A campanha excede 10.000 identidades.")
                for identity, contact, phone, preference in rows:
                    avaliacao = self.consentimentos.avaliar_dados(
                        contact, identity, phone, preference
                    )
                    if avaliacao["elegivel_consentimento"]:
                        selecionados.append(
                            CampanhaSelecao(
                                campanha_id=identifier,
                                identidade_contato_id=identity.id,
                                selecionado_em=stamp,
                            )
                        )
                s.execute(delete(CampanhaSelecao).where(CampanhaSelecao.campanha_id == identifier))
                s.execute(
                    delete(CampanhaSelecaoTelegram).where(
                        CampanhaSelecaoTelegram.campanha_id == identifier
                    )
                )
            changed = s.execute(
                update(Campanha)
                .where(Campanha.id == identifier, Campanha.versao == data.versao)
                .values(versao=data.versao + 1, atualizado_em=stamp)
            )
            if changed.rowcount != 1:
                raise conflito()
            s.add_all(selecionados)
            return self._registrar(
                s,
                data,
                user,
                digest,
                "selecionar_elegiveis",
                identifier,
                data.versao + 1,
                selecionados=len(selecionados),
            )

    def revisao(self, identifier, pagina=1, limite=20, somente_selecionadas=False):
        with self.repo.transacao() as s:
            item = self.repo.obter(s, Campanha, identifier)
            refs = self._referencias(s, campanha_view(item), self._listas(s, identifier))
            legacy = s.scalar(
                select(CampanhaSelecao.identidade_contato_id)
                .where(CampanhaSelecao.campanha_id == identifier)
                .limit(1)
            )
            if refs["conta"]["plataforma"] == "telegram" and legacy is None:
                return self._revisao_telegram(s, item, refs, pagina, limite, somente_selecionadas)
            selected = (
                select(CampanhaSelecao.identidade_contato_id)
                .where(
                    CampanhaSelecao.campanha_id == identifier,
                    CampanhaSelecao.identidade_contato_id == IdentidadeContato.id,
                )
                .exists()
            )
            belongs = self._pertence(identifier, ativa=False)
            candidate = and_(
                self._pertence(identifier), IdentidadeContato.canal == refs["conta"]["plataforma"]
            )
            included = selected if somente_selecionadas else or_(candidate, selected)
            total = s.scalar(select(func.count()).select_from(IdentidadeContato).where(included))
            rows = s.execute(
                select(
                    IdentidadeContato,
                    Contato,
                    TelefoneContato,
                    PreferenciaContato,
                    selected.label("selecionada"),
                    belongs.label("pertence"),
                    candidate.label("candidata"),
                )
                .join(Contato, Contato.id == IdentidadeContato.contato_id)
                .join(TelefoneContato, TelefoneContato.id == IdentidadeContato.telefone_id)
                .outerjoin(
                    PreferenciaContato,
                    and_(
                        PreferenciaContato.identidade_contato_id == IdentidadeContato.id,
                        PreferenciaContato.escopo_id == item.escopo_id,
                        PreferenciaContato.categoria == item.categoria,
                    ),
                )
                .where(included)
                .order_by(Contato.nome_busca, Contato.id, IdentidadeContato.id)
                .offset((pagina - 1) * limite)
                .limit(limite)
            ).all()
            counts = dict(
                s.execute(
                    select(IdentidadeContato.contato_id, func.count())
                    .where(IdentidadeContato.contato_id.in_([row[1].id for row in rows]))
                    .group_by(IdentidadeContato.contato_id)
                ).all()
            )
            stamp = agora()
            itens = []
            for identity, contact, phone, preference, sel, member, cand in rows:
                itens.append(
                    {
                        "identidade_contato_id": identity.id,
                        "contato_id": contact.id,
                        "nome": contact.nome,
                        "telefone": phone.telefone_e164,
                        "identidades_do_contato": counts[contact.id],
                        "canal": identity.canal,
                        "selecionada": bool(sel),
                        "pertence_listas": bool(member),
                        "candidata_atual": bool(cand),
                        "consentimento": self.consentimentos.avaliar_dados(
                            contact, identity, phone, preference
                        ),
                    }
                )
            return {
                "itens": itens,
                "total": total,
                "versao": item.versao,
                "avaliado_em": stamp,
                "referencias": refs,
                "aviso": AVISO,
                "condicoes_nao_avaliadas": NAO_AVALIADAS,
            }

    def _revisao_telegram(self, s, item, refs, pagina, limite, somente_selecionadas):
        selected = (
            select(CampanhaSelecaoTelegram.identidade_telegram_id)
            .where(
                CampanhaSelecaoTelegram.campanha_id == item.id,
                CampanhaSelecaoTelegram.identidade_telegram_id == IdentidadeTelegram.id,
            )
            .exists()
        )
        belongs = self._pertence_telegram(item.id, ativa=False)
        active_subscription = aliased(InscricaoConta)
        candidate = and_(
            self._pertence_telegram(item.id),
            select(active_subscription.id)
            .where(
                active_subscription.conta_envio_id == item.conta_envio_id,
                active_subscription.identidade_telegram_id == IdentidadeTelegram.id,
                active_subscription.estado == "iniciada",
            )
            .exists(),
        )
        included = selected if somente_selecionadas else or_(candidate, selected)
        total = s.scalar(select(func.count()).select_from(IdentidadeTelegram).where(included))
        rows = s.execute(
            select(
                IdentidadeTelegram,
                Contato,
                InscricaoConta,
                selected.label("selecionada"),
                belongs.label("pertence"),
                candidate.label("candidata"),
            )
            .join(Contato, Contato.id == IdentidadeTelegram.contato_id)
            .outerjoin(
                InscricaoConta,
                and_(
                    InscricaoConta.identidade_telegram_id == IdentidadeTelegram.id,
                    InscricaoConta.conta_envio_id == item.conta_envio_id,
                ),
            )
            .where(included)
            .order_by(Contato.nome_busca, Contato.id, IdentidadeTelegram.id)
            .offset((pagina - 1) * limite)
            .limit(limite)
        ).all()
        account = self.repo.obter(s, ContaEnvio, item.conta_envio_id)
        itens = []
        for identity, contact, subscription, sel, member, cand in rows:
            eligibility = self.consentimentos._avaliar_telegram(
                s,
                account,
                identity,
                contact,
                subscription,
                item.escopo_id,
                item.categoria,
            )
            itens.append(
                {
                    "identidade_contato_id": None,
                    "identidade_telegram_id": identity.id,
                    "inscricao_id": subscription.id if subscription else None,
                    "contato_id": contact.id,
                    "nome": contact.nome,
                    "telefone": None,
                    "destino_mascarado": "***" + identity.chat_externo_id[-4:],
                    "canal": "telegram",
                    "selecionada": bool(sel),
                    "pertence_listas": bool(member),
                    "candidata_atual": bool(cand),
                    "consentimento": {
                        key: eligibility[key]
                        for key in ("estado", "elegivel_consentimento", "avaliacao")
                    },
                    "motivos": eligibility["motivos"],
                }
            )
        return {
            "itens": itens,
            "total": total,
            "versao": item.versao,
            "avaliado_em": agora(),
            "referencias": refs,
            "aviso": AVISO,
            "condicoes_nao_avaliadas": [
                value
                for value in NAO_AVALIADAS
                if value not in ("inscricao_telegram", "alcance_autorizacao_na_conta")
            ],
        }

    def previa(self, identifier, data):
        with self.repo.transacao() as s:
            item = self.repo.obter(s, Campanha, identifier)
            if item.versao != data.versao:
                raise conflito()
            self._validar(s, campanha_view(item), self._listas(s, identifier))
            version = self.repo.obter(s, TemplateVersao, item.template_versao_id)
            result = self.templates.previa(
                TemplatePrevia(
                    plataforma=version.plataforma,
                    conteudo=version.conteudo,
                    valores={"nome": data.nome} if data.nome is not None else {},
                )
            )
            return {**result, "transitoria": True, "aviso": AVISO}
