import importlib.util
import os
import sqlite3
from threading import Event, Thread

import pytest

from muscles.data.sql import SqlDatabase
from muscles.data.migrations import MigrationRegistry
from muscles.data.sql import build_native_sql_migration_registry
from muscles.data.sql.native_migrations import NativeSqlMigration, NativeSqlMigrationBackend


def migration(revision="001", checksum="hash1", *, reversible=True):
    def upgrade(tx):
        tx.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")

    def downgrade(tx):
        tx.execute("DROP TABLE example")

    return NativeSqlMigration(revision, checksum, upgrade, downgrade if reversible else None, dialects=("sqlite",))


def test_status_plan_upgrade_and_rollback_are_explicit(tmp_path):
    db = SqlDatabase.sqlite(tmp_path / "test.sqlite")
    backend = NativeSqlMigrationBackend(db, "accounts", [migration()], dialect="sqlite")
    try:
        assert backend.status()["current"] == []
        assert backend.plan() == ["001"]
        with db.transaction() as tx:
            assert tx.fetch_one("SELECT name FROM sqlite_master WHERE name = ?", ("muscles_data_migrations",)) is None
        backend.upgrade()
        assert backend.status()["current"] == ["001"]
        assert backend.plan() == []
        backend.upgrade()
        backend.downgrade("base")
        assert backend.status()["current"] == []
        with db.transaction() as tx:
            assert tx.fetch_one("SELECT name FROM sqlite_master WHERE name = ?", ("example",)) is None
    finally:
        db.close()


def test_changed_migration_or_missing_downgrade_fails_closed(tmp_path):
    db = SqlDatabase.sqlite(tmp_path / "test.sqlite")
    try:
        NativeSqlMigrationBackend(db, "accounts", [migration(reversible=False)], dialect="sqlite").upgrade()
        with pytest.raises(ValueError, match="changed"):
            NativeSqlMigrationBackend(db, "accounts", [migration(checksum="changed")], dialect="sqlite").status()
        with pytest.raises(ValueError, match="downgrade"):
            NativeSqlMigrationBackend(db, "accounts", [migration(reversible=False)], dialect="sqlite").downgrade("base")
        with db.transaction() as tx:
            assert tx.fetch_one("SELECT name FROM sqlite_master WHERE name = ?", ("example",)) is not None
    finally:
        db.close()


def test_explicit_compatible_checksum_preserves_existing_history(tmp_path):
    db = SqlDatabase.sqlite(tmp_path / "test.sqlite")
    try:
        old = migration(checksum="original")
        NativeSqlMigrationBackend(db, "example", [old], dialect="sqlite").upgrade()
        compatible = NativeSqlMigration("001", "updated", old.upgrade, old.downgrade,
                                        dialects=("sqlite",), compatible_checksums=("original",))
        assert NativeSqlMigrationBackend(db, "example", [compatible], dialect="sqlite").status()["current"] == ["001"]
        unrelated = NativeSqlMigration("001", "updated", old.upgrade, old.downgrade,
                                       dialects=("sqlite",), compatible_checksums=("different",))
        with pytest.raises(ValueError, match="changed"):
            NativeSqlMigrationBackend(db, "example", [unrelated], dialect="sqlite").status()
    finally:
        db.close()


def test_failed_migration_rolls_back_schema_and_journal(tmp_path):
    db = SqlDatabase.sqlite(tmp_path / "test.sqlite")

    def fail(tx):
        tx.execute("CREATE TABLE example (id INTEGER)")
        raise RuntimeError("failure")

    backend = NativeSqlMigrationBackend(db, "accounts", [NativeSqlMigration("001", "hash1", fail, dialects=("sqlite",))], dialect="sqlite")
    try:
        with pytest.raises(RuntimeError, match="failure"):
            backend.upgrade()
        assert backend.status()["current"] == []
        with db.transaction() as tx:
            assert tx.fetch_one("SELECT name FROM sqlite_master WHERE name = ?", ("example",)) is None
    finally:
        db.close()


def test_concurrent_sqlite_migrators_do_not_overlap(tmp_path):
    path = str(tmp_path / "test.sqlite")
    db1 = SqlDatabase(sqlite3.connect(path, timeout=0.1, check_same_thread=False), begin_sql="BEGIN")
    db2 = SqlDatabase(sqlite3.connect(path, timeout=0.1, check_same_thread=False), begin_sql="BEGIN")
    started, release = Event(), Event()
    errors = []

    def slow(tx):
        started.set()
        if not release.wait(5):
            raise RuntimeError("test synchronization timed out")
        tx.execute("CREATE TABLE example (id INTEGER)")

    first = NativeSqlMigrationBackend(db1, "accounts", [NativeSqlMigration("001", "hash", slow, dialects=("sqlite",))], dialect="sqlite")
    second = NativeSqlMigrationBackend(db2, "accounts", [NativeSqlMigration("001", "hash", slow, dialects=("sqlite",))], dialect="sqlite")

    def run_first():
        try:
            first.upgrade()
        except Exception as exc:
            errors.append(exc)

    thread = Thread(target=run_first)
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.upgrade()
    finally:
        release.set()
        thread.join(timeout=5)
        db1.close()
        db2.close()
    assert not thread.is_alive() and not errors


def test_migration_checksum_is_calculated_from_module_file(tmp_path):
    path = tmp_path / "m001.py"
    path.write_text("revision = '001'\ndialects = ('sqlite',)\ndef upgrade(tx):\n    pass\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("test_migration_001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = NativeSqlMigration.from_module(module)
    path.write_text(path.read_text() + "# changed\n", encoding="utf-8")
    assert NativeSqlMigration.from_module(module).checksum != before.checksum
    db = SqlDatabase.sqlite(":memory:")
    try:
        backend = NativeSqlMigrationBackend(db, "example", [before], dialect="sqlite")
        with pytest.raises(ValueError, match="changed"):
            backend.upgrade()
    finally:
        db.close()


@pytest.mark.skipif(not os.getenv("MUSCLES_SQL_TEST_POSTGRES"), reason="isolated PostgreSQL not configured")
def test_postgres_atomic_migration_and_lock():
    dsn = os.environ["MUSCLES_SQL_TEST_POSTGRES"]
    db1, db2 = SqlDatabase.postgres(dsn), SqlDatabase.postgres(dsn)
    started, release = Event(), Event()
    errors = []

    def slow(tx):
        started.set()
        if not release.wait(5):
            raise RuntimeError("test synchronization timed out")
        tx.execute("CREATE TABLE example_migration_test (id INTEGER)")

    def undo(tx):
        tx.execute("DROP TABLE example_migration_test")

    item = NativeSqlMigration("001", "hash", slow, undo, dialects=("postgres",))
    first = NativeSqlMigrationBackend(db1, "test_native", [item], dialect="postgres")
    second = NativeSqlMigrationBackend(db2, "test_native", [item], dialect="postgres")

    def run_first():
        try:
            first.upgrade()
        except Exception as exc:
            errors.append(exc)

    thread = Thread(target=run_first)
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(RuntimeError, match="locked"):
            second.upgrade()
    finally:
        release.set()
        thread.join(timeout=5)
    try:
        assert not thread.is_alive() and not errors
        assert second.status()["current"] == ["001"]
        second.downgrade("base")
        assert second.status()["current"] == []
    finally:
        db1.close()
        db2.close()


def test_migrations_reject_undeclared_or_unsupported_dialects():
    db = SqlDatabase.sqlite(":memory:")
    try:
        for dialects in ((), ("postgres",)):
            item = NativeSqlMigration("001", "checksum", lambda tx: None, dialects=dialects)
            with pytest.raises(ValueError, match="dialect"):
                NativeSqlMigrationBackend(db, "example", [item], dialect="sqlite")
    finally:
        db.close()


def test_package_migration_registration_is_discovered_and_dependency_ordered(
    monkeypatch, tmp_path
):
    root = tmp_path / "migration_packages"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    for package_name, namespace, revision in (
        ("accounts", "accounts", "0001"),
        ("storage", "storage", "0001"),
        ("workspace", "workspace", "0001"),
        ("without_migrations", "without_migrations", None),
    ):
        package = root / package_name
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "main.py").write_text(
            f"class Package:\n    namespace = {namespace!r}\n"
            + (
                "    dependencies = ('storage',)\n"
                if namespace == "accounts"
                else "    dependencies = ('accounts', 'storage')\n"
                if namespace == "workspace"
                else ""
            ),
            encoding="utf-8",
        )
        if revision is not None:
            migration_package = package / "migrate"
            migration_package.mkdir()
            (migration_package / "__init__.py").write_text("", encoding="utf-8")
            (migration_package / f"{revision}_initial.py").write_text(
                f"revision = {revision!r}\n"
                "dialects = ('sqlite',)\n"
                "def upgrade(transaction):\n    pass\n",
                encoding="utf-8",
            )
    monkeypatch.syspath_prepend(str(tmp_path))

    from importlib import import_module

    packages = [
        import_module(f"migration_packages.{name}.main").Package
        for name in ("workspace", "accounts", "storage", "without_migrations")
    ]
    registry = build_native_sql_migration_registry(
        packages,
        "sqlite:///migrations.db",
    )

    ordered = registry.ordered()
    assert [source.name for source in ordered] == ["storage", "accounts", "workspace"]
    assert [[item.revision for item in source.backend.migrations] for source in ordered] == [
        ["0001"],
        ["0001"],
        ["0001"],
    ]
