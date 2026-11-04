import os
import subprocess
import sys


def test_sql_cli_smoke_with_core_on_pythonpath():
    env = {**os.environ, "PYTHONPATH": "../muscles/src:src"}
    process = subprocess.run(
        [sys.executable, "-m", "muscles.data.sql.cli", "inspect", "--url", "sqlite:///:memory:"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert process.returncode == 0
    assert "\"status\": \"ok\"" in process.stdout

