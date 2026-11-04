from typing import Any


class UnitOfWork:
    def __init__(self, session_factory, *, isolation_level: str | None = None):
        self._session_factory = session_factory
        self._isolation_level = isolation_level
        self.session: Any | None = None

    def _require_session(self):
        if self.session is None:
            raise RuntimeError("UnitOfWork requires an active session; use it as a context manager first.")
        return self.session

    def __enter__(self):
        session = self._session_factory()
        if self._isolation_level:
            session.connection(execution_options={"isolation_level": self._isolation_level})
        self.session = session
        return self

    def __exit__(self, exc_type, exc, tb):
        session = self._require_session()
        if exc_type is None:
            session.commit()
        else:
            session.rollback()
        session.close()
        self.session = None

    def begin_nested(self):
        return self._require_session().begin_nested()

    def with_retry(self, fn, *, max_attempts: int = 3, retry_on: tuple[type[Exception], ...] = ()):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        attempt = 0
        while True:
            attempt += 1
            session = self._require_session()
            try:
                return fn(session)
            except Exception as exc:
                if retry_on and isinstance(exc, retry_on) and attempt < max_attempts:
                    session.rollback()
                    continue
                raise
