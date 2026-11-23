"""Orquestração restrita à infraestrutura, antes da prontidão HTTP."""

from app.core.config import Settings
from app.persistencia.database import Database
from app.persistencia.errors import DatabaseAccessError, InstanceInUseError, PersistenceError
from app.persistencia.instance_lock import InstanceLock
from app.persistencia.migrations import (
    bundled_migrations,
    migrate,
    migration_config,
    read_revision,
    schema_validator,
    validate_history,
)


class Persistence:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = InstanceLock(settings.database_path)
        self.database: Database | None = None
        self.ready = False
        self.revision: str | None = None

    def start(self) -> None:
        if self.ready:
            raise InstanceInUseError("A persistência desta instância já foi iniciada.")
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            self.lock.acquire()
            path = self.settings.database_path
            existing = path.exists()
            with bundled_migrations() as location:
                config = migration_config(location)
                current = read_revision(path)
                validate_history(config, current)
                if not existing:
                    path.touch(exist_ok=False)
                self.database = Database(path)
                migrate(
                    self.database.engine,
                    path,
                    self.settings.snapshots_dir,
                    current,
                    config,
                    existing=existing,
                )
                self.database.schema_validator = schema_validator(config, read_revision(path))
            self.revision = read_revision(path)
            from app.consentimentos.suspensoes import SuspensoesConsentimento

            self.database.suspensoes_consentimento = SuspensoesConsentimento(self.database, path)
            self.database.suspensoes_consentimento.recuperar()
            if not self.database.probe(self.revision):
                raise DatabaseAccessError("O banco local não está disponível.")
            self.ready = True
        except BaseException as error:
            self.close()
            if isinstance(error, (PersistenceError, KeyboardInterrupt, SystemExit)):
                raise
            raise DatabaseAccessError(
                "Não foi possível inicializar a persistência local."
            ) from None

    def probe(self) -> bool:
        return self.ready and self.database is not None and self.database.probe(self.revision)

    def close(self) -> None:
        self.ready = False
        try:
            if self.database is not None:
                self.database.close()
        finally:
            self.lock.release()
