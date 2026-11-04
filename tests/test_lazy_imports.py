import subprocess
import sys
import os


def test_import_muscles_data_sql_does_not_eager_import_sqlalchemy():
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import muscles.data.sql, sys; print('sqlalchemy' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert process.returncode == 0
    assert process.stdout.strip() == "False"


def test_public_exports_are_importable():
    from muscles.data.sql import DatabaseConfig
    from muscles.data.sql import EngineManager
    from muscles.data.sql import FilterClause
    from muscles.data.sql import JoinClause
    from muscles.data.sql import QuerySpec
    from muscles.data.sql import SqlConnectionConfig
    from muscles.data.sql import SqlConnectionRegistry
    from muscles.data.sql import SqlRepository
    from muscles.data.sql import UnitOfWork
    from muscles.data.sql import map_model

    assert DatabaseConfig.__name__ == "DatabaseConfig"
    assert EngineManager.__name__ == "EngineManager"
    assert FilterClause.__name__ == "FilterClause"
    assert JoinClause.__name__ == "JoinClause"
    assert QuerySpec.__name__ == "QuerySpec"
    assert SqlConnectionConfig.__name__ == "SqlConnectionConfig"
    assert SqlConnectionRegistry.__name__ == "SqlConnectionRegistry"
    assert SqlRepository.__name__ == "SqlRepository"
    assert UnitOfWork.__name__ == "UnitOfWork"
    assert callable(map_model)
