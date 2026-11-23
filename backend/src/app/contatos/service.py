"""Cadastro, busca e arquivamento; sem dependência de canais."""

import re

from sqlalchemy import and_, case, func, or_, select, update

from app.consentimentos.models import (
    EscopoComunicacao,
    IdentidadeContato,
    IdentidadeTelegram,
    PreferenciaContato,
)
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Contato, ListaContato, TelefoneContato
from app.contatos.normalizacao import busca, telefone, texto
from app.contatos.repository import agora, uuid


def registro(item):
    return dict(
        id=item.id,
        ativo=bool(item.ativo),
        versao=item.versao,
        criado_em=item.criado_em,
        atualizado_em=item.atualizado_em,
    )


def telefone_view(item):
    return {**registro(item), "telefone_e164": item.telefone_e164, "contato_id": item.contato_id}


def modificar(session, model, item, versao, valores):
    if item.versao != versao:
        raise conflito()
    changes = {k: v for k, v in valores.items() if getattr(item, k) != v}
    if changes:
        changes.update(versao=versao + 1, atualizado_em=max(agora(), item.atualizado_em))
        result = session.execute(
            update(model).where(model.id == item.id, model.versao == versao).values(**changes)
        )
        if result.rowcount != 1:
            raise conflito()
        session.refresh(item)
    return bool(changes)


class ContatosService:
    def __init__(self, repository):
        self.repo = repository

    def _view(self, session, item):
        phones = session.scalars(
            select(TelefoneContato)
            .where(TelefoneContato.contato_id == item.id)
            .order_by(TelefoneContato.telefone_e164)
        ).all()
        lists = session.scalars(
            select(ListaContato.lista_id)
            .where(ListaContato.contato_id == item.id)
            .order_by(ListaContato.lista_id)
        ).all()
        telegram = session.scalars(
            select(IdentidadeTelegram)
            .where(IdentidadeTelegram.contato_id == item.id)
            .order_by(IdentidadeTelegram.id)
        ).all()
        return {
            **registro(item),
            "nome": item.nome,
            "observacoes": item.observacoes,
            "telefones": [telefone_view(p) for p in phones],
            "destinos_telegram": ["***" + i.chat_externo_id[-4:] for i in telegram],
            "listas": list(lists),
        }

    def criar(self, data, usuario):
        normalized = telefone(data.telefone, data.pais)
        name, notes = texto(data.nome), texto(data.observacoes, 2000)
        now = agora()
        with self.repo.transacao() as session:
            item = Contato(
                id=uuid(),
                nome=name,
                nome_busca=busca(name or ""),
                observacoes=notes,
                ativo=1,
                versao=1,
                criado_em=now,
                atualizado_em=now,
            )
            session.add(item)
            session.flush()
            session.add(
                TelefoneContato(
                    id=uuid(),
                    contato_id=item.id,
                    telefone_e164=normalized,
                    ativo=1,
                    versao=1,
                    criado_em=now,
                    atualizado_em=now,
                )
            )
            self.repo.auditar(session, usuario, "criar", "contato", item.id)
            session.flush()
            return self._view(session, item)

    def obter(self, identifier):
        with self.repo.transacao() as session:
            return self._view(session, self.repo.obter(session, Contato, identifier))

    def listar(
        self,
        q="",
        ativo=True,
        lista_id=None,
        pagina=1,
        limite=50,
        escopo_id=None,
        categoria=None,
        consentimento=None,
        canal=None,
    ):
        with self.repo.transacao() as session:
            conditions = []
            if ativo is not None:
                conditions.append(Contato.ativo == int(ativo))
            if q:
                normalized = busca(q)
                # contains(autoescape) não interpreta curingas submetidos.
                phones = select(TelefoneContato.contato_id).where(
                    TelefoneContato.telefone_e164.contains(
                        re.sub(r"[ ()\-.]", "", q), autoescape=True
                    )
                )
                conditions.append(
                    or_(
                        Contato.nome_busca.contains(normalized, autoescape=True),
                        Contato.id.in_(phones),
                    )
                )
            if lista_id:
                conditions.append(
                    Contato.id.in_(
                        select(ListaContato.contato_id).where(ListaContato.lista_id == lista_id)
                    )
                )
            if canal:
                conditions.append(
                    Contato.id.in_(
                        select(IdentidadeContato.contato_id).where(IdentidadeContato.canal == canal)
                    )
                )
            if consentimento:
                self.repo.obter(session, EscopoComunicacao, escopo_id)
                effective = case(
                    (Contato.bloqueio_global_em.is_not(None), "bloqueado"),
                    (IdentidadeContato.bloqueio_canal_em.is_not(None), "bloqueado"),
                    else_=func.coalesce(PreferenciaContato.estado, "desconhecido"),
                )
                matches = (
                    select(Contato.id)
                    .outerjoin(
                        IdentidadeContato,
                        IdentidadeContato.contato_id == Contato.id,
                    )
                    .outerjoin(
                        PreferenciaContato,
                        and_(
                            PreferenciaContato.identidade_contato_id == IdentidadeContato.id,
                            PreferenciaContato.escopo_id == escopo_id,
                            PreferenciaContato.categoria == categoria,
                        ),
                    )
                    .where(effective == consentimento)
                )
                if canal:
                    matches = matches.where(IdentidadeContato.canal == canal)
                conditions.append(Contato.id.in_(matches))
            total = session.scalar(select(func.count()).select_from(Contato).where(*conditions))
            items = session.scalars(
                select(Contato)
                .where(*conditions)
                .order_by(Contato.nome_busca, Contato.id)
                .offset((pagina - 1) * limite)
                .limit(limite)
            ).all()
            ids = [i.id for i in items]
            phones = {i: [] for i in ids}
            telegram = {i: [] for i in ids}
            for p in session.scalars(
                select(TelefoneContato)
                .where(TelefoneContato.contato_id.in_(ids))
                .order_by(TelefoneContato.telefone_e164)
            ):
                phones[p.contato_id].append(telefone_view(p))
            for identity in session.scalars(
                select(IdentidadeTelegram)
                .where(IdentidadeTelegram.contato_id.in_(ids))
                .order_by(IdentidadeTelegram.id)
            ):
                telegram[identity.contato_id].append("***" + identity.chat_externo_id[-4:])
            return {
                "total": total,
                "pagina": pagina,
                "limite": limite,
                "itens": [
                    {
                        **registro(i),
                        "nome": i.nome,
                        "telefones": phones[i.id],
                        "destinos_telegram": telegram[i.id],
                    }
                    for i in items
                ],
            }

    def editar(self, identifier, data, usuario):
        values = data.model_dump(exclude_unset=True, exclude={"versao"})
        if "nome" in values:
            values["nome"] = texto(values["nome"])
            values["nome_busca"] = busca(values["nome"] or "")
        if "observacoes" in values:
            values["observacoes"] = texto(values["observacoes"], 2000)
        if "ativo" in values:
            if values["ativo"] is None:
                raise NegocioError("validacao", "Informe a situação do cadastro.")
            values["ativo"] = int(values["ativo"])
        with self.repo.transacao() as session:
            item = self.repo.obter(session, Contato, identifier)
            if modificar(session, Contato, item, data.versao, values):
                self.repo.auditar(
                    session, usuario, "editar", "contato", identifier, campos=sorted(values)
                )
            return self._view(session, item)

    def adicionar_telefone(self, identifier, data, usuario):
        value = telefone(data.telefone, data.pais)
        with self.repo.transacao() as session:
            contact = self.repo.obter(session, Contato, identifier)
            if not contact.ativo:
                raise NegocioError("arquivado", "O contato está arquivado.", 409)
            now = agora()
            item = TelefoneContato(
                id=uuid(),
                contato_id=identifier,
                telefone_e164=value,
                ativo=1,
                versao=1,
                criado_em=now,
                atualizado_em=now,
            )
            session.add(item)
            self.repo.auditar(session, usuario, "adicionar", "telefone", item.id)
            session.flush()
            return telefone_view(item)

    def arquivar_telefone(self, identifier, phone_id, data, usuario):
        with self.repo.transacao() as session:
            item = self.repo.obter(session, TelefoneContato, phone_id)
            if item.contato_id != identifier:
                from app.contatos.errors import ausente

                raise ausente()
            if data.ativo:
                raise NegocioError("validacao", "Esta operação somente arquiva telefones.")
            if modificar(session, TelefoneContato, item, data.versao, {"ativo": 0}):
                self.repo.auditar(session, usuario, "arquivar", "telefone", phone_id)
            return telefone_view(item)
