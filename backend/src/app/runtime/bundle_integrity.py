"""Valida recursos executaveis antes de qualquer acesso aos dados do cliente."""

import hashlib
from pathlib import Path

from alembic.script import ScriptDirectory

from app.persistencia.errors import ConfigurationError
from app.persistencia.migrations import migration_config

HEAD = "0019_whatsapp_envio_unitario"
RESOURCE_FILES = (
    "env.py",
    "script.py.mako",
    "versions/0001_autenticacao_local.py",
    "versions/0002_contatos_listas_importacoes.py",
    "versions/0003_consentimentos.py",
    "versions/0004_templates.py",
    "versions/0005_contas_envio.py",
    "versions/0006_campanhas_rascunho.py",
    "versions/0007_preparacoes_cifradas.py",
    "versions/0008_contas_mock.py",
    "versions/0009_autorizacoes_simulacao.py",
    "versions/0010_autorizacoes_utc.py",
    "versions/0011_telegram_optin.py",
    "versions/0012_telegram_recepcao.py",
    "versions/0013_telegram_consentimento.py",
    "versions/0014_telegram_campanhas.py",
    "versions/0015_telegram_envio_unitario.py",
    "versions/0016_telegram_reativacao.py",
    "versions/0017_telegram_lote_preparacao.py",
    "versions/0018_telegram_executor_lote.py",
    "versions/0019_whatsapp_envio_unitario.py",
)


def verify_migrations(location: Path, expected: dict[str, str]) -> None:
    """O catalogo esperado e incorporado ao executavel, nunca confiado ao bundle."""
    try:
        actual = {
            p.relative_to(location).as_posix()
            for p in location.rglob("*")
            if p.is_file() and p.suffix in {".py", ".pyc", ".mako"}
        }
        if actual != set(RESOURCE_FILES) or set(expected) != set(RESOURCE_FILES):
            raise ValueError
        for relative, digest in expected.items():
            if hashlib.sha256((location / relative).read_bytes()).hexdigest() != digest:
                raise ValueError
        scripts = ScriptDirectory.from_config(migration_config(location))
        revisions = list(scripts.walk_revisions())
        if scripts.get_heads() != [HEAD] or len(revisions) != 19:
            raise ValueError
        chain = [
            HEAD,
            "0018_telegram_executor_lote",
            "0017_telegram_lote_preparacao",
            "0016_telegram_reativacao",
            "0015_telegram_envio_unitario",
            "0014_telegram_campanhas",
            "0013_telegram_consentimento",
            "0012_telegram_recepcao",
            "0011_telegram_optin",
            "0010_autorizacoes_utc",
            "0009_autorizacoes_simulacao",
            "0008_contas_mock",
            "0007_preparacoes_cifradas",
            "0006_campanhas_rascunho",
            "0005_contas_envio",
            "0004_templates",
            "0003_consentimentos",
            "0002_contatos_listas_importacoes",
            "0001_autenticacao_local",
        ]
        if [(r.revision, r.down_revision) for r in revisions] != list(
            zip(chain, [*chain[1:], None], strict=True)
        ):
            raise ValueError
    except Exception:
        raise ConfigurationError(
            "Recursos de migracao ausentes, alterados ou incompativeis."
        ) from None


def verify_frozen_bundle() -> None:
    # Este modulo gerado existe somente dentro do executavel.
    from app.persistencia.migrations import bundled_migrations
    from app.runtime._bundle_catalog import MIGRATIONS

    with bundled_migrations() as location:
        verify_migrations(location, MIGRATIONS)
