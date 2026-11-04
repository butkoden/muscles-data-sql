"""Discover a package's migration modules and run them on isolated SQLite.

Run from muscles-data-sql with PYTHONPATH=src:../muscles/src:../muscles-data/src.
"""
from muscles.data import MigrationRegistry, MigrationSource, discover_migration_modules
from muscles.data.sql import NativeSqlMigration, NativeSqlMigrationBackend, SqlDatabase


def main():
    db = SqlDatabase.sqlite(":memory:")
    try:
        modules = discover_migration_modules("demo_migrations")
        revisions = [NativeSqlMigration.from_module(module) for module in modules]
        registry = MigrationRegistry()
        registry.register(MigrationSource("demo", NativeSqlMigrationBackend(db, "demo", revisions, dialect="sqlite")))
        print("plan:", registry.plan())
        registry.upgrade()
        print("status after upgrade:", registry.status())
        registry.downgrade("demo", "base")
        print("status after rollback:", registry.status())
    finally:
        db.close()


if __name__ == "__main__":
    main()
