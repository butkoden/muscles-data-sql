from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit


@dataclass(slots=True)
class DatabaseConfig:
    url: str
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    future: bool = True


@dataclass(slots=True)
class SqlConnectionConfig:
    name: str
    url: str
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    future: bool = True
    role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def safe_url(self) -> str:
        parsed = urlsplit(self.url)
        if "@" not in parsed.netloc:
            return self.url
        credentials, host = parsed.netloc.rsplit("@", 1)
        if ":" not in credentials:
            return self.url
        username, _ = credentials.split(":", 1)
        return urlunsplit((parsed.scheme, f"{username}:***@{host}", parsed.path, parsed.query, parsed.fragment))

    def to_database_config(self) -> DatabaseConfig:
        return DatabaseConfig(
            url=self.url,
            echo=self.echo,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            future=self.future,
        )

    def to_contract(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.safe_url,
            "role": self.role,
            "metadata": dict(self.metadata),
        }
