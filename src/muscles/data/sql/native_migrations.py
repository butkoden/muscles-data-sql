"""Transactional, explicitly registered SQLite and PostgreSQL migrations."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from pathlib import PurePosixPath
from types import ModuleType
from typing import TYPE_CHECKING, Callable, Iterable
from urllib.parse import unquote

from .native import SqlDatabase, SqlTransaction

if TYPE_CHECKING:
    from muscles.data.migrations import MigrationRegistry


@dataclass(frozen=True)
class NativeSqlMigration:
    revision: str
    checksum: str
    upgrade: Callable[[SqlTransaction], None]
    downgrade: Callable[[SqlTransaction], None] | None = None
    dialects: tuple[str, ...] = ()
    source_path: str | None = None

    @classmethod
    def from_module(cls, module: ModuleType) -> NativeSqlMigration:
        path = getattr(module, "__file__", None)
        if not path or not str(path).endswith(".py"):
            raise ValueError("Migration must be backed by a Python source file")
        checksum = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        loaded_checksum = getattr(module, "__muscles_migration_checksum__", checksum)
        if loaded_checksum != checksum:
            raise ValueError("Migration file changed after module discovery")
        return cls(module.revision, checksum,
                   module.upgrade, getattr(module, "downgrade", None), getattr(module, "dialects", ()), str(path))


class NativeSqlMigrationBackend:
    """Run a source atomically; only SQLite and PostgreSQL transactional SQL are supported."""

    def __init__(self, db: SqlDatabase, source: str, migrations: list[NativeSqlMigration], *, dialect: str):
        if dialect not in {"sqlite", "postgres"}:
            raise ValueError("Native migrations support only sqlite and postgres")
        if not isinstance(source, str) or not source:
            raise ValueError("Migration source is required")
        if any(not isinstance(item, NativeSqlMigration) or not isinstance(item.revision, str)
                or not item.revision or not isinstance(item.checksum, str) or not item.checksum
                or not callable(item.upgrade) or (item.downgrade is not None and not callable(item.downgrade))
                for item in migrations):
            raise ValueError("Migrations must have unique ordered revisions, checksums and callable operations")
        revisions = [item.revision for item in migrations]
        if revisions != sorted(set(revisions)):
            raise ValueError("Migrations must have unique ordered revisions, checksums and callable operations")
        if any(not isinstance(item.dialects, tuple) or dialect not in item.dialects for item in migrations):
            raise ValueError("Every migration must explicitly support the selected SQL dialect")
        self.db, self.source, self.migrations, self.dialect = db, source, tuple(migrations), dialect
        self._param = "?" if dialect == "sqlite" else "%s"

    @contextmanager
    def _write_transaction(self):
        begin = "BEGIN IMMEDIATE" if self.dialect == "sqlite" else self.db._begin_sql
        with self.db._transaction(begin) as tx:
            if self.dialect == "postgres":
                row = tx.fetch_one("SELECT pg_try_advisory_xact_lock(%s) AS locked", (778212473,))
                if not row or not row["locked"]:
                    raise RuntimeError("SQL migrations are locked by another process")
            yield tx

    def _state(self, tx: SqlTransaction) -> list[str]:
        for migration in self.migrations:
            if migration.source_path is not None:
                try:
                    checksum = hashlib.sha256(Path(migration.source_path).read_bytes()).hexdigest()
                except OSError as exc:
                    raise ValueError("A migration file is missing or unreadable") from exc
                if checksum != migration.checksum:
                    raise ValueError("A migration file changed after discovery")
        if self.dialect == "sqlite":
            found = tx.fetch_one("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                                 ("muscles_data_migrations",))
        else:
            found = tx.fetch_one("SELECT to_regclass(%s) AS name", ("muscles_data_migrations",))
        if not found or not found["name"]:
            return []
        rows = tx.fetch_all(f"SELECT revision, checksum FROM muscles_data_migrations WHERE source = {self._param} ORDER BY revision",
                            (self.source,))
        expected = self.migrations[:len(rows)]
        if len(rows) > len(self.migrations) or [row["revision"] for row in rows] != [item.revision for item in expected]:
            raise ValueError("Migration history is missing, unknown or out of order")
        if any(row["checksum"] != item.checksum for row, item in zip(rows, expected)):
            raise ValueError("An applied migration has changed")
        return [row["revision"] for row in rows]

    def status(self) -> dict:
        with self.db.transaction() as tx:
            current = self._state(tx)
        return {"current": current[-1:] if current else [], "applied": current,
                "pending": [item.revision for item in self.migrations[len(current):]],
                "verification": "checksums"}

    def plan(self) -> list[str]:
        return self.status()["pending"]

    def upgrade(self, target: str = "head") -> None:
        revisions = [item.revision for item in self.migrations]
        if target != "head" and target not in revisions:
            raise ValueError("Unknown migration target")
        end = len(revisions) if target == "head" else revisions.index(target) + 1
        with self._write_transaction() as tx:
            current = self._state(tx)
            if end < len(current):
                raise ValueError("Upgrade target is older than current revision")
            if end == len(current):
                return
            tx.execute("CREATE TABLE IF NOT EXISTS muscles_data_migrations ("
                       "source TEXT NOT NULL, revision TEXT NOT NULL, checksum TEXT NOT NULL, "
                       "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (source, revision))")
            for item in self.migrations[len(current):end]:
                item.upgrade(tx)
                tx.execute(f"INSERT INTO muscles_data_migrations (source, revision, checksum) VALUES ({self._param}, {self._param}, {self._param})",
                           (self.source, item.revision, item.checksum))

    def downgrade(self, target: str) -> None:
        revisions = [item.revision for item in self.migrations]
        if target != "base" and target not in revisions:
            raise ValueError("Unknown migration target")
        end = 0 if target == "base" else revisions.index(target) + 1
        with self._write_transaction() as tx:
            current = self._state(tx)
            if end > len(current):
                raise ValueError("Downgrade target is not applied")
            to_revert = tuple(reversed(self.migrations[end:len(current)]))
            if any(item.downgrade is None for item in to_revert):
                raise ValueError("A migration has no downgrade")
            for item in to_revert:
                item.downgrade(tx)
                tx.execute(f"DELETE FROM muscles_data_migrations WHERE source = {self._param} AND revision = {self._param}",
                           (self.source, item.revision))


class NativeSqlMigrationSourceBackend:
    """MigrationRegistry backend that owns a short-lived native SQL connection per command."""

    def __init__(
        self,
        url: str,
        source: str,
        migrations: list[NativeSqlMigration],
    ) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("A database URL is required for native SQL migrations")
        self.url = url
        self.source = source
        self.migrations = list(migrations)

    def _open_database(self) -> tuple[SqlDatabase, str]:
        if self.url.startswith("sqlite:///"):
            path = unquote(self.url[len("sqlite:///"):])
            if not path:
                raise ValueError("SQLite migration URL must include a database path")
            return SqlDatabase.sqlite(path), "sqlite"
        if self.url.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")):
            return SqlDatabase.postgres(self.url), "postgres"
        raise ValueError("Native SQL migrations require a PostgreSQL or SQLite URL")

    def _backend(self, database: SqlDatabase, dialect: str) -> NativeSqlMigrationBackend:
        return NativeSqlMigrationBackend(database, self.source, self.migrations, dialect=dialect)

    def _run(self, operation: str, *args):
        database, dialect = self._open_database()
        try:
            backend = self._backend(database, dialect)
            return getattr(backend, operation)(*args)
        finally:
            database.close()

    def upgrade(self, target: str = "head") -> None:
        self._run("upgrade", target)

    def downgrade(self, target: str) -> None:
        self._run("downgrade", target)

    def plan(self) -> list[str]:
        return self.status()["pending"]

    def status(self) -> dict:
        database, dialect = self._open_database()
        try:
            backend = self._backend(database, dialect)
            with database.transaction() as transaction:
                applied = backend._state(transaction)
                revisions = [item.revision for item in self.migrations]
            return {
                "current": applied[-1:] if applied else [],
                "heads": revisions[-1:] if revisions else [],
                "applied": applied,
                "pending": revisions[len(applied):],
                "verification": "checksums",
            }
        finally:
            database.close()


def _register_native_sql_migrations(
    registry: MigrationRegistry,
    package_classes: Iterable[type],
    database_url: str | None,
    *,
    pattern: str = "migrate/000[0-9]_*.py",
) -> None:
    """Discover package migrations and derive source dependencies from package metadata."""
    from muscles.data.migrations import MigrationSource, discover_migration_modules

    directory_parts = PurePosixPath(pattern).parent.parts
    if not directory_parts or any(not part.isidentifier() for part in directory_parts):
        raise ValueError("Native SQL migration pattern must include a package directory")
    discovered = []

    for package_class in package_classes:
        class_module = getattr(package_class, "__module__", "")
        package_name = class_module.rpartition(".")[0]
        if not package_name:
            continue
        migration_package = f"{package_name}.{'.'.join(directory_parts)}"
        try:
            migration_package_spec = find_spec(migration_package)
        except ModuleNotFoundError as exc:
            if exc.name == migration_package:
                continue
            raise
        if migration_package_spec is None:
            continue

        namespace = getattr(package_class, "namespace", None)
        if not isinstance(namespace, str) or not namespace:
            raise ValueError(f"Package {package_class.__name__} has no namespace")
        modules = discover_migration_modules(package_name, pattern=pattern)
        if not modules:
            raise ValueError(
                f"No SQL migrations found for package {namespace!r} matching {pattern!r}"
            )
        if not database_url:
            raise ValueError(
                f"Package {package_class.__name__} migrations require the 'database' resource"
            )

        migrations = [NativeSqlMigration.from_module(module) for module in modules]
        discovered.append((namespace, package_class, migrations))

    migration_names = {namespace for namespace, _, _ in discovered}
    for namespace, package_class, migrations in discovered:
        dependencies = tuple(
            dependency
            for dependency in getattr(package_class, "dependencies", ())
            if dependency in migration_names
        )
        registry.register(MigrationSource(
            namespace,
            NativeSqlMigrationSourceBackend(database_url, namespace, migrations),
            depends_on=dependencies,
        ))


def build_native_sql_migration_registry(
    package_classes: Iterable[type],
    database_url: str | None,
    *,
    pattern: str = "migrate/000[0-9]_*.py",
) -> MigrationRegistry:
    """Build a registry and SQL lock, deriving source order from package dependencies."""
    from muscles.data.migrations import MigrationRegistry
    from .migration_lock import SqlMigrationExecutionLock

    registry = MigrationRegistry(
        execution_lock=SqlMigrationExecutionLock(database_url) if database_url else None,
    )
    _register_native_sql_migrations(
        registry,
        package_classes,
        database_url,
        pattern=pattern,
    )
    registry.ordered()
    return registry
