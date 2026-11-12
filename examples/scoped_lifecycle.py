"""Runnable example of scoped objects and transactional lifecycle events."""
from muscles.data.catalog import DataAdapterCatalog
from muscles.data.config import DataConfig
from muscles.data.policies import (
    DataOperationContext,
    DataOperationKind,
    DataPolicyDecision,
    DataPredicate,
    DataPredicateOperator,
    DataRelatedExists,
)
from muscles.data.ports import SqlTransactionPort
from muscles.data.runtime import DataRuntime
from muscles.data.sql import native_repository_transaction
from muscles.data.sql.data_adapter import NativeSqlFactory


class ScopePolicy:
    def evaluate(self, operation):
        if operation.object_type != "demo:document":
            return DataPolicyDecision()
        scope = operation.context.attributes.get("scope_id")
        if not operation.context.subject_uid or not scope:
            return DataPolicyDecision(allowed=False, reason="Authenticated scope required")
        if operation.kind is DataOperationKind.UPSERT:
            return DataPolicyDecision(allowed=False, reason="Use create or update")
        if operation.kind is DataOperationKind.CREATE:
            return DataPolicyDecision()
        return DataPolicyDecision(predicates=(DataPredicate(
            "uid", DataPredicateOperator.RELATED_EXISTS,
            DataRelatedExists("scope_links", "object_uid", {
                "scope_id": scope, "object_type": "demo:document",
            }),
        ),))


def maintain_links(transaction, event):
    if event.object_type != "demo:document":
        return
    if event.action == "created":
        transaction.execute(
            "INSERT INTO scope_links (scope_id, object_type, object_uid) VALUES (?, ?, ?)",
            (event.context.attributes["scope_id"], event.object_type, event.object_uid),
        )
    elif event.action == "deleted":
        transaction.execute(
            "DELETE FROM scope_links WHERE object_type = ? AND object_uid = ?",
            (event.object_type, event.object_uid),
        )


def reject_example(transaction, event):
    if event.action == "created" and event.object_uid == "broken":
        raise RuntimeError("Example handler failed")


def main():
    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    config = DataConfig.from_raw({"resources": {
        "database": {"type": "native_sql", "driver": "sqlite", "path": ":memory:"},
    }})
    runtime = DataRuntime(config=config, catalog=catalog)
    try:
        sql = runtime.connect("database", SqlTransactionPort)
        with sql.transaction() as tx:
            tx.execute("CREATE TABLE documents (uid TEXT PRIMARY KEY, title TEXT NOT NULL)")
            tx.execute("""CREATE TABLE scope_links (
                scope_id TEXT NOT NULL, object_type TEXT NOT NULL, object_uid TEXT NOT NULL,
                PRIMARY KEY (scope_id, object_type, object_uid))""")

        runtime.register_object_type("documents", "demo:document")
        runtime.policies.register(ScopePolicy())
        runtime.object_lifecycle.register(maintain_links)
        runtime.object_lifecycle.register(reject_example)

        first = DataOperationContext(subject_type="user", subject_uid="user-1", attributes={"scope_id": "one"})
        second = DataOperationContext(subject_type="user", subject_uid="user-1", attributes={"scope_id": "two"})
        with native_repository_transaction(runtime, "database", "documents", context=first, placeholder="?") as repo:
            repo.create({"uid": "doc-1", "title": "Example"})
            assert repo.get("doc-1")["title"] == "Example"
        with native_repository_transaction(runtime, "database", "documents", context=second, placeholder="?") as repo:
            assert repo.get("doc-1") is None
            assert repo.update("doc-1", {"title": "Not allowed"}) == 0

        try:
            with native_repository_transaction(runtime, "database", "documents", context=first, placeholder="?") as repo:
                repo.create({"uid": "broken", "title": "Rolled back"})
        except RuntimeError as exc:
            assert str(exc) == "Example handler failed"
        else:
            raise AssertionError("The failing handler should abort the transaction")
        with sql.transaction() as tx:
            assert tx.fetch_one("SELECT uid FROM documents WHERE uid = ?", ("broken",)) is None
            assert tx.fetch_one("SELECT object_uid FROM scope_links WHERE object_uid = ?", ("broken",)) is None

        with native_repository_transaction(runtime, "database", "documents", context=first, placeholder="?") as repo:
            assert repo.delete("doc-1") == 1
        with sql.transaction() as tx:
            assert tx.fetch_one("SELECT object_uid FROM scope_links WHERE object_uid = ?", ("doc-1",)) is None
        print("Scoped access and transactional lifecycle: OK")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
