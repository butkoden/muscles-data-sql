import pytest


def test_text_wraps_sqlalchemy_statement():
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from muscles.data.sql import text

    statement = text("SELECT :value")

    assert isinstance(statement, sqlalchemy.sql.elements.TextClause)
    assert statement.text == "SELECT :value"


def test_text_reports_missing_optional_dependency(monkeypatch):
    import builtins
    from muscles.data.sql import text

    original_import = builtins.__import__

    def import_without_sqlalchemy(name, *args, **kwargs):
        if name == "sqlalchemy":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_sqlalchemy)

    with pytest.raises(ImportError, match=r"muscles-data-sql\[legacy\]"):
        text("SELECT 1")
