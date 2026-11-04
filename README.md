# Muscles Data SQL

Muscles SQL is a data layer package for SQL databases:
- model-to-table mapping
- engine and session management
- repository CRUD and advanced query API (filters/operators/joins/aggregates)
- transactions and Unit of Work (nested/savepoint/retry helpers)
- native SQL migrations through the shared `muscles-data` registry, including
  framework-level discovery and registry construction for installed Muscles packages
- inspect/doctor support with machine-readable diagnostics

## Related Repositories

- [`muscles`](https://github.com/butkoden/muscles) - core schemas, actions, DI and canonical documentation.
- [`muscles-documents`](https://github.com/butkoden/muscles-documents) - document metadata/state can use SQL persistence in applications.
- [`muscles-ai`](https://github.com/butkoden/muscles-ai) - AI/RAG flows can use SQL-backed application state without making SQL generic storage.
- [`muscles-otel`](https://github.com/butkoden/muscles-otel) - observability hooks around SQL-backed flows.
- [`muscles-benchmarks`](https://github.com/butkoden/muscles-benchmarks) - SQL transaction and mapping regression checks.

## Quickstart

This low-level API is useful for standalone scripts and libraries that explicitly
own their database lifetime. Application packages should use the shared
`app.resource` integration below instead of constructing `SqlDatabase` objects.

The lightweight SQL API has no mandatory third-party dependencies. SQLite uses
Python's standard library; PostgreSQL requires the `postgres` extra. The
named-resource integration requires `data`; SQLAlchemy repositories and CLI
require `legacy`. In this checkout install from the library
directory using `pip install -e '.[postgres]'`, `.[data]` or `.[legacy]` as
needed. Publishing the renamed distribution is a separate step.

```python
from muscles.data.sql import SqlDatabase

db = SqlDatabase.sqlite("app.db")
try:
    with db.transaction() as tx:
        tx.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER, name TEXT)")
        tx.execute("INSERT INTO users VALUES (?, ?)", (1, "Denis"))
        users = tx.fetch_all("SELECT id, name FROM users WHERE id = ?", (1,))
finally:
    db.close()
```

`SqlDatabase.postgres(dsn)` uses psycopg and its native parameter syntax. A
database owns one connection and is not a connection pool. Transactions from
different threads are serialized on that connection; nested transactions in the
same thread are rejected. Direct SQL does not enforce data policies;
the caller must ensure access restrictions. Commit occurs on successful exit,
and exceptions roll the transaction back.

## `muscles-data` Resource Integration

The application composition root owns the catalog and `DataRuntime`. Register
`NativeSqlFactory` there once, and load `data.resources` from the central
application configuration:

```python
from muscles.data import DataConfig, DataRuntime
from muscles.data.catalog import DataAdapterCatalog
from muscles.data.sql.data_adapter import NativeSqlFactory

class App:
    def __init__(self):
        # self.config is the application's shared Muscles configuration.
        catalog = DataAdapterCatalog.with_defaults()
        catalog.register(NativeSqlFactory())
        self.resource = DataRuntime(
            config=DataConfig.from_raw({
                "resources": self.config.get("data.resources").value() or {},
            }),
            catalog=catalog,
        )
```

The database resource itself is declared once in the application's central
configuration, for example with `type: native_sql`, `driver: postgres` and
`url_env: DATABASE_URL`. A package uses the existing resource and does not
create another catalog, runtime or connection configuration:

```python
from muscles.data.ports import SqlTransactionPort

sql = app.resource.connect("database", SqlTransactionPort)
with sql.transaction() as tx:
    tx.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY)")
```

The adapter opens its native connection lazily on the first transaction and
closes it with the owning `DataRuntime`. Direct `SqlDatabase` examples above
are for standalone low-level use; application package code should resolve its
port through `app.resource`.

### Native migrations in Muscles applications

The composition root configures migrations once:

```python
from muscles.data.sql import build_native_sql_migration_registry

self.resource.migrations = build_native_sql_migration_registry(
    package_classes,
    database_url,
)
```

The builder creates the shared registry and execution lock, discovers each
installed package's `migrate/000[0-9]_*.py` files, and orders sources using
`Package.dependencies`. Packages need no `migration_sources()` method or manual
revision list. The same `app.resource.migrations` instance is used by HTTP and
CLI integrations; migrations are not applied automatically at application startup.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For local development with the test and legacy dependencies:

```bash
pip install -e ".[dev]"
pytest -q
muscles-data-sql doctor --url sqlite:///./app.db
```

## Named SQL Connections

`muscles-data-sql` can manage multiple SQL connections without becoming a generic
storage registry. The registry is SQL-only: it owns SQL connection configs,
lazy SQLAlchemy `EngineManager` instances, sessions, inspect and doctor reports.

```python
from muscles.data.sql import SqlConnectionConfig, SqlConnectionRegistry

registry = SqlConnectionRegistry(
    [
        SqlConnectionConfig(name="default", url="sqlite:///./app.db"),
        SqlConnectionConfig(name="analytics", url="sqlite:///./analytics.db", role="read"),
    ]
)

session = registry.session("analytics")
report = registry.inspect("analytics")
```

CLI diagnostics can read a JSON config:

```json
{
  "connections": {
    "default": "sqlite:///./app.db",
    "analytics": {"url": "sqlite:///./analytics.db", "role": "read"}
  }
}
```

```bash
muscles-data-sql inspect --config sql-connections.json --connection analytics
muscles-data-sql doctor --config sql-connections.json --all
```

Diagnostic output uses safe URLs and does not print passwords from DSNs.

## Advanced Query Example

```python
from sqlalchemy import func
from muscles.data.sql import FilterClause, JoinClause, QuerySpec, SqlRepository

spec = QuerySpec(
    filters=[FilterClause("status", "eq", "active")],
    joins=[JoinClause(table=orders, on=users.c.id == orders.c.user_id)],
    select_columns=[users.c.id, func.count(orders.c.id).label("orders_total")],
    group_by=[users.c.id],
    order_by=[users.c.id.asc()],
)
rows = SqlRepository(session, users).aggregate(spec)
```

## Data operation policies

`SqlRepository` accepts the policy pipeline and a request-scoped context from
`muscles-data`. Policy predicates are combined with caller filters using AND;
they apply to reads, counts, updates, deletes, creates, bulk creates and upserts.
For update/delete, predicates are part of the SQL statement. Create and changed
values are checked before execution. Under an active policy pipeline, upsert
uses the constrained repository path instead of a native conflict statement.

```python
from muscles.data import DataOperationContext
from muscles.data.sql import SqlRepository

repo = SqlRepository(
    session,
    documents,
    policies=data_runtime.policies,
    context=DataOperationContext(
        subject_type="account",
        subject_uid=account_uid,
        attributes={"workspace_uid": workspace_uid},
    ),
)
rows = repo.find({"owner_uid": account_uid}, limit=50)
```

The policy contract is backend-neutral; each adapter is responsible for
translating predicates and enforcing them before pagination or mutation. Direct
SQLAlchemy session use is outside this enforcement boundary.

Repository: https://github.com/butkoden/muscles-data-sql
