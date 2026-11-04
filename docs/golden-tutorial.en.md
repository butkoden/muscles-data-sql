# Golden Tutorial (EN)

1. Install package:
```bash
pip install -e ".[dev]"
```

2. Validate SQL layer:
```bash
muscles-data-sql doctor --url sqlite:///./app.db
```

3. Generate SQL resource:
```bash
muscles-data-sql generate resource bookings --sql
```

Define migrations as Python modules and register them with the shared
`MigrationRegistry` from `muscles-data`; see `examples/native_migrations.py`.
