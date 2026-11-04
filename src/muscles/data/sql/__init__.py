from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .native_migrations import (
        NativeSqlMigration,
        NativeSqlMigrationBackend,
        NativeSqlMigrationSourceBackend,
        build_native_sql_migration_registry,
    )
    from .drivers import SqlDriverCatalog
    from .native import SqlDatabase, SqlTransaction
    from .model_mapper import ModelMapper
    from .config import DatabaseConfig
    from .config import SqlConnectionConfig
    from .connections import SqlConnectionRegistry, UnknownSqlConnection
    from .engine import EngineManager
    from .mapping import map_model
    from .providers import SqlResourceGeneratorProvider
    from .query import FilterClause, JoinClause, QuerySpec
    from .repository import SqlRepository
    from .uow import UnitOfWork
    from .sqlalchemy_compat import text

__all__ = [
    "NativeSqlMigration",
    "NativeSqlMigrationBackend",
    "NativeSqlMigrationSourceBackend",
    "build_native_sql_migration_registry",
    "SqlDriverCatalog",
    "SqlDatabase",
    "SqlTransaction",
    "ModelMapper",
    "DatabaseConfig",
    "SqlConnectionConfig",
    "SqlConnectionRegistry",
    "UnknownSqlConnection",
    "EngineManager",
    "map_model",
    "SqlRepository",
    "UnitOfWork",
    "text",
    "QuerySpec",
    "FilterClause",
    "JoinClause",
    "SqlResourceGeneratorProvider",
]


def __getattr__(name: str):
    if name in {
        "NativeSqlMigration",
        "NativeSqlMigrationBackend",
        "NativeSqlMigrationSourceBackend",
        "build_native_sql_migration_registry",
    }:
        from . import native_migrations

        return getattr(native_migrations, name)
    if name == "SqlDriverCatalog":
        from .drivers import SqlDriverCatalog

        return SqlDriverCatalog
    if name == "ModelMapper":
        from .model_mapper import ModelMapper

        return ModelMapper
    if name in {"SqlDatabase", "SqlTransaction"}:
        from .native import SqlDatabase, SqlTransaction

        return {"SqlDatabase": SqlDatabase, "SqlTransaction": SqlTransaction}[name]
    if name == "DatabaseConfig":
        from .config import DatabaseConfig

        return DatabaseConfig
    if name == "SqlConnectionConfig":
        from .config import SqlConnectionConfig

        return SqlConnectionConfig
    if name == "SqlConnectionRegistry":
        from .connections import SqlConnectionRegistry

        return SqlConnectionRegistry
    if name == "UnknownSqlConnection":
        from .connections import UnknownSqlConnection

        return UnknownSqlConnection
    if name == "EngineManager":
        from .engine import EngineManager

        return EngineManager
    if name == "map_model":
        from .mapping import map_model

        return map_model
    if name == "SqlRepository":
        from .repository import SqlRepository

        return SqlRepository
    if name == "UnitOfWork":
        from .uow import UnitOfWork

        return UnitOfWork
    if name == "text":
        from .sqlalchemy_compat import text

        return text
    if name == "QuerySpec":
        from .query import QuerySpec

        return QuerySpec
    if name == "FilterClause":
        from .query import FilterClause

        return FilterClause
    if name == "JoinClause":
        from .query import JoinClause

        return JoinClause
    if name == "SqlResourceGeneratorProvider":
        from .providers import SqlResourceGeneratorProvider

        return SqlResourceGeneratorProvider
    raise AttributeError(name)
