import os

import pytest

from muscles.data.sql.migration_lock import SqlMigrationExecutionLock


def test_sqlite_execution_lock_is_shared_and_released(tmp_path):
    url = f"sqlite:///{tmp_path / 'database.db'}"
    first = SqlMigrationExecutionLock(url)
    second = SqlMigrationExecutionLock(url)
    with first():
        with pytest.raises(RuntimeError, match="locked"):
            with second():
                pass
    with second():
        pass


def test_sqlite_memory_cannot_be_a_shared_coordinator():
    with pytest.raises(ValueError, match="memory"):
        with SqlMigrationExecutionLock("sqlite:///:memory:")():
            pass


@pytest.mark.skipif(not os.getenv("MUSCLES_SQL_MIGRATIONS_TEST_URL"), reason="isolated PostgreSQL not configured")
def test_postgres_execution_lock_is_shared_and_released():
    lock = SqlMigrationExecutionLock(os.environ["MUSCLES_SQL_MIGRATIONS_TEST_URL"])
    with lock():
        with pytest.raises(RuntimeError, match="locked"):
            with lock():
                pass
    with lock():
        pass
