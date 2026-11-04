# Muscles SQL Advanced Query Guide

## QuerySpec

Use `QuerySpec` as a machine-readable contract for complex SQL queries:

- filter operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `like`, `is_null`;
- joins via `JoinClause`;
- projection (`select_columns`);
- sorting/pagination;
- group by / having / distinct.

```python
from sqlalchemy import func
from muscles.data.sql import FilterClause, JoinClause, QuerySpec

spec = QuerySpec(
    filters=[FilterClause("name", "like", "D%")],
    joins=[JoinClause(table=bookings, on=users.c.id == bookings.c.user_id)],
    select_columns=[users.c.name, func.count(bookings.c.id).label("total")],
    group_by=[users.c.name],
    order_by=[users.c.name.asc()],
)
```

## Repository v2 methods

- `query(spec)` -> rows
- `first(spec)` -> first row or `None`
- `scalar(spec)` -> scalar result
- `aggregate(spec)` -> aggregate rows
- `bulk_create(rows)`, `bulk_update(pk_field, rows)`, `upsert(row, conflict_fields)`

## Transactions v2

`UnitOfWork` now supports:

- `begin_nested()` for savepoints;
- `with_retry(fn, max_attempts, retry_on=...)` for transient failures.
