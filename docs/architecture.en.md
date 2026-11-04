# Muscles SQL Architecture

Muscles SQL is a dedicated SQL data layer:
- `config.py`: database config
- `engine.py`: engine/session factory and pooling
- `mapping.py`: Muscles Model/Column to SQL table mapping
- `query.py`: declarative query contract (`QuerySpec`, filters, joins)
- `repository.py`: CRUD + advanced query/aggregate/upsert/bulk API
- `uow.py`: Unit of Work, nested transaction, retry helpers
- `native_migrations.py`: native SQL migrations with a checksummed journal
- `inspect.py`: inspect/doctor contract with latency/schema diagnostics
- `cli.py`: AI-first commands (`doctor`, `inspect`, `generate --sql`); applications run migrations through the shared registry
- compatible with core DI optimization (`muscles#36`): sql CLI/runtime path has no behavior regression.
