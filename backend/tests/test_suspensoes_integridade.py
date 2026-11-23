"""Contrato SQL: confirmado, rejeitado ou indeterminado; JSON nao decide estado."""

import json
from uuid import uuid4

import pytest
import test_auditoria18_optout as anterior
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.consentimentos.suspensoes import SuspensoesConsentimento
from app.contas_envio.segredos import dpapi
from app.contatos.errors import NegocioError
from app.contatos.models import Auditoria
from app.contatos.repository import agora
from app.persistencia.errors import DatabaseAccessError

cadastro = anterior.cadastro
consentimento = anterior.consentimento
rascunho = anterior.rascunho
pronta = anterior.pronta
simulavel = anterior.simulavel
confirmada = anterior.confirmada
autorizada = anterior.autorizada
operacional = anterior.operacional


def recuperar(f):
    guard = SuspensoesConsentimento(f["db"], f["app"].state.persistence.settings.database_path)
    guard.recuperar()
    return guard


def pendente(f, monkeypatch):
    svc, cid, data, token = anterior.revogacao(f)

    def falhar(*_):
        raise NegocioError("persistencia_indisponivel", "Falha ficticia", 503)

    with monkeypatch.context() as patch:
        patch.setattr(svc, "_registrar", falhar)
        with pytest.raises(NegocioError) as error:
            svc.registrar(cid, data, token)
    assert error.value.codigo == "optout_indeterminado"
    return dict(f["db"].suspensoes_consentimento.pendentes[str(data.operacao_id)])


def legado(f, value):
    # Formato autentico do protocolo anterior; e evidencia historica, nao autoridade.
    base = str(uuid4())
    with f["db"].transaction() as s:
        s.add(
            Auditoria(
                id=base,
                usuario_id=None,
                acao="ancorar_diario_optout_v1",
                entidade="consentimento",
                entidade_id=base,
                resumo_json='{"formato":1}',
                ocorrido_em=agora(),
            )
        )
    path = f["db"].suspensoes_consentimento.path
    path.mkdir(exist_ok=True)
    manifest = path / "manifesto.dpapi"
    old = dpapi(json.dumps(dict(formato=1, base=base, intencoes={})).encode())
    current = dpapi(json.dumps(dict(formato=1, base=base, intencoes={value["id"]: value})).encode())
    manifest.write_bytes(current)
    item = path / (value["id"] + ".json")
    item.write_text(json.dumps(value), encoding="utf-8")
    return manifest, item, old


@pytest.mark.parametrize("fase", ["intencao", "evento", "confirmado"])
def test_replay_manifesto_antigo_e_exclusao_nao_apagam_estado_sql(
    operacional, monkeypatch, tmp_path, fase
):
    f, receipt, *_ = operacional
    svc, cid, data, token = anterior.revogacao(f)
    if fase == "confirmado":
        result = svc.registrar(cid, data, token)
        assert result["id"] == str(data.operacao_id)
    else:
        original = svc._registrar

        def falhar(*args):
            if fase == "evento":
                original(*args)
            raise NegocioError("persistencia_indisponivel", "Falha apos commit parcial", 503)

        with monkeypatch.context() as patch:
            patch.setattr(svc, "_registrar", falhar)
            with pytest.raises(NegocioError) as error:
                svc.registrar(cid, data, token)
        assert error.value.codigo == "optout_indeterminado"
    with f["db"]._sessions() as s:
        from app.campanhas.execucoes_models import SuspensaoConsentimento

        value = f["db"].suspensoes_consentimento._dados(
            s.get(SuspensaoConsentimento, str(data.operacao_id))
        )
    manifest, item, old = legado(f, value)
    history = {
        t: anterior.b2.linhas(f, t)
        for t in (
            "eventos_consentimento",
            "preferencias_contato",
            "campanha_preparacoes",
            "campanha_preparacao_itens",
            "campanha_autorizacoes_simulacao",
        )
    }
    manifest.write_bytes(old)
    item.unlink()
    guard = recuperar(f)
    assert (value["id"] in guard.pendentes) == (fase == "intencao")
    guard.recuperar()
    for table, rows in history.items():
        assert anterior.b2.linhas(f, table) == rows
    assert anterior.reabrir(f, receipt, tmp_path / "reaberta") == {
        "permitida": False,
        "tentativas": [],
    }
    assert manifest.read_bytes() == old  # Nao corrige nem reescreve artefato legado.


@pytest.mark.parametrize(
    "field,value",
    [
        ("categoria", "eventos_grupos"),
        ("identidade_contato_id", "uuid"),
        ("contato_id", "uuid"),
        ("escopo_id", "uuid"),
        ("id", "uuid"),
        ("requisicao_hash", "a" * 64),
    ],
)
def test_alteracao_json_nao_muda_alvo_sql_pendente(operacional, monkeypatch, field, value):
    f, receipt, *_ = operacional
    original = pendente(f, monkeypatch)
    _, item, _ = legado(f, original)
    changed = {**original, field: str(uuid4()) if value == "uuid" else value}
    item.write_text(json.dumps(changed), encoding="utf-8")
    if field == "id":
        item.rename(item.with_name(changed["id"] + ".json"))
    before = anterior.b2.linhas(f, "consentimento_suspensoes")
    guard = recuperar(f)
    assert guard.pendentes[original["id"]] == original
    assert guard.bloqueia(receipt["execucao_id"])
    assert anterior.b2.linhas(f, "consentimento_suspensoes") == before


def test_json_isolado_nao_e_promovido_a_intencao_confirmada(operacional, monkeypatch):
    f, _, *_ = operacional
    svc, cid, data, token = anterior.revogacao(f)
    guard = f["db"].suspensoes_consentimento

    def negar(*_):
        raise OperationalError("Sem escrita", None, None)

    with monkeypatch.context() as patch:
        patch.setattr(guard, "_espelhar", negar)
        with pytest.raises(NegocioError) as error:
            svc.registrar(cid, data, token)
    assert error.value.codigo == "optout_rejeitado"
    value = dict(id=str(data.operacao_id), categoria="promocoes")
    _, item, _ = legado(f, value)
    before = item.read_bytes()
    assert recuperar(f).pendentes == {}
    assert anterior.b2.linhas(f, "consentimento_suspensoes") == []
    assert not [r for r in anterior.b2.linhas(f, "eventos_consentimento") if r["tipo"] == "revogou"]
    assert item.read_bytes() == before


@pytest.mark.parametrize("fase", ["intencao", "evento", "confirmacao", "evento_inesperado"])
def test_http_erro_apos_commit_e_replay_exato(operacional, monkeypatch, fase):
    f, _, *_ = operacional
    _, cid, data, _ = anterior.revogacao(f)
    svc = f["app"].state.consentimentos
    guard = f["db"].suspensoes_consentimento
    target, name = {
        "intencao": (guard, "_espelhar"),
        "evento": (svc, "_registrar"),
        "confirmacao": (guard, "confirmar"),
        "evento_inesperado": (svc, "_registrar"),
    }[fase]
    original = getattr(target, name)

    def perder_confirmacao(*args):
        original(*args)
        if fase == "evento_inesperado":
            raise RuntimeError("Erro inesperado depois do commit")
        raise OperationalError("Confirmacao perdida depois do commit", None, None)

    route = f"/contatos/{cid}/consentimentos/eventos"
    body = data.model_dump(mode="json")
    with monkeypatch.context() as patch:
        patch.setattr(target, name, perder_confirmacao)
        response = f["c"].post(route, json=body)
    assert response.status_code == 503, response.text
    assert response.json()["codigo"] == "optout_indeterminado"
    assert guard.inseguro and f["app"].state.execution_runtime._closed.is_set()
    assert len(anterior.b2.linhas(f, "consentimento_suspensoes")) == 1
    first = f["c"].post(route, json=body)
    second = f["c"].post(route, json=body)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert (
        len([r for r in anterior.b2.linhas(f, "eventos_consentimento") if r["tipo"] == "revogou"])
        == 1
    )
    assert anterior.b2.linhas(f, "consentimento_suspensoes")[0]["confirmado_em"] is not None


def test_http_rejeitado_antes_commit_fecha_abertura(operacional):
    f, _, ctx, executor, adm = operacional
    _, cid, data, _ = anterior.revogacao(f)

    writing = False

    def identificar(_conn, _cursor, sql, *_):
        nonlocal writing
        if sql.startswith("INSERT INTO consentimento_suspensoes"):
            writing = True

    def negar(_):
        nonlocal writing
        if writing:
            writing = False
            raise OperationalError("Commit recusado", None, None)

    event.listen(f["db"].engine, "before_cursor_execute", identificar)
    event.listen(f["db"].engine, "commit", negar)
    try:
        response = f["c"].post(
            f"/contatos/{cid}/consentimentos/eventos", json=data.model_dump(mode="json")
        )
    finally:
        event.remove(f["db"].engine, "commit", negar)
        event.remove(f["db"].engine, "before_cursor_execute", identificar)
    assert response.status_code == 503
    assert response.json()["codigo"] == "optout_rejeitado"
    assert ctx.suspenso.is_set() and f["db"].suspensoes_consentimento.inseguro
    with pytest.raises(NegocioError):
        executor.passo(adm)
    assert anterior.b2.linhas(f, "consentimento_suspensoes") == []
    assert not anterior.b2.linhas(f, "tentativas_envio")


@pytest.mark.parametrize("error_type", [DatabaseAccessError, ValueError])
def test_leitura_indisponivel_nao_prova_rejeicao_e_recuperacao_bloqueia(
    operacional, monkeypatch, error_type
):
    f, _, *_ = operacional
    guard = f["db"].suspensoes_consentimento
    _, _, data, _ = anterior.revogacao(f)

    def negar():
        raise error_type("Leitura indisponivel")

    with monkeypatch.context() as patch:
        patch.setattr(f["db"], "_sessions", negar)
        assert guard.falhar(str(data.operacao_id)).codigo == "optout_indeterminado"
        with pytest.raises(DatabaseAccessError):
            guard.recuperar()
    assert guard.inseguro


def test_falha_na_conferencia_pos_commit_preserva_intencao(operacional, monkeypatch):
    f, receipt, *_ = operacional
    svc, cid, data, token = anterior.revogacao(f)
    guard = f["db"].suspensoes_consentimento

    def negar(*_):
        raise DatabaseAccessError("Confirmacao indisponivel")

    with monkeypatch.context() as patch:
        patch.setattr(guard, "_verificar_intencao", negar)
        with pytest.raises(NegocioError) as error:
            svc.registrar(cid, data, token)
    assert error.value.codigo == "optout_indeterminado"
    restored = recuperar(f)
    assert str(data.operacao_id) in restored.pendentes
    assert restored.bloqueia(receipt["execucao_id"])
    before = anterior.b2.linhas(f, "consentimento_suspensoes")
    restored.recuperar()
    assert anterior.b2.linhas(f, "consentimento_suspensoes") == before
