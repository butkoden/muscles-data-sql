import json
import os

import pytest
from click.testing import CliRunner

from muscles.data.sql.cli import main
from muscles.data.sql.config import SqlConnectionConfig
from muscles.data.sql.connections import SqlConnectionRegistry, UnknownSqlConnection


def test_sql_connection_config_redacts_passwords():
    config = SqlConnectionConfig(
        name="postgres.main",
        url="postgresql://user:secret@localhost/app",
        role="primary",
        metadata={"owner": "app"},
    )

    assert config.safe_url != config.url
    assert "secret" not in config.safe_url
    assert "***" in config.safe_url
    assert config.to_database_config().url == config.url


def test_sql_connection_registry_resolves_databases_lazily_by_name():
    registry = SqlConnectionRegistry(
        [
            SqlConnectionConfig(name="default", url="sqlite:///:memory:"),
            SqlConnectionConfig(name="analytics", url="sqlite:///:memory:", role="read"),
        ]
    )

    assert registry.names() == ["analytics", "default"]
    assert registry.database("default") is registry.database("default")
    assert registry.database("analytics") is not registry.database("default")
    assert registry.config("analytics").role == "read"

    with pytest.raises(UnknownSqlConnection, match="missing"):
        registry.database("missing")


def test_sql_connection_registry_inspect_includes_safe_connection_contract():
    registry = SqlConnectionRegistry([SqlConnectionConfig(name="default", url="sqlite:///:memory:")])

    report = registry.inspect("default")

    assert report["status"] == "ok"
    assert report["dialect"] == "sqlite"
    assert report["connection"]["name"] == "default"
    assert report["connection"]["url"] == "sqlite:///:memory:"


def test_native_inspect_reports_tables_columns_indexes_and_primary_keys():
    registry = SqlConnectionRegistry([SqlConnectionConfig(name="default", url="sqlite:///:memory:")])
    with registry.database().transaction() as tx:
        tx.execute('CREATE TABLE "odd table" (id INTEGER PRIMARY KEY, name TEXT)')
        tx.execute('CREATE INDEX idx_name ON "odd table" (name)')
    assert registry.inspect()["tables"] == [{
        "name": "odd table", "columns": ["id", "name"], "indexes": ["idx_name"], "pk": ["id"]
    }]


@pytest.mark.skipif(not os.getenv("MUSCLES_SQL_TEST_POSTGRES"), reason="isolated PostgreSQL not configured")
def test_native_postgres_inspect():
    registry = SqlConnectionRegistry([SqlConnectionConfig(name="default", url=os.environ["MUSCLES_SQL_TEST_POSTGRES"])])
    try:
        report = registry.inspect()
        assert report["status"] == "ok"
        assert report["dialect"] == "postgresql"
        assert isinstance(report["tables"], list)
    finally:
        registry.database().close()


def test_cli_inspect_named_connection_from_config(tmp_path):
    config_path = tmp_path / "sql-connections.json"
    config_path.write_text(
        json.dumps(
            {
                "connections": {
                    "default": "sqlite:///:memory:",
                    "analytics": {"url": "sqlite:///:memory:", "role": "read"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["inspect", "--config", str(config_path), "--connection", "analytics"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["connection"]["name"] == "analytics"
    assert payload["connection"]["role"] == "read"


def test_cli_doctor_all_named_connections_from_config(tmp_path):
    config_path = tmp_path / "sql-connections.json"
    config_path.write_text(
        json.dumps({"connections": {"default": "sqlite:///:memory:", "analytics": "sqlite:///:memory:"}}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["doctor", "--config", str(config_path), "--all"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert set(payload["connections"]) == {"default", "analytics"}
    assert payload["connections"]["analytics"]["connection"]["name"] == "analytics"
