"""Alembic recebe somente conexão já validada e protegida pelo ciclo de vida."""

from alembic import context

import app.auth.models  # noqa: F401
import app.campanhas.execucoes_models  # noqa: F401
import app.campanhas.models  # noqa: F401
import app.consentimentos.models  # noqa: F401
import app.contas_envio.models  # noqa: F401
import app.contatos.models  # noqa: F401
import app.importacoes.models  # noqa: F401
import app.templates.models  # noqa: F401
from app.persistencia.models import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Execute migrações pelo ciclo de vida da persistência eDisk.")

context.configure(connection=connection, target_metadata=Base.metadata, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
