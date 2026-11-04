from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FilterClause:
    field: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class JoinClause:
    table: Any
    on: Any
    is_outer: bool = False


@dataclass(frozen=True)
class QuerySpec:
    filters: list[FilterClause] = field(default_factory=list)
    or_filters: list[FilterClause] = field(default_factory=list)
    joins: list[JoinClause] = field(default_factory=list)
    select_columns: list[Any] = field(default_factory=list)
    order_by: list[Any] = field(default_factory=list)
    group_by: list[Any] = field(default_factory=list)
    having: list[Any] = field(default_factory=list)
    limit: int | None = 100
    offset: int = 0
    distinct: bool = False

