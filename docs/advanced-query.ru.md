# Muscles SQL: расширенные запросы

## QuerySpec

`QuerySpec` — машинно-читаемый контракт для сложных SQL-запросов:

- операторы фильтрации: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `like`, `is_null`;
- joins через `JoinClause`;
- projection (`select_columns`);
- сортировка/пагинация;
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

## Методы Repository v2

- `query(spec)` -> список строк
- `first(spec)` -> первая строка или `None`
- `scalar(spec)` -> scalar-значение
- `aggregate(spec)` -> агрегированные строки
- `bulk_create(rows)`, `bulk_update(pk_field, rows)`, `upsert(row, conflict_fields)`

## Транзакции v2

`UnitOfWork` поддерживает:

- `begin_nested()` для savepoint;
- `with_retry(fn, max_attempts, retry_on=...)` для транзиентных ошибок.
