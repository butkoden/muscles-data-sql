import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from muscles.data.sql import SqlDatabase
from muscles.data.object_lifecycle import ObjectLifecycle, ObjectLifecycleEvent
from muscles.data.policies import DataOperationContext, DataOperationKind, DataOperationRequest, DataPredicate, DataPredicateOperator, DataRelatedExists, DataPolicyDecision, DataPolicyPipeline
from muscles.data.errors import DataPolicyDeniedError, DataUnsupportedOperationError
from muscles.data.sql.native_policy import compile_related_exists, compile_operation_scope
from muscles.data.sql.native_repository import NativePolicyRepository, native_repository_from_runtime, native_repository_transaction
from muscles.data.config import DataConfig
from muscles.data.runtime import DataRuntime


@pytest.mark.skipif(not os.getenv("MUSCLES_SQL_TEST_POSTGRES"), reason="isolated PostgreSQL not configured")
def test_postgres_native_repository_scope_and_lifecycle():
    db = SqlDatabase.postgres(os.environ["MUSCLES_SQL_TEST_POSTGRES"])
    lifecycle = ObjectLifecycle()
    class ScopePolicy:
        def evaluate(self, operation):
            if operation.kind.value == "create":
                return DataPolicyDecision()
            return DataPolicyDecision(predicates=(DataPredicate("uid", DataPredicateOperator.RELATED_EXISTS,
                DataRelatedExists("links", "object_uid", {"scope": "mine"})),))
    def handler(tx, event):
        if event.action == "created":
            tx.execute("INSERT INTO links (object_uid, scope) VALUES (%s, %s)", (event.object_uid, "mine"))
        else:
            tx.execute("DELETE FROM links WHERE object_uid = %s", (event.object_uid,))
    lifecycle.register(handler)
    try:
        with db.transaction() as tx:
            tx.execute("CREATE TEMPORARY TABLE objects (uid TEXT PRIMARY KEY, name TEXT) ON COMMIT DROP")
            tx.execute("CREATE TEMPORARY TABLE links (object_uid TEXT, scope TEXT) ON COMMIT DROP")
            repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((ScopePolicy(),)),
                                          context=DataOperationContext(), object_lifecycle=lifecycle,
                                          object_type="test:object")
            assert repo.create({"uid": "1", "name": "visible"}) == 1
            tx.execute("INSERT INTO objects VALUES (%s, %s)", ("2", "hidden"))
            assert repo.list() == [{"uid": "1", "name": "visible"}]
            assert repo.update("2", {"name": "blocked"}) == 0
            assert repo.delete("1") == 1
            assert tx.fetch_all("SELECT * FROM links") == []
    finally:
        db.close()


def test_native_create_requires_policy_values():
    db = SqlDatabase.sqlite(":memory:")
    class ScopePolicy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("scope", DataPredicateOperator.EQ, "mine"),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, scope TEXT)")
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((ScopePolicy(),)),
                                      context=DataOperationContext(), placeholder="?")
        with pytest.raises(DataPolicyDeniedError):
            repo.create({"uid": "1", "scope": "other"})
        assert repo.create({"uid": "2", "scope": "mine"}) == 1
        assert repo.list() == [{"uid": "2", "scope": "mine"}]
    db.close()


def test_native_create_does_not_treat_boolean_as_integer_policy_value():
    db = SqlDatabase.sqlite(":memory:")
    class Policy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("scope", DataPredicateOperator.EQ, 1),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, scope INTEGER)")
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((Policy(),)),
                                      context=DataOperationContext(), placeholder="?")
        with pytest.raises(DataPolicyDeniedError):
            repo.create({"uid": "1", "scope": True})
        assert tx.fetch_all("SELECT * FROM objects") == []
    db.close()


def test_native_like_is_case_sensitive_and_escapes_glob_metacharacters():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, name TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?), (?, ?), (?, ?)",
                   ("1", "Alpha*", "2", "alpha*", "3", "AlphaX"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(),
                                      context=DataOperationContext(), placeholder="?")
        filters = (DataPredicate("name", DataPredicateOperator.LIKE, "Alpha*"),)
        assert repo.list(filters=filters) == [{"uid": "1", "name": "Alpha*"}]
        filters = (DataPredicate("name", DataPredicateOperator.LIKE, "Alpha%"),)
        assert repo.count(filters=filters) == 2
    db.close()


def test_native_create_rejects_related_record_policy_before_insert():
    db = SqlDatabase.sqlite(":memory:")
    class Policy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("uid", DataPredicateOperator.RELATED_EXISTS,
                DataRelatedExists("links", "object_uid", {"scope": "mine"})),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT)")
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((Policy(),)),
                                      context=DataOperationContext(), placeholder="?")
        with pytest.raises(DataUnsupportedOperationError):
            repo.create({"uid": "1"})
        assert tx.fetch_all("SELECT * FROM objects") == []
    db.close()


@pytest.mark.parametrize("operator, expected, actual, allowed", [
    (DataPredicateOperator.NE, "blocked", "allowed", True),
    (DataPredicateOperator.NE, "blocked", None, False),
    (DataPredicateOperator.GT, 5, 6, True),
    (DataPredicateOperator.GTE, 5, 4, False),
    (DataPredicateOperator.IN, [1, 2], 2, True),
    (DataPredicateOperator.IN, [1, 2], True, False),
    (DataPredicateOperator.IS_NULL, True, None, True),
    (DataPredicateOperator.IS_NULL, False, None, False),
])
def test_native_create_validates_scalar_policy(operator, expected, actual, allowed):
    db = SqlDatabase.sqlite(":memory:")
    class Policy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("value", operator, expected),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, value)")
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((Policy(),)),
                                      context=DataOperationContext(), placeholder="?")
        if allowed:
            assert repo.create({"uid": "1", "value": actual}) == 1
        else:
            with pytest.raises(DataPolicyDeniedError):
                repo.create({"uid": "1", "value": actual})
    db.close()


def test_native_bulk_create_rolls_back_when_policy_denies_a_row():
    db = SqlDatabase.sqlite(":memory:")
    class ScopePolicy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("scope", DataPredicateOperator.EQ, "mine"),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, scope TEXT)")
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((ScopePolicy(),)),
                                      context=DataOperationContext(), placeholder="?")
        with pytest.raises(DataPolicyDeniedError):
            repo.bulk_create([{"uid": "1", "scope": "mine"}, {"uid": "2", "scope": "other"}])
        assert tx.fetch_all("SELECT * FROM objects") == []
    db.close()


def test_native_bulk_update_rolls_back_after_invalid_row():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, name TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?)", ("1", "original"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(),
                                      context=DataOperationContext(), placeholder="?")
        with pytest.raises(ValueError):
            repo.bulk_update([{"uid": "1", "name": "changed"}, {"name": "missing id"}])
        assert repo.get("1")["name"] == "original"
    db.close()


def test_native_bulk_delete_rolls_back_after_invalid_identifier():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT)")
        tx.execute("INSERT INTO objects VALUES (?)", ("1",))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(),
                                      context=DataOperationContext(), placeholder="?")
        with pytest.raises(ValueError):
            repo.bulk_delete(["1", None])
        assert repo.get("1") == {"uid": "1"}
    db.close()


def test_native_bulk_operations_do_not_touch_inaccessible_objects():
    db = SqlDatabase.sqlite(":memory:")
    lifecycle = ObjectLifecycle()
    events = []
    lifecycle.register(lambda tx, event: events.append((event.action, event.object_uid)))
    class Policy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("scope", DataPredicateOperator.EQ, "mine"),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT PRIMARY KEY, scope TEXT, name TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?, ?), (?, ?, ?)",
                   ("1", "mine", "one", "2", "other", "two"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((Policy(),)),
                                      context=DataOperationContext(), placeholder="?",
                                      object_lifecycle=lifecycle, object_type="test:object")
        assert repo.bulk_update([{"uid": "1", "name": "changed"}, {"uid": "2", "name": "blocked"}]) == 1
        assert repo.bulk_delete(["1", "2"]) == 1
        assert events == [("deleted", "1")]
        assert tx.fetch_all("SELECT * FROM objects") == [{"uid": "2", "scope": "other", "name": "two"}]
    db.close()


def test_native_policy_receives_target_and_update_values():
    db = SqlDatabase.sqlite(":memory:")
    seen = []
    class ObservePolicy:
        def evaluate(self, operation):
            seen.append(operation)
            return DataPolicyDecision()
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, name TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?)", ("1", "original"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((ObservePolicy(),)),
                                      context=DataOperationContext(), placeholder="?")
        assert repo.update("1", {"name": "changed"}) == 1
        assert seen[-1].filters[0].value == "1"
        assert seen[-1].values["name"] == "changed"
    db.close()


def test_native_repository_enforces_related_policy_for_reads_and_deletes():
    db = SqlDatabase.sqlite(":memory:")
    class ScopePolicy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("uid", DataPredicateOperator.RELATED_EXISTS,
                DataRelatedExists("links", "object_uid", {"scope": "mine"})),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, name TEXT)")
        tx.execute("CREATE TABLE links (object_uid TEXT, scope TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?), (?, ?)", ("1", "one", "2", "two"))
        tx.execute("INSERT INTO links VALUES (?, ?)", ("1", "mine"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((ScopePolicy(),)),
                                      context=DataOperationContext(), placeholder="?")
        assert repo.list() == [{"uid": "1", "name": "one"}]
        assert repo.list(order_by="uid", limit=1) == [{"uid": "1", "name": "one"}]
        assert repo.count() == 1
        assert repo.exists()
        matching = (DataPredicate("name", DataPredicateOperator.EQ, "one"),)
        assert repo.count(filters=matching) == 1
        hidden = (DataPredicate("name", DataPredicateOperator.EQ, "two"),)
        assert repo.list(filters=hidden) == []
        assert not repo.exists(filters=hidden)
        assert repo.list(filters=(DataPredicate("uid", DataPredicateOperator.IN, []),)) == []
        assert repo.get("2") is None
        assert repo.update("2", {"name": "blocked"}) == 0
        assert repo.update("1", {"name": "changed"}) == 1
        assert repo.get("1")["name"] == "changed"
        assert repo.delete("2") == 0
        assert repo.delete("1") == 1
        with pytest.raises(ValueError):
            repo.delete(None)
    db.close()


def test_native_related_policy_is_parameterized_and_filters_rows():
    db = SqlDatabase.sqlite(":memory:")
    predicate = DataPredicate("uid", DataPredicateOperator.RELATED_EXISTS,
                              DataRelatedExists("links", "object_uid", {"scope": "mine"}))
    fragment, params = compile_related_exists(predicate, target_alias="o", placeholder="?")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT)")
        tx.execute("CREATE TABLE links (object_uid TEXT, scope TEXT)")
        tx.execute("INSERT INTO objects VALUES (?), (?)", ("1", "2"))
        tx.execute("INSERT INTO links VALUES (?, ?)", ("1", "mine"))
        assert tx.fetch_all("SELECT o.uid FROM objects AS o WHERE " + fragment, params) == [{"uid": "1"}]
    with pytest.raises(ValueError, match="identifier"):
        compile_related_exists(predicate, target_alias="o; DROP TABLE objects", placeholder="?")
    db.close()


def test_compiled_operation_scope_protects_custom_sql():
    db = SqlDatabase.sqlite(":memory:")
    class Policy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("uid", DataPredicateOperator.RELATED_EXISTS,
                DataRelatedExists("links", "object_uid", {"scope": "mine"})),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, name TEXT)")
        tx.execute("CREATE TABLE links (object_uid TEXT, scope TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?), (?, ?)", ("1", "one", "2", "two"))
        tx.execute("INSERT INTO links VALUES (?, ?)", ("1", "mine"))
        request = DataOperationRequest(DataOperationKind.READ, "objects", filters=(
            DataPredicate("name", DataPredicateOperator.IN, ("one", "two")),))
        clause, params = compile_operation_scope(DataPolicyPipeline((Policy(),)), request,
                                                   target_alias="o", placeholder="?")
        assert tx.fetch_all("SELECT o.uid FROM objects AS o WHERE " + clause, params) == [{"uid": "1"}]
    db.close()


def test_object_event_handler_failure_rolls_back_object_write():
    db = SqlDatabase.sqlite(":memory:")
    lifecycle = ObjectLifecycle()

    def handler(transaction, event):
        transaction.execute("INSERT INTO links VALUES (?)", (event.object_uid,))
        raise ValueError("abort")

    lifecycle.register(handler)
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT)")
        tx.execute("CREATE TABLE links (uid TEXT)")
    with pytest.raises(ValueError, match="abort"):
        with db.transaction() as tx:
            tx.execute("INSERT INTO objects VALUES (?)", ("1",))
            tx.emit_object_event(lifecycle, ObjectLifecycleEvent("created", "test:object", "1", DataOperationContext()))
    with db.transaction() as tx:
        assert tx.fetch_all("SELECT * FROM objects") == []
        assert tx.fetch_all("SELECT * FROM links") == []
    db.close()


def test_native_delete_handler_failure_restores_object_and_link():
    db = SqlDatabase.sqlite(":memory:")
    lifecycle = ObjectLifecycle()

    def handler(tx, event):
        tx.execute("DELETE FROM links WHERE uid = ?", (event.object_uid,))
        raise ValueError("handler failed")

    lifecycle.register(handler)
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT PRIMARY KEY)")
        tx.execute("CREATE TABLE links (uid TEXT)")
        tx.execute("INSERT INTO objects VALUES (?)", ("1",))
        tx.execute("INSERT INTO links VALUES (?)", ("1",))
    with pytest.raises(ValueError, match="handler failed"):
        with db.transaction() as tx:
            repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(), context=DataOperationContext(),
                                          placeholder="?", object_lifecycle=lifecycle, object_type="test:object")
            repo.delete("1")
    with db.transaction() as tx:
        assert tx.fetch_all("SELECT * FROM objects") == [{"uid": "1"}]
        assert tx.fetch_all("SELECT * FROM links") == [{"uid": "1"}]
    db.close()


def test_caught_object_event_failure_still_rolls_back():
    db = SqlDatabase.sqlite(":memory:")
    lifecycle = ObjectLifecycle()

    def fail(transaction, event):
        raise ValueError("handler failed")

    lifecycle.register(fail)
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT)")
    with pytest.raises(RuntimeError, match="cannot commit"):
        with db.transaction() as tx:
            tx.execute("INSERT INTO objects VALUES (?)", ("1",))
            with pytest.raises(ValueError, match="handler failed"):
                tx.emit_object_event(lifecycle, ObjectLifecycleEvent("created", "test:object", "1", DataOperationContext()))
    with db.transaction() as tx:
        assert tx.fetch_all("SELECT * FROM objects") == []
    db.close()


def test_native_repository_creates_and_deletes_with_lifecycle():
    db = SqlDatabase.sqlite(":memory:")
    lifecycle = ObjectLifecycle()
    def handler(tx, event):
        if event.action == "created":
            tx.execute("INSERT INTO links VALUES (?)", (event.object_uid,))
        else:
            tx.execute("DELETE FROM links WHERE uid = ?", (event.object_uid,))
    lifecycle.register(handler)
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT PRIMARY KEY)")
        tx.execute("CREATE TABLE links (uid TEXT)")
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(), context=DataOperationContext(),
                                      placeholder="?", object_lifecycle=lifecycle, object_type="test:object")
        assert repo.create({"uid": "1"}) == 1
        assert tx.fetch_all("SELECT uid FROM links") == [{"uid": "1"}]
        assert repo.delete("1") == 1
        assert tx.fetch_all("SELECT uid FROM links") == []
    db.close()


def test_native_repository_uses_runtime_policies_and_events():
    db = SqlDatabase.sqlite(":memory:")
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {}}))
    runtime.register_object_type("objects", "test:object")
    class ScopePolicy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("scope", DataPredicateOperator.EQ, "mine"),))
    runtime.policies.register(ScopePolicy())
    events = []
    runtime.object_lifecycle.register(lambda transaction, event: events.append(event.action))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, scope TEXT)")
        repo = native_repository_from_runtime(runtime, tx, "objects", context=DataOperationContext(), placeholder="?")
        assert repo.object_type == "test:object"
        with pytest.raises(ValueError, match="registered"):
            native_repository_from_runtime(runtime, tx, "objects", context=DataOperationContext(),
                                           placeholder="?", object_type="other:object")
        with pytest.raises(DataPolicyDeniedError):
            repo.create({"uid": "1", "scope": "other"})
        assert repo.create({"uid": "2", "scope": "mine"}) == 1
        assert events == ["created"]
        assert repo.list() == [{"uid": "2", "scope": "mine"}]
    runtime.close()
    db.close()


def test_native_upsert_emits_creation_once():
    db = SqlDatabase.sqlite(":memory:")
    lifecycle = ObjectLifecycle()
    events = []
    lifecycle.register(lambda tx, event: events.append(event.action))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT PRIMARY KEY, name TEXT)")
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(), context=DataOperationContext(),
                                      placeholder="?", object_lifecycle=lifecycle, object_type="test:object")
        assert repo.upsert({"uid": "1", "name": "one"}) == 1
        assert repo.upsert({"uid": "1", "name": "updated"}) == 1
        assert repo.get("1")["name"] == "updated"
        assert events == ["created"]
    db.close()


def test_native_upsert_enforces_upsert_policy_in_both_branches():
    db = SqlDatabase.sqlite(":memory:")
    class Policy:
        def evaluate(self, operation):
            if operation.kind.value == "upsert":
                return DataPolicyDecision(predicates=(DataPredicate("uid", DataPredicateOperator.EQ, "allowed"),))
            return DataPolicyDecision()
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT PRIMARY KEY, name TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?)", ("other", "original"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((Policy(),)),
                                      context=DataOperationContext(), placeholder="?")
        assert repo.upsert({"uid": "allowed", "name": "one"}) == 1
        assert repo.upsert({"uid": "allowed", "name": "updated"}) == 1
        with pytest.raises(DataPolicyDeniedError):
            repo.upsert({"uid": "other", "name": "blocked"})
        assert repo.get("other")["name"] == "original"
    db.close()


def test_native_upsert_cannot_replace_an_inaccessible_object():
    import sqlite3

    db = SqlDatabase.sqlite(":memory:")
    class Policy:
        def evaluate(self, operation):
            if operation.kind.value == "update":
                return DataPolicyDecision(predicates=(DataPredicate("scope", DataPredicateOperator.EQ, "mine"),))
            return DataPolicyDecision()
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT PRIMARY KEY, scope TEXT, name TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?, ?)", ("1", "other", "original"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((Policy(),)),
                                      context=DataOperationContext(), placeholder="?")
        with pytest.raises(sqlite3.IntegrityError):
            repo.upsert({"uid": "1", "name": "blocked"})
        assert tx.fetch_one("SELECT name FROM objects WHERE uid = ?", ("1",)) == {"name": "original"}
    db.close()


def test_related_policy_filters_before_pagination_and_count():
    db = SqlDatabase.sqlite(":memory:")
    class Policy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("uid", DataPredicateOperator.RELATED_EXISTS,
                DataRelatedExists("links", "object_uid", {"scope": "mine"})),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT PRIMARY KEY, amount INTEGER)")
        tx.execute("CREATE TABLE links (object_uid TEXT, scope TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?), (?, ?), (?, ?)", ("1", 100, "2", 2, "3", 3))
        tx.execute("INSERT INTO links VALUES (?, ?), (?, ?)", ("2", "mine", "3", "mine"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((Policy(),)),
                                      context=DataOperationContext(), placeholder="?")
        assert repo.list(limit=1, offset=0, order_by="uid") == [{"uid": "2", "amount": 2}]
        assert repo.list(limit=1, offset=1, order_by="uid") == [{"uid": "3", "amount": 3}]
        assert repo.count() == 2
        assert repo.aggregate("sum", "amount") == 5
        assert sorted(repo.aggregate_grouped("sum", "amount", group_by=("uid",)), key=lambda row: row["uid"]) == [
            {"uid": "2", "value": 2}, {"uid": "3", "value": 3}
        ]
        with pytest.raises(ValueError, match="aggregate"):
            repo.aggregate("unsupported", "amount")
    db.close()


def test_native_join_applies_policy_to_both_tables_before_pagination():
    db = SqlDatabase.sqlite(":memory:")
    class ScopePolicy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("scope", DataPredicateOperator.EQ, "mine"),))
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT, scope TEXT)")
        tx.execute("CREATE TABLE related (object_uid TEXT, scope TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?), (?, ?)", ("1", "mine", "2", "mine"))
        tx.execute("INSERT INTO related VALUES (?, ?), (?, ?)", ("1", "other", "2", "mine"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((ScopePolicy(),)),
                                      context=DataOperationContext(), placeholder="?")
        assert repo.list_joined("related", target_field="uid", related_field="object_uid", limit=1) == [
            {"uid": "2", "scope": "mine"}
        ]
        assert repo.list_joined("related", target_field="uid", related_field="object_uid", outer=True) == [
            {"uid": "1", "scope": "mine"}, {"uid": "2", "scope": "mine"}
        ]
        assert repo.list_joined("related", target_field="uid", related_field="object_uid", outer=True,
                                related_fields=("scope",)) == [
            {"uid": "1", "scope": "mine", "related__scope": None},
            {"uid": "2", "scope": "mine", "related__scope": "mine"},
        ]
        assert repo.list_joined("related", target_field="uid", related_field="object_uid", outer=True,
                                order_by="uid", limit=1, offset=1) == [{"uid": "2", "scope": "mine"}]
        with pytest.raises(ValueError, match="identifier"):
            repo.list_joined("related", target_field="uid", related_field="object_uid",
                             related_fields=("scope; DROP TABLE objects",))
    db.close()


def test_native_repository_rejects_unsupported_configuration_before_writes():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT)")
        with pytest.raises(ValueError, match="placeholder"):
            NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(),
                                   context=DataOperationContext(), placeholder="unsafe")
        with pytest.raises(TypeError, match="policy pipeline"):
            NativePolicyRepository(tx, "objects", policies=None, context=DataOperationContext())
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(),
                                      context=DataOperationContext(), placeholder="?")
        with pytest.raises(ValueError, match="pagination"):
            repo.list(limit=True)
        assert tx.fetch_all("SELECT * FROM objects") == []
    db.close()


def test_native_repository_denied_policy_blocks_all_object_operations():
    db = SqlDatabase.sqlite(":memory:")
    class DenyPolicy:
        def evaluate(self, operation):
            return DataPolicyDecision(allowed=False, reason="denied")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT PRIMARY KEY, name TEXT)")
        tx.execute("INSERT INTO objects VALUES (?, ?)", ("1", "original"))
        repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline((DenyPolicy(),)),
                                      context=DataOperationContext(), placeholder="?")
        operations = [lambda: repo.list(), lambda: repo.count(), lambda: repo.exists(),
                      lambda: repo.get("1"), lambda: repo.create({"uid": "2"}),
                      lambda: repo.update("1", {"name": "blocked"}), lambda: repo.delete("1"),
                      lambda: repo.upsert({"uid": "1", "name": "blocked"})]
        for operation in operations:
            with pytest.raises(DataPolicyDeniedError, match="denied"):
                operation()
        assert tx.fetch_all("SELECT * FROM objects") == [{"uid": "1", "name": "original"}]
    db.close()


def test_runtime_repository_transaction_rolls_back_on_error():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT)")
    class Runtime:
        policies = DataPolicyPipeline()
        object_lifecycle = ObjectLifecycle()
        def connect(self, resource, port):
            assert resource == "database"
            return db
    with pytest.raises(ValueError, match="abort"):
        with native_repository_transaction(Runtime(), "database", "objects", context=DataOperationContext(),
                                           placeholder="?") as repo:
            repo.create({"uid": "1"})
            raise ValueError("abort")
    with db.transaction() as tx:
        assert tx.fetch_all("SELECT * FROM objects") == []
    db.close()


def test_native_bulk_create_rolls_back_events_when_handler_fails():
    db = SqlDatabase.sqlite(":memory:")
    lifecycle = ObjectLifecycle()
    def handler(tx, event):
        tx.execute("INSERT INTO links VALUES (?)", (event.object_uid,))
        if event.object_uid == "2":
            raise ValueError("handler failed")
    lifecycle.register(handler)
    with db.transaction() as tx:
        tx.execute("CREATE TABLE objects (uid TEXT)")
        tx.execute("CREATE TABLE links (uid TEXT)")
    with pytest.raises(RuntimeError, match="cannot commit"):
        with db.transaction() as tx:
            repo = NativePolicyRepository(tx, "objects", policies=DataPolicyPipeline(), context=DataOperationContext(),
                                          placeholder="?", object_lifecycle=lifecycle, object_type="test:object")
            with pytest.raises(ValueError, match="handler failed"):
                repo.bulk_create([{"uid": "1"}, {"uid": "2"}])
    with db.transaction() as tx:
        assert tx.fetch_all("SELECT * FROM objects") == []
        assert tx.fetch_all("SELECT * FROM links") == []
    db.close()


def test_sqlite_raw_queries_and_transaction_rollback(tmp_path):
    db = SqlDatabase.sqlite(tmp_path / "test.db")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        tx.execute("INSERT INTO users VALUES (?, ?)", (1, "Denis"))
    with pytest.raises(ValueError):
        with db.transaction() as tx:
            tx.execute("INSERT INTO users VALUES (?, ?)", (2, "Rollback"))
            raise ValueError("abort")
    with db.transaction() as tx:
        assert tx.fetch_all("SELECT id, name FROM users ORDER BY id") == [{"id": 1, "name": "Denis"}]
        assert tx.fetch_one("SELECT name FROM users WHERE id = ?", (99,)) is None


def test_sqlite_memory_database_is_shared_between_transactions():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        tx.execute("CREATE TABLE sample (value INTEGER)")
        tx.execute("INSERT INTO sample VALUES (?)", (42,))
    with db.transaction() as tx:
        assert tx.fetch_one("SELECT value FROM sample") == {"value": 42}
    db.close()


def test_sqlite_ddl_is_rolled_back_on_error():
    db = SqlDatabase.sqlite(":memory:")
    with pytest.raises(ValueError):
        with db.transaction() as tx:
            tx.execute("CREATE TABLE transient (value INTEGER)")
            raise ValueError("abort")
    with db.transaction() as tx:
        assert tx.fetch_one("SELECT name FROM sqlite_master WHERE name = ?", ("transient",)) is None
    db.close()


def test_transaction_cannot_be_used_after_context_exit():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        assert tx.fetch_one("SELECT 1 AS value") == {"value": 1}
    with pytest.raises(RuntimeError, match="transaction"):
        tx.fetch_one("SELECT 1")
    db.close()


def test_duplicate_column_names_are_rejected():
    db = SqlDatabase.sqlite(":memory:")
    with db.transaction() as tx:
        with pytest.raises(ValueError, match="duplicate"):
            tx.fetch_all("SELECT 1 AS id, 2 AS id")
    db.close()


def test_sqlite_allows_sequential_transactions_on_different_threads():
    db = SqlDatabase.sqlite(":memory:")
    try:
        with db.transaction() as tx:
            tx.execute("CREATE TABLE example (value INTEGER)")
            tx.execute("INSERT INTO example VALUES (42)")

        def read_value():
            with db.transaction() as tx:
                return tx.fetch_one("SELECT value FROM example")

        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(read_value).result() == {"value": 42}
    finally:
        db.close()


def test_sqlite_serializes_overlapping_transactions_across_threads():
    db = SqlDatabase.sqlite(":memory:")
    try:
        started = Event()

        def start_transaction():
            started.set()
            with db.transaction() as tx:
                return tx.fetch_one("SELECT 1 AS value")

        with ThreadPoolExecutor(max_workers=1) as executor:
            with db.transaction():
                future = executor.submit(start_transaction)
                assert started.wait(timeout=5)
                assert not future.done()
            assert future.result(timeout=5) == {"value": 1}

        with db.transaction():
            with pytest.raises(RuntimeError, match="Nested SQL transactions"):
                with db.transaction():
                    pass
    finally:
        db.close()


def test_active_transaction_cannot_be_used_from_another_thread():
    db = SqlDatabase.sqlite(":memory:")
    try:
        with db.transaction() as tx, ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(RuntimeError, match="different thread"):
                executor.submit(tx.fetch_one, "SELECT 1").result()
            assert tx.fetch_one("SELECT 1 AS value") == {"value": 1}
    finally:
        db.close()


def test_data_runtime_can_use_native_sqlite(tmp_path):
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.local": {"type": "native_sql", "driver": "sqlite", "path": str(tmp_path / "test.db")}
    }}), catalog=catalog)
    try:
        sql = runtime.require_port("sql.local", SqlTransactionPort)
        with sql.transaction() as tx:
            assert tx.fetch_one("SELECT 42 AS value") == {"value": 42}
        assert runtime.doctor()["checks"][0]["status"] == "ok"
    finally:
        runtime.close()


def test_data_runtime_rejects_incomplete_native_configuration():
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.errors import DataConfigurationError
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.local": {"type": "native_sql", "driver": "sqlite"}
    }}), catalog=catalog)
    try:
        sql = runtime.require_port("sql.local", SqlTransactionPort)
        with pytest.raises(DataConfigurationError):
            with sql.transaction():
                pass
    finally:
        runtime.close()


def test_data_resource_accepts_a_project_registered_driver():
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    opened = []

    def open_project_database(options):
        opened.append(options["database"])
        return SqlDatabase.sqlite(":memory:")

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory(drivers={"project_db": open_project_database}))
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.custom": {"type": "native_sql", "driver": "project_db", "database": "demo"}
    }}), catalog=catalog)
    try:
        sql = runtime.require_port("sql.custom", SqlTransactionPort)
        assert opened == []
        with sql.transaction() as tx:
            assert tx.fetch_one("SELECT 1 AS value") == {"value": 1}
        assert opened == ["demo"]
    finally:
        runtime.close()


def test_data_resource_rejects_unknown_driver():
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.errors import DataConfigurationError
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.custom": {"type": "native_sql", "driver": "missing"}
    }}), catalog=catalog)
    try:
        with pytest.raises(DataConfigurationError, match="driver"):
            runtime.require_port("sql.custom", SqlTransactionPort).transaction()
    finally:
        runtime.close()


def test_data_resource_uses_driver_specific_health_query():
    import sqlite3

    from muscles.data.config import DataConfig
    from muscles.data.sql.data_adapter import NativeSqlFactory

    executed = []

    def connect(options):
        connection = sqlite3.connect(":memory:")
        connection.set_trace_callback(executed.append)
        return SqlDatabase(connection, health_query="SELECT 42 AS status")

    factory = NativeSqlFactory(drivers={"custom": connect})
    config = DataConfig.from_raw({"resources": {
        "sql.custom": {"type": "native_sql", "driver": "custom"}
    }}).resources["sql.custom"]
    adapter = factory.create(config)
    try:
        assert adapter.health().status == "ok"
        assert "SELECT 42 AS status" in executed
    finally:
        adapter.close()


def test_data_resource_cannot_open_database_after_runtime_close():
    from muscles.data.catalog import DataAdapterCatalog
    from muscles.data.config import DataConfig
    from muscles.data.ports import SqlTransactionPort
    from muscles.data.runtime import DataRuntime
    from muscles.data.sql.data_adapter import NativeSqlFactory

    catalog = DataAdapterCatalog.with_defaults()
    catalog.register(NativeSqlFactory())
    runtime = DataRuntime(config=DataConfig.from_raw({"resources": {
        "sql.local": {"type": "native_sql", "driver": "sqlite", "path": ":memory:"}
    }}), catalog=catalog)
    sql = runtime.require_port("sql.local", SqlTransactionPort)
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        with sql.transaction():
            pass


def test_model_mapper_uses_explicit_field_mapping():
    from muscles import Column, Integer, Model, String
    from muscles.data.sql import ModelMapper

    class Person(Model):
        id = Column(Integer)
        name = Column(String)

    mapper = ModelMapper(Person, {"id": "person_id", "name": "display_name"})
    person = mapper.from_record({"person_id": 1, "display_name": "Denis", "ignored": True})
    assert person.id == 1 and person.name == "Denis"
    assert mapper.to_record(person) == {"person_id": 1, "display_name": "Denis"}
    assert mapper.from_records([{"person_id": 1, "display_name": "Denis"}])[0].name == "Denis"
    with pytest.raises(KeyError):
        mapper.from_record({"person_id": 1})
    invalid_mapper = ModelMapper(Person, {"naem": "display_name"})
    with pytest.raises(ValueError, match="naem"):
        invalid_mapper.from_record({"display_name": "Denis"})


@pytest.mark.skipif(not os.getenv("MUSCLES_SQL_TEST_POSTGRES"), reason="isolated PostgreSQL not configured")
def test_postgres_transactions_and_native_parameters():
    db = SqlDatabase.postgres(os.environ["MUSCLES_SQL_TEST_POSTGRES"])
    try:
        with db.transaction() as tx:
            tx.execute("CREATE TEMP TABLE muscles_data_sql_test (id INTEGER, name TEXT)")
            tx.execute("INSERT INTO muscles_data_sql_test VALUES (%s, %s)", (1, "Denis"))
        with pytest.raises(ValueError):
            with db.transaction() as tx:
                tx.execute("INSERT INTO muscles_data_sql_test VALUES (%s, %s)", (2, "Rollback"))
                raise ValueError("abort")
        with db.transaction() as tx:
            assert tx.fetch_all("SELECT id, name FROM muscles_data_sql_test WHERE id >= %s", (1,)) == [
                {"id": 1, "name": "Denis"}
            ]
            assert tx.fetch_one("SELECT '50%' AS ratio") == {"ratio": "50%"}
    finally:
        db.close()


@pytest.mark.skipif(not os.getenv("MUSCLES_SQL_TEST_POSTGRES"), reason="isolated PostgreSQL not configured")
def test_postgres_driver_health():
    from muscles.data.config import DataConfig
    from muscles.data.sql.data_adapter import NativeSqlFactory

    config = DataConfig.from_raw({"resources": {"sql.pg": {
        "type": "native_sql", "driver": "postgres", "url_env": "MUSCLES_SQL_TEST_POSTGRES"
    }}}).resources["sql.pg"]
    adapter = NativeSqlFactory().create(config)
    try:
        assert adapter.health().status == "ok"
    finally:
        adapter.close()
