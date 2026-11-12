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
    from .native_policy import compile_operation_scope
    from .native_repository import NativePolicyRepository, native_repository_from_runtime, native_repository_transaction
    from .model_mapper import ModelMapper
    from .config import DatabaseConfig
    from .config import SqlConnectionConfig
    from .connections import SqlConnectionRegistry, UnknownSqlConnection
    from .providers import SqlResourceGeneratorProvider
    from .query import FilterClause, JoinClause, QuerySpec

__all__ = [
    "NativeSqlMigration",
    "NativeSqlMigrationBackend",
    "NativeSqlMigrationSourceBackend",
    "build_native_sql_migration_registry",
    "SqlDriverCatalog",
    "SqlDatabase",
    "SqlTransaction",
    "compile_operation_scope",
    "NativePolicyRepository",
    "native_repository_from_runtime",
    "native_repository_transaction",
    "ModelMapper",
    "DatabaseConfig",
    "SqlConnectionConfig",
    "SqlConnectionRegistry",
    "UnknownSqlConnection",
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
    if name == "compile_operation_scope":
        from .native_policy import compile_operation_scope
        return compile_operation_scope
    if name in {"NativePolicyRepository", "native_repository_from_runtime", "native_repository_transaction"}:
        from . import native_repository
        return getattr(native_repository, name)
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
