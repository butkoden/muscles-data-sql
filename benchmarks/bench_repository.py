import time

from sqlalchemy import Column, Integer, MetaData, String, Table

from muscles_data_sql.config import DatabaseConfig
from muscles_data_sql.engine import EngineManager
from muscles_data_sql.repository import SqlRepository
from muscles_data_sql.uow import UnitOfWork


def run(iterations: int = 1000):
    manager = EngineManager(DatabaseConfig(url="sqlite:///:memory:"))
    metadata = MetaData()
    table = Table(
        "items",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
    )
    metadata.create_all(manager.engine)

    start = time.perf_counter()
    with UnitOfWork(manager.session_factory) as uow:
        repo = SqlRepository(uow.session, table)
        for i in range(iterations):
            repo.create({"id": i + 1, "name": f"item-{i}"})
        repo.list(limit=50, offset=0)
    elapsed = time.perf_counter() - start
    print({"iterations": iterations, "elapsed_seconds": elapsed, "per_op_ms": (elapsed / iterations) * 1000})


if __name__ == "__main__":
    run()
