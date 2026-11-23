"""Preparacao cifrada e recuperacao local; nenhum despacho ou acesso a credenciais."""

import base64
import hashlib
import hmac
import json
import os
from uuid import UUID

from sqlalchemy import and_, func, select

from app.campanhas.autorizacoes import revogar_para_rascunho
from app.campanhas.configuracao_simulacao import criar_configuracao, validar_cabecalho
from app.campanhas.crypto import GCM, indisponivel
from app.campanhas.execucoes import encerrar_para_rascunho
from app.campanhas.models import (
    Campanha,
    CampanhaLista,
    CampanhaSelecao,
    CampanhaSelecaoTelegram,
    ContextoCampanhas,
    Preparacao,
    PreparacaoItem,
    PreparacaoItemTelegram,
)
from app.campanhas.service import campanha_view
from app.consentimentos.models import (
    IdentidadeContato,
    IdentidadeTelegram,
    InscricaoConta,
    PreferenciaContato,
)
from app.contas_envio.models import ContaEnvio
from app.contas_envio.segredos import dpapi
from app.contas_envio.service import administrador
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Auditoria, Contato, Lista, ListaContato, TelefoneContato
from app.contatos.repository import agora, uuid
from app.templates.models import TemplateVersao
from app.templates.renderizacao import previa

MAX_ITENS = 10000
MAX_TEXTO = 65536
MAX_TOTAL = 67108864
AVISO = "Conteudo preparado. Envio indisponivel nesta etapa."


def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
        "ascii"
    )


def aad(header, purpose, identity=None):
    return canon(
        {"formato": 1, "finalidade": purpose, "fotografia": header, "identidade": identity}
    )


def novo_nonce(used):
    for _ in range(128):
        value = os.urandom(12)
        if value not in used:
            used.add(value)
            return value
    raise indisponivel()


def proteger(base, key):
    return dpapi(
        canon({"formato": 1, "finalidade": "campanhas", "base_id": base, "chave": key.hex()})
    )


def abrir(ctx):
    try:
        env = json.loads(dpapi(ctx.chave_dpapi, decrypt=True))
        key = bytes.fromhex(env["chave"])
        if (
            env
            != {"formato": 1, "finalidade": "campanhas", "base_id": ctx.base_id, "chave": key.hex()}
            or len(key) != 32
        ):
            raise ValueError
        with GCM(key) as g:
            if g.decrypt(ctx.nonce, ctx.verificador, canon(["campanhas-v1", ctx.base_id])) != bytes(
                32
            ):
                raise ValueError
        return key
    except ValueError, KeyError, TypeError, UnicodeError, NegocioError:
        raise indisponivel() from None


def codigo(base, key):
    return (
        "EDC1."
        + UUID(base).hex
        + "."
        + os.urandom(8).hex()
        + "."
        + base64.b32encode(key).decode("ascii").rstrip("=")
    )


def decodificar(ctx, value):
    try:
        parts = value.split(".")
        if (
            len(parts) != 4
            or parts[0] != "EDC1"
            or parts[1] != UUID(ctx.base_id).hex
            or len(parts[2]) != 16
            or len(parts[3]) != 52
        ):
            raise ValueError
        bytes.fromhex(parts[2])
        key = base64.b32decode(parts[3] + "====")
        if base64.b32encode(key).decode("ascii").rstrip("=") != parts[3]:
            raise ValueError
        with GCM(key) as g:
            if g.decrypt(ctx.nonce, ctx.verificador, canon(["campanhas-v1", ctx.base_id])) != bytes(
                32
            ):
                raise ValueError
        return key
    except ValueError, TypeError, NegocioError:
        raise indisponivel() from None


class PreparacoesService:
    def __init__(self, campanhas):
        self.c = campanhas
        self.repo, self.auth = campanhas.repo, campanhas.auth
        self.abertura = uuid()

    def _ctx(self, s, conferido=False):
        ctx = s.get(ContextoCampanhas, "campanhas-v1")
        if ctx is None or (conferido and not ctx.conferido):
            raise NegocioError(
                "chave_nao_conferida",
                "O administrador deve inicializar e conferir o codigo de recuperacao.",
                409,
            )
        return ctx

    def estado_chave(self, token):
        with self.repo.transacao() as s:
            user = self.auth.current(token, session=s).usuario
            ctx = s.get(ContextoCampanhas, "campanhas-v1")
            return {
                "inicializada": ctx is not None,
                "conferida": bool(ctx and ctx.conferido),
                "administrador": user.perfil == "administrador",
            }

    def chave(self, action, data, token):
        with self.repo.transacao() as s:
            user = administrador(self.auth, token, s)
            old = s.get(Auditoria, str(data.operacao_id))
            if old:
                # Segredos nunca sao reapresentados por replay.
                raise conflito()
            ctx = s.get(ContextoCampanhas, "campanhas-v1")
            result = {"concluida": True}
            if action == "inicializar":
                if ctx is not None:
                    raise conflito()
                key, base = os.urandom(32), uuid()
                nonce = os.urandom(12)
                with GCM(key) as g:
                    verifier = g.encrypt(nonce, bytes(32), canon(["campanhas-v1", base]))
                ctx = ContextoCampanhas(
                    id="campanhas-v1",
                    base_id=base,
                    chave_dpapi=proteger(base, key),
                    nonce=nonce,
                    verificador=verifier,
                    conferido=0,
                    criado_em=agora(),
                )
                s.add(ctx)
                result["codigo_recuperacao"] = codigo(base, key)
            else:
                ctx = self._ctx(s)
                if action == "reemitir":
                    key = abrir(ctx)
                    ctx.conferido = 0
                    result["codigo_recuperacao"] = codigo(ctx.base_id, key)
                else:
                    key = decodificar(ctx, data.codigo.get_secret_value())
                    if action == "recuperar":
                        # O verificador AEAD comprova chave/base sem percorrer fotografias.
                        ctx.chave_dpapi = proteger(ctx.base_id, key)
                        result.update(
                            chave_recuperada=True,
                            integridade_fotografias="nao_verificada",
                            aviso=(
                                "Chave recuperada. A integridade de cada fotografia "
                                "sera verificada ao consultar. Esta operacao nao restaura o banco."
                            ),
                        )
                    elif action != "conferir":
                        raise conflito()
                    ctx.conferido = 1
            s.add(
                Auditoria(
                    id=str(data.operacao_id),
                    usuario_id=user,
                    acao="chave_" + action,
                    entidade="campanha_chaves",
                    entidade_id=ctx.base_id,
                    ocorrido_em=agora(),
                    resumo_json='{"formato":1}',
                )
            )
            s.flush()
            administrador(self.auth, token, s)
            return result

    def _marcador(self, s, campaign, ctx, configuracao=None):
        marker = ".".join(
            (ctx.base_id, self.abertura, str(campaign.versao), str(self.repo.revisao(s)))
        )
        return marker if configuracao is None else marker + "." + configuracao.resumo_configuracao

    def _fontes(self, s, campaign):
        refs = self.c._validar(s, campanha_view(campaign), self.c._listas(s, campaign.id))
        if refs["conta"]["provedor"] == "telegram_bot_api":
            return refs, self._fontes_telegram(s, campaign)
        rows = s.execute(
            select(
                IdentidadeContato,
                Contato,
                TelefoneContato,
                PreferenciaContato,
                self.c._pertence(campaign.id).label("pertence"),
            )
            .join(
                CampanhaSelecao,
                and_(
                    CampanhaSelecao.identidade_contato_id == IdentidadeContato.id,
                    CampanhaSelecao.campanha_id == campaign.id,
                ),
            )
            .join(Contato, Contato.id == IdentidadeContato.contato_id)
            .join(TelefoneContato, TelefoneContato.id == IdentidadeContato.telefone_id)
            .outerjoin(
                PreferenciaContato,
                and_(
                    PreferenciaContato.identidade_contato_id == IdentidadeContato.id,
                    PreferenciaContato.escopo_id == campaign.escopo_id,
                    PreferenciaContato.categoria == campaign.categoria,
                ),
            )
            .order_by(IdentidadeContato.id)
            .limit(MAX_ITENS + 1)
        ).all()
        if len(rows) > MAX_ITENS:
            raise NegocioError(
                "volume_excessivo", "A campanha excede o limite de identidades.", 422
            )
        result = []
        requires_name = "{{nome}}" in s.get(
            TemplateVersao, campaign.template_versao_id
        ).conteudo
        for identity, contact, phone, pref, belongs in rows:
            reasons = self.c.consentimentos.avaliar_dados(contact, identity, phone, pref)["motivos"]
            guard = getattr(self.repo.database, "suspensoes_consentimento", None)
            if guard is not None and guard.afetado(
                contact.id, identity.id, campaign.escopo_id, campaign.categoria
            ):
                reasons.append("descadastramento_pendente")
            if not belongs:
                reasons.append("fora_das_listas")
            if identity.canal != refs["conta"]["plataforma"]:
                reasons.append("canal_incompativel")
            if not reasons and requires_name and not (contact.nome or "").strip():
                reasons.append("nome_ausente")
            result.append(
                {
                    "identidade_contato_id": identity.id,
                    "nome": contact.nome,
                    "destino_mascarado": "***" + phone.telefone_e164[-4:],
                    "motivos": reasons,
                    "evento": pref.evento_origem_id if pref else None,
                }
            )
        return refs, result

    def _fontes_telegram(self, s, campaign):
        if s.scalar(
            select(CampanhaSelecao.identidade_contato_id)
            .where(CampanhaSelecao.campanha_id == campaign.id)
            .limit(1)
        ):
            raise NegocioError(
                "selecao_incompativel",
                "Remova identidades de outro canal antes da preparação Telegram.",
                409,
            )
        selections = s.scalars(
            select(CampanhaSelecaoTelegram)
            .where(CampanhaSelecaoTelegram.campanha_id == campaign.id)
            .order_by(CampanhaSelecaoTelegram.identidade_telegram_id)
            .limit(MAX_ITENS + 1)
        ).all()
        if len(selections) > MAX_ITENS:
            raise NegocioError(
                "volume_excessivo", "A campanha excede o limite de identidades.", 422
            )
        if not selections:
            return []
        account = self.repo.obter(s, ContaEnvio, campaign.conta_envio_id)
        requires_name = "{{nome}}" in s.get(TemplateVersao, campaign.template_versao_id).conteudo
        rows = []
        for selection in selections:
            identity = self.repo.obter(
                s, IdentidadeTelegram, selection.identidade_telegram_id
            )
            contact = self.repo.obter(s, Contato, identity.contato_id)
            subscription = self.repo.obter(s, InscricaoConta, selection.inscricao_id)
            eligibility = self.c.consentimentos._avaliar_telegram(
                s,
                account,
                identity,
                contact,
                subscription,
                campaign.escopo_id,
                campaign.categoria,
            )
            reasons = list(eligibility["motivos"])
            member = s.scalar(
                select(ListaContato.contato_id)
                .join(Lista, Lista.id == ListaContato.lista_id)
                .join(CampanhaLista, CampanhaLista.lista_id == ListaContato.lista_id)
                .where(
                    CampanhaLista.campanha_id == campaign.id,
                    ListaContato.contato_id == contact.id,
                    Lista.ativo == 1,
                )
                .limit(1)
            )
            if member is None:
                reasons.append("fora_das_listas")
            if not reasons and requires_name and not (contact.nome or "").strip():
                reasons.append("nome_ausente")
            rows.append(
                {
                    "canal": "telegram",
                    "identidade_telegram_id": identity.id,
                    "inscricao_id": subscription.id,
                    "conta_envio_id": account.id,
                    "nome": contact.nome,
                    "destino_mascarado": "***" + identity.chat_externo_id[-4:],
                    "motivos": reasons,
                    "evento": eligibility["evento_telegram_id"],
                }
            )
        return rows

    def revisao(self, identifier, token, pagina=1, limite=20, marcador=None, cenario=None):
        with self.repo.transacao() as s:
            self.auth.current(token, session=s)
            campaign = self.repo.obter(s, Campanha, identifier)
            ctx = self._ctx(s, True)
            if campaign.status != "rascunho":
                raise conflito()
            current = self._marcador(s, campaign, ctx)
            refs, rows = self._fontes(s, campaign)
            configuracao = None
            if refs["conta"]["provedor"] == "mock":
                if cenario is None:
                    raise NegocioError(
                        "configuracao_obrigatoria", "Escolha um cenario de simulacao."
                    )
                configuracao = criar_configuracao(
                    campaign.conta_envio_id, current, cenario, campaign.id
                )
                current = self._marcador(s, campaign, ctx, configuracao)
            elif cenario is not None:
                raise NegocioError("configuracao_incompativel", "Conta real nao admite simulacao.")
            if marcador is not None and marcador != current:
                raise conflito()
            self.auth.current(token, session=s)
            included = sum(not row["motivos"] for row in rows)
            return {
                "versao": campaign.versao,
                "marcador": current,
                **({"configuracao_simulacao": configuracao.model_dump()} if configuracao else {}),
                "incluidos": included,
                "excluidos": len(rows) - included,
                "total": len(rows),
                "itens": [
                    {
                        "identidade_contato_id": r.get("identidade_contato_id"),
                        "identidade_telegram_id": r.get("identidade_telegram_id"),
                        "inscricao_id": r.get("inscricao_id"),
                        "nome_atual": r["nome"],
                        "destino_mascarado": r["destino_mascarado"],
                        "incluido": not r["motivos"],
                        "motivos": r["motivos"],
                    }
                    for r in rows[(pagina - 1) * limite : pagina * limite]
                ],
                "referencias": refs,
                "autorizacao_requerida": (
                    "envio_real"
                    if refs["conta"]["provedor"] in {"telegram_bot_api", "qr_candidato"}
                    else "simulacao"
                    if refs["conta"]["provedor"] == "mock"
                    else "nao_definida"
                ),
                "aviso": AVISO,
            }

    def _recibo(self, s, data, user, digest, action, campaign, prep):
        result = {"id": campaign.id, "versao": campaign.versao, "preparacao_id": prep.id}
        s.add(
            Auditoria(
                id=str(data.operacao_id),
                usuario_id=user,
                acao=action,
                entidade="campanha",
                entidade_id=campaign.id,
                ocorrido_em=agora(),
                resumo_json=json.dumps({"requisicao_hash": digest, "resultado": result}),
            )
        )
        s.flush()
        return result

    def preparar(self, identifier, data, token):
        with self.repo.transacao() as s:
            user, digest, old = self.c._repeticao(s, data, token, "preparar", identifier)
            if old is not None:
                return old
            campaign = self.repo.obter(s, Campanha, identifier)
            ctx = self._ctx(s, True)
            marker = self._marcador(s, campaign, ctx, data.configuracao_simulacao)
            if (
                campaign.status != "rascunho"
                or campaign.versao != data.versao
                or marker != data.marcador
            ):
                raise conflito()
            refs, rows = self._fontes(s, campaign)
            configuracao = data.configuracao_simulacao
            if refs["conta"]["provedor"] == "mock":
                if configuracao is None:
                    raise NegocioError(
                        "configuracao_obrigatoria", "Revise a configuracao de simulacao."
                    )
                expected = criar_configuracao(
                    campaign.conta_envio_id,
                    self._marcador(s, campaign, ctx),
                    configuracao.cenario,
                    campaign.id,
                )
                if configuracao != expected:
                    raise conflito()
            elif configuracao is not None:
                raise NegocioError("configuracao_incompativel", "Conta real nao admite simulacao.")
            template = self.repo.obter(s, TemplateVersao, campaign.template_versao_id).conteudo
            key = abrir(ctx)
            root_nonce = novo_nonce({ctx.nonce, *s.scalars(select(Preparacao.chave_nonce)).all()})
            prep_id = uuid()
            header = {
                "formato": 1,
                "base_id": ctx.base_id,
                "campanha_id": identifier,
                "preparacao_id": prep_id,
                "conta_envio_id": campaign.conta_envio_id,
                "escopo_id": campaign.escopo_id,
                "categoria": campaign.categoria,
                "template_versao_id": campaign.template_versao_id,
                "plataforma": refs["conta"]["plataforma"],
                "provedor": refs["conta"]["provedor"],
                "listas": self.c._listas(s, identifier),
                "marcador": marker,
                **(
                    {"tipo_autorizacao": "envio_real"}
                    if refs["conta"]["provedor"] in {"telegram_bot_api", "qr_candidato"}
                    else {}
                ),
                **({"configuracao_simulacao": configuracao.model_dump()} if configuracao else {}),
            }
            origin, base = campaign.versao, ctx.base_id
        # Nenhum lock/transacao SQLite durante renderizacao e cifragem.
        raw_key, used, items, total, included = os.urandom(32), set(), [], 0, 0
        hasher = hashlib.sha256(canon(header))
        with GCM(raw_key) as content:
            for row in rows:
                reasons = row["motivos"]
                if not reasons and "{{nome}}" in template and not (row["nome"] or "").strip():
                    reasons.append("nome_ausente")
                telegram = row.get("canal") == "telegram"
                identity_key = row.get("identidade_telegram_id") or row["identidade_contato_id"]
                item = (
                    {
                        "preparacao_id": prep_id,
                        "conta_envio_id": row["conta_envio_id"],
                        "identidade_telegram_id": row["identidade_telegram_id"],
                        "inscricao_id": row["inscricao_id"],
                        "incluido": int(not reasons),
                        "motivos_json": json.dumps(reasons),
                        "evento_telegram_id": row["evento"],
                        "nonce": None,
                        "conteudo_cifrado": None,
                        "bytes_texto": 0,
                    }
                    if telegram
                    else {
                        "preparacao_id": prep_id,
                        "identidade_contato_id": row["identidade_contato_id"],
                        "incluido": int(not reasons),
                        "motivos_json": json.dumps(reasons),
                        "evento_consentimento_id": row["evento"],
                        "nonce": None,
                        "conteudo_cifrado": None,
                        "bytes_texto": 0,
                    }
                )
                if not reasons:
                    plain = previa(template, {"nome": row["nome"] or ""})[
                        "conteudo_renderizado"
                    ].encode("utf-8")
                    size = len(plain)
                    if not 0 < size <= MAX_TEXTO or total + size > MAX_TOTAL:
                        raise NegocioError(
                            "volume_excessivo", "O conteudo excede o limite de preparacao.", 422
                        )
                    nonce = novo_nonce(used)
                    item.update(
                        nonce=nonce,
                        conteudo_cifrado=content.encrypt(
                            nonce, plain, aad(header, "texto", identity_key)
                        ),
                        bytes_texto=size,
                    )
                    del plain
                    included += 1
                    total += size
                (self._hash_item_telegram if telegram else self._hash_item)(hasher, item)
                items.append(item)
            if not included:
                raise NegocioError(
                    "sem_destinatarios",
                    "Nenhum destinatario pode ser incluido. Revise as exclusoes.",
                    409,
                )
            manifest_nonce = novo_nonce(used)
            manifest = content.encrypt(manifest_nonce, hasher.digest(), aad(header, "manifesto"))
        with GCM(key) as root:
            wrapped = root.encrypt(root_nonce, raw_key, aad(header, "chave"))
        del raw_key, key, rows
        with self.repo.transacao() as s:
            user, digest, old = self.c._repeticao(s, data, token, "preparar", identifier)
            if old is not None:
                return old
            campaign = self.repo.obter(s, Campanha, identifier)
            ctx = self._ctx(s, True)
            if (
                self._marcador(s, campaign, ctx, data.configuracao_simulacao) != marker
                or campaign.status != "rascunho"
            ):
                raise conflito()
            self.c._validar(s, campanha_view(campaign), self.c._listas(s, identifier))
            now = agora()
            prep = Preparacao(
                id=prep_id,
                campanha_id=identifier,
                base_id=base,
                versao_origem=origin,
                revisao_pos=self.repo.revisao(s) + 1,
                fotografia_json=canon(header).decode("ascii"),
                chave_nonce=root_nonce,
                chave_cifrada=wrapped,
                manifesto_nonce=manifest_nonce,
                manifesto=manifest,
                impressao=hashlib.sha256(manifest_nonce + manifest).hexdigest(),
                incluidos=included,
                excluidos=len(items) - included,
                bytes_texto=total,
                criado_por_usuario_id=user,
                criado_em=now,
            )
            s.add(prep)
            s.flush()
            # Lotes sao parte da mesma transacao, sem commits intermediarios.
            table = (
                PreparacaoItemTelegram.__table__
                if header["provedor"] == "telegram_bot_api"
                else PreparacaoItem.__table__
            )
            for offset in range(0, len(items), 250):
                s.execute(table.insert(), items[offset : offset + 250])
            campaign.status, campaign.versao, campaign.atualizado_em = "preparada", origin + 1, now
            result = self._recibo(s, data, user, digest, "preparar", campaign, prep)
            self.auth.current(token, session=s)
            return result

    def _header(self, p, ctx):
        try:
            header = json.loads(p.fotografia_json)
            if (
                header["base_id"] != ctx.base_id
                or p.base_id != ctx.base_id
                or header["campanha_id"] != p.campanha_id
                or header["preparacao_id"] != p.id
                or header["formato"] != 1
            ):
                raise ValueError
            validar_cabecalho(header)
            return header
        except ValueError, KeyError, TypeError:
            raise indisponivel() from None

    @staticmethod
    def _hash_item(hasher, item):
        get = item.get if isinstance(item, dict) else lambda k: getattr(item, k)
        hasher.update(
            canon(
                [
                    get("identidade_contato_id"),
                    get("incluido"),
                    get("motivos_json"),
                    get("evento_consentimento_id"),
                    get("bytes_texto"),
                    (get("nonce") or b"").hex(),
                    hashlib.sha256(get("conteudo_cifrado") or b"").hexdigest(),
                ]
            )
        )

    @staticmethod
    def _hash_item_telegram(hasher, item):
        get = item.get if isinstance(item, dict) else lambda k: getattr(item, k)
        hasher.update(
            canon(
                [
                    get("conta_envio_id"),
                    get("identidade_telegram_id"),
                    get("inscricao_id"),
                    get("incluido"),
                    get("motivos_json"),
                    get("evento_telegram_id"),
                    get("bytes_texto"),
                    (get("nonce") or b"").hex(),
                    hashlib.sha256(get("conteudo_cifrado") or b"").hexdigest(),
                ]
            )
        )

    def _integridade(self, s, p, header, content):
        digest = hashlib.sha256(canon(header))
        count = included = total = 0
        if header["provedor"] == "telegram_bot_api":
            for item in s.scalars(
                select(PreparacaoItemTelegram)
                .where(PreparacaoItemTelegram.preparacao_id == p.id)
                .order_by(PreparacaoItemTelegram.identidade_telegram_id)
                .execution_options(yield_per=50)
            ):
                self._hash_item_telegram(digest, item)
                count += 1
                included += item.incluido
                total += item.bytes_texto
            if s.scalar(
                select(PreparacaoItem.preparacao_id)
                .where(PreparacaoItem.preparacao_id == p.id)
                .limit(1)
            ):
                raise indisponivel()
        else:
            for item in s.scalars(
                select(PreparacaoItem)
                .where(PreparacaoItem.preparacao_id == p.id)
                .order_by(PreparacaoItem.identidade_contato_id)
                .execution_options(yield_per=50)
            ):
                self._hash_item(digest, item)
                count += 1
                included += item.incluido
                total += item.bytes_texto
        expected = content.decrypt(p.manifesto_nonce, p.manifesto, aad(header, "manifesto"))
        if (
            not hmac.compare_digest(digest.digest(), expected)
            or count != p.incluidos + p.excluidos
            or included != p.incluidos
            or total != p.bytes_texto
            or not hmac.compare_digest(
                p.impressao, hashlib.sha256(p.manifesto_nonce + p.manifesto).hexdigest()
            )
        ):
            raise indisponivel()

    def _obter(self, s, identifier, prep_id):
        p = self.repo.obter(s, Preparacao, prep_id)
        if p.campanha_id != identifier:
            raise NegocioError("nao_encontrado", "Preparacao indisponivel.", 404)
        return p

    @staticmethod
    def _revisao_autenticada(p, header):
        # Formato 1 ja autentica base/abertura/versao/contador no AAD e manifesto.
        base, abertura, versao, contador, *config = header["marcador"].split(".")
        expected = (
            [header["configuracao_simulacao"]["resumo_configuracao"]]
            if "configuracao_simulacao" in header
            else []
        )
        if config != expected:
            raise ValueError
        origem, revisao = int(versao), int(contador)
        if (
            base != p.base_id
            or str(UUID(abertura)) != abertura
            or origem < 1
            or revisao < 0
            or str(origem) != versao
            or str(revisao) != contador
            or origem != p.versao_origem
            or revisao + 1 != p.revisao_pos
        ):
            raise ValueError
        return revisao + 1

    def _autenticar_fotografia(self, s, p, ctx, root):
        try:
            header = self._header(p, ctx)
            key = root.decrypt(p.chave_nonce, p.chave_cifrada, aad(header, "chave"))
            with GCM(key) as content:
                self._integridade(s, p, header, content)
                revisao = self._revisao_autenticada(p, header)
            return header, key, revisao
        except NegocioError, ValueError, KeyError, TypeError, OverflowError:
            raise NegocioError(
                "integridade_comprometida",
                "Nao foi possivel autenticar a fotografia. Leitura e confirmacao bloqueadas. "
                "Preserve os dados.",
                503,
            ) from None

    def _atuais(self, s, header, items):
        # A fotografia determina identidades e finalidade; nunca o rascunho atual.
        try:
            structural = self.c._referencias(s, header, header["listas"])["problemas"]
        except NegocioError:
            structural = ["referencia_invalida"]
        if header["provedor"] == "telegram_bot_api":
            return self._atuais_telegram(s, header, items, structural)
        belongs = (
            select(ListaContato.contato_id)
            .join(Lista, Lista.id == ListaContato.lista_id)
            .where(
                ListaContato.contato_id == IdentidadeContato.contato_id,
                ListaContato.lista_id.in_(header["listas"]),
                Lista.ativo == 1,
            )
            .exists()
        )
        rows = s.execute(
            select(IdentidadeContato, Contato, TelefoneContato, PreferenciaContato, belongs)
            .join(Contato, Contato.id == IdentidadeContato.contato_id)
            .join(TelefoneContato, TelefoneContato.id == IdentidadeContato.telefone_id)
            .outerjoin(
                PreferenciaContato,
                and_(
                    PreferenciaContato.identidade_contato_id == IdentidadeContato.id,
                    PreferenciaContato.escopo_id == header["escopo_id"],
                    PreferenciaContato.categoria == header["categoria"],
                ),
            )
            .where(IdentidadeContato.id.in_([i.identidade_contato_id for i in items]))
        )
        result = {}
        for identity, contact, phone, pref, member in rows:
            reasons = self.c.consentimentos.avaliar_dados(contact, identity, phone, pref)["motivos"]
            guard = getattr(self.repo.database, "suspensoes_consentimento", None)
            if guard is not None and guard.afetado(
                contact.id, identity.id, header["escopo_id"], header["categoria"]
            ):
                reasons.append("descadastramento_pendente")
            reasons.extend(structural)
            if identity.canal != header["plataforma"]:
                reasons.append("canal_incompativel")
            if not member:
                reasons.append("fora_das_listas")
            result[identity.id] = {
                "nome_atual": contact.nome,
                "destino_mascarado": "***" + phone.telefone_e164[-4:],
                "impedimentos_atuais": reasons,
            }
        return result

    def _atuais_telegram(self, s, header, items, structural):
        result = {}
        account = s.get(ContaEnvio, header["conta_envio_id"])
        for item in items:
            identity = s.get(IdentidadeTelegram, item.identidade_telegram_id)
            subscription = s.get(InscricaoConta, item.inscricao_id)
            if account is None or identity is None or subscription is None:
                continue
            contact = s.get(Contato, identity.contato_id)
            if contact is None:
                continue
            eligibility = self.c.consentimentos._avaliar_telegram(
                s,
                account,
                identity,
                contact,
                subscription,
                header["escopo_id"],
                header["categoria"],
            )
            reasons = [*eligibility["motivos"], *structural]
            member = s.scalar(
                select(ListaContato.contato_id)
                .join(Lista, Lista.id == ListaContato.lista_id)
                .where(
                    ListaContato.contato_id == contact.id,
                    ListaContato.lista_id.in_(header["listas"]),
                    Lista.ativo == 1,
                )
                .limit(1)
            )
            if member is None:
                reasons.append("fora_das_listas")
            if (
                item.conta_envio_id != header["conta_envio_id"]
                or header.get("tipo_autorizacao") != "envio_real"
            ):
                reasons.append("vinculo_telegram_incompativel")
            result[identity.id] = {
                "nome_atual": contact.nome,
                "destino_mascarado": "***" + identity.chat_externo_id[-4:],
                "impedimentos_atuais": sorted(set(reasons)),
            }
        return result

    def consultar(self, identifier, token, prep_id=None, pagina=1, limite=20):
        with self.repo.transacao() as s:
            self.auth.current(token, session=s)
            self.repo.obter(s, Campanha, identifier)
            if prep_id is None:
                query = select(Preparacao).where(Preparacao.campanha_id == identifier)
                preps = s.scalars(
                    query.order_by(Preparacao.criado_em.desc(), Preparacao.id)
                    .offset((pagina - 1) * limite)
                    .limit(limite)
                ).all()
                total = s.scalar(
                    select(func.count())
                    .select_from(Preparacao)
                    .where(Preparacao.campanha_id == identifier)
                )
                if not preps:
                    # Lista vazia nao apresenta fotografia nem exige inicializar chave.
                    self.auth.current(token, session=s)
                    return {"itens": [], "total": total, "aviso": AVISO}
            ctx = self._ctx(s)
            # A chave local e verificada antes de classificar qualquer fotografia.
            with GCM(abrir(ctx)) as root:
                if prep_id is None:
                    views = []
                    for p in preps:
                        self.auth.current(token, session=s)
                        try:
                            header, _, _ = self._autenticar_fotografia(s, p, ctx, root)
                        except NegocioError as error:
                            if error.codigo != "integridade_comprometida":
                                raise
                            # Apenas localizador; nao confiar em quantidades ou confirmacao.
                            views.append(
                                {
                                    "preparacao_id": p.id,
                                    "integridade": "comprometida",
                                    "bloqueada": True,
                                }
                            )
                        else:
                            views.append(
                                {
                                    **self._view(p),
                                    "integridade": "verificada",
                                    "bloqueada": False,
                                    **(
                                        {"configuracao_simulacao": header["configuracao_simulacao"]}
                                        if "configuracao_simulacao" in header
                                        else {}
                                    ),
                                    **(
                                        {"autorizacao_requerida": header["tipo_autorizacao"]}
                                        if "tipo_autorizacao" in header
                                        else {}
                                    ),
                                }
                            )
                    result = {"itens": views, "total": total, "aviso": AVISO}
                else:
                    p = self._obter(s, identifier, prep_id)
                    header, _, revisao = self._autenticar_fotografia(s, p, ctx, root)
                    # Uma verificacao integral por fotografia/pagina; nenhum texto aberto.
                    telegram = header["provedor"] == "telegram_bot_api"
                    if telegram:
                        items = s.scalars(
                            select(PreparacaoItemTelegram)
                            .where(PreparacaoItemTelegram.preparacao_id == p.id)
                            .order_by(PreparacaoItemTelegram.identidade_telegram_id)
                            .offset((pagina - 1) * limite)
                            .limit(limite)
                        ).all()
                    else:
                        items = s.scalars(
                            select(PreparacaoItem)
                            .where(PreparacaoItem.preparacao_id == p.id)
                            .order_by(PreparacaoItem.identidade_contato_id)
                            .offset((pagina - 1) * limite)
                            .limit(limite)
                        ).all()
                    current = self._atuais(s, header, items)
                    result = {
                        **self._view(p),
                        "referencias_congeladas": {
                            k: v for k, v in header.items() if k != "marcador"
                        },
                        "integridade": "verificada",
                        "bloqueada": False,
                        "total": p.incluidos + p.excluidos,
                        "itens": [self._item_view(i, current, telegram) for i in items],
                        "avaliacao_atual": "consentimento_supressao_e_referencias_congeladas",
                        "avaliado_em": agora(),
                        "revisao_desatualizada": p.confirmado_em is None
                        and revisao != self.repo.revisao(s),
                        "aviso": AVISO,
                    }
            self.auth.current(token, session=s)
            return result

    @staticmethod
    def _item_view(item, current, telegram):
        identity = item.identidade_telegram_id if telegram else item.identidade_contato_id
        return {
            "identidade_contato_id": None if telegram else identity,
            "identidade_telegram_id": identity if telegram else None,
            "inscricao_id": item.inscricao_id if telegram else None,
            "incluido": bool(item.incluido),
            "motivos": json.loads(item.motivos_json),
            **current.get(
                identity,
                {
                    "nome_atual": None,
                    "destino_mascarado": None,
                    "impedimentos_atuais": ["identidade_indisponivel"],
                },
            ),
        }

    @staticmethod
    def _view(p):
        return {
            "preparacao_id": p.id,
            "incluidos": p.incluidos,
            "excluidos": p.excluidos,
            "bytes_texto": p.bytes_texto,
            "impressao": p.impressao,
            "confirmado_em": p.confirmado_em,
            "invalidado_em": p.invalidado_em,
            "criado_em": p.criado_em,
        }

    def conteudo(self, identifier, prep_id, identity, token):
        with self.repo.transacao() as s:
            self.auth.current(token, session=s)
            p = self._obter(s, identifier, prep_id)
            ctx = self._ctx(s)
            with GCM(abrir(ctx)) as root:
                header, key, _ = self._autenticar_fotografia(s, p, ctx, root)
            if header["provedor"] == "telegram_bot_api":
                item = self.repo.obter(s, PreparacaoItemTelegram, (p.id, identity))
            else:
                item = self.repo.obter(s, PreparacaoItem, (p.id, identity))
            if not item.incluido:
                raise NegocioError("sem_conteudo", "Identidade excluida da fotografia.", 409)
            try:
                with GCM(key) as content:
                    raw = content.decrypt(
                        item.nonce, item.conteudo_cifrado, aad(header, "texto", identity)
                    )
                value = raw.decode("utf-8")
            except NegocioError, UnicodeError, TypeError:
                raise NegocioError(
                    "integridade_comprometida",
                    "Nao foi possivel autenticar o conteudo. Preserve os dados.",
                    503,
                ) from None
            self.auth.current(token, session=s)
            return {"conteudo_renderizado": value, "aviso": AVISO}

    def transicao(self, identifier, data, token, confirmar=False):
        action = "confirmar_preparacao" if confirmar else "retornar_rascunho"
        with self.repo.transacao() as s:
            user, digest, old = self.c._repeticao(s, data, token, action, identifier)
            if old is not None:
                return old
            campaign = self.repo.obter(s, Campanha, identifier)
            p = self._obter(s, identifier, str(data.preparacao_id))
            if (
                campaign.status != "preparada"
                or campaign.versao != data.versao
                or p.invalidado_em is not None
            ):
                raise conflito()
            if confirmar:
                if p.confirmado_em is not None or p.impressao != data.impressao:
                    raise conflito()
                ctx = self._ctx(s, True)
                with GCM(abrir(ctx)) as root:
                    _, _, revisao = self._autenticar_fotografia(s, p, ctx, root)
                if revisao != self.repo.revisao(s):
                    raise conflito()
                self.c._validar(s, campanha_view(campaign), self.c._listas(s, identifier))
                p.confirmado_em, p.confirmado_por_usuario_id = agora(), user
            else:
                encerrado_em = encerrar_para_rascunho(self.repo, s, identifier, p.id, user)
                revogar_para_rascunho(self.repo, s, identifier, p.id, user)
                p.invalidado_em = max(agora(), p.criado_em, encerrado_em or p.criado_em)
                s.flush()
                campaign.status = "rascunho"
            campaign.versao += 1
            campaign.atualizado_em = max(
                agora(), campaign.atualizado_em, p.invalidado_em or p.criado_em
            )
            result = self._recibo(s, data, user, digest, action, campaign, p)
            self.auth.current(token, session=s)
            return result
