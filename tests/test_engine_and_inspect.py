from muscles.data.sql.config import DatabaseConfig
from muscles.data.sql.engine import EngineManager
from muscles.data.sql.inspect import inspect_sql_layer


def test_engine_manager_and_inspect_sqlite():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    report = inspect_sql_layer(manager)
    assert report["status"] == "ok"
    assert report["dialect"] == "sqlite"
    assert isinstance(report["latency_ms"], float)
    assert isinstance(report["tables"], list)
