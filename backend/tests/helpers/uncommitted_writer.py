"""Harness de teste interrompido com transação real ainda não confirmada."""

import sys

from sqlalchemy import text

from app.core.config import Settings
from app.persistencia.database import Database
from app.persistencia.instance_lock import InstanceLock

settings = Settings.from_environment()
lock = InstanceLock(settings.database_path)
lock.acquire()
database = Database(settings.database_path)
with database.transaction() as session:
    session.execute(text("INSERT INTO alembic_version VALUES ('nao_confirmado')"))
    print("uncommitted", flush=True)
    sys.stdin.readline()
database.close()
lock.release()
