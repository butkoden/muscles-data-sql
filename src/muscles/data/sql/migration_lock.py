"""Optional SQL-backed coordination lock for the migration registry."""
from contextlib import contextmanager
from pathlib import Path


class SqlMigrationExecutionLock:
    """All runners must use the same database URL and this lock implementation."""

    def __init__(self, url: str):
        self.url = url

    @contextmanager
    def __call__(self):
        from sqlalchemy.engine import make_url

        sqlalchemy_url = self.url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
        sqlalchemy_url = sqlalchemy_url.replace("postgresql://", "postgresql+psycopg://", 1)
        url = make_url(sqlalchemy_url)
        dialect = url.get_backend_name()
        if dialect == "sqlite":
            import fcntl

            database = url.database
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
            from sqlalchemy import create_engine, text
            from sqlalchemy.pool import NullPool

            engine = create_engine(url, poolclass=NullPool)
            try:
                with engine.connect() as connection:
                    acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": 778212474}).scalar_one()
                    if not acquired:
                        raise RuntimeError("Migration execution is locked")
                    try:
                        yield
                    finally:
                        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": 778212474})
            finally:
                engine.dispose()
        else:
            raise ValueError("Migration coordinator supports only SQLite and PostgreSQL")
