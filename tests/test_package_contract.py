from pathlib import Path
import pytest

tomllib = pytest.importorskip("tomllib", reason="stdlib TOML parser requires Python 3.11")


def test_lightweight_install_and_optional_integration_dependencies():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
    extras = project["optional-dependencies"]
    assert "muscles-data>=0.2.0,<1.0.0" in extras["data"]
    assert "muscles-data>=0.2.0,<1.0.0" in extras["legacy"]
    assert any(dependency.startswith("psycopg[") for dependency in extras["postgres"])
