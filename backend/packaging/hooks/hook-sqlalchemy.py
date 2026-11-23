"""Somente SQLite; nao coletar drivers externos ou plugins de desenvolvimento."""

hiddenimports = ["sqlalchemy.dialects.sqlite.pysqlite", "sqlalchemy.sql.default_comparator"]
excludedimports = ["sqlalchemy.testing"]
