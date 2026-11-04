# Data operation policies

`DataRuntime.policies` holds the shared policy pipeline. Pass the request's
context explicitly to `SqlRepository` together with the pipeline:

```python
from muscles.data import DataOperationContext
from muscles.data.sql import SqlRepository

repo = SqlRepository(
    session,
    documents,
    policies=data_runtime.policies,
    context=DataOperationContext(subject_uid=user_id, attributes={"workspace_uid": workspace_id}),
)
```

Policies run in order; denial or an invalid decision stops the operation.
Read constraints are added before ordering and limits. Update and delete
constraints are part of the SQL `WHERE`; created and changed values are checked
before writes. Batch operations with policies use the guarded path and a
savepoint. `upsert` requires every field of an existing primary or unique key.

`repo.explain()` reports operation, target, field names and operators without
predicate values. It evaluates policies but does not execute SQL. Its result is
specific to the supplied context and filters; it does not validate a future
operation with different arguments.

Policies apply only through `SqlRepository`. Direct SQL, including
`SqlDatabase` and direct SQLAlchemy session calls, bypasses them. Repository
constraints protect its primary table; JOINed tables do not automatically get
their own policies. Database-level isolation requires a separate database
mechanism.

Some SQL operators cannot be safely checked against write values: for example,
`LIKE` on a changed field is denied. Policy-protected `upsert` reads and then
writes, so concurrent operations can produce a uniqueness error that the
application must handle in its transaction. Install the SQLAlchemy repository
and shared contracts using `muscles-data-sql[legacy]`; the lightweight SQL API can
be used independently.
