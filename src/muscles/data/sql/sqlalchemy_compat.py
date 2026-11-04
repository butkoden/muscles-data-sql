"""Small compatibility helpers for optional SQLAlchemy integrations."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import TextClause


def text(statement: str) -> TextClause:
    """Create a SQLAlchemy text clause without importing SQLAlchemy eagerly."""
    try:
        from sqlalchemy import text as sqlalchemy_text
    except ImportError as exc:
        raise ImportError(
            "SQLAlchemy text statements require muscles-data-sql[legacy]"
        ) from exc
    return sqlalchemy_text(statement)
