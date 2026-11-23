"""Prévia, conflitos, atomicidade, arquivos e reimportação."""

import csv
import io
import json
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
from test_contatos import cadastro as _cadastro
from test_contatos import criar

from app.contatos.models import Auditoria, Contato, ListaContato, TelefoneContato
from app.importacoes.models import Importacao, ImportacaoLinha
from app.importacoes.readers import MAX_BYTES

cadastro = _cadastro


def test_csv_largo_rejeitado_antes_de_materializar_campos(monkeypatch):
    from app.contatos.errors import NegocioError
    from app.importacoes import readers
    from app.importacoes.schemas import ConfiguracaoImportacao

    def proibido(*args, **kwargs):
        pytest.fail("CSV largo não deve criar stream, campos ou células.")

    monkeypatch.setattr(readers.csv, "reader", proibido)
    monkeypatch.setattr(readers.io, "StringIO", proibido)
    monkeypatch.setattr(readers, "Celula", proibido)
    config = ConfiguracaoImportacao(coluna_telefone=0, cabecalho=None)
    with pytest.raises(NegocioError) as error:
        list(readers.linhas(b";" * 200000, ".csv", config))
    assert error.value.codigo == "limites_excedidos"


@pytest.mark.parametrize("columns", [100, 101])
def test_csv_fronteira_de_colunas_sem_truncamento(columns):
    from app.contatos.errors import NegocioError
    from app.importacoes import readers
    from app.importacoes.schemas import ConfiguracaoImportacao

    values = [str(index) for index in range(columns)]
    iterator = readers.linhas(
        ";".join(values).encode(),
        ".csv",
        ConfiguracaoImportacao(coluna_telefone=0, cabecalho=None),
    )
    if columns == 100:
        rows = list(iterator)
        assert len(rows) == 1 and [cell.valor for cell in rows[0][1]] == values
    else:
        with pytest.raises(NegocioError) as error:
            list(iterator)
        assert error.value.codigo == "limites_excedidos"


@pytest.mark.parametrize("malformado", [False, True])
def test_csv_fecha_stream_em_erro_ou_fechamento_antecipado(monkeypatch, malformado):
    from app.contatos.errors import NegocioError
    from app.importacoes import readers
    from app.importacoes.schemas import ConfiguracaoImportacao

    original = io.StringIO
    streams = []

    def abrir(*args, **kwargs):
        stream = original(*args, **kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(readers.io, "StringIO", abrir)
    payload = b'"incompleto' if malformado else b'"primeira;linha";123\nsegunda;456\n'
    iterator = readers.linhas(
        payload, ".csv", ConfiguracaoImportacao(coluna_telefone=1, cabecalho=None)
    )
    if malformado:
        with pytest.raises(NegocioError) as error:
            list(iterator)
        assert error.value.codigo == "arquivo_invalido"
    else:
        assert next(iterator)[1][0].valor == "primeira;linha"
        iterator.close()
    assert len(streams) == 1 and streams[0].closed


@pytest.mark.parametrize("header", [None, 1])
def test_limite_linhas_sem_truncar(header, monkeypatch):
    from app.contatos.errors import NegocioError
    from app.importacoes import readers
    from app.importacoes.schemas import ConfiguracaoImportacao

    monkeypatch.setattr(readers, "MAX_ROWS", 2)
    config = ConfiguracaoImportacao(coluna_telefone=0, cabecalho=header)
    prefix = "telefone\n" if header else ""
    payload = (prefix + "+442083661177\n+390236618300\n").encode()
    assert len(list(readers.linhas(payload, ".csv", config))) == 2
    with pytest.raises(NegocioError, match="limites"):
        list(readers.linhas(payload + b"+16502530000\n", ".csv", config))


def preparar(cadastro, payload, config=None, name="contatos.csv"):
    c, app, _ = cadastro
    result = c.post("/importacoes", json={"nome_arquivo": name})
    assert result.status_code == 201, result.text
    identifier = result.json()["id"]
    result = c.put(f"/importacoes/{identifier}/arquivo", content=payload)
    assert result.status_code == 200, result.text
    result = c.post(
        f"/importacoes/{identifier}/validacao",
        json={"coluna_nome": 0, "coluna_telefone": 1, **(config or {})},
    )
    assert result.status_code == 202, result.text
    app.state.importacoes.future.result(timeout=30)
    return identifier, c.get("/importacoes/" + identifier).json()


def confirmar(c, identifier):
    return c.post(f"/importacoes/{identifier}/confirmacao")


def xlsx(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


@pytest.mark.parametrize(
    "encoding,delimiter,header",
    [("utf-8-sig", ";", 1), ("cp1252", ",", None), ("utf-8-sig", "\t", 1)],
)
def test_csv_aspas_delimitadores_e_quebras_preservados(cadastro, encoding, delimiter, header):
    c, _, _ = cadastro
    name = f'José{delimiter} "apelido"'
    notes = f'Primeira {delimiter * 150} "citação"\nSegunda'
    with io.StringIO(newline="") as buffer:
        writer = csv.writer(buffer, delimiter=delimiter)
        if header:
            writer.writerow(["nome", "telefone", "notas"])
        writer.writerow([name, "+390236618300", notes])
        payload = buffer.getvalue().encode(encoding)
    identifier, state = preparar(
        cadastro,
        payload,
        {
            "codificacao": encoding,
            "delimitador": delimiter,
            "cabecalho": header,
            "coluna_observacoes": 2,
        },
    )
    assert state["contagens"] == {"inserido": 1}
    assert confirmar(c, identifier).status_code == 200
    item = c.get("/contatos").json()["itens"][0]
    detail = c.get("/contatos/" + item["id"]).json()
    assert detail["nome"] == name and detail["observacoes"] == notes
    assert detail["telefones"][0]["telefone_e164"] == "+390236618300"


def test_csv_largo_falha_sem_gravar_e_libera_importacao(cadastro):
    c, app, _ = cadastro
    payload = b"nome;telefone\nValido;+442083661177\n" + b";" * 200000
    identifier, state = preparar(cadastro, payload)
    assert state["status"] == "falhou" and state["contagens"] == {"erro": "limites_excedidos"}
    assert confirmar(c, identifier).status_code == 409
    with app.state.contatos.repo.transacao() as session:
        for model in (Contato, TelefoneContato, ListaContato, ImportacaoLinha, Auditoria):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert app.state.importacoes.previa is None
    assert app.state.importacoes.future.done()
    another, state = preparar(cadastro, b"nome;telefone\nValido;+442083661177\n")
    assert state["status"] == "pronta" and confirmar(c, another).status_code == 200


def preparar_com_lista(cadastro):
    c, _, _ = cadastro
    group = c.post("/listas", json={"nome": "Lista da confirmação"}).json()["id"]
    identifier, state = preparar(
        cadastro,
        b"nome;telefone\nA;+442083661177\nB;+390236618300\nA;+442083661177\nErro;invalido\n",
        {"listas": [group]},
    )
    assert state["status"] == "pronta"
    return identifier, group


def verificar_confirmacao_unica(cadastro, identifier, group):
    c, app, _ = cadastro
    result = c.get("/importacoes/" + identifier).json()
    counts = {
        "inserido": 2,
        "duplicado": 1,
        "invalido": 1,
        "total_linhas": 4,
        "associacoes_adicionadas": 2,
    }
    assert result["status"] == "confirmada" and result["contagens"] == counts
    with app.state.contatos.repo.transacao() as session:
        models = (Contato, TelefoneContato, ListaContato, ImportacaoLinha, Importacao, Auditoria)
        snapshot = {
            model.__tablename__: session.execute(
                select(model.__table__).order_by(*model.__table__.primary_key.columns)
            )
            .mappings()
            .all()
            for model in models
        }
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    contacts = {r["id"] for r in snapshot["contatos"]}
    assert len(contacts) == 2 and len(snapshot["telefones_contato"]) == 2
    assert {r["contato_id"] for r in snapshot["telefones_contato"]} == contacts
    assert {(r["lista_id"], r["contato_id"]) for r in snapshot["lista_contatos"]} == {
        (group, contact) for contact in contacts
    }
    assert len(snapshot["lista_contatos"]) == 2
    assert len(snapshot["importacao_linhas"]) == 4
    assert {r["numero_linha"] for r in snapshot["importacao_linhas"]} == {2, 3, 4, 5}
    assert len(snapshot["importacoes"]) == 1
    assert snapshot["importacoes"][0]["status"] == "confirmada"
    assert json.loads(snapshot["importacoes"][0]["contagens_json"]) == counts
    audits = [r for r in snapshot["auditoria"] if r["acao"] == "confirmar"]
    assert len(audits) == 1 and audits[0]["entidade_id"] == identifier
    assert json.loads(audits[0]["resumo_json"]) == counts
    return result, snapshot


def test_confirmacoes_simultaneas_sem_efeitos_duplicados(cadastro, settings, monkeypatch):
    c, app, _ = cadastro
    identifier, group = preparar_com_lista(cadastro)
    imports = app.state.importacoes
    original = imports.confirmar
    barrier = Barrier(2)
    inserted, release = Event(), Event()

    def confirmar_juntas(*args):
        barrier.wait(timeout=10)
        return original(*args)

    def pausar(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO contatos"):
            inserted.set()
            if not release.wait(timeout=10):
                raise TimeoutError("Confirmação não liberada pelo teste.")

    monkeypatch.setattr(imports, "confirmar", confirmar_juntas)
    engine = app.state.persistence.database.engine
    event.listen(engine, "after_cursor_execute", pausar)
    try:
        with ThreadPoolExecutor(2) as executor:
            futures = [executor.submit(confirmar, c, identifier) for _ in range(2)]
            try:
                assert inserted.wait(timeout=10)
                # Outra conexão SQLite real só pode observar o estado anterior ao commit.
                with closing(sqlite3.connect(settings.database_path)) as connection:
                    for table in (
                        "contatos",
                        "telefones_contato",
                        "lista_contatos",
                        "importacao_linhas",
                    ):
                        assert connection.execute("SELECT COUNT(*) FROM " + table).fetchone() == (
                            0,
                        )
                    assert connection.execute("SELECT status FROM importacoes").fetchone() == (
                        "pronta",
                    )
                    assert connection.execute(
                        "SELECT COUNT(*) FROM auditoria WHERE acao='confirmar'"
                    ).fetchone() == (0,)
            finally:
                release.set()
            responses = [future.result(timeout=15) for future in futures]
    finally:
        event.remove(engine, "after_cursor_execute", pausar)
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    result, _ = verificar_confirmacao_unica(cadastro, identifier, group)
    assert result == responses[0].json()


def test_resposta_http_perdida_apos_commit_nao_repete_efeitos(cadastro):
    c, app, _ = cadastro
    identifier, group = preparar_com_lista(cadastro)
    path = f"/importacoes/{identifier}/confirmacao"

    async def perder_resposta(scope, receive, send):
        async def enviar(message):
            if scope.get("path") == path and message["type"] == "http.response.body":
                raise ConnectionError("Resposta HTTP perdida após commit.")
            await send(message)

        await app(scope, receive, enviar)

    client = TestClient(perder_resposta, base_url="http://127.0.0.1", headers=dict(c.headers))
    try:
        with pytest.raises(ConnectionError, match="Resposta HTTP perdida"):
            client.post(path)
    finally:
        client.close()
    result, snapshot = verificar_confirmacao_unica(cadastro, identifier, group)
    assert app.state.importacoes.previa is None
    replay = confirmar(c, identifier)
    assert replay.status_code == 200 and replay.json() == result
    assert verificar_confirmacao_unica(cadastro, identifier, group) == (result, snapshot)


def test_repetir_confirmacao_apos_commit_preserva_todos_os_registros(cadastro):
    c, _, _ = cadastro
    identifier, group = preparar_com_lista(cadastro)
    first = confirmar(c, identifier)
    assert first.status_code == 200
    result, snapshot = verificar_confirmacao_unica(cadastro, identifier, group)
    replay = confirmar(c, identifier)
    assert replay.status_code == 200 and replay.json() == first.json() == result
    assert verificar_confirmacao_unica(cadastro, identifier, group) == (result, snapshot)


def test_importar_reimportar_idempotente_e_relatorio(cadastro):
    c, app, _ = cadastro
    payload = "nome;telefone\nJosé;+5511912345678\nJosé;+5511912345678\n;inválido\n;\n".encode()
    identifier, state = preparar(cadastro, payload)
    assert state["status"] == "pronta"
    assert state["contagens"] == {"inserido": 1, "duplicado": 1, "invalido": 1, "ignorado": 1}
    preview = c.get(f"/importacoes/{identifier}/linhas").json()
    assert preview["total"] == 4
    first = confirmar(c, identifier)
    assert first.status_code == 200, first.text
    assert confirmar(c, identifier).json() == first.json()
    old = c.get("/contatos").json()["itens"][0]
    again, state = preparar(cadastro, payload)
    assert state["contagens"]["sem_alteracao"] == 1
    assert confirmar(c, again).status_code == 200
    current = c.get("/contatos").json()["itens"][0]
    assert current == old
    report = c.get(f"/importacoes/{identifier}/relatorio")
    assert "José" not in report.text and "+5511" not in report.text
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(Contato)) == 1
        assert s.scalar(select(func.count()).select_from(ImportacaoLinha)) == 8


def test_conflito_no_arquivo_sem_ultima_linha_vencer(cadastro):
    c, _, _ = cadastro
    identifier, state = preparar(cadastro, b"nome;telefone\nA;+5511912345678\nB;+5511912345678\n")
    assert state["contagens"] == {"invalido": 2}
    assert confirmar(c, identifier).status_code == 200
    assert c.get("/contatos").json()["total"] == 0


def test_atualizacao_vazios_e_listas_preservadas(cadastro):
    c, _, _ = cadastro
    item = criar(c, observacoes="Manter").json()
    a = c.post("/listas", json={"nome": "A"}).json()["id"]
    b = c.post("/listas", json={"nome": "B"}).json()["id"]
    c.put(f"/listas/{a}/contatos/{item['id']}")
    identifier, state = preparar(
        cadastro,
        b"nome;telefone;notas\nNovo;+5511912345678;\n",
        {
            "coluna_observacoes": 2,
            "politica": "substituir",
            "campos_atualizar": ["nome", "observacoes"],
            "listas": [b],
        },
    )
    assert confirmar(c, identifier).status_code == 200
    current = c.get("/contatos/" + item["id"]).json()
    assert current["nome"] == "Novo" and current["observacoes"] == "Manter"
    assert set(current["listas"]) == {a, b}
    assert current["telefones"][0]["id"] == item["telefones"][0]["id"]


def test_arquivamento_nao_reativa(cadastro):
    c, _, _ = cadastro
    item = criar(c).json()
    c.patch("/contatos/" + item["id"], json={"versao": 1, "ativo": False})
    identifier, state = preparar(cadastro, b"nome;telefone\nNovo;+5511912345678\n")
    assert state["contagens"] == {"ignorado": 1}
    assert confirmar(c, identifier).status_code == 200
    assert c.get("/contatos").json()["total"] == 0


def test_previa_desatualizada_exige_revalidar(cadastro):
    c, _, _ = cadastro
    identifier, _ = preparar(cadastro, b"nome;telefone\nNovo;+442083661177\n")
    criar(c)
    result = confirmar(c, identifier)
    assert result.status_code == 409 and result.json()["codigo"] == "previa_desatualizada"
    assert c.get("/importacoes/" + identifier).json()["status"] == "carregada"
    assert c.get("/contatos").json()["total"] == 1


def test_falha_durante_lote_rollback_completo(cadastro):
    c, app, _ = cadastro
    identifier, _ = preparar(cadastro, b"nome;telefone\nA;+5511912345678\nB;+442083661177\n")
    engine = app.state.persistence.database.engine

    def fail(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO importacao_linhas"):
            raise OperationalError("privado", {}, Exception("privado"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        assert confirmar(c, identifier).status_code == 503
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert c.get("/contatos").json()["total"] == 0
    with app.state.contatos.repo.transacao() as s:
        assert s.scalar(select(func.count()).select_from(TelefoneContato)) == 0
        assert s.scalar(select(func.count()).select_from(Auditoria)) == 0
    assert confirmar(c, identifier).status_code == 200


def test_xlsx_formula_numeros_e_pais(cadastro):
    c, _, _ = cadastro
    payload = xlsx(
        [
            ["nome", "telefone"],
            ["Texto", "+442083661177"],
            ["Formula", "=1+1"],
            ["Numero", 11912345678],
            ["Zeros", "020 8366 1177"],
        ]
    )
    identifier, state = preparar(cadastro, payload, {"aba": "Sheet", "pais": "BR"}, "exemplo.xlsx")
    assert state["status"] == "pronta"
    assert state["contagens"] == {"inserido": 2, "invalido": 2}
    assert confirmar(c, identifier).status_code == 200


@pytest.mark.parametrize(
    "payload,name",
    [(b"PKcorrompido", ".xlsx"), (b"\x00binary", ".csv"), (b"x" * (MAX_BYTES + 1), ".csv")],
)
def test_uploads_invalidos(cadastro, payload, name):
    c, _, _ = cadastro
    identifier = c.post("/importacoes", json={"nome_arquivo": "contatos" + name}).json()["id"]
    assert c.put(f"/importacoes/{identifier}/arquivo", content=payload).status_code in {413, 422}


def test_csv_codificacao_limites_e_cancelamento(cadastro):
    c, _, _ = cadastro
    identifier, state = preparar(
        cadastro,
        "nome,telefone\nJoão,+442083661177".encode("cp1252"),
        {"codificacao": "cp1252", "delimitador": ","},
    )
    assert state["status"] == "pronta"
    assert c.post(f"/importacoes/{identifier}/cancelamento").status_code == 204
    assert confirmar(c, identifier).status_code == 409
    assert c.post("/importacoes", json={"nome_arquivo": "C:/dados.csv"}).status_code == 422


def test_zip_bomb_rejeitada(cadastro):
    c, _, _ = cadastro
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "x" * 1000000)
        z.writestr("xl/workbook.xml", "x")
    identifier = c.post("/importacoes", json={"nome_arquivo": "ataque.xlsx"}).json()["id"]
    assert c.put(f"/importacoes/{identifier}/arquivo", content=output.getvalue()).status_code == 422


def test_carga_8000_mista_preserva_contagens(cadastro):
    c, _, _ = cadastro
    valid = [f"Ficticio {i};+55119{10000000 + i:08}" for i in range(7000)]
    valid[0] = "Ficticio 0;+442083661177"
    valid[1] = "Ficticio 1;+390236618300"
    rows = valid + valid[:800] + ["Erro;invalido"] * 100 + [";"] * 100
    identifier, state = preparar(cadastro, ("nome;telefone\n" + "\n".join(rows) + "\n").encode())
    assert state["contagens"] == {
        "inserido": 7000,
        "duplicado": 800,
        "invalido": 100,
        "ignorado": 100,
    }
    assert confirmar(c, identifier).json()["contagens"]["total_linhas"] == 8000
    assert c.get("/contatos").json()["total"] == 7000


def test_interrupcao_e_expiracao_sem_gravacao(cadastro):
    c, app, _ = cadastro
    identifier, _ = preparar(cadastro, b"nome;telefone\nNovo;+442083661177\n")
    app.state.importacoes.previa.criada -= 1801
    assert c.get("/importacoes/" + identifier).json()["status"] == "expirada"
    assert confirmar(c, identifier).status_code == 409
    new = c.post("/importacoes", json={"nome_arquivo": "novo.csv"}).json()["id"]
    app.state.importacoes.close()
    assert c.get("/importacoes/" + new).json()["status"] == "interrompida"
    assert c.get("/contatos").json()["total"] == 0
