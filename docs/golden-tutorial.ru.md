# Golden Tutorial (RU)

1. Установите пакет:
```bash
pip install -e ".[dev]"
```

2. Проверьте SQL слой:
```bash
muscles-data-sql doctor --url sqlite:///./app.db
```

3. Сгенерируйте SQL-ресурс:
```bash
muscles-data-sql generate resource bookings --sql
```

Миграции описывайте Python-модулями и регистрируйте в общем
`MigrationRegistry` из `muscles-data`; пример: `examples/native_migrations.py`.
