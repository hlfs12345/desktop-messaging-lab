"""Executor Telegram sequencial sobre a fila operacional comum."""

from datetime import timedelta

from sqlalchemy import select

from app.auth.errors import AuthError
from app.campanhas.crypto import GCM, indisponivel
from app.campanhas.execucoes import ABERTOS
from app.campanhas.execucoes_models import (
    ControleEnvioTelegram,
    ExecucaoCampanha,
    ItemExecucao,
    TentativaEnvio,
)
from app.campanhas.executor_mock import ExecutorMock
from app.campanhas.fotografia_operacional import verificar_cabecalho, verificar_item
from app.campanhas.models import (
    AutorizacaoEnvioTelegram,
    Campanha,
    PreparacaoItemTelegram,
)
from app.consentimentos.models import IdentidadeTelegram
from app.contas_envio.models import ContaEnvio
from app.contatos.errors import NegocioError, conflito
from app.contatos.repository import agora, uuid
from app.provedores.telegram import ResultadoEnvioTelegram


class ExecutorTelegram(ExecutorMock):
    def __init__(self, execucoes, envios, recepcao, contexto=None):
        super().__init__(execucoes, contexto)
        self.envios, self.recepcao = envios, recepcao

    def _autorizacao(self, s, e):
        a = self.repo.obter(s, AutorizacaoEnvioTelegram, e.autorizacao_telegram_id)
        if (
            a.modo != "lote"
            or a.campanha_id != e.campanha_id
            or a.preparacao_id != e.preparacao_id
        ):
            raise indisponivel()
        return a

    @staticmethod
    def _autorizacao_vigente(autorizacao):
        return autorizacao.modo == "lote"

    def _fotografia(self, s, e, *, integral=False):
        from app.campanhas.preparacoes import aad, abrir

        campanha = self.repo.obter(s, Campanha, e.campanha_id)
        prep = self.p._obter(s, e.campanha_id, e.preparacao_id)
        autorizacao = self._autorizacao(s, e)
        if campanha.status != "preparada" or prep.invalidado_em or prep.confirmado_em is None:
            raise conflito()
        ctx = self.p._ctx(s, True)
        if not integral:
            operacional = self._contexto()
            adm = operacional._admissoes.get(e.id)
            if adm is None or e.estado != "executando":
                raise indisponivel()
            header = self.p._header(prep, ctx)
            verificar_cabecalho(
                s,
                operacional._selos.get(e.id),
                operacional._chave_selos,
                adm,
                e,
                prep,
                autorizacao,
                header,
            )
        with GCM(abrir(ctx)) as root:
            if integral:
                header, key, _ = self.p._autenticar_fotografia(s, prep, ctx, root)
            else:
                key = root.decrypt(prep.chave_nonce, prep.chave_cifrada, aad(header, "chave"))
        conta = s.get(ContaEnvio, campanha.conta_envio_id)
        if (
            e.modo != "telegram"
            or header.get("provedor") != "telegram_bot_api"
            or header.get("plataforma") != "telegram"
            or conta is None
            or conta.id != autorizacao.conta_envio_id
            or conta.versao != autorizacao.conta_versao
            or conta.telegram_bot_id != autorizacao.bot_externo_id
            or autorizacao.impressao != prep.impressao
            or autorizacao.conteudo_hash != prep.impressao
            or autorizacao.quantidade != prep.incluidos
            or autorizacao.categoria != campanha.categoria
            or any(
                header[k] != getattr(campanha, k)
                for k in ("conta_envio_id", "escopo_id", "template_versao_id", "categoria")
            )
        ):
            raise indisponivel()
        configuracao, resumo = self.envios.configuracao_lote(conta.id)
        if (
            autorizacao.configuracao_hash != resumo
            or autorizacao.intervalo_segundos != configuracao["intervalo_minimo_segundos"]
        ):
            raise indisponivel()
        return prep, header, key, configuracao

    def _acessar_item(self, s, e, t):
        prep, header, key, cfg = self._fotografia(s, e)
        item = self.repo.obter(s, ItemExecucao, t.item_id)
        foto = self.repo.obter(
            s, PreparacaoItemTelegram, (prep.id, item.identidade_telegram_id)
        )
        operacional = self._contexto()
        verificar_item(s, operacional._selos.get(e.id), operacional._chave_selos, item, foto)
        return header, key, cfg, item, foto

    def _conta_id(self, adm):
        with self.repo.transacao() as s:
            e = self._validar_admissao(s, adm)
            return self._autorizacao(s, e).conta_envio_id

    def _esperar_cadencia(self, adm, conta_id):
        while True:
            with self.repo.transacao() as s:
                self._validar_admissao(s, adm)
                controle = s.get(ControleEnvioTelegram, conta_id)
                alvo = controle.liberar_em if controle else None
            restante = (alvo - agora()).total_seconds() if alvo else 0
            if restante <= 0:
                return
            if self._contexto().fechado.wait(min(restante, 1)):
                raise conflito()

    def _pausar_impedimento(self, adm, conta_id, codigo):
        with self.repo.transacao() as s:
            e = self._validar_admissao(s, adm)
            controle = s.get(ControleEnvioTelegram, conta_id)
            if controle is None:
                controle = ControleEnvioTelegram(
                    conta_envio_id=conta_id,
                    liberar_em=None,
                    impedimento_codigo=codigo,
                    impedido_ate=None,
                    atualizado_em=agora(),
                )
                s.add(controle)
            else:
                controle.impedimento_codigo = codigo
                controle.impedido_ate = None
                controle.atualizado_em = max(agora(), controle.atualizado_em)
            self._estado(s, e, "pausada", adm.usuario_id)
            self._auditar(s, e, "pausar_executor_telegram", adm.usuario_id, motivo=codigo)

    @staticmethod
    def _cadencia(s, conta_id, stamp):
        controle = s.get(ControleEnvioTelegram, conta_id)
        if controle is None:
            controle = ControleEnvioTelegram(
                conta_envio_id=conta_id,
                liberar_em=stamp + timedelta(seconds=5),
                impedimento_codigo=None,
                impedido_ate=None,
                atualizado_em=stamp,
            )
            s.add(controle)
        else:
            controle.liberar_em = stamp + timedelta(seconds=5)
            controle.impedimento_codigo = None
            controle.impedido_ate = None
            controle.atualizado_em = max(stamp, controle.atualizado_em)
        return controle

    def _marcar(self, adm, tentativa_id):
        from app.campanhas.preparacoes import aad

        with self.repo.transacao() as s:
            e = self._validar_admissao(s, adm)
            t = self.repo.obter(s, TentativaEnvio, tentativa_id)
            if t.execucao_id != e.id or t.estado != "reservada":
                raise conflito()
            header, key, cfg, item, foto = self._acessar_item(s, e, t)
            atuais = self.p._atuais(s, header, [foto])
            motivos = atuais.get(foto.identidade_telegram_id, {}).get(
                "impedimentos_atuais", ["identidade_indisponivel"]
            )
            if motivos:
                self._finalizar_reserva(s, t, adm.usuario_id)
                s.refresh(t)
                self._cadencia(s, foto.conta_envio_id, t.finalizada_em)
                s.refresh(item)
                motivo = (
                    "sincronizacao"
                    if any("sincronizacao" in value for value in motivos)
                    else "consentimento"
                )
                self._cas(
                    s,
                    ItemExecucao,
                    item,
                    estado="ignorado",
                    motivo_codigo=motivo,
                    atualizado_em=max(agora(), item.atualizado_em),
                    alterado_por_usuario_id=adm.usuario_id,
                )
                self._concluir(s, e, adm.usuario_id)
                self._auditar(
                    s,
                    e,
                    "impedir_tentativa_telegram",
                    adm.usuario_id,
                    tentativa_id=t.id,
                    motivos=sorted(set(motivos)),
                )
                return None
            try:
                with GCM(key) as content:
                    texto = content.decrypt(
                        foto.nonce,
                        foto.conteudo_cifrado,
                        aad(header, "texto", foto.identidade_telegram_id),
                    ).decode("utf-8")
                identidade = self.repo.obter(s, IdentidadeTelegram, foto.identidade_telegram_id)
                conta = self.repo.obter(s, ContaEnvio, foto.conta_envio_id)
                if not texto.strip() or len(texto) > 4096:
                    raise ValueError
            except (ValueError, TypeError, UnicodeError):
                raise indisponivel() from None
            finally:
                del key
            self._validar_admissao(s, adm)
            stamp = max(agora(), t.atualizado_em)
            self._cas(
                s,
                TentativaEnvio,
                t,
                estado="iniciada",
                invocacao_em=stamp,
                atualizado_em=stamp,
                alterado_por_usuario_id=adm.usuario_id,
            )
            self._auditar(
                s, e, "marcar_invocacao_telegram", adm.usuario_id, tentativa_id=t.id
            )
            return conta.id, conta.segredo_ref, identidade.chat_externo_id, texto, cfg

    def _resultado_telegram(self, adm, tentativa_id, resultado, conta_id):
        if resultado.estado == "confirmada" and not (
            type(resultado.message_id) is int and resultado.message_id > 0
        ):
            resultado = ResultadoEnvioTelegram("desconhecida", "sem_confirmacao")
        with self.repo.transacao() as s:
            e = self.repo.obter(s, ExecucaoCampanha, adm.execucao_id)
            if (e.proprietario_id, e.geracao) != (adm.proprietario_id, adm.geracao):
                raise conflito()
            t = self.repo.obter(s, TentativaEnvio, tentativa_id)
            if t.execucao_id != e.id or t.estado != "iniciada":
                raise conflito()
            estado = {
                "confirmada": "aceita_provedor",
                "falhou": "falhou",
                "desconhecida": "desconhecida",
            }.get(resultado.estado)
            if estado is None:
                raise conflito()
            stamp = max(agora(), t.atualizado_em)
            self._cas(
                s,
                TentativaEnvio,
                t,
                estado=estado,
                codigo_resultado=resultado.codigo,
                message_id=resultado.message_id,
                retry_after=resultado.retry_after,
                finalizada_em=stamp,
                atualizado_em=stamp,
                alterado_por_usuario_id=adm.usuario_id,
            )
            controle = self._cadencia(s, conta_id, stamp)
            if resultado.codigo == "limite_telegram":
                espera = resultado.retry_after or 60
                controle.impedimento_codigo = "limite_telegram"
                controle.impedido_ate = stamp + timedelta(seconds=espera)
                self._estado(s, e, "pausada", adm.usuario_id)
            else:
                self._concluir(s, e, adm.usuario_id)
            self._auditar(
                s,
                e,
                "resultado_tentativa_telegram",
                adm.usuario_id,
                tentativa_id=t.id,
                estado=estado,
                codigo=resultado.codigo,
                retry_after=resultado.retry_after,
            )

    def passo(self, adm):
        with self._exclusivo() as ctx:
            conta_id = self._conta_id(adm)
            self._esperar_cadencia(adm, conta_id)
            try:
                with self.recepcao.exclusivo(conta_id):
                    try:
                        self.recepcao.sincronizar(conta_id, adm.token)
                    except Exception:
                        self._pausar_impedimento(adm, conta_id, "sincronizacao")
                        return {"processado": False, "impedimento": "sincronizacao"}
                    tentativa_id = self._reservar(adm)
                    if tentativa_id is None:
                        return {"processado": False}
                    with self.repo.lock:
                        preparado = self._marcar(adm, tentativa_id)
                        if preparado is None:
                            return {"processado": False, "tentativa_id": tentativa_id}
                        conta_id, segredo_ref, chat_id, texto, _ = preparado
                        resultado = ResultadoEnvioTelegram("desconhecida", "sem_confirmacao")
                        token_bot = None
                        try:
                            if not segredo_ref:
                                resultado = ResultadoEnvioTelegram(
                                    "falhou", "credencial_indisponivel"
                                )
                            else:
                                token_bot = self.envios.contas.segredos.recuperar(
                                    conta_id, "telegram_bot_api", segredo_ref
                                )
                                provedor = self.envios.provedor_factory()
                                ctx._provedor = provedor
                                with self.repo.transacao() as s:
                                    self._validar_admissao(s, adm)
                                ctx.barreira(execucao_id=adm.execucao_id)
                                resultado = provedor.enviar(token_bot, chat_id, texto)
                        except NegocioError as error:
                            if error.codigo == "segredo_indisponivel":
                                resultado = ResultadoEnvioTelegram(
                                    "falhou", "credencial_indisponivel"
                                )
                        except Exception:
                            pass
                        finally:
                            ctx._provedor = None
                            token_bot = None
                            del texto, preparado
                        self._resultado_telegram(adm, tentativa_id, resultado, conta_id)
                    return {
                        "processado": True,
                        "tentativa_id": tentativa_id,
                        "estado": resultado.estado,
                    }
            except (NegocioError, AuthError, ValueError, TypeError):
                ctx.suspenso.set()
                ctx._selos.clear()
                raise NegocioError(
                    "executor_suspenso", "Executor suspenso. Reconcilie o historico.", 503
                ) from None

    def recuperar(self):
        with self._exclusivo() as ctx, self.repo.transacao() as s:
            ctx.suspenso.set()
            ctx._admissoes.clear()
            ctx._selos.clear()
            alteradas = 0
            for e in s.scalars(
                select(ExecucaoCampanha).where(
                    ExecucaoCampanha.modo == "telegram",
                    ExecucaoCampanha.estado.in_(ABERTOS),
                )
            ):
                if e.estado == "pausada":
                    continue
                if e.estado == "executando":
                    self._estado(s, e, "pausando", proprietario_id=uuid(), geracao=e.geracao + 1)
                for t in s.scalars(
                    select(TentativaEnvio).where(
                        TentativaEnvio.execucao_id == e.id,
                        TentativaEnvio.estado.in_(("reservada", "iniciada")),
                    )
                ):
                    if t.estado == "reservada":
                        self._finalizar_reserva(s, t)
                    else:
                        stamp = max(agora(), t.atualizado_em)
                        self._cas(
                            s,
                            TentativaEnvio,
                            t,
                            estado="desconhecida",
                            codigo_resultado="sem_confirmacao",
                            finalizada_em=stamp,
                            atualizado_em=stamp,
                            alterado_por_usuario_id=None,
                        )
                self._estado(s, e, "pausada")
                self._auditar(s, e, "recuperar_executor_telegram")
                alteradas += 1
            return {"reconciliadas": alteradas, "processamento_reativado": False}
