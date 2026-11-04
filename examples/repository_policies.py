"""Request-scoped repository policies with an in-memory SQLite database.

Requires the SQLAlchemy-based legacy extra. Raw SQL does not apply policies.
"""
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, func
from sqlalchemy.orm import Session

from muscles_data import (
    DataConfig, DataOperationContext, DataOperationKind, DataPolicyDecision,
    DataPredicate, DataPredicateOperator, DataRuntime,
)
from muscles_data.errors import DataPolicyDeniedError
from muscles_data_sql import SqlRepository
from muscles_data_sql.query import FilterClause, QuerySpec


class WorkspacePolicy:
    def evaluate(self, operation):
        workspace = operation.context.attributes["workspace_uid"]
        return DataPolicyDecision(predicates=(DataPredicate("workspace", DataPredicateOperator.EQ, workspace),))


def main():
    runtime = DataRuntime(config=DataConfig.from_raw({}))
    runtime.policies.register(WorkspacePolicy())
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    documents = Table("documents", metadata, Column("id", Integer, primary_key=True),
                      Column("workspace", String, nullable=False), Column("title", String))
    metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.execute(documents.insert(), [
                {"id": 1, "workspace": "team-a", "title": "Visible"},
                {"id": 2, "workspace": "team-b", "title": "Other team"},
            ])
            repo = SqlRepository(session, documents, policies=runtime.policies,
                                 context=DataOperationContext(subject_uid="user-1",
                                                              attributes={"workspace_uid": "team-a"}))
            print("visible:", [row.title for row in repo.list()])
            print("get:", repo.get(1).title, "count:", repo.count())
            print("find:", [row.title for row in repo.find({"title": "Visible"})])
            query = QuerySpec(filters=[FilterClause("title", "like", "%Visible%")], limit=10)
            print("query:", [row.title for row in repo.query(query)])
            print("first:", repo.first(query).title)
            total = QuerySpec(select_columns=[func.count(documents.c.id).label("total")])
            print("scalar:", repo.scalar(total), "aggregate:", repo.aggregate(total)[0]["total"])
            repo.create({"id": 3, "workspace": "team-a", "title": "Created"})
            repo.bulk_create([{"id": 4, "workspace": "team-a", "title": "Batch"}])
            repo.bulk_update("id", [{"id": 4, "title": "Batch updated"}])
            repo.upsert({"id": 4, "workspace": "team-a", "title": "Upserted"}, ["id"])
            print("upsert:", repo.get(4).title, "deleted:", repo.delete(3))
            print("other workspace update count:", repo.update(2, {"title": "Denied"}))
            try:
                repo.create({"id": 5, "workspace": "team-b", "title": "Denied"})
            except DataPolicyDeniedError:
                print("cross-workspace creation denied")
            print("explain:", repo.explain(DataOperationKind.READ))
            session.rollback()
    finally:
        engine.dispose()
        runtime.close()


if __name__ == "__main__":
    main()
