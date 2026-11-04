from typing import Any

from .config import DatabaseConfig


class EngineManager:
    def __init__(self, config: DatabaseConfig):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        kwargs: dict[str, Any] = {"echo": config.echo, "future": config.future}
        if not config.url.startswith("sqlite"):
            kwargs["pool_size"] = config.pool_size
            kwargs["max_overflow"] = config.max_overflow
        self.engine = create_engine(config.url, **kwargs)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)

    def session(self):
        return self.session_factory()
