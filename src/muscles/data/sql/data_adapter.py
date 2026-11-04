"""Optional integration with muscles-data; the SQL core does not import it."""
from __future__ import annotations

from threading import Lock
from typing import Mapping

from muscles.data.errors import DataConfigurationError
from muscles.data.models import DataCapability, HealthResult

from .drivers import SqlDriverCatalog, SqlDriverConfigError, SqlDriverFactory, SqlDriverNotFoundError


class NativeSqlAdapter:
    resource_type = "native_sql"

    def __init__(self, config, drivers: SqlDriverCatalog):
        self.config = config
        self._drivers = drivers
        self._db = None
        self._lock = Lock()
        self._closed = False

    def _database(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("SQL resource is closed")
            if self._db is None:
                options = self.config.resolved_options()
                driver = options.get("driver")
                try:
                    self._db = self._drivers.open(driver, options)
                except (SqlDriverNotFoundError, SqlDriverConfigError) as exc:
                    raise DataConfigurationError(str(exc)) from exc
            return self._db

    def transaction(self):
        return self._database().transaction()

    def inspect(self):
        return {"status": "ok", "details": {"driver": self.config.options.get("driver")}}

    def health(self):
        try:
            self._database().check_health()
        except Exception:
            return HealthResult(status="failed", message="SQL health check failed")
        return HealthResult(status="ok")

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
            self._closed = True


class NativeSqlFactory:
    resource_type = "native_sql"

    def __init__(self, *, drivers: SqlDriverCatalog | Mapping[str, SqlDriverFactory] | None = None):
        if isinstance(drivers, SqlDriverCatalog):
            self._drivers = drivers
        else:
            self._drivers = SqlDriverCatalog.with_defaults()
            for name, factory in (drivers or {}).items():
                self._drivers.register(name, factory)

    def capabilities(self, config):
        return {DataCapability.SQL_TRANSACTION, DataCapability.HEALTHCHECK}

    def create(self, config):
        return NativeSqlAdapter(config, self._drivers)
