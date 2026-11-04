from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import DatabaseConfig, SqlConnectionConfig
from .engine import EngineManager


class UnknownSqlConnection(KeyError):
    def __init__(self, name: str):
        super().__init__(f"Unknown SQL connection: {name}")
        self.name = name


class SqlConnectionRegistry:
    def __init__(self, configs: Iterable[SqlConnectionConfig] | None = None):
        self._configs: dict[str, SqlConnectionConfig] = {}
        self._managers: dict[str, EngineManager] = {}
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
        self._managers.pop(config.name, None)

    def names(self) -> list[str]:
        return sorted(self._configs)

    def config(self, name: str = "default") -> SqlConnectionConfig:
        try:
            return self._configs[name]
        except KeyError as exc:
            raise UnknownSqlConnection(name) from exc

    def manager(self, name: str = "default") -> EngineManager:
        if name not in self._managers:
            self._managers[name] = EngineManager(self.config(name).to_database_config())
        return self._managers[name]

    def session_factory(self, name: str = "default"):
        return self.manager(name).session_factory

    def session(self, name: str = "default"):
        return self.manager(name).session()

    def inspect(self, name: str = "default") -> dict[str, Any]:
        from muscles.data.sql.inspect import inspect_sql_layer

        config = self.config(name)
        report = inspect_sql_layer(self.manager(name))
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
