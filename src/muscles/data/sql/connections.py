from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import DatabaseConfig, SqlConnectionConfig
from .native import SqlDatabase
from urllib.parse import unquote, urlsplit
from time import perf_counter


class UnknownSqlConnection(KeyError):
    def __init__(self, name: str):
        super().__init__(f"Unknown SQL connection: {name}")
        self.name = name


class SqlConnectionRegistry:
    def __init__(self, configs: Iterable[SqlConnectionConfig] | None = None):
        self._configs: dict[str, SqlConnectionConfig] = {}
        self._databases: dict[str, SqlDatabase] = {}
        for config in configs or ():
            self.register(config)

    @classmethod
    def from_database_config(cls, config: DatabaseConfig, *, name: str = "default") -> "SqlConnectionRegistry":
        return cls(
            [
                SqlConnectionConfig(
                    name=name,
                    url=config.url,
                    echo=config.echo,
                    pool_size=config.pool_size,
                    max_overflow=config.max_overflow,
                    future=config.future,
                )
            ]
        )

    def register(self, config: SqlConnectionConfig) -> None:
        self._configs[config.name] = config
        old = self._databases.pop(config.name, None)
        if old is not None:
            old.close()

    def names(self) -> list[str]:
        return sorted(self._configs)

    def config(self, name: str = "default") -> SqlConnectionConfig:
        try:
            return self._configs[name]
        except KeyError as exc:
            raise UnknownSqlConnection(name) from exc

    def database(self, name: str = "default") -> SqlDatabase:
        if name not in self._databases:
            url = self.config(name).url
            if url.startswith("sqlite:///"):
                path = unquote(url[len("sqlite:///"):].split("?", 1)[0])
                self._databases[name] = SqlDatabase.sqlite(path)
            elif urlsplit(url).scheme in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
                self._databases[name] = SqlDatabase.postgres(url)
            else:
                raise ValueError("Unsupported SQL connection URL")
        return self._databases[name]

    def inspect(self, name: str = "default") -> dict[str, Any]:
        config = self.config(name)
        dialect = "sqlite" if config.url.startswith("sqlite:///") else "postgresql"
        started = perf_counter()
        with self.database(name).transaction() as transaction:
            transaction.fetch_one("SELECT 1 AS value")
            if dialect == "sqlite":
                names = transaction.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
                tables = []
                for row in names:
                    table = row["name"]
                    quoted = '"' + table.replace('"', '""') + '"'
                    columns = transaction.fetch_all(f"PRAGMA table_info({quoted})")
                    indexes = transaction.fetch_all(f"PRAGMA index_list({quoted})")
                    tables.append({"name": table, "columns": [item["name"] for item in columns], "indexes": [item["name"] for item in indexes], "pk": [item["name"] for item in sorted(columns, key=lambda item: item["pk"]) if item["pk"]]})
            else:
                names = transaction.fetch_all("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' ORDER BY table_name")
                tables = []
                for row in names:
                    table = row["table_name"]
                    columns = transaction.fetch_all("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position", (table,))
                    indexes = transaction.fetch_all("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename = %s ORDER BY indexname", (table,))
                    primary = transaction.fetch_all("SELECT a.attname AS name FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum WHERE i.indisprimary AND n.nspname = current_schema() AND t.relname = %s ORDER BY k.ord", (table,))
                    tables.append({"name": table, "columns": [item["column_name"] for item in columns], "indexes": [item["indexname"] for item in indexes], "pk": [item["name"] for item in primary]})
        report = {"status": "ok", "dialect": dialect, "driver": "sqlite3" if dialect == "sqlite" else "psycopg", "latency_ms": round((perf_counter() - started) * 1000, 3), "tables": tables}
        report["connection"] = config.to_contract()
        return report

    def inspect_all(self) -> dict[str, Any]:
        reports = {name: self.inspect(name) for name in self.names()}
        status = "ok" if all(report.get("status") == "ok" for report in reports.values()) else "error"
        return {"status": status, "connections": reports}


def connection_config_from_mapping(name: str, payload: str | Mapping[str, Any]) -> SqlConnectionConfig:
    if isinstance(payload, str):
        return SqlConnectionConfig(name=name, url=payload)
    return SqlConnectionConfig(
        name=str(payload.get("name", name)),
        url=str(payload["url"]),
        echo=bool(payload.get("echo", False)),
        pool_size=int(payload.get("pool_size", 5)),
        max_overflow=int(payload.get("max_overflow", 10)),
        future=bool(payload.get("future", True)),
        role=payload.get("role"),
        metadata=dict(payload.get("metadata") or {}),
    )


def connection_registry_from_mapping(payload: Mapping[str, Any]) -> SqlConnectionRegistry:
    raw_connections = payload.get("connections", payload)
    if not isinstance(raw_connections, Mapping):
        raise ValueError("SQL connection config must contain a mapping of connections.")
    configs = [connection_config_from_mapping(str(name), value) for name, value in raw_connections.items()]
    return SqlConnectionRegistry(configs)


def load_connection_registry(path: str | Path) -> SqlConnectionRegistry:
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("SQL connection config must be a JSON object.")
    return connection_registry_from_mapping(payload)
