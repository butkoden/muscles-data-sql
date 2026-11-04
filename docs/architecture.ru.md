# Архитектура Muscles SQL

Muscles SQL строится как отдельный слой данных:
- `config.py` — конфигурация БД
- `engine.py` — engine/session factory
- `mapping.py` — преобразование Muscles Model/Column в SQL table
- `query.py` — декларативный контракт запросов (`QuerySpec`, filters, joins)
- `repository.py` — CRUD + advanced query/aggregate/upsert/bulk API
- `uow.py` — Unit of Work, nested transaction, retry helpers
- `native_migrations.py` — нативные SQL-миграции с журналом, проверкой контрольных сумм
  и единым сборщиком реестра для установленных пакетов Muscles
- `inspect.py` — контракт диагностики `inspect`/`doctor` с latency/schema отчётом
- `cli.py` — AI-first CLI (`doctor`, `inspect`, `generate --sql`); миграции выполняются общим CLI приложения
- совместим с оптимизацией DI из core (`muscles#36`): без регрессий поведения SQL CLI/runtime path.
