# PyInstaller 6.22.3, Windows x64, CPython 3.14; executar a partir de qualquer cwd.
import hashlib
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

backend = Path(SPECPATH).resolve().parent
source = backend / "src"
resources = source / "app/persistencia/alembic"
required = ("env.py", "script.py.mako", "versions/0001_autenticacao_local.py",
            "versions/0002_contatos_listas_importacoes.py", "versions/0003_consentimentos.py",
            "versions/0004_templates.py", "versions/0005_contas_envio.py", "versions/0006_campanhas_rascunho.py", "versions/0007_preparacoes_cifradas.py", "versions/0008_contas_mock.py", "versions/0009_autorizacoes_simulacao.py", "versions/0010_autorizacoes_utc.py", "versions/0011_telegram_optin.py", "versions/0012_telegram_recepcao.py", "versions/0013_telegram_consentimento.py", "versions/0014_telegram_campanhas.py", "versions/0015_telegram_envio_unitario.py", "versions/0016_telegram_reativacao.py", "versions/0017_telegram_lote_preparacao.py", "versions/0018_telegram_executor_lote.py", "versions/0019_whatsapp_envio_unitario.py")
# Catalogo compilado no PYZ; um manifesto editavel nao e autoridade de integridade.
catalog = Path(workpath) / "generated/app/runtime"
catalog.mkdir(parents=True, exist_ok=True)
hashes = {name: hashlib.sha256((resources / name).read_bytes()).hexdigest() for name in required}
(catalog / "_bundle_catalog.py").write_text("MIGRATIONS = " + repr(hashes) + "\n", encoding="utf-8")

a = Analysis(
    [str(backend / "packaging/entry.py")],
    pathex=[str(source)],
    binaries=[],
    datas=[(str(resources / name), "app/persistencia/alembic/" + str(Path(name).parent))
           for name in required]
          + copy_metadata("alembic") + copy_metadata("sqlalchemy")
          + copy_metadata("uvicorn") + copy_metadata("opentelemetry-api"),
    # Uvicorn escolhe esses modulos por strings; SQLAlchemy resolve o dialect por URL.
    hiddenimports=["uvicorn.logging", "uvicorn.loops.asyncio",
                   "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on",
                   "sqlalchemy.dialects.sqlite.pysqlite", "_cffi_backend",
                   "app.runtime.bundle_integrity", "app.campanhas.execucoes_models"],
    excludes=["pytest", "ruff", "httpx2", "httpcore2", "unittest", "tkinter",
              "pygments", "packaging", "pluggy", "iniconfig", "colorama",
              "greenlet.tests", "sqlalchemy.testing", "pydoc", "doctest", "setuptools", "pkg_resources", "importlib_metadata", "importlib_resources"],
    hookspath=[str(backend / "packaging/hooks")], runtime_hooks=[], noarchive=False,
)
# Pacote app regular: registrar o modulo gerado diretamente no grafo.
a.pure.append(("app.runtime._bundle_catalog", str(catalog / "_bundle_catalog.py"), "PYMODULE"))
# Windows 10/11 fornece UCRT/API sets. Nao redistribuir copias coletadas de System32.
a.binaries = [item for item in a.binaries
              if not item[0].lower().startswith("api-ms-")
              and item[0].lower() != "ucrtbase.dll"]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="edisk-backend",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="edisk-backend")

# PyInstaller percorre um conjunto de modulos ao criar base_library.zip.
# Canonicalizar somente a ordem das entradas; bytecode e metadados sao preservados.
import zipfile
base = Path(DISTPATH) / "edisk-backend/_internal/base_library.zip"
with zipfile.ZipFile(base) as archive:
    entries = [(info, archive.read(info)) for info in archive.infolist()]
temporary = base.with_suffix(".sorted.tmp")
with zipfile.ZipFile(temporary, "w") as archive:
    for info, payload in sorted(entries, key=lambda item: item[0].filename):
        archive.writestr(info, payload)
temporary.replace(base)
