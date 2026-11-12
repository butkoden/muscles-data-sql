from pathlib import Path
import pytest

tomllib = pytest.importorskip("tomllib", reason="stdlib TOML parser requires Python 3.11")


def test_lightweight_install_and_optional_integration_dependencies():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
    assert "click>=8.1,<9.0" in project["dependencies"]
    extras = project["optional-dependencies"]
    assert "muscles-data>=0.2.3,<1.0.0" in extras["data"]
    assert "legacy" not in extras
    assert any(dependency.startswith("psycopg[") for dependency in extras["postgres"])
