"""Executor interno sequencial. Nenhum agendamento ou integracao automatica ao runtime."""

import secrets
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event, Lock
from uuid import UUID

from sqlalchemy import func, select, update

from app.auth.errors import AuthError
from app.campanhas.configuracao_simulacao import ConfiguracaoSimulacao
from app.campanhas.crypto import GCM, indisponivel
from app.campanhas.execucoes import ABERTOS, _instante
from app.campanhas.execucoes_models import ExecucaoCampanha, ItemExecucao, TentativaEnvio
from app.campanhas.fotografia_operacional import gerar_selo, verificar_cabecalho, verificar_item
from app.campanhas.models import AutorizacaoSimulacao, Campanha, PreparacaoItem
from app.consentimentos.models import IdentidadeContato
from app.contatos.errors import NegocioError, conflito
from app.contatos.models import TelefoneContato
from app.contatos.repository import agora, uuid
from app.persistencia.lifecycle import Persistence
from app.provedores.contratos import (
    ContextoProvedor,
    EntradaTentativa,
    Plataforma,
    Provedor,
    ResultadoTentativa,
)
from app.provedores.mock import CenarioMock, MockProvedor


@dataclass(frozen=True, slots=True)
class AdmissaoMock:
    execucao_id: str
    proprietario_id: str
    geracao: int
    usuario_id: str
    sessao_id: str
    abertura: str
    token: str = field(repr=False)
    admissao_id: str = field(default_factory=uuid)


class _BloqueiosConsentimento:
    def __init__(self):
        self.lock = Lock()
        self.pendentes = 0
        self.falhas = set()


class ContextoOperacionalMock:
    """Capacidade interna que C devera criar/fechar sob o ciclo de vida real.

    Sem este objeto, lock de instancia vivo e abertura correta, nao ha despacho.
    O lock sequencial e a suspensao sao compartilhados por todos os executores
    deste repositorio; constraints e posse persistida completam a exclusividade.
    """

    def __init__(self, execucoes, persistence, abertura, *, lifetime=None):
        if not isinstance(persistence, Persistence):
            raise conflito()
        anterior = getattr(execucoes.repo.database, "controle_mock", None)
        if anterior is not None and not anterior.fechado.is_set():
            raise conflito()
        self.svc, self.persistence, self.abertura = execucoes, persistence, abertura
        self.lifetime = lifetime
        self.fechado, self.suspenso = Event(), Event()
        self.sequencial = getattr(execucoes.repo.database, "sequencial_mock", None) or Lock()
        execucoes.repo.database.sequencial_mock = self.sequencial
        self._consentimento = (
            getattr(execucoes.repo.database, "bloqueios_mock", None) or _BloqueiosConsentimento()
        )
        execucoes.repo.database.bloqueios_mock = self._consentimento
        self._admissoes = {}
        self._selos = {}
        self._provedor = None
        self._chave_selos = secrets.token_bytes(32)
        self.validar()
        execucoes.repo.controle_mock = self
        execucoes.repo.database.controle_mock = self

    def validar(self):
        if self.lifetime is not None:
            try:
                self.lifetime.require_open()
            except NegocioError:
                self.fechar()
                raise
        p = self.persistence
        if (
            self.fechado.is_set()
            or not p.ready
            or not p.lock.ativo
            or p.database is not self.svc.repo.database
            or p.database.closed
            or self.abertura != self.svc.p.abertura
        ):
            raise NegocioError("contexto_indisponivel", "Contexto operacional indisponivel.", 409)

    def barreira(self, *, session=None, execucao=None, execucao_id=None):
        self.validar()
        guard = getattr(self.svc.repo.database, "suspensoes_consentimento", None)
        if guard is not None and guard.bloqueia(execucao_id, session, execucao):
            raise NegocioError(
                "descadastramento_pendente",
                "Processamento afetado suspenso por descadastramento pendente.",
                409,
            )
        with self._consentimento.lock:
            if (
                self.suspenso.is_set()
                or self._consentimento.pendentes
                or (self._consentimento.falhas and guard is None)
            ):
                raise NegocioError(
                    "execucao_suspensa", "Processamento suspenso. Revise o estado.", 409
                )

    def fechar(self):
        # Nao espera pelo lock de cadastro: fecha a admissao imediatamente.
        self.fechado.set()
        if self._provedor is not None:
            self._provedor.cancelar()
        self._admissoes.clear()
        self._selos.clear()

    @contextmanager
    def proteger_optout(self, operacao_id):
        # Sinaliza ANTES de esperar pelo lock de cadastro. Falha fica retida.
        bloqueio = self._consentimento
        with bloqueio.lock:
            bloqueio.pendentes += 1
        try:
            yield
        except BaseException:
            with bloqueio.lock:
                bloqueio.falhas.add(operacao_id)
            self.suspenso.set()
            self._selos.clear()
            raise
        else:
            with bloqueio.lock:
                bloqueio.falhas.discard(operacao_id)
        finally:
            with bloqueio.lock:
                bloqueio.pendentes -= 1


class ExecutorMock:
    def __init__(self, execucoes, contexto=None):
        self.svc, self.repo, self.p = execucoes, execucoes.repo, execucoes.p
        self.contexto = contexto

    def _contexto(self):
        ctx = self.contexto
        if (
            not isinstance(ctx, ContextoOperacionalMock)
            or ctx.svc is not self.svc
            or getattr(self.repo.database, "controle_mock", None) is not ctx
        ):
            raise conflito()
        ctx.validar()
        return ctx

    @contextmanager
    def _exclusivo(self):
        ctx = self._contexto()
        if not ctx.sequencial.acquire(blocking=False):
            raise NegocioError("executor_ocupado", "Executor ocupado.", 409)
        try:
            yield ctx
        finally:
            ctx.sequencial.release()

    def _auditar(self, s, e, acao, usuario=None, **campos):
        self.repo.auditar(s, usuario, acao, "campanha", e.campanha_id, execucao_id=e.id, **campos)
        s.flush()

    @staticmethod
    def _cas(s, model, registro, **campos):
        changed = s.execute(
            update(model)
            .where(model.id == registro.id, model.versao == registro.versao)
            .values(versao=registro.versao + 1, **campos)
        )
        if changed.rowcount != 1:
            raise conflito()
        s.flush()

    def _estado(self, s, e, estado, usuario=None, **campos):
        self._cas(
            s,
            ExecucaoCampanha,
            e,
            estado=estado,
            atualizado_em=_instante(s, e),
            alterado_por_usuario_id=usuario,
            **campos,
        )

    def _autorizacao(self, s, e):
        a = self.repo.obter(s, AutorizacaoSimulacao, e.autorizacao_id)
        if a.campanha_id != e.campanha_id or a.preparacao_id != e.preparacao_id:
            raise indisponivel()
        return a

    @staticmethod
    def _autorizacao_vigente(autorizacao):
        return autorizacao.estado == "vigente"

    def _validar_admissao(self, s, adm):
        ctx = self._contexto()
        if ctx.lifetime is None:
            raise conflito()
        if not isinstance(adm, AdmissaoMock) or ctx._admissoes.get(adm.execucao_id) is not adm:
            raise conflito()
        if adm.abertura != ctx.abertura:
            raise conflito()
        e = self.repo.obter(s, ExecucaoCampanha, adm.execucao_id)
        ctx.barreira(session=s, execucao=e)
        if (e.proprietario_id, e.geracao) != (adm.proprietario_id, adm.geracao):
            raise conflito()
        if e.estado != "executando":
            raise conflito()
        usuario, sid = self.svc._contexto(s, adm.token)
        if (usuario, sid) != (adm.usuario_id, adm.sessao_id):
            raise conflito()
        return e

    def admitir(self, execucao_id, token, versao, *, retomar=False, registrar=None):
        if type(versao) is not int or versao < 1 or type(retomar) is not bool:
            raise conflito()
        with self._exclusivo() as ctx:
            if ctx.lifetime is None:
                raise conflito()
            with self.repo.transacao() as s:
                usuario, sid = self.svc._contexto(s, token)
                e = self.repo.obter(s, ExecucaoCampanha, execucao_id)
                ctx.barreira(session=s, execucao=e)
                if e.versao != versao or e.estado != ("pausada" if retomar else "executando"):
                    raise conflito()
                if not self._autorizacao_vigente(self._autorizacao(s, e)) or s.scalar(
                    select(TentativaEnvio.id)
                    .where(
                        TentativaEnvio.execucao_id == e.id,
                        TentativaEnvio.estado.in_(("reservada", "iniciada")),
                    )
                    .limit(1)
                ):
                    raise conflito()
                if retomar:
                    self._estado(
                        s, e, "executando", usuario, proprietario_id=uuid(), geracao=e.geracao + 1
                    )
                adm = AdmissaoMock(
                    e.id, e.proprietario_id, e.geracao, usuario, sid, ctx.abertura, token
                )
                # Falha ao renovar nunca deixa a admissao anterior utilizavel.
                ctx._admissoes.pop(e.id, None)
                ctx._selos.pop(e.id, None)
                prep, header, key, _ = self._fotografia(s, e, integral=True)
                del key
                selo = gerar_selo(
                    s, ctx._chave_selos, adm, e, prep, self._autorizacao(s, e), header
                )
                self._auditar(
                    s, e, "admitir_executor_mock", usuario, geracao=e.geracao, retomada=retomar
                )
                if registrar is not None:
                    registrar(s, e, usuario)
                self.svc._contexto(s, token)
            # Publicar depois do commit e ainda sob exclusividade. Nao despacha.
            ctx._admissoes[e.id] = adm
            ctx._selos[e.id] = selo
            return adm

    def _finalizar_reserva(self, s, t, usuario=None):
        stamp = max(agora(), t.atualizado_em)
        self._cas(
            s,
            TentativaEnvio,
            t,
            estado="interrompida",
            finalizada_em=stamp,
            atualizado_em=stamp,
            alterado_por_usuario_id=usuario,
        )

    def _encerrar(self, s, e, usuario=None):
        stamp = _instante(s, e)
        s.execute(
            update(ItemExecucao)
            .where(
                ItemExecucao.execucao_id == e.id,
                ItemExecucao.estado == "pendente",
            )
            .values(
                estado="interrompido",
                motivo_codigo="encerramento",
                versao=ItemExecucao.versao + 1,
                atualizado_em=stamp,
                alterado_por_usuario_id=usuario,
            )
        )
        self._estado(s, e, "interrompida", usuario)

    def _concluir(self, s, e, usuario=None):
        def existe(estados):
            return (
                s.scalar(
                    select(ItemExecucao.id)
                    .where(ItemExecucao.execucao_id == e.id, ItemExecucao.estado.in_(estados))
                    .limit(1)
                )
                is not None
            )

        if not existe(("pendente", "reservado", "processando")):
            falhas = existe(("falhou", "desconhecida", "interrompido"))
            self._estado(s, e, "concluida_com_falhas" if falhas else "concluida", usuario)
        elif e.estado == "pausando":
            self._estado(s, e, "pausada", usuario)

    def _reservar(self, adm):
        with self.repo.transacao() as s:
            e = self._validar_admissao(s, adm)
            if not self._autorizacao_vigente(self._autorizacao(s, e)):
                self._encerrar(s, e, adm.usuario_id)
                self._auditar(s, e, "encerrar_executor_revogado", adm.usuario_id)
                return None
            item = s.scalar(
                select(ItemExecucao)
                .where(
                    ItemExecucao.execucao_id == e.id,
                    ItemExecucao.estado == "pendente",
                )
                .order_by(ItemExecucao.ordem)
                .limit(1)
            )
            if item is None:
                self._concluir(s, e, adm.usuario_id)
                self._auditar(s, e, "concluir_executor_mock", adm.usuario_id)
                return None
            stamp = max(agora(), item.atualizado_em, e.atualizado_em)
            t = TentativaEnvio(
                id=uuid(),
                execucao_id=e.id,
                item_id=item.id,
                numero=1
                + (
                    s.scalar(
                        select(func.max(TentativaEnvio.numero)).where(
                            TentativaEnvio.item_id == item.id
                        )
                    )
                    or 0
                ),
                estado="reservada",
                versao=1,
                proprietario_id=e.proprietario_id,
                geracao=e.geracao,
                reservada_em=stamp,
                atualizado_em=stamp,
                alterado_por_usuario_id=adm.usuario_id,
            )
            s.add(t)
            s.flush()  # Trigger aplica CAS pendente -> reservado; UNIQUE ativa global.
            self._auditar(s, e, "reservar_tentativa_mock", adm.usuario_id, tentativa_id=t.id)
            self._validar_admissao(s, adm)
            return t.id

    def _fotografia(self, s, e, *, integral=False):
        from app.campanhas.preparacoes import aad, abrir

        campanha = self.repo.obter(s, Campanha, e.campanha_id)
        prep = self.p._obter(s, e.campanha_id, e.preparacao_id)
        a = self._autorizacao(s, e)
        if campanha.status != "preparada" or prep.invalidado_em or prep.confirmado_em is None:
            raise conflito()
        ctx = self.p._ctx(s)
        if not ctx.conferido:
            raise indisponivel()
        if not integral:
            operacional = self._contexto()
            adm = operacional._admissoes.get(e.id)
            if adm is None or e.estado != "executando":
                raise indisponivel()
            header = self.p._header(prep, ctx)
            verificar_cabecalho(
                s, operacional._selos.get(e.id), operacional._chave_selos, adm, e, prep, a, header
            )
        with GCM(abrir(ctx)) as root:
            if integral:
                header, key, _ = self.p._autenticar_fotografia(s, prep, ctx, root)
            else:
                key = root.decrypt(prep.chave_nonce, prep.chave_cifrada, aad(header, "chave"))
        if (
            e.modo != "mock"
            or header["provedor"] != "mock"
            or header["plataforma"] != "whatsapp"
        ):
            raise indisponivel()
        cfg = ConfiguracaoSimulacao.model_validate(header["configuracao_simulacao"])
        refs = self.svc.c._referencias(s, header, header["listas"])
        if refs["conta"]["provedor"] != "mock" or refs["conta"]["plataforma"] != "whatsapp":
            raise indisponivel()
        if (
            a.base_id != prep.base_id
            or a.impressao != prep.impressao
            or a.resumo_configuracao != cfg.resumo_configuracao
            or any(
                header[k] != getattr(campanha, k)
                for k in ("conta_envio_id", "escopo_id", "template_versao_id", "categoria")
            )
        ):
            raise indisponivel()
        return prep, header, key, cfg

    def _acessar_item(self, s, e, t):
        prep, header, key, cfg = self._fotografia(s, e)
        item = self.repo.obter(s, ItemExecucao, t.item_id)
        foto = self.repo.obter(s, PreparacaoItem, (prep.id, item.identidade_contato_id))
        operacional = self._contexto()
        verificar_item(s, operacional._selos.get(e.id), operacional._chave_selos, item, foto)
        return header, key, cfg, item, foto

    def _marcar(self, adm, tentativa_id):
        from app.campanhas.preparacoes import aad

        with self.repo.transacao() as s:
            e = self._validar_admissao(s, adm)
            t = self.repo.obter(s, TentativaEnvio, tentativa_id)
            if t.execucao_id != e.id or t.estado != "reservada":
                raise conflito()
            if not self._autorizacao_vigente(self._autorizacao(s, e)):
                self._finalizar_reserva(s, t, adm.usuario_id)
                self._encerrar(s, e, adm.usuario_id)
                self._auditar(s, e, "encerrar_executor_revogado", adm.usuario_id)
                return None
            header, key, cfg, item, foto = self._acessar_item(s, e, t)
            atuais = self.p._atuais(s, header, [foto])
            motivos = atuais.get(foto.identidade_contato_id, {}).get(
                "impedimentos_atuais", ["identidade_indisponivel"]
            )
            if motivos:
                self._finalizar_reserva(s, t, adm.usuario_id)
                s.refresh(item)  # O trigger devolveu o item a pendente.
                if "conta_inativa" in motivos:
                    self._estado(s, e, "pausada", adm.usuario_id)
                else:
                    self._cas(
                        s,
                        ItemExecucao,
                        item,
                        estado="ignorado",
                        motivo_codigo="impedimento_atual",
                        atualizado_em=max(agora(), item.atualizado_em),
                        alterado_por_usuario_id=adm.usuario_id,
                    )
                    self._concluir(s, e, adm.usuario_id)
                self._auditar(
                    s,
                    e,
                    "impedir_tentativa_mock",
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
                        aad(header, "texto", foto.identidade_contato_id),
                    ).decode("utf-8")
                destino = s.scalar(
                    select(TelefoneContato.telefone_e164)
                    .join(
                        IdentidadeContato,
                        IdentidadeContato.telefone_id == TelefoneContato.id,
                    )
                    .where(IdentidadeContato.id == foto.identidade_contato_id)
                )
                entrada = EntradaTentativa(
                    UUID(t.id), ContextoProvedor(Provedor.MOCK, Plataforma.WHATSAPP), destino, texto
                )
            except ValueError, TypeError, UnicodeError:
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
            self._auditar(s, e, "marcar_invocacao_mock", adm.usuario_id, tentativa_id=t.id)
            return entrada, cfg

    @staticmethod
    def _invocar(entrada, cfg):
        # Unico conector permitido, puro e sincrono. Nunca recebe um provider externo.
        return MockProvedor(padrao=CenarioMock(cfg.cenario)).executar(entrada)

    def _resultado(self, adm, tentativa_id, estado, codigo):
        # Resultado admitido nao depende da sessao, admissao em memoria ou chave.
        with self.repo.transacao() as s:
            e = self.repo.obter(s, ExecucaoCampanha, adm.execucao_id)
            if (e.proprietario_id, e.geracao) != (adm.proprietario_id, adm.geracao):
                raise conflito()
            t = self.repo.obter(s, TentativaEnvio, tentativa_id)
            if t.execucao_id != e.id or t.estado != "iniciada":
                raise conflito()
            stamp = max(agora(), t.atualizado_em)
            self._cas(
                s,
                TentativaEnvio,
                t,
                estado=estado,
                codigo_resultado=codigo,
                finalizada_em=stamp,
                atualizado_em=stamp,
                alterado_por_usuario_id=adm.usuario_id,
            )
            if not self._autorizacao_vigente(self._autorizacao(s, e)):
                self._encerrar(s, e, adm.usuario_id)
            else:
                self._concluir(s, e, adm.usuario_id)
            self._auditar(
                s,
                e,
                "resultado_tentativa_mock",
                adm.usuario_id,
                tentativa_id=t.id,
                estado=estado,
                codigo=codigo,
            )

    def passo(self, adm):
        with self._exclusivo() as ctx:
            try:
                tentativa_id = self._reservar(adm)
                if tentativa_id is None:
                    return {"processado": False}
                # Para mock puro: lock compartilhado por uma unica tentativa. A
                # transacao do marcador termina ANTES da barreira e da invocacao.
                with self.repo.lock:
                    preparado = self._marcar(adm, tentativa_id)
                    if preparado is None:
                        return {"processado": False, "tentativa_id": tentativa_id}
                    entrada, cfg = preparado
                    estado, codigo = "desconhecida", "sem_confirmacao"
                    try:
                        with self.repo.transacao() as s:
                            self._validar_admissao(s, adm)
                        # Ultimo ponto de admissao, apos o commit da leitura e
                        # sem transacao: observa o evento Windows do supervisor.
                        ctx.barreira(execucao_id=adm.execucao_id)
                        resultado = self._invocar(entrada, cfg)
                        if (
                            not isinstance(resultado, ResultadoTentativa)
                            or resultado.tentativa_id != entrada.tentativa_id
                            or resultado.contexto != entrada.contexto
                        ):
                            raise ValueError
                        estado, codigo = resultado.estado.value, resultado.codigo.value
                    except NegocioError, AuthError:
                        # Falha da barreira (inclusive leitura/commit) impede a
                        # chamada e novos passos, mesmo se pudermos gravar a
                        # classificacao conservadora deste marcador.
                        ctx.suspenso.set()
                        ctx._selos.clear()
                    except Exception:
                        # Nunca usar texto de excecao, nem retornar item a pendente.
                        pass
                    finally:
                        del entrada, preparado
                    self._resultado(adm, tentativa_id, estado, codigo)
                return {"processado": True, "tentativa_id": tentativa_id, "estado": estado}
            except NegocioError, AuthError, ValueError, TypeError:
                ctx.suspenso.set()
                ctx._selos.clear()
                # A reserva/marcador ficam para reconciliacao explicita; nao
                # continuar nem esconder falha de persistencia com nova escrita.
                raise NegocioError(
                    "executor_suspenso", "Executor suspenso. Reconcilie o historico.", 503
                ) from None
            except BaseException:
                ctx.suspenso.set()
                ctx._selos.clear()
                raise

    def recuperar(self):
        with self._exclusivo() as ctx, self.repo.transacao() as s:
            ctx.suspenso.set()
            ctx._admissoes.clear()
            ctx._selos.clear()
            alteradas = 0
            for e in s.scalars(
                select(ExecucaoCampanha).where(
                    ExecucaoCampanha.modo == "mock",
                    ExecucaoCampanha.estado.in_(ABERTOS),
                )
            ):
                revogada = not self._autorizacao_vigente(self._autorizacao(s, e))
                if e.estado == "pausada":
                    if revogada:
                        self._encerrar(s, e)
                        self._auditar(s, e, "recuperar_executor_mock")
                        alteradas += 1
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
                if revogada:
                    self._encerrar(s, e)
                else:
                    self._estado(s, e, "pausada")
                self._auditar(s, e, "recuperar_executor_mock")
                alteradas += 1
            return {"reconciliadas": alteradas, "processamento_reativado": False}
