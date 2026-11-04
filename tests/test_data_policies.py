import os
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, UniqueConstraint, create_engine
from sqlalchemy.orm import Session

from muscles.data.errors import DataPolicyDeniedError
from muscles.data.policies import (
    DataOperationContext, DataOperationKind, DataPolicyDecision,
    DataPolicyPipeline, DataPredicate, DataPredicateOperator,
)
from muscles.data.sql.query import FilterClause, QuerySpec
from muscles.data.sql.repository import SqlRepository


@pytest.fixture
def repo():
    engine = create_engine(os.getenv("MUSCLES_SQL_POLICY_TEST_URL", "sqlite:///:memory:"))
    meta = MetaData()
    table = Table(f"policy_test_{uuid4().hex}", meta, Column("id", Integer, primary_key=True),
                  Column("workspace", String, nullable=False), Column("name", String),
                  UniqueConstraint("workspace", "name"))
    meta.create_all(engine)
    try:
        with Session(engine) as session:
            session.execute(table.insert(), [
                {"id": 1, "workspace": "other", "name": "first"},
                {"id": 2, "workspace": "mine", "name": "second"},
                {"id": 3, "workspace": "mine", "name": "third"},
            ])

            class WorkspacePolicy:
                def evaluate(self, operation):
                    return DataPolicyDecision(predicates=(DataPredicate(
                        "workspace", DataPredicateOperator.EQ, operation.context.attributes["workspace"]
                    ),))

            yield SqlRepository(session, table, policies=DataPolicyPipeline((WorkspacePolicy(),)),
                                context=DataOperationContext(subject_uid="user", attributes={"workspace": "mine"}))
    finally:
        meta.drop_all(engine)
        engine.dispose()


def test_reads_filter_before_limit_and_preserve_user_or_filters(repo):
    assert repo.get(1) is None
    assert [row.id for row in repo.list(limit=1, order_by=repo.table.c.id)] == [2]
    assert repo.count() == 2
    assert [row.id for row in repo.find({"name": "third"})] == [3]
    spec = QuerySpec(or_filters=[FilterClause("name", "eq", "first"), FilterClause("name", "eq", "second")])
    assert [row.id for row in repo.query(spec)] == [2]
    assert repo.first(spec).id == 2
    assert repo.scalar(QuerySpec(select_columns=[repo.table.c.id], order_by=[repo.table.c.id])) == 2
    assert [row.id for row in repo.aggregate(QuerySpec())] == [2, 3]


def test_writes_and_bulk_operations_apply_policy(repo):
    assert repo.update(1, {"name": "blocked"}) == 0
    assert repo.delete(1) == 0
    with pytest.raises(DataPolicyDeniedError):
        repo.create({"id": 4, "workspace": "other", "name": "blocked"})
    with pytest.raises(DataPolicyDeniedError):
        repo.update(2, {"workspace": "other"})
    with pytest.raises(DataPolicyDeniedError):
        repo.bulk_create([{"id": 4, "workspace": "mine", "name": "ok"},
                          {"id": 5, "workspace": "other", "name": "blocked"}])
    assert repo.get(4) is None
    assert repo.bulk_update("id", [{"id": 1, "name": "blocked"}, {"id": 2, "name": "updated"}]) == 1


def test_upsert_supports_composite_unique_constraint_under_policies(repo):
    assert repo.upsert({"id": 4, "workspace": "mine", "name": "new"}, ["workspace", "name"]) == 1
    assert repo.upsert({"id": 5, "workspace": "mine", "name": "new"}, ["workspace", "name"]) == 1
    assert repo.count({"name": "new"}) == 1


def test_upsert_rejects_missing_conflict_fields_without_mutation(repo):
    with pytest.raises(ValueError, match="conflict"):
        repo.upsert({"id": 4, "workspace": "mine"}, ["workspace", "name"])
    assert repo.get(2).name == "second"


def test_null_values_do_not_bypass_not_equal_policy(repo):
    class NotOtherPolicy:
        def evaluate(self, operation):
            return DataPolicyDecision(predicates=(DataPredicate("name", DataPredicateOperator.NE, "blocked"),))

    repo.policies = DataPolicyPipeline((NotOtherPolicy(),))
    with pytest.raises(DataPolicyDeniedError):
        repo.create({"id": 4, "workspace": "mine", "name": None})


def test_explain_hides_predicate_values(repo):
    result = repo.explain(DataOperationKind.READ, filters=(DataPredicate("name", DataPredicateOperator.EQ, "secret"),))
    assert [item["field"] for item in result["filters"]] == ["name", "workspace"]
    assert "secret" not in str(result) and "mine" not in str(result)


def test_policy_denial_blocks_every_repository_path(repo):
    class Deny:
        def evaluate(self, operation):
            return DataPolicyDecision(allowed=False, reason="denied")

    repo.policies = DataPolicyPipeline((Deny(),))
    spec = QuerySpec()
    actions = [
        lambda: repo.get(2), lambda: repo.list(), lambda: repo.find(), lambda: repo.count(),
        lambda: repo.exists({}), lambda: repo.query(spec), lambda: repo.first(spec),
        lambda: repo.scalar(spec), lambda: repo.aggregate(spec),
        lambda: repo.create({"id": 4, "workspace": "mine", "name": "new"}),
        lambda: repo.update(2, {"name": "new"}), lambda: repo.delete(2),
        lambda: repo.bulk_create([{"id": 4, "workspace": "mine", "name": "new"}]),
        lambda: repo.bulk_update("id", [{"id": 2, "name": "new"}]),
        lambda: repo.upsert({"id": 2, "workspace": "mine", "name": "new"}, ["id"]),
    ]
    for action in actions:
        with pytest.raises(DataPolicyDeniedError, match="denied"):
            action()
    assert repo.session.execute(repo.table.select().where(repo.table.c.id == 2)).mappings().one()["name"] == "second"


def test_bulk_update_rolls_back_prior_changes_on_policy_denial(repo):
    with pytest.raises(DataPolicyDeniedError):
        repo.bulk_update("id", [{"id": 2, "name": "changed"}, {"id": 3, "workspace": "other"}])
    assert repo.get(2).name == "second"
