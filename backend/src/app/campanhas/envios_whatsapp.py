"""Uma tentativa WhatsApp confirmada, sem fila ou repeticao automatica."""

import hashlib
import hmac
import json
import secrets

from sqlalchemy import func, select, update

from app.auth.errors import AuthError
from app.campanhas.crypto import GCM, indisponivel
from app.campanhas.models import (
    AutorizacaoEnvioWhatsapp,
    Campanha,
    PreparacaoItem,
    TentativaEnvioWhatsapp,
)
from app.campanhas.preparacoes import aad, abrir
from app.consentimentos.models import IdentidadeContato, PreferenciaContato
from app.contas_envio.models import ContaEnvio
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import Contato, TelefoneContato
from app.contatos.repository import agora, uuid
from app.persistencia.models import utc_text
from app.provedores.whatsapp_baileys import ResultadoEnvioWhatsapp


class EnviosWhatsappService:
    def __init__(self, preparacoes, whatsapp):
        self.p, self.c = preparacoes, preparacoes.c
        self.repo, self.auth = preparacoes.repo, preparacoes.auth
        self.whatsapp = whatsapp
        self._chave_marcador = secrets.token_bytes(32)

    def _usuario(self, session, token):
        user = self.auth.current(token, session=session).usuario
        if user.perfil not in {"administrador", "operador"}:
            raise NegocioError("acesso_negado", "Perfil sem permissao para operar campanhas.", 403)
        return user.id

    def _avaliar(self, session, campanha_id, preparacao_id):
        campanha = self.repo.obter(session, Campanha, campanha_id)
        prep = self.p._obter(session, campanha_id, preparacao_id)
        quantidade = session.scalar(
            select(func.count())
            .select_from(PreparacaoItem)
            .where(PreparacaoItem.preparacao_id == prep.id)
        )
        if quantidade != 1:
            raise NegocioError(
                "envio_whatsapp_unitario_obrigatorio",
                "Selecione exatamente um destinatario WhatsApp.",
                409,
            )
        try:
            ctx = self.p._ctx(session, True)
            with GCM(abrir(ctx)) as root:
                header, key, _ = self.p._autenticar_fotografia(session, prep, ctx, root)
        except NegocioError:
            raise indisponivel() from None
        item = session.scalar(select(PreparacaoItem).where(PreparacaoItem.preparacao_id == prep.id))
        if (
            item is None
            or header.get("provedor") != "qr_candidato"
            or header.get("plataforma") != "whatsapp"
            or header.get("tipo_autorizacao") != "envio_real"
        ):
            raise NegocioError("configuracao_incompativel", "Preparacao WhatsApp invalida.", 409)
        conta = session.get(ContaEnvio, campanha.conta_envio_id)
        identidade = session.get(IdentidadeContato, item.identidade_contato_id)
        contato = session.get(Contato, identidade.contato_id) if identidade else None
        telefone = session.get(TelefoneContato, identidade.telefone_id) if identidade else None
        preferencia = (
            session.get(
                PreferenciaContato,
                (campanha.escopo_id, item.identidade_contato_id, campanha.categoria),
            )
            if identidade
            else None
        )
        impedimentos = []
        if (
            campanha.status != "preparada"
            or prep.confirmado_em is None
            or prep.invalidado_em is not None
            or prep.incluidos != 1
            or prep.excluidos != 0
            or not item.incluido
        ):
            impedimentos.append("preparacao_invalida")
        if (
            conta is None
            or conta.provedor != "qr_candidato"
            or conta.plataforma != "whatsapp"
            or not conta.ativo
            or conta.removido_em is not None
        ):
            impedimentos.append("conta_whatsapp_incompativel")
        if not all((identidade, contato, telefone, preferencia)):
            impedimentos.append("vinculo_whatsapp_indisponivel")
        atuais = self.p._atuais(session, header, [item])
        impedimentos.extend(
            atuais.get(item.identidade_contato_id, {}).get(
                "impedimentos_atuais", ["identidade_indisponivel"]
            )
        )
        try:
            with GCM(key) as content:
                texto = content.decrypt(
                    item.nonce,
                    item.conteudo_cifrado,
                    aad(header, "texto", item.identidade_contato_id),
                ).decode("utf-8")
        except NegocioError, UnicodeError, TypeError:
            raise indisponivel() from None
        if not texto.strip() or len(texto.encode("utf-8")) > 65_536:
            impedimentos.append("conteudo_whatsapp_invalido")
        if impedimentos:
            raise NegocioError(
                "envio_whatsapp_impedido",
                "As condicoes atuais nao permitem o envio WhatsApp.",
                409,
            )
        return {
            "campanha": campanha,
            "preparacao": prep,
            "item": item,
            "conta": conta,
            "identidade": identidade,
            "contato": contato,
            "telefone": telefone,
            "evento_id": preferencia.evento_origem_id,
            "texto": texto,
            "conteudo_hash": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
        }

    def _marcador(self, session, usuario, contexto):
        payload = {
            "finalidade": "envio-whatsapp-v1",
            "abertura": self.p.abertura,
            "usuario_id": usuario,
            "campanha_id": contexto["campanha"].id,
            "versao": contexto["campanha"].versao,
            "preparacao_id": contexto["preparacao"].id,
            "impressao": contexto["preparacao"].impressao,
            "conta_id": contexto["conta"].id,
            "conta_versao": contexto["conta"].versao,
            "identidade_id": contexto["identidade"].id,
            "destino": contexto["telefone"].telefone_e164,
            "evento_id": contexto["evento_id"],
            "conteudo_hash": contexto["conteudo_hash"],
            "confirmado_em": utc_text(contexto["preparacao"].confirmado_em),
            "revisao": self.repo.revisao(session),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        return (
            "envio-whatsapp-v1." + hmac.new(self._chave_marcador, raw, hashlib.sha256).hexdigest()
        )

    def revisao(self, campanha_id, preparacao_id, token):
        with self.repo.transacao() as session:
            usuario = self._usuario(session, token)
            contexto = self._avaliar(session, campanha_id, preparacao_id)
            if session.scalar(
                select(AutorizacaoEnvioWhatsapp.id).where(
                    AutorizacaoEnvioWhatsapp.preparacao_id == preparacao_id
                )
            ):
                raise conflito()
            return {
                "canal": "whatsapp",
                "campanha_id": campanha_id,
                "preparacao_id": preparacao_id,
                "versao": contexto["campanha"].versao,
                "impressao": contexto["preparacao"].impressao,
                "conta": {
                    "id": contexto["conta"].id,
                    "nome": contexto["conta"].nome,
                },
                "destinatario": {
                    "identidade_contato_id": contexto["identidade"].id,
                    "nome": contexto["contato"].nome,
                    "destino_mascarado": "***" + contexto["telefone"].telefone_e164[-4:],
                },
                "categoria": contexto["campanha"].categoria,
                "conteudo": contexto["texto"],
                "marcador_envio": self._marcador(session, usuario, contexto),
                "aviso": "Aceitacao pelo WhatsApp nao confirma entrega. Confirme antes de enviar.",
            }

    @staticmethod
    def _recibo(tentativa):
        return {
            "tentativa_id": tentativa.id,
            "estado": tentativa.estado,
            "codigo_resultado": tentativa.codigo_resultado,
            "message_id": tentativa.message_id,
            "aceitacao_nao_e_entrega": tentativa.estado == "confirmada",
            "reenvio_automatico": False,
        }

    @staticmethod
    def _hash_requisicao(data, usuario, campanha_id, preparacao_id):
        payload = {
            "acao": "enviar_whatsapp_real",
            "usuario_id": usuario,
            "campanha_id": campanha_id,
            "preparacao_id": preparacao_id,
            "entrada": data.model_dump(mode="json"),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")
        ).hexdigest()

    def _persistir_resultado(self, tentativa_id, resultado):
        with self.repo.transacao() as session:
            tentativa = self.repo.obter(session, TentativaEnvioWhatsapp, tentativa_id)
            if tentativa.estado != "iniciada":
                return self._recibo(tentativa)
            now = agora()
            tentativa.estado = resultado.estado
            tentativa.codigo_resultado = resultado.codigo
            tentativa.message_id = resultado.message_id
            tentativa.finalizada_em = now
            tentativa.atualizado_em = now
            session.flush()
            return self._recibo(tentativa)

    def confirmar(self, campanha_id, preparacao_id, data, token):
        with self.repo.lock:
            with self.repo.transacao() as session:
                usuario = self._usuario(session, token)
                digest = self._hash_requisicao(data, usuario, campanha_id, preparacao_id)
                anterior = session.scalar(
                    select(AutorizacaoEnvioWhatsapp).where(
                        AutorizacaoEnvioWhatsapp.operacao_id == str(data.operacao_id)
                    )
                )
                if anterior is not None:
                    if not hmac.compare_digest(anterior.requisicao_hash, digest):
                        raise conflito()
                    tentativa = session.scalar(
                        select(TentativaEnvioWhatsapp).where(
                            TentativaEnvioWhatsapp.autorizacao_id == anterior.id
                        )
                    )
                    return self._recibo(tentativa)
                contexto = self._avaliar(session, campanha_id, preparacao_id)
                if (
                    contexto["campanha"].versao != data.versao
                    or contexto["preparacao"].impressao != data.impressao
                    or not hmac.compare_digest(
                        self._marcador(session, usuario, contexto), data.marcador_envio
                    )
                ):
                    raise conflito()
                now, autorizacao_id, tentativa_id = agora(), uuid(), uuid()
                autorizacao = AutorizacaoEnvioWhatsapp(
                    id=autorizacao_id,
                    operacao_id=str(data.operacao_id),
                    requisicao_hash=digest,
                    campanha_id=campanha_id,
                    preparacao_id=preparacao_id,
                    conta_envio_id=contexto["conta"].id,
                    identidade_contato_id=contexto["identidade"].id,
                    evento_consentimento_id=contexto["evento_id"],
                    destino_e164=contexto["telefone"].telefone_e164,
                    conta_versao=contexto["conta"].versao,
                    impressao=contexto["preparacao"].impressao,
                    conteudo_hash=contexto["conteudo_hash"],
                    autorizado_por_usuario_id=usuario,
                    autorizado_em=now,
                )
                tentativa = TentativaEnvioWhatsapp(
                    id=tentativa_id,
                    autorizacao_id=autorizacao_id,
                    conta_envio_id=contexto["conta"].id,
                    estado="iniciada",
                    codigo_resultado=None,
                    message_id=None,
                    iniciada_em=now,
                    finalizada_em=None,
                    atualizado_em=now,
                )
                session.add_all((autorizacao, tentativa))
                self.repo.auditar(
                    session,
                    usuario,
                    "autorizar_envio_whatsapp",
                    "campanha",
                    campanha_id,
                    autorizacao_id=autorizacao_id,
                    tentativa_id=tentativa_id,
                )
                session.flush()
                conta_id = contexto["conta"].id
                destino = contexto["telefone"].telefone_e164
                texto = contexto["texto"]

            # Lock compartilhado continua retido durante a unica tentativa.
            try:
                self.auth.current(token)
            except AuthError:
                resultado = ResultadoEnvioWhatsapp("falhou", "sessao_encerrada")
            else:
                try:
                    resultado = self.whatsapp.enviar(conta_id, destino, texto, tentativa_id)
                except Exception:
                    resultado = ResultadoEnvioWhatsapp("desconhecida", "sem_confirmacao")
            try:
                return self._persistir_resultado(tentativa_id, resultado)
            except NegocioError:
                try:
                    return self._persistir_resultado(
                        tentativa_id,
                        ResultadoEnvioWhatsapp("desconhecida", "sem_confirmacao"),
                    )
                except NegocioError:
                    return {
                        "tentativa_id": tentativa_id,
                        "estado": "desconhecida",
                        "codigo_resultado": "sem_confirmacao",
                        "message_id": None,
                        "aceitacao_nao_e_entrega": False,
                        "reenvio_automatico": False,
                    }

    def recuperar(self):
        with self.repo.transacao() as session:
            now = agora()
            session.execute(
                update(TentativaEnvioWhatsapp)
                .where(TentativaEnvioWhatsapp.estado == "iniciada")
                .values(
                    estado="desconhecida",
                    codigo_resultado="sem_confirmacao",
                    finalizada_em=now,
                    atualizado_em=now,
                )
            )
