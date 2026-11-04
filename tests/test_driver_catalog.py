import pytest

from muscles.data.sql import SqlDatabase, SqlDriverCatalog
from muscles.data.sql.drivers import SqlDriverNotFoundError


def test_custom_driver_registration_and_lazy_creation():
    calls = []
    catalog = SqlDriverCatalog.with_defaults()
    catalog.register("custom", lambda options: calls.append(options["name"]) or SqlDatabase.sqlite(":memory:"))
    assert calls == []
    db = catalog.open("custom", {"name": "test"})
    try:
        with db.transaction() as transaction:
            assert transaction.fetch_one("SELECT 1 AS result") == {"result": 1}
        assert calls == ["test"]
    finally:
        db.close()


def test_catalog_validates_registration_and_unknown_drivers():
    catalog = SqlDriverCatalog.with_defaults()
    with pytest.raises(ValueError):
        catalog.register("sqlite", lambda options: None)
    with pytest.raises(ValueError):
        catalog.register("", lambda options: None)
    with pytest.raises(ValueError):
        catalog.register("invalid", None)
    with pytest.raises(SqlDriverNotFoundError):
        catalog.open("missing", {})


def test_adapter_accepts_explicit_driver_catalog():
    from muscles.data.config import DataConfig
    from muscles.data.sql.data_adapter import NativeSqlFactory

    drivers = SqlDriverCatalog()
    drivers.register("custom", lambda options: SqlDatabase.sqlite(":memory:"))
    config = DataConfig.from_raw({"resources": {
        "db": {"type": "native_sql", "driver": "custom"}
    }}).resources["db"]
    adapter = NativeSqlFactory(drivers=drivers).create(config)
    try:
        with adapter.transaction() as transaction:
            assert transaction.fetch_one("SELECT 42 AS result") == {"result": 42}
    finally:
        adapter.close()
