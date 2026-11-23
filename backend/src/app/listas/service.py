"""Listas e associações explícitas, sem exclusão de contatos."""

from sqlalchemy import delete, func, select

from app.contatos.errors import NegocioError
from app.contatos.models import Contato, Lista, ListaContato
from app.contatos.normalizacao import busca, texto
from app.contatos.repository import agora, uuid
from app.contatos.service import modificar, registro


def view(item):
    return {**registro(item), "nome": item.nome, "descricao": item.descricao}


class ListasService:
    def __init__(self, repository):
        self.repo = repository

    def listar(self, q="", ativo=True, pagina=1, limite=50):
        with self.repo.transacao() as session:
            conditions = []
            if ativo is not None:
                conditions.append(Lista.ativo == int(ativo))
            if q:
                conditions.append(Lista.nome_normalizado.contains(busca(q), autoescape=True))
            total = session.scalar(select(func.count()).select_from(Lista).where(*conditions))
            items = session.scalars(
                select(Lista)
                .where(*conditions)
                .order_by(Lista.nome_normalizado, Lista.id)
                .offset((pagina - 1) * limite)
                .limit(limite)
            ).all()
            return {
                "total": total,
                "pagina": pagina,
                "limite": limite,
                "itens": [view(i) for i in items],
            }

    def criar(self, data, usuario):
        name = texto(data.nome)
        if not name:
            raise NegocioError("validacao", "Informe o nome da lista.")
        with self.repo.transacao() as session:
            now = agora()
            item = Lista(
                id=uuid(),
                nome=name,
                nome_normalizado=busca(name),
                descricao=texto(data.descricao, 2000),
                ativo=1,
                versao=1,
                criado_em=now,
                atualizado_em=now,
            )
            session.add(item)
            self.repo.auditar(session, usuario, "criar", "lista", item.id)
            session.flush()
            return view(item)

    def obter(self, identifier):
        with self.repo.transacao() as session:
            return view(self.repo.obter(session, Lista, identifier))

    def editar(self, identifier, data, usuario):
        values = data.model_dump(exclude_unset=True, exclude={"versao"})
        if "nome" in values:
            values["nome"] = texto(values["nome"])
            if not values["nome"]:
                raise NegocioError("validacao", "Informe o nome da lista.")
            values["nome_normalizado"] = busca(values["nome"])
        if "descricao" in values:
            values["descricao"] = texto(values["descricao"], 2000)
        if "ativo" in values:
            if values["ativo"] is None:
                raise NegocioError("validacao", "Informe a situação da lista.")
            values["ativo"] = int(values["ativo"])
        with self.repo.transacao() as session:
            item = self.repo.obter(session, Lista, identifier)
            if modificar(session, Lista, item, data.versao, values):
                self.repo.auditar(
                    session, usuario, "editar", "lista", identifier, campos=sorted(values)
                )
            return view(item)

    def associar(self, identifier, contact_id, usuario, remover=False):
        with self.repo.transacao() as session:
            group = self.repo.obter(session, Lista, identifier)
            contact = self.repo.obter(session, Contato, contact_id)
            exists = session.get(ListaContato, (identifier, contact_id))
            if remover:
                if exists:
                    session.execute(
                        delete(ListaContato).where(
                            ListaContato.lista_id == identifier,
                            ListaContato.contato_id == contact_id,
                        )
                    )
                    self.repo.auditar(
                        session,
                        usuario,
                        "remover_contato",
                        "lista",
                        identifier,
                        contato_id=contact_id,
                    )
            elif not exists:
                if not group.ativo or not contact.ativo:
                    raise NegocioError("arquivado", "Contato ou lista arquivado.", 409)
                session.add(
                    ListaContato(lista_id=identifier, contato_id=contact_id, adicionado_em=agora())
                )
                self.repo.auditar(
                    session,
                    usuario,
                    "adicionar_contato",
                    "lista",
                    identifier,
                    contato_id=contact_id,
                )
