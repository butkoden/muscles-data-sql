"""The native SQL package must not depend on SQLAlchemy."""

import ast
from pathlib import Path
import pytest

tomllib = pytest.importorskip("tomllib", reason="stdlib TOML parser requires Python 3.11")


def test_package_does_not_import_sqlalchemy():
    source = Path(__file__).resolve().parents[1] / "src" / "muscles" / "data" / "sql"
    imports = []
    for path in source.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(module == "sqlalchemy" or module.startswith("sqlalchemy.") for module in modules):
                imports.append(f"{path.relative_to(source)}:{node.lineno}")
    assert not imports, "SQLAlchemy imports remain: " + ", ".join(imports)


def test_package_dependencies_do_not_include_sqlalchemy():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        dependencies.extend(group)
    assert not [dependency for dependency in dependencies if dependency.lower().startswith("sqlalchemy")]
