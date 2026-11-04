import json
from pathlib import Path

from click.testing import CliRunner

from muscles.data.sql.cli import main


def test_doctor_and_inspect():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--url", "sqlite:///:memory:"])
    assert result.exit_code == 0
    assert json.loads(result.output)["status"] == "ok"

    result2 = runner.invoke(main, ["inspect", "--url", "sqlite:///:memory:"])
    assert result2.exit_code == 0
    assert json.loads(result2.output)["dialect"] == "sqlite"


def test_generate_sql_resource(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(tmp_path)):
        gen = runner.invoke(main, ["generate", "resource", "booking", "--sql"])
        assert gen.exit_code == 0
        generated = Path("generated") / "booking.py"
        assert generated.exists()
        assert "sql_enabled = True" in generated.read_text(encoding="utf-8")
