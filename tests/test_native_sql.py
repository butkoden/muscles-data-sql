import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from muscles.data.sql import SqlDatabase


def test_sqlite_raw_queries_and_transaction_rollback(tmp_path):
    db = SqlDatabase.sqlite(tmp_path / "test.db")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        tx.execute("INSERT INTO users VALUES (?, ?)", (1, "Denis"))
    with pytest.raises(ValueError):
        with db.transaction() as tx:
            tx.execute("INSERT INTO users VALUES (?, ?)", (2, "Rollback"))
            raise ValueError("abort")
    with db.transaction() as tx:
        assert tx.fetch_all("SELECT id, name FROM users ORDER BY id") == [{"id": 1, "name": "Denis"}]
        assert tx.fetch_one("SELECT name FROM users WHERE id = ?", (99,)) is None


def test_sqlite_memory_database_is_shared_between_transactions():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE sample (value INTEGER)")
        tx.execute("INSERT INTO sample VALUES (?)", (42,))
    with db.transaction() as tx:
        assert tx.fetch_one("SELECT value FROM sample") == {"value": 42}
    db.close()


def test_sqlite_ddl_is_rolled_back_on_error():
    db = SqlDatabase.sqlite(":memory:")
    with pytest.raises(ValueError):
        with db.transaction() as tx:
            tx.execute("CREATE TABLE transient (value INTEGER)")
            raise ValueError("abort")
    with db.transaction() as tx:
        assert tx.fetch_one("SELECT name FROM sqlite_master WHERE name = ?", ("transient",)) is None
    db.close()


def test_transaction_cannot_be_used_after_context_exit():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        assert tx.fetch_one("SELECT 1 AS value") == {"value": 1}
    with pytest.raises(RuntimeError, match="transaction"):
        tx.fetch_one("SELECT 1")
    db.close()


def test_duplicate_column_names_are_rejected():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        with pytest.raises(ValueError, match="duplicate"):
            tx.fetch_all("SELECT 1 AS id, 2 AS id")
    db.close()


def test_sqlite_allows_sequential_transactions_on_different_threads():
    db = SqlDatabase.sqlite(":memory:")
    try:
        with db.transaction() as tx:
            tx.execute("CREATE TABLE example (value INTEGER)")
            tx.execute("INSERT INTO example VALUES (42)")

        def read_value():
            with db.transaction() as tx:
                return tx.fetch_one("SELECT value FROM example")

        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(read_value).result() == {"value": 42}
    finally:
        db.close()


def test_sqlite_serializes_overlapping_transactions_across_threads():
    db = SqlDatabase.sqlite(":memory:")
    try:
        started = Event()

        def start_transaction():
            started.set()
            with db.transaction() as tx:
                return tx.fetch_one("SELECT 1 AS value")

        with ThreadPoolExecutor(max_workers=1) as executor:
            with db.transaction():
                future = executor.submit(start_transaction)
                assert started.wait(timeout=5)
                assert not future.done()
            assert future.result(timeout=5) == {"value": 1}

        with db.transaction():
            with pytest.raises(RuntimeError, match="Nested SQL transactions"):
                with db.transaction():
                    pass
    finally:
        db.close()


def test_active_transaction_cannot_be_used_from_another_thread():
    db = SqlDatabase.sqlite(":memory:")
    try:
        with db.transaction() as tx, ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(RuntimeError, match="different thread"):
                executor.submit(tx.fetch_one, "SELECT 1").result()
            assert tx.fetch_one("SELECT 1 AS value") == {"value": 1}
    finally:
        db.close()


def test_data_runtime_can_use_native_sqlite(tmp_path):
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.local": {"type": "native_sql", "driver": "sqlite", "path": str(tmp_path / "test.db")}
    }}), catalog=catalog)
    try:
        sql = runtime.require_port("sql.local", SqlTransactionPort)
        with sql.transaction() as tx:
            assert tx.fetch_one("SELECT 42 AS value") == {"value": 42}
        assert runtime.doctor()["checks"][0]["status"] == "ok"
    finally:
        runtime.close()


def test_data_runtime_rejects_incomplete_native_configuration():
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.errors import DataConfigurationError
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.local": {"type": "native_sql", "driver": "sqlite"}
    }}), catalog=catalog)
    try:
        sql = runtime.require_port("sql.local", SqlTransactionPort)
        with pytest.raises(DataConfigurationError):
            with sql.transaction():
                pass
    finally:
        runtime.close()


def test_data_resource_accepts_a_project_registered_driver():
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    opened = []

    def open_project_database(options):
        opened.append(options["database"])
        return SqlDatabase.sqlite(":memory:")

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory(drivers={"project_db": open_project_database}))
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.custom": {"type": "native_sql", "driver": "project_db", "database": "demo"}
    }}), catalog=catalog)
    try:
        sql = runtime.require_port("sql.custom", SqlTransactionPort)
        assert opened == []
        with sql.transaction() as tx:
            assert tx.fetch_one("SELECT 1 AS value") == {"value": 1}
        assert opened == ["demo"]
    finally:
        runtime.close()


def test_data_resource_rejects_unknown_driver():
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.errors import DataConfigurationError
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.custom": {"type": "native_sql", "driver": "missing"}
    }}), catalog=catalog)
    try:
        with pytest.raises(DataConfigurationError, match="driver"):
            runtime.require_port("sql.custom", SqlTransactionPort).transaction()
    finally:
        runtime.close()


def test_data_resource_uses_driver_specific_health_query():
    import sqlite3

    from muscles.data.config import DataConfig
    from muscles.data.sql.data_adapter import NativeSqlFactory

    executed = []

    def connect(options):
        connection = sqlite3.connect(":memory:")
        connection.set_trace_callback(executed.append)
        return SqlDatabase(connection, health_query="SELECT 42 AS status")

    factory = NativeSqlFactory(drivers={"custom": connect})
    config = DataConfig.from_raw({"resources": {
        "sql.custom": {"type": "native_sql", "driver": "custom"}
    }}).resources["sql.custom"]
    adapter = factory.create(config)
    try:
        assert adapter.health().status == "ok"
        assert "SELECT 42 AS status" in executed
    finally:
        adapter.close()


def test_data_resource_cannot_open_database_after_runtime_close():
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.local": {"type": "native_sql", "driver": "sqlite", "path": ":memory:"}
    }}), catalog=catalog)
    sql = runtime.require_port("sql.local", SqlTransactionPort)
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        with sql.transaction():
            pass


def test_model_mapper_uses_explicit_field_mapping():
    from muscles import Column, Integer, Model, String
    from muscles.data.sql import ModelMapper

    class Person(Model):
        id = Column(Integer)
        name = Column(String)

    mapper = ModelMapper(Person, {"id": "person_id", "name": "display_name"})
    person = mapper.from_record({"person_id": 1, "display_name": "Denis", "ignored": True})
    assert person.id == 1 and person.name == "Denis"
    assert mapper.to_record(person) == {"person_id": 1, "display_name": "Denis"}
    assert mapper.from_records([{"person_id": 1, "display_name": "Denis"}])[0].name == "Denis"
    with pytest.raises(KeyError):
        mapper.from_record({"person_id": 1})
    invalid_mapper = ModelMapper(Person, {"naem": "display_name"})
    with pytest.raises(ValueError, match="naem"):
        invalid_mapper.from_record({"display_name": "Denis"})


@pytest.mark.skipif(not os.getenv("MUSCLES_SQL_TEST_POSTGRES"), reason="isolated PostgreSQL not configured")
def test_postgres_transactions_and_native_parameters():
    db = SqlDatabase.postgres(os.environ["MUSCLES_SQL_TEST_POSTGRES"])
    try:
        with db.transaction() as tx:
            tx.execute("CREATE TEMP TABLE muscles_data_sql_test (id INTEGER, name TEXT)")
            tx.execute("INSERT INTO muscles_data_sql_test VALUES (%s, %s)", (1, "Denis"))
        with pytest.raises(ValueError):
            with db.transaction() as tx:
                tx.execute("INSERT INTO muscles_data_sql_test VALUES (%s, %s)", (2, "Rollback"))
                raise ValueError("abort")
        with db.transaction() as tx:
            assert tx.fetch_all("SELECT id, name FROM muscles_data_sql_test WHERE id >= %s", (1,)) == [
                {"id": 1, "name": "Denis"}
            ]
            assert tx.fetch_one("SELECT '50%' AS ratio") == {"ratio": "50%"}
    finally:
        db.close()


@pytest.mark.skipif(not os.getenv("MUSCLES_SQL_TEST_POSTGRES"), reason="isolated PostgreSQL not configured")
def test_postgres_driver_health():
    from muscles.data.config import DataConfig
    from muscles.data.sql.data_adapter import NativeSqlFactory

    config = DataConfig.from_raw({"resources": {"sql.pg": {
        "type": "native_sql", "driver": "postgres", "url_env": "MUSCLES_SQL_TEST_POSTGRES"
    }}}).resources["sql.pg"]
    adapter = NativeSqlFactory().create(config)
    try:
        assert adapter.health().status == "ok"
    finally:
        adapter.close()
