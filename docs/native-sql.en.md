# Lightweight SQL

`SqlDatabase.sqlite(path)` uses the standard library. Install `muscles-data-sql[postgres]`
for `SqlDatabase.postgres(dsn)`. These APIs do not require SQLAlchemy or
`muscles-data`. Each database owns one connection; close it after use. A
transaction commits on normal exit and rolls back on an exception. Concurrent
transactions from different threads are serialized on that connection. A
nested transaction in the same thread raises an error. Result columns must have
unique names; alias duplicate columns in joins. An active transaction can only
be used on the thread that opened it.

Queries use the driver's native SQL and parameter syntax (`?` for SQLite,
`%s` for psycopg). Bind values separately from SQL. Identifiers cannot be
supplied as parameters. Raw SQL does not apply data policies automatically.

`ModelMapper(ModelClass, {"model_field": "column_name"})` converts explicit
fields using `Model.as_dict()`. `from_records()` maps a sequence of rows.
Missing result columns raise `KeyError` and unknown model fields raise `ValueError`.
It does not validate a model or generate SQL and is not an authorization or
schema-validation boundary.

For the optional `muscles-data` integration, install `muscles-data-sql[data]` (which
requires `muscles-data` 0.2.0 or newer), register `muscles.data.sql.data_adapter.NativeSqlFactory()` in its catalog,
and configure a `native_sql` resource with `driver: sqlite` and `path`, or
`driver: postgres` and `url`/`url_env`. Request `SqlTransactionPort`. Listing
resources does not open a connection; a transaction or doctor does. SQLite
may create the named file on first connection, including during doctor.

Register another driver without changing the adapter:

```python
from muscles.data.sql import SqlDatabase, SqlDriverCatalog
from muscles.data.sql.data_adapter import NativeSqlFactory

def open_custom(options):
    connection = custom_driver.connect(options["url"])
    return SqlDatabase(connection, health_query="SELECT 1 FROM dual")

drivers = SqlDriverCatalog.with_defaults()
drivers.register("custom", open_custom)
catalog.register(NativeSqlFactory(drivers=drivers))
```

The factory receives resolved resource options and returns `SqlDatabase`.
`SqlDriverCatalog()` starts empty; `with_defaults()` registers SQLite and
PostgreSQL. Duplicate names are rejected. Configure the catalog during startup
before using resources concurrently.
See [`examples/native_resources.py`](../examples/native_resources.py) for a
working example with two independent resources.
Set `begin_sql` when the database requires an explicit transaction start.
The driver must provide compatible DB-API connection and cursor methods and
support the required thread usage; test each new backend in an integration
environment. SQL and parameters remain native to that backend. Use
`health_query` to provide a health check compatible with its SQL dialect.

Existing SQLAlchemy-based repositories and migrations require the `legacy`
extra. This dependency change requires a coordinated migration for existing
installations.
