"""Prévia em memória e confirmação única, atômica e auditável."""

import csv
import hashlib
import io
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import PurePath

from sqlalchemy import func, insert, select, update

from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Contato, Lista, ListaContato, TelefoneContato
from app.contatos.normalizacao import VERSAO, busca, telefone, texto
from app.contatos.repository import agora, uuid
from app.importacoes.models import Importacao, ImportacaoLinha
from app.importacoes.readers import Celula, inspecionar, linhas

TERMINAIS = {"confirmada", "cancelada", "interrompida", "falhou", "expirada"}


@dataclass
class Previa:
    id: str
    usuario: str
    extension: str
    criada: float = field(default_factory=time.monotonic)
    payload: bytes = b""
    info: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)
    revisao: int = -1
    config: object = None
    cancelada: threading.Event = field(default_factory=threading.Event)


def view(item):
    return {
        "id": item.id,
        "nome_arquivo": item.nome_arquivo,
        "hash_arquivo": item.hash_arquivo,
        "status": item.status,
        "contagens": json.loads(item.contagens_json),
        "criado_em": item.criado_em,
        "concluido_em": item.concluido_em,
        "configuracao": json.loads(item.configuracao_json),
        "normalizacao_versao": item.normalizacao_versao,
    }


class ImportacoesService:
    def __init__(self, repository, auth):
        self.repo, self.auth = repository, auth
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="edisk-importacao")
        self.previa = None
        self.future = None
        self.closed = False
        self.expiration = None
        with self.repo.transacao() as session:
            session.execute(
                update(Importacao)
                .where(Importacao.status.not_in(TERMINAIS))
                .values(status="interrompida", concluido_em=agora())
            )

    def close(self, *, espera=None):
        with self.lock:
            self.closed = True
            if self.expiration:
                self.expiration.cancel()
            if self.previa:
                self.previa.cancelada.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        try:
            with self.repo.transacao(espera=espera) as session:
                session.execute(
                    update(Importacao)
                    .where(Importacao.status.not_in(TERMINAIS))
                    .values(status="interrompida", concluido_em=agora())
                )
        finally:
            self.previa = None

    def _memory(self, identifier, usuario):
        p = self.previa
        if self.closed or p is None or p.id != identifier or p.usuario != usuario:
            raise NegocioError(
                "previa_indisponivel", "Selecione o arquivo e valide novamente.", 409
            )
        if time.monotonic() - p.criada > 1800:
            p.cancelada.set()
            with self.repo.transacao() as session:
                item = self.repo.obter(session, Importacao, identifier)
                if item.status not in TERMINAIS:
                    item.status = "expirada"
                    item.concluido_em = agora()
            self.previa = None
            raise NegocioError(
                "previa_expirada", "A prévia expirou. Selecione o arquivo novamente.", 409
            )
        return p

    def criar(self, nome, usuario):
        # Nome do arquivo é metadado; nunca é aberto no sistema de arquivos.
        name = texto(nome, 180)
        if (
            not name
            or any(c in name for c in "/\\\n\r\t")
            or PurePath(name).suffix.lower() not in {".csv", ".xlsx"}
        ):
            raise NegocioError("formato_invalido", "Selecione um arquivo .xlsx ou .csv.")
        with self.lock:
            if self.closed:
                raise conflito()
            if self.previa:
                try:
                    self._memory(self.previa.id, self.previa.usuario)
                except NegocioError:
                    pass
                if self.previa:
                    raise NegocioError(
                        "importacao_ativa", "Conclua ou cancele a importação em andamento.", 409
                    )
            identifier = uuid()
            with self.repo.transacao() as session:
                item = Importacao(
                    id=identifier,
                    usuario_id=usuario,
                    nome_arquivo=name,
                    status="criada",
                    normalizacao_versao=VERSAO,
                    criado_em=agora(),
                    configuracao_json="{}",
                    contagens_json="{}",
                )
                session.add(item)
                session.flush()
                result = view(item)
            self.previa = Previa(identifier, usuario, PurePath(name).suffix.lower())
            self.expiration = threading.Timer(1800, self._expirar, args=(identifier, usuario))
            self.expiration.daemon = True
            self.expiration.start()
            return result

    def _expirar(self, identifier, usuario):
        with self.lock:
            if self.closed or not self.previa or self.previa.id != identifier:
                return
            try:
                self._memory(identifier, usuario)
            except NegocioError:
                pass

    def carregar(self, identifier, usuario, payload):
        with self.lock:
            p = self._memory(identifier, usuario)
            with self.repo.transacao() as session:
                item = self.repo.obter(session, Importacao, identifier)
                if item.status not in {"criada", "carregada"}:
                    raise conflito()
            info = inspecionar(payload, p.extension)
            with self.repo.transacao() as session:
                item = self.repo.obter(session, Importacao, identifier)
                item.hash_arquivo = hashlib.sha256(payload).hexdigest()
                item.status = "carregada"
            p.payload, p.info = payload, info
            return info

    def validar(self, identifier, usuario, config):
        with self.lock:
            p = self._memory(identifier, usuario)
            with self.repo.transacao() as session:
                item = self.repo.obter(session, Importacao, identifier)
                if item.status not in {"carregada", "pronta"}:
                    raise conflito()
                item.status = "validando"
                item.configuracao_json = config.model_dump_json()
            p.config = config
            p.rows = []
            self.future = self.executor.submit(self._validar_job, p)
            return {"status": "validando"}

    def amostra(self, identifier, usuario, config):
        with self.lock:
            p = self._memory(identifier, usuario)
            config = config.model_copy(update={"cabecalho": None})
            iterator = linhas(p.payload, p.extension, config)
            result = []
            try:
                for _ in range(5):
                    row = next(iterator, None)
                    if row is None:
                        break
                    number, cells = row
                    result.append(
                        {
                            "linha": number,
                            "celulas": [
                                c.erro
                                or (c.valor if len(c.valor) <= 80 else c.valor[:80] + "… [prévia]")
                                for c in cells
                            ],
                        }
                    )
            finally:
                iterator.close()
            return {"itens": result}

    def _validar_job(self, p):
        try:
            rows, revision = self._planejar(p)
            with self.lock:
                if self.closed or p.cancelada.is_set():
                    return
                with self.repo.transacao() as session:
                    item = self.repo.obter(session, Importacao, p.id)
                    item.status = "pronta"
                    item.contagens_json = json.dumps(dict(Counter(r["resultado"] for r in rows)))
                p.rows, p.revisao = rows, revision
        except Exception as error:
            with self.lock:
                if self.closed or p.cancelada.is_set():
                    return
                code = error.codigo if isinstance(error, NegocioError) else "arquivo_invalido"
                with self.repo.transacao() as session:
                    item = self.repo.obter(session, Importacao, p.id)
                    item.status = "falhou"
                    item.concluido_em = agora()
                    item.contagens_json = json.dumps({"erro": code})
                if self.previa is p:
                    self.previa = None
                    if self.expiration:
                        self.expiration.cancel()

    def _planejar(self, p):
        config = p.config
        with self.repo.transacao() as session:
            for identifier in config.listas:
                if not self.repo.obter(session, Lista, identifier).ativo:
                    raise NegocioError("lista_arquivada", "Escolha listas ativas.")
            revision = self.repo.revisao(session)
            existing = {
                phone.telefone_e164: (phone, contact)
                for phone, contact in session.execute(
                    select(TelefoneContato, Contato).join(
                        Contato, TelefoneContato.contato_id == Contato.id
                    )
                )
            }
        result = []
        groups = {}
        contact_changes = {}
        for number, cells in linhas(p.payload, p.extension, config):
            if p.cancelada.is_set() or self.closed:
                raise conflito()
            row = {
                "numero_linha": number,
                "resultado": "invalido",
                "motivo": "",
                "contato_id": None,
                "telefone_id": None,
                "changes": {},
            }
            result.append(row)
            if not any(c.valor or c.erro for c in cells):
                row.update(resultado="ignorado", motivo="linha_vazia")
                continue

            def cell(index, cells=cells):
                return cells[index] if index is not None and index < len(cells) else Celula()

            try:
                phone = cell(config.coluna_telefone)
                name = cell(config.coluna_nome)
                notes = cell(config.coluna_observacoes)
                country = cell(config.coluna_pais)
                for c in (phone, name, notes, country):
                    if c.erro:
                        raise NegocioError(c.erro, "Corrija o valor na origem.")
                if name.aviso or notes.aviso or country.aviso:
                    raise NegocioError("tipo_invalido", "Texto esperado para o campo.")
                normalized = telefone(phone.valor, country.valor.strip() or config.pais)
                values = {"nome": texto(name.valor), "observacoes": texto(notes.valor, 2000)}
                row.update(telefone_e164=normalized, nome=values["nome"], motivo=phone.aviso)
                duplicate = groups.get(normalized)
                if duplicate:
                    if duplicate["values"] != values or duplicate["conflito"]:
                        duplicate["conflito"] = True
                        for prior in duplicate["rows"] + [row]:
                            prior.update(
                                resultado="invalido", motivo="duplicidade_conflitante", changes={}
                            )
                    else:
                        row.update(resultado="duplicado", motivo="repetido_no_arquivo")
                    duplicate["rows"].append(row)
                    continue
                groups[normalized] = {"values": values, "rows": [row], "conflito": False}
                found = existing.get(normalized)
                if found:
                    old_phone, contact = found
                    row.update(contato_id=contact.id, telefone_id=old_phone.id)
                    if not old_phone.ativo or not contact.ativo:
                        row.update(resultado="ignorado", motivo="registro_arquivado")
                        continue
                    changes = {}
                    for key in config.campos_atualizar:
                        if (
                            values[key]
                            and values[key] != getattr(contact, key)
                            and config.politica != "preservar"
                            and (config.politica == "substituir" or not getattr(contact, key))
                        ):
                            changes[key] = values[key]
                    row.update(
                        resultado="atualizado" if changes else "sem_alteracao",
                        changes=changes,
                        versao=contact.versao,
                    )
                    contact_changes.setdefault(contact.id, []).append(row)
                else:
                    row.update(
                        resultado="inserido", contato_id=uuid(), telefone_id=uuid(), changes=values
                    )
            except NegocioError as error:
                row.update(resultado="invalido", motivo=error.codigo)
        # Dois telefones do mesmo contato não podem sobrescrever o mesmo campo.
        for rows in contact_changes.values():
            for key in ("nome", "observacoes"):
                if len({r["changes"][key] for r in rows if key in r["changes"]}) > 1:
                    for r in rows:
                        r.update(resultado="invalido", motivo="atualizacao_conflitante", changes={})
        return result, revision

    def confirmar(self, identifier, usuario, token):
        with self.lock:
            # A dependência HTTP não substitui esta verificação após espera.
            self.auth.current(token)
            with self.repo.transacao() as session:
                item = self.repo.obter(session, Importacao, identifier)
                if item.usuario_id != usuario:
                    raise NegocioError(
                        "operacao_indisponivel", "Operação indisponível para este usuário.", 403
                    )
                if item.status == "confirmada":
                    return view(item)
            p = self._memory(identifier, usuario)
            stale = False
            with self.repo.transacao() as session:
                item = self.repo.obter(session, Importacao, identifier)
                if item.status != "pronta":
                    raise conflito()
                if self.repo.revisao(session) != p.revisao:
                    item.status = "carregada"
                    item.contagens_json = "{}"
                    stale = True
                else:
                    now = agora()
                    # Primeira escrita antes dos lotes; tudo reverte junto.
                    item.status = "confirmada"
                    session.flush()
                    contacts = []
                    phones = []
                    updates = {}
                    contact_ids = set()
                    reports = []
                    for row in p.rows:
                        outcome = row["resultado"]
                        if outcome in {"inserido", "atualizado", "sem_alteracao"}:
                            contact_ids.add(row["contato_id"])
                        if outcome == "inserido":
                            values = row["changes"]
                            contacts.append(
                                dict(
                                    id=row["contato_id"],
                                    nome=values["nome"],
                                    nome_busca=busca(values["nome"] or ""),
                                    observacoes=values["observacoes"],
                                    ativo=1,
                                    versao=1,
                                    criado_em=now,
                                    atualizado_em=now,
                                )
                            )
                            phones.append(
                                dict(
                                    id=row["telefone_id"],
                                    contato_id=row["contato_id"],
                                    telefone_e164=row["telefone_e164"],
                                    ativo=1,
                                    versao=1,
                                    criado_em=now,
                                    atualizado_em=now,
                                )
                            )
                        elif outcome == "atualizado":
                            updates.setdefault(row["contato_id"], {}).update(row["changes"])
                        reports.append(
                            dict(
                                id=uuid(),
                                importacao_id=identifier,
                                numero_linha=row["numero_linha"],
                                resultado=outcome,
                                motivo=row["motivo"],
                                contato_id=row["contato_id"]
                                if outcome not in {"invalido", "duplicado"}
                                else None,
                                telefone_id=row["telefone_id"]
                                if outcome not in {"invalido", "duplicado"}
                                else None,
                            )
                        )
                    if contacts:
                        session.execute(insert(Contato), contacts)
                        session.execute(insert(TelefoneContato), phones)
                    for contact_id, values in updates.items():
                        old = self.repo.obter(session, Contato, contact_id)
                        if "nome" in values:
                            values["nome_busca"] = busca(values["nome"])
                        values.update(
                            versao=old.versao + 1, atualizado_em=max(now, old.atualizado_em)
                        )
                        session.execute(
                            update(Contato).where(Contato.id == contact_id).values(**values)
                        )
                    associations = []
                    for group_id in p.config.listas:
                        group = self.repo.obter(session, Lista, group_id)
                        if not group.ativo:
                            raise conflito()
                        existing = set(
                            session.scalars(
                                select(ListaContato.contato_id).where(
                                    ListaContato.lista_id == group_id
                                )
                            )
                        )
                        associations.extend(
                            dict(lista_id=group_id, contato_id=c, adicionado_em=now)
                            for c in contact_ids - existing
                        )
                    if associations:
                        session.execute(insert(ListaContato), associations)
                    if reports:
                        session.execute(insert(ImportacaoLinha), reports)
                    counts = dict(Counter(r["resultado"] for r in p.rows))
                    counts["total_linhas"] = len(p.rows)
                    counts["associacoes_adicionadas"] = len(associations)
                    item.contagens_json = json.dumps(counts)
                    item.concluido_em = now
                    self.repo.auditar(
                        session, usuario, "confirmar", "importacao", identifier, **counts
                    )
                    result = view(item)
            if stale:
                p.rows = []
                raise NegocioError(
                    "previa_desatualizada",
                    "O cadastro mudou. Valide o arquivo novamente antes de confirmar.",
                    409,
                )
            self.previa = None
            if self.expiration:
                self.expiration.cancel()
            return result

    def cancelar(self, identifier, usuario):
        with self.lock:
            with self.repo.transacao() as session:
                item = self.repo.obter(session, Importacao, identifier)
                if item.usuario_id != usuario:
                    raise NegocioError(
                        "operacao_indisponivel", "Operação indisponível para este usuário.", 403
                    )
                if item.status == "confirmada":
                    raise conflito()
                if item.status not in TERMINAIS:
                    item.status = "cancelada"
                    item.concluido_em = agora()
            if self.previa and self.previa.id == identifier:
                self.previa.cancelada.set()
                self.previa = None
                if self.expiration:
                    self.expiration.cancel()

    def obter(self, identifier):
        with self.lock:
            if self.previa and self.previa.id == identifier:
                try:
                    self._memory(identifier, self.previa.usuario)
                except NegocioError:
                    pass
            with self.repo.transacao() as session:
                result = view(self.repo.obter(session, Importacao, identifier))
            if self.previa and self.previa.id == identifier:
                result["arquivo"] = self.previa.info
            return result

    def listar(self, pagina=1, limite=50):
        with self.repo.transacao() as session:
            total = session.scalar(select(func.count()).select_from(Importacao))
            items = session.scalars(
                select(Importacao)
                .order_by(Importacao.criado_em.desc(), Importacao.id)
                .offset((pagina - 1) * limite)
                .limit(limite)
            ).all()
            return {
                "total": total,
                "pagina": pagina,
                "limite": limite,
                "itens": [view(i) for i in items],
            }

    def resultados(self, identifier, pagina=1, limite=50, resultado=None):
        with self.lock:
            item = self.obter(identifier)
            if item["status"] == "pronta" and self.previa and self.previa.id == identifier:
                rows = [
                    {
                        k: v
                        for k, v in r.items()
                        if k
                        in {
                            "numero_linha",
                            "resultado",
                            "motivo",
                            "contato_id",
                            "telefone_id",
                            "telefone_e164",
                            "nome",
                        }
                    }
                    for r in self.previa.rows
                    if not resultado or r["resultado"] == resultado
                ]
                return {
                    "total": len(rows),
                    "pagina": pagina,
                    "limite": limite,
                    "itens": rows[(pagina - 1) * limite : pagina * limite],
                }
            with self.repo.transacao() as session:
                conditions = [ImportacaoLinha.importacao_id == identifier]
                if resultado:
                    conditions.append(ImportacaoLinha.resultado == resultado)
                total = session.scalar(
                    select(func.count()).select_from(ImportacaoLinha).where(*conditions)
                )
                rows = session.scalars(
                    select(ImportacaoLinha)
                    .where(*conditions)
                    .order_by(ImportacaoLinha.numero_linha)
                    .offset((pagina - 1) * limite)
                    .limit(limite)
                ).all()
                return {
                    "total": total,
                    "pagina": pagina,
                    "limite": limite,
                    "itens": [
                        dict(
                            numero_linha=r.numero_linha,
                            resultado=r.resultado,
                            motivo=r.motivo,
                            contato_id=r.contato_id,
                            telefone_id=r.telefone_id,
                        )
                        for r in rows
                    ],
                }

    def relatorio(self, identifier):
        self.obter(identifier)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["linha", "resultado", "motivo"])
        page = 1
        while True:
            result = self.resultados(identifier, page, 100)
            for row in result["itens"]:
                # Somente número e códigos internos fixos, sem valores do arquivo.
                writer.writerow([row["numero_linha"], row["resultado"], row["motivo"]])
            if page * 100 >= result["total"]:
                break
            page += 1
        return buffer.getvalue().encode("utf-8-sig")
