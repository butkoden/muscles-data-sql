import pytest

from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, func

from muscles.data.sql.config import DatabaseConfig
from muscles.data.sql.engine import EngineManager
from muscles.data.sql.query import FilterClause, JoinClause, QuerySpec
from muscles.data.sql.repository import SqlRepository
from muscles.data.sql.uow import UnitOfWork


def _users_table():
    metadata = MetaData()
    return Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
    ), metadata


def test_repository_crud_and_uow_commit():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    table, metadata = _users_table()
    metadata.create_all(manager.engine)

    with UnitOfWork(manager.session_factory) as uow:
        repo = SqlRepository(uow.session, table)
        pk = repo.create({"id": 1, "name": "Denis"})
        assert pk[0] == 1
        row = repo.get(1)
        assert row["name"] == "Denis"
        repo.update(1, {"name": "Butko"})
        row2 = repo.get(1)
        assert row2["name"] == "Butko"
        repo.delete(1)
        assert repo.get(1) is None


def test_repository_find_count_exists():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    table, metadata = _users_table()
    metadata.create_all(manager.engine)

    with UnitOfWork(manager.session_factory) as uow:
        repo = SqlRepository(uow.session, table)
        repo.create({"id": 1, "name": "Denis"})
        repo.create({"id": 2, "name": "Ira"})
        repo.create({"id": 3, "name": "Denis"})

        found = repo.find({"name": "Denis"})
        assert len(found) == 2
        assert repo.count({"name": "Denis"}) == 2
        assert repo.exists({"name": "Ira"}) is True
        assert repo.exists({"name": "Missing"}) is False


def test_repository_queryspec_join_and_aggregate():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    metadata = MetaData()
    users = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
    )
    bookings = Table(
        "bookings",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
        Column("title", String, nullable=False),
    )
    metadata.create_all(manager.engine)
    with UnitOfWork(manager.session_factory) as uow:
        user_repo = SqlRepository(uow.session, users)
        booking_repo = SqlRepository(uow.session, bookings)
        user_repo.bulk_create([{"id": 1, "name": "Denis"}, {"id": 2, "name": "Ira"}])
        booking_repo.bulk_create(
            [
                {"id": 1, "user_id": 1, "title": "Call"},
                {"id": 2, "user_id": 1, "title": "Review"},
                {"id": 3, "user_id": 2, "title": "Demo"},
            ]
        )
        spec = QuerySpec(
            joins=[JoinClause(table=bookings, on=users.c.id == bookings.c.user_id)],
            select_columns=[users.c.name.label("name"), func.count(bookings.c.id).label("total")],
            group_by=[users.c.name],
            order_by=[users.c.name.asc()],
        )
        rows = user_repo.aggregate(spec)
        assert rows[0]["name"] == "Denis"
        assert rows[0]["total"] == 2
        assert rows[1]["name"] == "Ira"
        assert rows[1]["total"] == 1


def test_repository_upsert_sqlite():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    table, metadata = _users_table()
    metadata.create_all(manager.engine)
    with UnitOfWork(manager.session_factory) as uow:
        repo = SqlRepository(uow.session, table)
        repo.upsert({"id": 1, "name": "Denis"}, conflict_fields=["id"])
        repo.upsert({"id": 1, "name": "Butko"}, conflict_fields=["id"])
        assert repo.get(1)["name"] == "Butko"


def test_repository_upsert_fallback_uses_conflict_fields():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    table, metadata = _users_table()
    metadata.create_all(manager.engine)
    with UnitOfWork(manager.session_factory) as uow:
        repo = SqlRepository(uow.session, table)
        repo.create({"id": 1, "name": "Denis"})
        with pytest.raises(ValueError, match="PRIMARY KEY or UNIQUE constraint"):
            repo.upsert({"id": 2, "name": "Denis"}, conflict_fields=["name"])


def test_uow_nested_transaction_and_retry():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    table, metadata = _users_table()
    metadata.create_all(manager.engine)
    attempts = {"count": 0}
    with UnitOfWork(manager.session_factory) as uow:
        repo = SqlRepository(uow.session, table)

        with uow.begin_nested():
            repo.create({"id": 1, "name": "Denis"})

        class Retryable(Exception):
            pass

        def flaky(_session):
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise Retryable("retry")
            return "ok"

        assert uow.with_retry(flaky, max_attempts=3, retry_on=(Retryable,)) == "ok"
        assert attempts["count"] == 2


def test_uow_rolls_back_failed_business_unit_and_releases_session():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    table, metadata = _users_table()
    metadata.create_all(manager.engine)

    uow = UnitOfWork(manager.session_factory)
    with pytest.raises(RuntimeError, match="business failure"):
        with uow:
            SqlRepository(uow.session, table).create({"id": 1, "name": "Not committed"})
            raise RuntimeError("business failure")

    assert uow.session is None
    with UnitOfWork(manager.session_factory) as check:
        assert SqlRepository(check.session, table).get(1) is None


def test_uow_requires_active_session_for_transaction_helpers():
    uow = UnitOfWork(lambda: None)

    with pytest.raises(RuntimeError, match="active session"):
        uow.begin_nested()

    with pytest.raises(RuntimeError, match="active session"):
        uow.with_retry(lambda session: session)


def test_repository_queryspec_filter_operators():
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    table, metadata = _users_table()
    metadata.create_all(manager.engine)
    with UnitOfWork(manager.session_factory) as uow:
        repo = SqlRepository(uow.session, table)
        repo.bulk_create([{"id": 1, "name": "Denis"}, {"id": 2, "name": "Ira"}, {"id": 3, "name": "Dina"}])
        spec = QuerySpec(
            filters=[FilterClause("id", "in", [1, 3]), FilterClause("name", "like", "D%")],
            order_by=[table.c.id.asc()],
        )
        rows = repo.query(spec)
        assert [row["id"] for row in rows] == [1, 3]
