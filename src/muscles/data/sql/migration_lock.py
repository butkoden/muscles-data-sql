"""Optional SQL-backed coordination lock for the migration registry."""
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit


class SqlMigrationExecutionLock:
    """All runners must use the same database URL and this lock implementation."""

    def __init__(self, url: str):
        self.url = url

    @contextmanager
    def __call__(self):
        url = urlsplit(self.url)
        dialect = url.scheme.split("+", 1)[0]
        if dialect == "sqlite":
            import fcntl

            database = unquote(url.path[1:]) if url.path.startswith("/") else ""
            if not database or database == ":memory:" or database.startswith("file:"):
                raise ValueError("SQLite migration coordinator requires a shared file, not memory or a URI")
            lock_path = Path(f"{Path(database).resolve()}.migrations.lock")
            with lock_path.open("a+b") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("Migration execution is locked") from exc
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif dialect == "postgresql":
            import psycopg

            dsn = self.url.replace(url.scheme + "://", "postgresql://", 1)
            with psycopg.connect(dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", (778212474,))
                    acquired = cursor.fetchone()[0]
                    if not acquired:
                        raise RuntimeError("Migration execution is locked")
                    try:
                        yield
                    finally:
                        cursor.execute("SELECT pg_advisory_unlock(%s)", (778212474,))
        else:
            raise ValueError("Migration coordinator supports only SQLite and PostgreSQL")
