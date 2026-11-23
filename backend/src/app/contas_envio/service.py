"""Gerenciamento local; autorização efetiva e auditoria na transação de cada efeito."""

import hashlib
import hmac
import json

from sqlalchemy import func, select

from app.contas_envio.models import ContaEnvio
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Auditoria
from app.contatos.normalizacao import busca
from app.contatos.repository import agora, uuid
from app.provedores.telegram import Telegram


def administrador(auth, token, session=None):
    user = auth.current(token, session=session).usuario
    if user.perfil != "administrador":
        raise NegocioError("acesso_negado", "Somente o administrador pode gerenciar contas.", 403)
    return user.id


class ContasService:
    def __init__(self, repository, auth, segredos, telegram=None, whatsapp=None):
        self.repo, self.auth, self.segredos = repository, auth, segredos
        self.telegram = telegram or Telegram()
        self.whatsapp = whatsapp

    def _conta_whatsapp(self, identifier, token, *, exigir_ativa=False, versao=None):
        with self.repo.transacao() as s:
            administrador(self.auth, token, s)
            item = self.repo.obter(s, ContaEnvio, identifier)
            if item.provedor != "qr_candidato":
                raise NegocioError(
                    "whatsapp_conta_invalida", "Selecione uma conta WhatsApp QR."
                )
            if versao is not None and item.versao != versao:
                raise conflito()
            if exigir_ativa and (not item.ativo or item.removido_em):
                raise NegocioError(
                    "whatsapp_conta_inativa", "Ative a conta WhatsApp antes de conectar."
                )
            return item.versao

    def status_whatsapp(self, identifier, token):
        self._conta_whatsapp(identifier, token)
        return self.whatsapp.status(identifier)

    def conectar_whatsapp(self, identifier, token, *, parear):
        with self.repo.lock:
            self._conta_whatsapp(identifier, token, exigir_ativa=True)
            status = self.whatsapp.status(identifier)
            if parear and status["sessao_persistida"]:
                raise NegocioError(
                    "whatsapp_sessao_existente",
                    "A conta já possui sessão. Use Reconectar.",
                    409,
                )
            if not parear and not status["sessao_persistida"]:
                raise NegocioError(
                    "whatsapp_sem_sessao",
                    "A conta ainda não possui sessão. Inicie o pareamento.",
                    409,
                )
            return self.whatsapp.conectar(identifier)

    def desconectar_whatsapp(self, identifier, token):
        self._conta_whatsapp(identifier, token)
        return self.whatsapp.desconectar(identifier)

    def desvincular_whatsapp(self, identifier, data, token):
        with self.repo.lock:
            with self.repo.transacao() as s:
                user, digest, replay = self._repeticao(
                    s, data, token, "solicitar_desvinculo_whatsapp", identifier
                )
                item = self.repo.obter(s, ContaEnvio, identifier)
                if item.versao != data.versao:
                    raise conflito()
                if item.provedor != "qr_candidato":
                    raise NegocioError(
                        "whatsapp_conta_invalida", "Selecione uma conta WhatsApp QR."
                    )
                if replay is None:
                    self._registrar(
                        s, data, user, digest, "solicitar_desvinculo_whatsapp", item
                    )
            return self.whatsapp.desvincular(identifier)

    def verificar_telegram(self, identifier, token):
        with self.repo.transacao() as s:
            administrador(self.auth, token, s)
            item = self.repo.obter(s, ContaEnvio, identifier)
            if item.provedor != "telegram_bot_api" or not item.ativo or item.removido_em:
                raise NegocioError("telegram_conta_invalida", "Selecione uma conta Telegram ativa.")
            if not item.segredo_ref:
                raise NegocioError("telegram_sem_credencial", "Armazene a credencial da conta.")
            versao, referencia = item.versao, item.segredo_ref
            secret = self.segredos.recuperar(item.id, item.provedor, referencia)
        result = self.telegram.verificar(secret)
        # Não manter transação/lock durante rede; descartar resultado de contexto alterado.
        with self.repo.transacao() as s:
            administrador(self.auth, token, s)
            item = self.repo.obter(s, ContaEnvio, identifier)
            if item.versao != versao or item.segredo_ref != referencia:
                raise conflito()
        return result

    def _view(self, item):
        config = "aguarda_integracao" if item.provedor == "qr_candidato" else "pendente"
        if item.provedor == "mock":
            config = "simulacao_sem_credenciais"
        elif item.segredo_ref:
            try:
                self.segredos.recuperar(item.id, item.provedor, item.segredo_ref)
                config = "credencial_armazenada"
            except NegocioError:
                config = "armazenamento_indisponivel"
        return {
            "id": item.id,
            "nome": item.nome,
            "plataforma": item.plataforma,
            "provedor": item.provedor,
            "ativo": bool(item.ativo),
            "estado_cadastral": "removida"
            if item.removido_em
            else ("ativa" if item.ativo else "desativada"),
            "configuracao_local": config,
            "conexao": "nao_aplicavel" if item.provedor == "mock" else "nao_verificada",
            **(
                {"modo": "simulacao", "aviso": "Simulacao - sem envio externo."}
                if item.provedor == "mock"
                else {}
            ),
            "versao": item.versao,
            "criado_em": item.criado_em,
            "atualizado_em": item.atualizado_em,
            "removido_em": item.removido_em,
        }

    def listar(
        self, token, pagina=1, limite=20, q="", plataforma=None, provedor=None, estado="ativa"
    ):
        with self.repo.transacao() as s:
            administrador(self.auth, token, s)
            filters = (
                [ContaEnvio.removido_em.is_not(None)]
                if estado == "removida"
                else [ContaEnvio.removido_em.is_(None), ContaEnvio.ativo == int(estado == "ativa")]
            )
            if q:
                filters.append(ContaEnvio.nome_busca.contains(busca(q.strip()), autoescape=True))
            if plataforma:
                filters.append(ContaEnvio.plataforma == plataforma)
            if provedor:
                filters.append(ContaEnvio.provedor == provedor)
            total = s.scalar(select(func.count()).select_from(ContaEnvio).where(*filters))
            rows = list(
                s.scalars(
                    select(ContaEnvio)
                    .where(*filters)
                    .order_by(ContaEnvio.nome_busca, ContaEnvio.id)
                    .offset((pagina - 1) * limite)
                    .limit(limite)
                )
            )
            somente_mock = provedor == "mock" or (rows and all(i.provedor == "mock" for i in rows))
            return {
                "itens": [self._view(i) for i in rows],
                "total": total,
                "limpeza_pendente": False if somente_mock else self._limpeza_pendente(s),
            }

    def obter(self, identifier, token):
        with self.repo.transacao() as s:
            administrador(self.auth, token, s)
            item = self.repo.obter(s, ContaEnvio, identifier)
            return {
                **self._view(item),
                "limpeza_pendente": False if item.provedor == "mock" else self._limpeza_pendente(s),
            }

    def _repeticao(self, s, data, token, acao, identifier, digest_secret=None):
        user = administrador(self.auth, token, s)
        fields = data.model_dump(mode="json", exclude_unset=True, exclude={"credencial"})
        request = {"acao": acao, "conta_id": identifier, "usuario_id": user, "entrada": fields}
        raw = json.dumps(request, sort_keys=True, ensure_ascii=True).encode("ascii")
        digest = hashlib.sha256(raw).hexdigest() if digest_secret is None else digest_secret(raw)
        old = s.get(Auditoria, str(data.operacao_id))
        if old:
            summary = json.loads(old.resumo_json)
            if old.entidade != "conta_envio" or not hmac.compare_digest(
                summary.get("requisicao_hash", ""), digest
            ):
                raise conflito()
            return user, digest, summary["resultado"]
        return user, digest, None

    def _registrar(self, s, data, user, digest, acao, item):
        result = {"id": item.id, "versao": item.versao}
        s.add(
            Auditoria(
                id=str(data.operacao_id),
                usuario_id=user,
                acao=acao,
                entidade="conta_envio",
                entidade_id=item.id,
                resumo_json=json.dumps({"requisicao_hash": digest, "resultado": result}),
                ocorrido_em=agora(),
            )
        )
        s.flush()
        return result

    def criar(self, data, token):
        with self.repo.transacao() as s:
            user, digest, replay = self._repeticao(s, data, token, "criar", None)
            if replay is not None:
                return replay
            now = agora()
            item = ContaEnvio(
                id=uuid(),
                nome=data.nome,
                nome_busca=busca(data.nome),
                plataforma=data.plataforma,
                provedor=data.provedor,
                ativo=1,
                versao=1,
                criado_por_usuario_id=user,
                criado_em=now,
                atualizado_em=now,
            )
            s.add(item)
            return self._registrar(s, data, user, digest, "criar", item)

    def _editavel(self, s, identifier, versao):
        item = self.repo.obter(s, ContaEnvio, identifier)
        if item.versao != versao or item.removido_em is not None:
            raise conflito()
        return item

    def editar(self, identifier, data, token):
        with self.repo.lock:
            result, provider, whatsapp_action = self._editar(identifier, data, token)
            whatsapp_cleanup_pending = False
            if whatsapp_action == "suspender" and self.whatsapp is not None:
                self.whatsapp.suspender(identifier)
            elif whatsapp_action == "desvincular" and self.whatsapp is not None:
                try:
                    status = self.whatsapp.desvincular(identifier)
                    whatsapp_cleanup_pending = status.get("estado") == "desvinculando"
                except NegocioError:
                    # A remoção lógica já foi confirmada. Preservar a sessão permite
                    # repetir o desvínculo sem perder cadastro ou histórico.
                    whatsapp_cleanup_pending = True
            return {
                **result,
                "limpeza_pendente": False if provider == "mock" else not self.recuperar(),
                **(
                    {"whatsapp_limpeza_pendente": whatsapp_cleanup_pending}
                    if whatsapp_action == "desvincular"
                    else {}
                ),
            }

    def _editar(self, identifier, data, token):
        with self.repo.transacao() as s:
            user, digest, replay = self._repeticao(s, data, token, "editar", identifier)
            if replay is not None:
                item = self.repo.obter(s, ContaEnvio, identifier)
                action = (
                    "desvincular"
                    if item.provedor == "qr_candidato" and data.remover
                    else "suspender"
                    if item.provedor == "qr_candidato" and data.ativo is False
                    else None
                )
                return replay, item.provedor, action
            item = self._editavel(s, identifier, data.versao)
            if data.remover:
                item.ativo, item.removido_em, item.segredo_ref = 0, agora(), None
            else:
                if data.nome is not None:
                    item.nome, item.nome_busca = data.nome, busca(data.nome)
                if data.ativo is not None:
                    item.ativo = int(data.ativo)
            item.versao += 1
            item.atualizado_em = agora()
            whatsapp_action = (
                "desvincular"
                if item.provedor == "qr_candidato" and data.remover
                else "suspender"
                if item.provedor == "qr_candidato" and data.ativo is False
                else None
            )
            return (
                self._registrar(s, data, user, digest, "editar", item),
                item.provedor,
                whatsapp_action,
            )

    def _limpeza_pendente(self, s):
        refs = {(i.id, i.segredo_ref) for i in s.scalars(select(ContaEnvio)) if i.segredo_ref}
        return self.segredos.pendente(refs)

    def recuperar(self):
        # Uma falha na limpeza nunca desfaz um commit já concluído. O recibo informa
        # pendência e a próxima operação/inicialização tenta a limpeza novamente.
        with self.repo.transacao() as s:
            refs = {(i.id, i.segredo_ref) for i in s.scalars(select(ContaEnvio)) if i.segredo_ref}
            try:
                self.segredos.limpar(refs)
                return True
            except NegocioError:
                return False

    def credencial(self, identifier, data, token, *, excluir=False):
        with self.repo.lock:
            result = self._credencial(identifier, data, token, excluir=excluir)
            result = {**result, "limpeza_pendente": not self.recuperar()}
            return result

    def _credencial(self, identifier, data, token, *, excluir):
        with self.repo.transacao() as s:
            # Antes de tocar na chave DPAPI, autoriza novamente após o lock.
            administrador(self.auth, token, s)
            if self.repo.obter(s, ContaEnvio, identifier).provedor == "mock":
                raise NegocioError(
                    "credencial_nao_aplicavel", "Conta mock nao admite credenciais.", 422
                )
            secret = "" if excluir else data.credencial.get_secret_value()
            sign = None if excluir else lambda raw: self.segredos.assinatura(raw, secret)
            action = "excluir_credencial" if excluir else "armazenar_credencial"
            user, digest, replay = self._repeticao(s, data, token, action, identifier, sign)
            if replay is not None:
                return replay
            item = self._editavel(s, identifier, data.versao)
            if item.provedor == "qr_candidato":
                raise NegocioError(
                    "aguarda_integracao",
                    "WhatsApp QR aguarda integração experimental; não recebe credenciais.",
                )
            # Geração anterior só fica órfã após commit. Rollback mantém referência anterior.
            generation = None if excluir else self.segredos.gravar(item.id, item.provedor, secret)
            item.segredo_ref = generation
            item.versao += 1
            item.atualizado_em = agora()
            return self._registrar(s, data, user, digest, action, item)
