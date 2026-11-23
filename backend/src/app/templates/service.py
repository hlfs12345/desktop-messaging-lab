"""Catálogo transacional, sem contas, seleção de contatos ou despacho."""

import hashlib
import json

from sqlalchemy import func, select, update

from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Auditoria
from app.contatos.normalizacao import busca, texto
from app.contatos.repository import agora, uuid
from app.templates.models import Template, TemplateVersao
from app.templates.renderizacao import previa, variaveis


def template_view(item):
    return {
        "id": item.id,
        "nome": item.nome,
        "descricao": item.descricao,
        "categoria": item.categoria,
        "ativo": bool(item.ativo),
        "versao": item.versao,
        "criado_em": item.criado_em,
        "atualizado_em": item.atualizado_em,
    }


def versao_view(item):
    return {
        "id": item.id,
        "template_id": item.template_id,
        "plataforma": item.plataforma,
        "numero_versao": item.numero_versao,
        "conteudo": item.conteudo,
        "variaveis": json.loads(item.variaveis_json),
        "criado_por_usuario_id": item.criado_por_usuario_id,
        "criado_em": item.criado_em,
    }


class TemplatesService:
    def __init__(self, repository, auth):
        self.repo, self.auth = repository, auth

    def listar(self, pagina=1, limite=20, q="", categoria=None, plataforma=None, ativo=True):
        with self.repo.transacao() as s:
            filters = [Template.ativo == int(ativo)]
            if q:
                filters.append(Template.nome_busca.contains(busca(q.strip()), autoescape=True))
            if categoria:
                filters.append(Template.categoria == categoria)
            if plataforma:
                filters.append(
                    select(TemplateVersao.id)
                    .where(
                        TemplateVersao.template_id == Template.id,
                        TemplateVersao.plataforma == plataforma,
                    )
                    .exists()
                )
            total = s.scalar(select(func.count()).select_from(Template).where(*filters))
            rows = s.scalars(
                select(Template)
                .where(*filters)
                .order_by(Template.nome_busca, Template.id)
                .offset((pagina - 1) * limite)
                .limit(limite)
            )
            return {"itens": [template_view(i) for i in rows], "total": total}

    def obter(self, identifier):
        with self.repo.transacao() as s:
            return template_view(self.repo.obter(s, Template, identifier))

    def historico(self, identifier, pagina=1, limite=20, plataforma=None):
        with self.repo.transacao() as s:
            self.repo.obter(s, Template, identifier)
            filters = [TemplateVersao.template_id == identifier]
            if plataforma:
                filters.append(TemplateVersao.plataforma == plataforma)
            total = s.scalar(select(func.count()).select_from(TemplateVersao).where(*filters))
            rows = s.scalars(
                select(TemplateVersao)
                .where(*filters)
                .order_by(TemplateVersao.plataforma, TemplateVersao.numero_versao.desc())
                .offset((pagina - 1) * limite)
                .limit(limite)
            )
            return {"itens": [versao_view(i) for i in rows], "total": total}

    def _repeticao(self, s, data, token, acao, identifier):
        # A sessão original é revalidada após o lock, na transação dos efeitos.
        usuario = self.auth.current(token, session=s).usuario.id
        request = {
            "acao": acao,
            "template_id": identifier,
            "usuario_id": usuario,
            "entrada": data.model_dump(mode="json", exclude_unset=True),
        }
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, ensure_ascii=True).encode("ascii")
        ).hexdigest()
        old = s.get(Auditoria, str(data.operacao_id))
        if old:
            summary = json.loads(old.resumo_json)
            if old.entidade != "template" or summary.get("requisicao_hash") != digest:
                raise conflito()
            return usuario, digest, summary["resultado"]
        return usuario, digest, None

    def _registrar(self, s, data, usuario, digest, acao, result):
        # Recibo só contém UUIDs/revisão. Conteúdo, nome e prévia não vão à auditoria.
        s.add(
            Auditoria(
                id=str(data.operacao_id),
                usuario_id=usuario,
                acao=acao,
                entidade="template",
                entidade_id=result["id"],
                resumo_json=json.dumps({"requisicao_hash": digest, "resultado": result}),
                ocorrido_em=agora(),
            )
        )
        s.flush()
        return result

    def _nova_versao(self, s, item, data, usuario):
        declared = variaveis(data.conteudo)
        number = (
            s.scalar(
                select(func.max(TemplateVersao.numero_versao)).where(
                    TemplateVersao.template_id == item.id,
                    TemplateVersao.plataforma == data.plataforma,
                )
            )
            or 0
        ) + 1
        version = TemplateVersao(
            id=uuid(),
            template_id=item.id,
            plataforma=data.plataforma,
            numero_versao=number,
            conteudo=data.conteudo,
            variaveis_json=json.dumps(declared, separators=(",", ":")),
            criado_por_usuario_id=usuario,
            criado_em=agora(),
        )
        s.add(version)
        return version

    def criar(self, data, token):
        with self.repo.transacao() as s:
            usuario, digest, old = self._repeticao(s, data, token, "criar", None)
            if old is not None:
                return old
            name = texto(data.nome)
            stamp = agora()
            item = Template(
                id=uuid(),
                nome=name,
                nome_busca=busca(name),
                descricao=texto(data.descricao, 2000),
                categoria=data.categoria,
                ativo=1,
                versao=1,
                criado_em=stamp,
                atualizado_em=stamp,
            )
            s.add(item)
            s.flush()
            version = self._nova_versao(s, item, data, usuario)
            return self._registrar(
                s,
                data,
                usuario,
                digest,
                "criar",
                {
                    "id": item.id,
                    "versao": 1,
                    "template_versao_id": version.id,
                },
            )

    def versionar(self, identifier, data, token):
        with self.repo.transacao() as s:
            usuario, digest, old = self._repeticao(s, data, token, "versionar", identifier)
            if old is not None:
                return old
            item = self.repo.obter(s, Template, identifier)
            if not item.ativo:
                raise NegocioError("arquivado", "Template arquivado não aceita novas versões.", 409)
            # Escrita antes de calcular MAX: serializa a atribuição no SQLite.
            changed = s.execute(
                update(Template)
                .where(Template.id == identifier, Template.versao == item.versao)
                .values(versao=Template.versao + 1, atualizado_em=agora())
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise conflito()
            version = self._nova_versao(s, item, data, usuario)
            return self._registrar(
                s,
                data,
                usuario,
                digest,
                "versionar",
                {
                    "id": item.id,
                    "versao": item.versao + 1,
                    "template_versao_id": version.id,
                },
            )

    def editar(self, identifier, data, token):
        with self.repo.transacao() as s:
            usuario, digest, old = self._repeticao(s, data, token, "editar", identifier)
            if old is not None:
                return old
            self.repo.obter(s, Template, identifier)
            values = {"versao": data.versao + 1, "atualizado_em": agora()}
            if "nome" in data.model_fields_set:
                values.update(nome=texto(data.nome), nome_busca=busca(texto(data.nome)))
            if "descricao" in data.model_fields_set:
                values["descricao"] = texto(data.descricao, 2000)
            if data.ativo is False:
                values["ativo"] = 0
            changed = s.execute(
                update(Template)
                .where(Template.id == identifier, Template.versao == data.versao)
                .values(**values)
            )
            if changed.rowcount != 1:
                raise conflito()
            return self._registrar(
                s,
                data,
                usuario,
                digest,
                "editar",
                {
                    "id": identifier,
                    "versao": data.versao + 1,
                },
            )

    def previa(self, data):
        return previa(data.conteudo, data.valores)
