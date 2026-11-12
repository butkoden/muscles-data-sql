# Muscles Data SQL

Muscles SQL provides native DB-API transactions, policy-aware repositories, migrations and diagnostics for SQLite and PostgreSQL.

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

SQLite uses Python's standard library. PostgreSQL requires the `postgres` extra; named-resource and policy integration requires the `data` extra. The CLI uses Click. Install with `pip install -e '.[postgres,data]'` as needed.

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
installed package's `migrations/000[0-9]_*.py` files by default, and orders sources using
`Package.dependencies`. Packages need no `migration_sources()` method or manual
revision list. The same `app.resource.migrations` instance is used by HTTP and
CLI integrations; migrations are not applied automatically at application startup.

For an audited compatibility change to an already applied migration, a migration
module may declare `compatible_checksums` as a tuple of exact previously released
checksums. The backend accepts only its current checksum or one explicitly listed
checksum; it still rejects other changes and changes after discovery. Use this only
when the old and new migration paths are intentionally compatible.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For local development with the test dependencies:

```bash
pip install -e ".[dev]"
pytest -q
muscles-data-sql doctor --url sqlite:///./app.db
```

## Named SQL Connections

`muscles-data-sql` can manage multiple SQL connections without becoming a generic
storage registry. The registry is SQL-only: it owns SQL connection configs,
lazy native `SqlDatabase` instances and inspect and doctor reports.

```python
from muscles.data.sql import SqlConnectionConfig, SqlConnectionRegistry

registry = SqlConnectionRegistry(
    [
        SqlConnectionConfig(name="default", url="sqlite:///./app.db"),
        SqlConnectionConfig(name="analytics", url="sqlite:///./analytics.db", role="read"),
    ]
)

database = registry.database("analytics")
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

## Data operation policies

Practical guide: [English](docs/data-policies.en.md) · [Русский](docs/data-policies.ru.md).
The complete [transactional scope example](examples/scoped_lifecycle.py) runs locally on SQLite.

### Native policy repository

`native_repository_transaction` uses a `DataRuntime` SQL resource, its policy
pipeline and its object lifecycle handlers. Pass the authenticated request's
`DataOperationContext` explicitly. Use `%s` for PostgreSQL and `?` for SQLite.

```python
from muscles.data.sql import native_repository_transaction

with native_repository_transaction(
    data_runtime, "database", "documents", context=request_context,
    object_type="example:document", placeholder="%s",
) as repo:
    rows = repo.list(limit=50, order_by="uid")
```

Policies must be registered with the runtime; an empty pipeline does not
restrict access. Object creation and deletion notifications execute inside the
same transaction when an object type is provided. Handler failures abort the
transaction. A `RELATED_EXISTS` predicate can constrain reads and mutations by
a related table; policies for object creation must instead use supported scalar
conditions, because the related row may not yet exist. Unsupported conditions
are rejected. Direct calls to `SqlTransaction.execute()` do not apply policies;
callers must use the policy-aware repository for protected operations.
For structured custom SQL, `compile_operation_scope` evaluates a
`DataOperationRequest` and returns an AND condition and bound parameters. The
caller must insert that condition into the actual read, update or delete query;
calling the compiler alone does not enforce anything. Create operations are
validated through the repository rather than through this SQL fragment helper.

Repository: https://github.com/butkoden/muscles-data-sql
