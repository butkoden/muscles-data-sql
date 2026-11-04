"""Registration of independent SQL connection factories."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from .native import SqlDatabase

SqlDriverFactory = Callable[[Mapping[str, Any]], SqlDatabase]


class SqlDriverNotFoundError(LookupError):
    """The requested driver has not been registered."""


class SqlDriverConfigError(ValueError):
    """A driver's required configuration is missing."""


def _sqlite_driver(options: Mapping[str, Any]) -> SqlDatabase:
    if not options.get("path"):
        raise SqlDriverConfigError("sqlite driver requires path")
    return SqlDatabase.sqlite(options["path"])


def _postgres_driver(options: Mapping[str, Any]) -> SqlDatabase:
    if not options.get("url"):
        raise SqlDriverConfigError("postgres driver requires url or url_env")
    return SqlDatabase.postgres(options["url"])


class SqlDriverCatalog:
    def __init__(self) -> None:
        self._drivers: dict[str, SqlDriverFactory] = {}

    @classmethod
    def with_defaults(cls) -> SqlDriverCatalog:
        catalog = cls()
        catalog.register("sqlite", _sqlite_driver)
        catalog.register("postgres", _postgres_driver)
        return catalog

    def register(self, name: str, factory: SqlDriverFactory) -> None:
        if not isinstance(name, str) or not name.strip() or not callable(factory):
            raise ValueError("SQL drivers require a nonempty name and callable factory")
        if name in self._drivers:
            raise ValueError(f"SQL driver '{name}' is already registered")
        self._drivers[name] = factory

    def open(self, name: str, options: Mapping[str, Any]) -> SqlDatabase:
        if not isinstance(name, str) or name not in self._drivers:
            raise SqlDriverNotFoundError("SQL driver is not registered")
        return self._drivers[name](options)
