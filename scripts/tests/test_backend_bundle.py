"""Executavel PyInstaller real; somente dados ficticios e temporarios."""

import hashlib
import io
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

import httpx2
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/tests"))
from helpers.private_backend import PrivateBackend

from app.runtime.control_protocol import Code, Kind

EXE = Path(sys.argv.pop(1)).resolve()
PASSWORD = "frase ficticia exclusiva ensaio empacotado 493!"


class BackendBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edisk-bundle-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, root=None, executable=EXE):
        return PrivateBackend(root or self.root / "dados", cwd=self.root, executable=executable)

    def client(self, backend):
        return httpx2.Client(base_url=backend.ready(), headers=backend.headers, trust_env=False)

    def test_authentication_reopen_and_replacement(self):
        old_token = None
        old_headers = None
        for opening in range(3):
            executable = EXE
            if opening == 2:
                copy = self.root / "outra versão com espaços e ação" / "backend"
                shutil.copytree(EXE.parent, copy)
                executable = copy / EXE.name
            with self.backend(executable=executable) as backend:
                with self.client(backend) as client:
                    self.assertEqual(client.get("/health").status_code, 200)
                    if old_headers:
                        self.assertEqual(
                            client.get("/health", headers=old_headers).status_code, 401
                        )
                    self.assertEqual(
                        httpx2.get(
                            str(client.base_url).rstrip("/") + "/health", trust_env=False
                        ).status_code,
                        401,
                    )
                    self.assertEqual(
                        client.get("/health", headers={"Origin": "null"}).status_code, 403
                    )
                    self.assertEqual(
                        client.get("/auth/configuracao-inicial").json()["necessaria"], opening == 0
                    )
                    if opening == 0:
                        self.assertEqual(
                            client.post(
                                "/auth/configuracao-inicial",
                                json={
                                    "nome": "Pessoa Fictícia",
                                    "login": "teste.local",
                                    "senha": PASSWORD,
                                },
                            ).status_code,
                            201,
                        )
                    if old_token:
                        self.assertEqual(
                            client.get(
                                "/auth/usuario", headers={"X-eDisk-Session": old_token}
                            ).status_code,
                            401,
                        )
                    response = client.post(
                        "/auth/login", json={"login": "teste.local", "senha": PASSWORD}
                    )
                    self.assertEqual(response.status_code, 200)
                    token = response.json()["token"]
                    self.assertEqual(
                        client.get("/auth/usuario", headers={"X-eDisk-Session": token}).status_code,
                        200,
                    )
                    self.assertEqual(
                        client.post("/auth/logout", headers={"X-eDisk-Session": token}).status_code,
                        204,
                    )
                    self.assertEqual(
                        client.get("/auth/usuario", headers={"X-eDisk-Session": token}).status_code,
                        401,
                    )
                    old_token = client.post(
                        "/auth/login", json={"login": "teste.local", "senha": PASSWORD}
                    ).json()["token"]
                old_headers = backend.headers
                if opening == 1:
                    backend.kill_backend()
                    backend.process.wait(timeout=5)
                else:
                    backend.stop()
                backend.logs.join(timeout=2)
                self.assertNotIn(PASSWORD.encode(), backend.diagnostics)
                self.assertNotIn(old_token.encode(), backend.diagnostics)
                self.assertNotIn(backend.authorization.token.encode(), backend.diagnostics)

    def test_missing_changed_and_extra_migration_before_database(self):
        for action in ("missing", "changed", "extra"):
            with self.subTest(action=action):
                copy = self.root / action
                shutil.copytree(EXE.parent, copy)
                migrations = copy / "_internal/app/persistencia/alembic"
                target = migrations / "versions/0001_autenticacao_local.py"
                if action == "missing":
                    target.unlink()
                elif action == "changed":
                    target.write_text("raise RuntimeError('nao executar')", encoding="utf-8")
                else:
                    (migrations / "versions/extra.py").write_text("", encoding="utf-8")
                data = self.root / ("dados-" + action)
                with self.backend(data, copy / EXE.name) as backend:
                    backend.next(Kind.HELLO)
                    self.assertEqual(
                        struct.unpack("<H", backend.next(Kind.ERROR))[0], Code.CONFIGURATION
                    )
                    self.assertNotEqual(backend.process.wait(timeout=10), 0)
                    self.assertFalse(data.exists())

    def test_conflict_preserves_first(self):
        with self.backend() as first:
            with self.client(first) as client:
                with self.backend() as second:
                    second.next(Kind.HELLO)
                    second.next(Kind.PREPARING)
                    self.assertEqual(
                        struct.unpack("<H", second.next(Kind.ERROR))[0], Code.INSTANCE_IN_USE
                    )
                    self.assertNotEqual(second.process.wait(timeout=10), 0)
                self.assertEqual(client.get("/health").status_code, 200)
            first.stop()

    def test_initial_migration_snapshot(self):
        data = self.root / "dados/data"
        data.mkdir(parents=True)
        with closing(sqlite3.connect(data / "edisk.sqlite3")) as db, db:
            db.execute("CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        for _ in range(2):
            with self.backend() as backend:
                backend.ready()
                backend.stop()
        snapshots = list((self.root / "dados/backups/pre-migration").glob("*.sqlite3"))
        self.assertEqual(len(snapshots), 1)

    def test_no_init_does_not_serve(self):
        result = subprocess.run([str(EXE)], input=b"", capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_missing_python_dll_fails(self):
        copy = self.root / "sem dll"
        shutil.copytree(EXE.parent, copy)
        (copy / "_internal/python314.dll").unlink()
        result = subprocess.run([str(copy / EXE.name)], input=b"", capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)

    def test_execution_does_not_change_bundle(self):
        def hashes():
            return {
                p.relative_to(EXE.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in EXE.parent.rglob("*")
                if p.is_file()
            }

        before = hashes()
        with self.backend() as backend:
            backend.ready()
            backend.stop()
        self.assertEqual(hashes(), before)

    def test_contacts_and_xlsx_csv_in_frozen_runtime(self):
        with self.backend() as backend:
            with self.client(backend) as client:
                self.assertEqual(client.get("/contatos").status_code, 401)
                self.assertEqual(
                    client.post(
                        "/auth/configuracao-inicial",
                        json={"nome": "Pessoa Fictícia", "login": "teste", "senha": PASSWORD},
                    ).status_code,
                    201,
                )
                token = client.post(
                    "/auth/login", json={"login": "teste", "senha": PASSWORD}
                ).json()["token"]
                client.headers["X-eDisk-Session"] = token
                # Exercita metadados internacionais lazy dentro do PYZ.
                for phone in ["+5511912345678", "+442083661177", "+390236618300", "+16502530000"]:
                    result = client.post("/contatos", json={"telefone": phone})
                    self.assertEqual(result.status_code, 201, result.text)
                group = client.post("/listas", json={"nome": "Ensaio"}).json()["id"]
                for extension in ["csv", "xlsx"]:
                    if extension == "csv":
                        payload = b"nome;telefone\nFicticio;+442083661177\n"
                    else:
                        book = Workbook()
                        book.active.append(["nome", "telefone"])
                        book.active.append(["Ficticio", "+442083661177"])
                        target = io.BytesIO()
                        book.save(target)
                        book.close()
                        payload = target.getvalue()
                    identifier = client.post(
                        "/importacoes", json={"nome_arquivo": "ensaio." + extension}
                    ).json()["id"]
                    self.assertEqual(
                        client.put(
                            f"/importacoes/{identifier}/arquivo", content=payload
                        ).status_code,
                        200,
                    )
                    self.assertEqual(
                        client.post(
                            f"/importacoes/{identifier}/validacao",
                            json={
                                "coluna_nome": 0,
                                "coluna_telefone": 1,
                                "aba": "Sheet" if extension == "xlsx" else None,
                                "listas": [group],
                            },
                        ).status_code,
                        202,
                    )
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        state = client.get("/importacoes/" + identifier).json()
                        if state["status"] != "validando":
                            break
                        time.sleep(0.05)
                    self.assertEqual(state["status"], "pronta", state)
                    response = client.post(f"/importacoes/{identifier}/confirmacao")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(
                        client.post(f"/importacoes/{identifier}/confirmacao").json(),
                        response.json(),
                    )
                self.assertEqual(client.get("/contatos").json()["total"], 4)
                self.assertEqual(client.get(f"/listas/{group}/contatos").json()["total"], 1)
            backend.stop()


if __name__ == "__main__":
    unittest.main()
