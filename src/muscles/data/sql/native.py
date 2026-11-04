"""Small DB-API based SQL access without an ORM or query builder."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Lock, get_ident
from typing import Any, ContextManager, Iterator


class SqlTransaction:
    def __init__(self, connection: Any):
        self._connection = connection
        self._active = True
        self._owner = get_ident()

    def execute(self, sql: str, parameters: Any = None) -> int:
        with self._cursor() as cursor:
            self._execute(cursor, sql, parameters)
            return cursor.rowcount

    def fetch_all(self, sql: str, parameters: Any = None) -> list[dict[str, Any]]:
        with self._cursor() as cursor:
            self._execute(cursor, sql, parameters)
            names = self._column_names(cursor)
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def fetch_one(self, sql: str, parameters: Any = None) -> dict[str, Any] | None:
        with self._cursor() as cursor:
            self._execute(cursor, sql, parameters)
            row = cursor.fetchone()
            names = self._column_names(cursor)
            return None if row is None else dict(zip(names, row))

    @staticmethod
    def _execute(cursor, sql, parameters):
        if parameters is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, parameters)

    @staticmethod
    def _column_names(cursor):
        names = [column[0] for column in cursor.description]
        if len(names) != len(set(names)):
            raise ValueError("SQL result contains duplicate column names; use aliases")
        return names

    @contextmanager
    def _cursor(self):
        if not self._active:
            raise RuntimeError("SQL transaction is no longer active")
        if self._owner != get_ident():
            raise RuntimeError("SQL transaction belongs to a different thread")
        cursor = self._connection.cursor()
        try:
            yield cursor
        finally:
            cursor.close()


class SqlDatabase:
    """Own one connection; transactions commit on success and roll back on error."""

    def __init__(self, connection: Any, *, begin_sql: str | None = None, health_query: str = "SELECT 1 AS value"):
        self._connection = connection
        self._begin_sql = begin_sql
        self._health_query = health_query
        self._lock = Lock()
        self._transaction_owner: int | None = None
        self._closed = False

    @classmethod
    def sqlite(cls, path: str | Path) -> SqlDatabase:
        import sqlite3

        return cls(sqlite3.connect(str(path), check_same_thread=False), begin_sql="BEGIN")

    @classmethod
    def postgres(cls, dsn: str) -> SqlDatabase:
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError("PostgreSQL requires muscles-data-sql[postgres]") from exc
        normalized_dsn = dsn.replace("postgresql+psycopg2://", "postgresql://", 1).replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        return cls(psycopg.connect(normalized_dsn))

    def transaction(self) -> ContextManager[SqlTransaction]:
        return self._transaction(self._begin_sql)

    @contextmanager
    def _transaction(self, begin_sql: str | None) -> Iterator[SqlTransaction]:
        owner = get_ident()
        if self._transaction_owner == owner:
            raise RuntimeError("Nested SQL transactions are not supported")
        self._lock.acquire()
        self._transaction_owner = owner
        try:
            if self._closed:
                raise RuntimeError("SQL database is closed")
            transaction = SqlTransaction(self._connection)
            try:
                if begin_sql is not None:
                    with transaction._cursor() as cursor:
                        cursor.execute(begin_sql)
                yield transaction
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            finally:
                transaction._active = False
        finally:
            self._transaction_owner = None
            self._lock.release()

    def close(self) -> None:
        if self._transaction_owner == get_ident():
            raise RuntimeError("Cannot close a database during a transaction")
        self._lock.acquire()
        try:
            if not self._closed:
                self._connection.close()
                self._closed = True
        finally:
            self._lock.release()

    def check_health(self) -> None:
        with self.transaction() as transaction:
            transaction.fetch_one(self._health_query)
