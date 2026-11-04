"""Two independent SQL resources and a project-registered driver.

Run from muscles-data-sql:
    PYTHONPATH=src:../muscles/src:../muscles-data/src python3 examples/native_resources.py
"""
from muscles_data.catalog import DataAdapterCatalog
from muscles_data.config import DataConfig
from muscles_data.ports import SqlTransactionPort
from muscles_data.runtime import DataRuntime

from muscles_data_sql import SqlDatabase, SqlDriverCatalog
from muscles_data_sql.data_adapter import NativeSqlFactory


def open_ephemeral(options):
    """Example of an application-specific connection factory."""
    return SqlDatabase.sqlite(":memory:")


def main():
    drivers = SqlDriverCatalog.with_defaults()
    drivers.register("ephemeral", open_ephemeral)

    adapters = DataAdapterCatalog.with_defaults()
    adapters.register(NativeSqlFactory(drivers=drivers))
    runtime = DataRuntime(
        config=DataConfig.from_raw({"resources": {
            "sql.primary": {"type": "native_sql", "driver": "sqlite", "path": ":memory:"},
            "sql.scratch": {"type": "native_sql", "driver": "ephemeral"},
        }}),
        catalog=adapters,
    )

    try:
        primary = runtime.require_port("sql.primary", SqlTransactionPort)
        scratch = runtime.require_port("sql.scratch", SqlTransactionPort)

        with primary.transaction() as tx:
            tx.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            tx.execute("INSERT INTO users (id, name) VALUES (?, ?)", (1, "Denis"))
            print("primary:", tx.fetch_one("SELECT id, name FROM users WHERE id = ?", (1,)))

        with scratch.transaction() as tx:
            result = tx.fetch_one("SELECT count(*) AS count FROM sqlite_master WHERE name = ?", ("users",))
            assert result == {"count": 0}, "the resources must have independent connections"
            print("scratch has primary's users table:", bool(result["count"]))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
