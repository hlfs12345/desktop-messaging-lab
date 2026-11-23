"""Erros operacionais com mensagens que não expõem SQL ou dados locais."""


class PersistenceError(Exception):
    """Falha impeditiva da persistência."""


class ConfigurationError(PersistenceError):
    pass


class DatabaseAccessError(PersistenceError):
    pass


class InstanceInUseError(PersistenceError):
    pass


class LockAccessError(PersistenceError):
    pass


class SchemaError(PersistenceError):
    pass


class IntermediateSchemaError(SchemaError):
    """Checkpoint conhecido da 0010; exige recuperacao supervisionada."""


class SnapshotError(PersistenceError):
    pass


class MigrationError(PersistenceError):
    pass
