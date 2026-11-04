# SQL RC checklist

The SQL RC must pass the SQLite integration path before publication:

```bash
PYTHONPATH=../muscles/src:src python -m pytest -q
python -m build --wheel --sdist
muscles-data-sql doctor --url sqlite:///./app.db
```

The acceptance path covers mapping, CRUD, advanced queries, named
connections, commit/rollback, savepoints, retry and migration diagnostics.
Diagnostic output must always use a redacted DSN.
