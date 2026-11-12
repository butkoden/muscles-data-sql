# SQL: data policies and transactional events

`muscles-data-sql` supplies a native DB-API adapter for SQLite and PostgreSQL. Use `NativePolicyRepository` or its `DataRuntime` factories for protected operations. SQLAlchemy is not required. The policy and lifecycle contracts, and the rationale for loosely coupled links, are described in `muscles-data/docs/data-policies.en.md`.

## Run the complete example

[examples/scoped_lifecycle.py](../examples/scoped_lifecycle.py) creates two scopes, a document, and a link table, then checks isolation, rollback after a handler error, and link cleanup. It uses local in-memory SQLite; it does not install application packages or access a working database.

From the `muscles-data-sql` directory, in an environment with `muscles-data` and `muscles-data-sql` installed:

```bash
python examples/scoped_lifecycle.py
# Scoped access and transactional lifecycle: OK
```

A real application must authenticate the user and verify scope membership **before** creating the context. This example deliberately does not implement HTTP authentication, roles, or a link-management interface.

## Application setup

Configure the shared runtime once at application startup, not once per request. In a Muscles application use its existing `app.resource`; the separate runtime below is for a standalone script:

```python
from muscles.data.catalog import DataAdapterCatalog
from muscles.data.config import DataConfig
from muscles.data.runtime import DataRuntime
from muscles.data.sql.data_adapter import NativeSqlFactory

catalog = DataAdapterCatalog.with_defaults()
catalog.register(NativeSqlFactory())
config = DataConfig.from_raw({"resources": {
    "database": {"type": "native_sql", "driver": "sqlite", "path": ":memory:"},
}})
runtime = DataRuntime(config=config, catalog=catalog)
runtime.register_object_type("documents", "demo:document", owner_field="owner_uid")
# At application shutdown: runtime.close()
```

For PostgreSQL configure a `native_sql` resource with the `postgres` driver and provide its URL through application configuration; do not place credentials in examples or source control. Schema creation and verification are separate migration tasks. `owner_field` declares metadata; it is not an automatic SQL ownership check.

## Policy, context, and CRUD

Register `ScopePolicy` and the `maintain_links` handler from the complete example in `runtime.policies` and `runtime.object_lifecycle`. The factory resolves the registered table type and supplies the runtime's policies and handlers:

```python
from muscles.data.policies import DataOperationContext
from muscles.data.sql import native_repository_transaction

# Build context from a verified session and selected scope.
context = DataOperationContext(
    subject_type="user", subject_uid="user-1", attributes={"scope_id": "one"},
)
with native_repository_transaction(
    runtime, "database", "documents", context=context, placeholder="?",
) as repo:
    repo.create({"uid": "doc-1", "title": "Example"})
    document = repo.get("doc-1")
    rows = repo.list(limit=20, offset=0, order_by="uid")
    count = repo.count()
    changed = repo.update("doc-1", {"title": "New title"})
    deleted = repo.delete("doc-1")
```

`?` is the SQLite placeholder; use `%s` for PostgreSQL. This snippet assumes registered policies, handlers, and tables. `get` returns `None` when a row is missing or hidden by policy; `update` and `delete` return the affected row count, including `0`. An explicit policy denial raises `DataPolicyDeniedError`; do not convert every database failure into “not found.”

`list` and `count` apply restrictions before pagination and counting. Other operations include `exists`, `aggregate`, `aggregate_grouped`, `list_joined`, `bulk_create`, `bulk_update`, and `upsert`. Bulk operations and upsert use per-record checks and savepoints; the enclosing transaction commits the overall result. A savepoint is not an independent commit. `upsert` checks both upsert predicates and the create/update branch; predicates that cannot be validated on creation are rejected. The example policy explicitly denies upsert.

## Related tables and reads

`DataRelatedExists` can restrict a document through a link table without coupling the document package to the package managing scopes. See `ScopePolicy` in the runnable example. The handler creates the link after insertion, so `CREATE` must not require that link to already exist.

Use `list_joined` for a join: it applies policy separately to the target and the related table. If the target has a registered object type, provide `related_object_type` for the related object. Requested `related_fields` are returned with a `related__` prefix:

```python
rows = repo.list_joined(
    "categories", target_field="category_uid", related_field="uid",
    related_object_type="demo:category", related_fields=("name",),
    limit=20, offset=0, outer=True,
)
```

This is a separate example: it requires a `categories` table, a `category_uid` field, and policies for both object types. Do not pass unchecked user input as table or field names. Only operations and operators implemented by the adapter are supported; unsupported conditions fail closed. For many-to-many links, ensure the application API checks access to each linked object.

## Existing transactions and raw SQL

If a transaction is already open, use `native_repository_from_runtime(runtime, tx, "documents", context=context, placeholder="?")`. Multiple operations in it commit or roll back together. The factory uses the runtime's object-type registration; an explicitly conflicting type raises an error.

Direct `tx.execute`, `fetch_one`, and `fetch_all` **do not automatically apply policies or emit events**. Use them for trusted migrations, maintenance, and handlers, not unprotected CRUD. For specialized SQL, `compile_operation_scope` returns a parameterized condition and parameters; the caller must place the condition into the actual query, including restrictions on all related data. Calling the compiler alone protects nothing. `CREATE` is validated separately with `validate_create_predicates`; never substitute user-supplied SQL for predicates. If dispatching an event manually, call `tx.emit_object_event(runtime.object_lifecycle, event)` inside the transaction and provide the same validation and semantics as the repository.

For example, for a trusted custom PostgreSQL query:

```python
from muscles.data.policies import DataOperationKind, DataOperationRequest
from muscles.data.sql.native_policy import compile_operation_scope

operation = DataOperationRequest(
    DataOperationKind.READ, "documents", context, object_type="demo:document",
)
clause, parameters = compile_operation_scope(
    runtime.policies, operation, target_alias="d", placeholder="%s",
)
# "d" is the same fixed alias passed to the compiler.
query = 'SELECT "d".* FROM "documents" AS "d"'
if clause:
    query += f" WHERE {clause}"
rows = tx.fetch_all(query, parameters)
```

This assumes an open `tx` and a registered policy. If adding `JOIN`s, aggregates, or caller filters, apply policies to all affected objects and preserve parameter order. Prefer repository methods: they perform these checks for supported operations.

## Migrating existing data

Policies and event registrations do not retroactively create links for existing objects. The application must migrate and verify them before enforcing visibility restrictions; use transactions, referential checks, and an idempotent backfill. Deleting an object should delete its links in the same transaction. Do not remove source data before verification.

For an audited change to an already applied migration, `compatible_checksums` may declare a tuple of **exact** previous checksums. This supports intentionally compatible installation paths; it is not a way to ignore arbitrary history changes. Other checksums and changes after discovery are rejected. See the [README](../README.md#native-migrations-in-muscles-applications).
