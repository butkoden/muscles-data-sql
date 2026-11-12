# SQL: политики доступа и транзакционные события

`muscles-data-sql` предоставляет нативный DB-API адаптер для SQLite и PostgreSQL. Для защищённых операций используйте `NativePolicyRepository` или его фабрики из `DataRuntime`. SQLAlchemy для этого не требуется. Контракты политик, жизненного цикла и смысл слабых связей описаны в `muscles-data/docs/data-policies.ru.md`.

## Запустите полный пример

[examples/scoped_lifecycle.py](../examples/scoped_lifecycle.py) создаёт две области, документ и таблицу ссылок, проверяет изоляцию, откат при ошибке обработчика и удаление ссылок. Это локальный пример на SQLite; он не устанавливает пакеты приложения и не обращается к рабочей базе.

Из каталога `muscles-data-sql`, в окружении с установленными `muscles-data` и `muscles-data-sql`:

```bash
python examples/scoped_lifecycle.py
# Scoped access and transactional lifecycle: OK
```

Реальное приложение обязано проверить пользователя и его членство в области **до** формирования контекста. Пример намеренно не реализует HTTP-аутентификацию, роли и интерфейс управления ссылками.

## Подключение в приложении

Настройте общий runtime один раз при запуске приложения, а не на каждый запрос. В Muscles-приложении используйте уже существующий `app.resource`; отдельный runtime ниже показан для автономного скрипта:

```python
from muscles.data.catalog import DataAdapterCatalog
from muscles.data.config import DataConfig
from muscles.data.runtime import DataRuntime
from muscles.data.sql.data_adapter import NativeSqlFactory

catalog = DataAdapterCatalog.with_defaults()
catalog.register(NativeSqlFactory())
config = DataConfig.from_raw({"resources": {
    "database": {"type": "native_sql", "driver": "sqlite", "path": ":memory:"},
}})
runtime = DataRuntime(config=config, catalog=catalog)
runtime.register_object_type("documents", "demo:document", owner_field="owner_uid")
# При завершении приложения: runtime.close()
```

Для PostgreSQL настройте ресурс `native_sql` с драйвером `postgres` и URL через конфигурацию приложения; не храните пароль в примерах или репозитории. Проверка и создание схемы — отдельная задача миграций. В примере выше `owner_field` — декларация метаданных, а не автоматическая SQL-проверка владельца.

## Политика, контекст и CRUD

Зарегистрируйте политику `ScopePolicy` и обработчик `maintain_links` из полного примера в `runtime.policies` и `runtime.object_lifecycle`. Фабрика сама определит зарегистрированный тип таблицы и передаст текущие политики и обработчики:

```python
from muscles.data.policies import DataOperationContext
from muscles.data.sql import native_repository_transaction

# Контекст создаётся из уже проверенной сессии и выбранной области.
context = DataOperationContext(
    subject_type="user", subject_uid="user-1", attributes={"scope_id": "one"},
)
with native_repository_transaction(
    runtime, "database", "documents", context=context, placeholder="?",
) as repo:
    repo.create({"uid": "doc-1", "title": "Пример"})
    document = repo.get("doc-1")
    rows = repo.list(limit=20, offset=0, order_by="uid")
    count = repo.count()
    changed = repo.update("doc-1", {"title": "Новое название"})
    deleted = repo.delete("doc-1")
```

Здесь `?` — placeholder SQLite; для PostgreSQL используйте `%s`. Пример предполагает зарегистрированную политику, обработчик и созданные таблицы. `get` вернёт `None`, если строка отсутствует или скрыта политикой; `update` и `delete` вернут число затронутых строк, включая `0`. Явный отказ политики вызывает `DataPolicyDeniedError`; не превращайте любую ошибку базы в «объект не найден».

`list` и `count` применяют ограничения до пагинации и подсчёта. Поддерживаются `exists`, `aggregate`, `aggregate_grouped`, `list_joined`, `bulk_create`, `bulk_update` и `upsert`. У массовых операций и upsert есть проверки каждой записи и savepoint; общий успех фиксируется внешней транзакцией. Не считайте savepoint отдельным коммитом. Для `upsert` проверяются условия операции и соответствующей ветки create/update; предикаты, невозможные для проверки при создании, отклоняются. В демонстрационной политике upsert запрещён явно.

## Связанные таблицы и выборка

`DataRelatedExists` позволяет применить проверку таблицы связей к строке документа без зависимости пакета документа от пакета, который управляет областью. Для примера см. `ScopePolicy` в файле примера. Обработчик создаёт связь после вставки, поэтому на `CREATE` политика не должна требовать заранее существующую ссылку.

Для объединения используйте `list_joined`: он отдельно применяет политику к основной и присоединяемой таблицам. При зарегистрированном типе основного объекта передайте `related_object_type` для связанного. `related_fields` возвращаются с префиксом `related__`:

```python
rows = repo.list_joined(
    "categories", target_field="category_uid", related_field="uid",
    related_object_type="demo:category", related_fields=("name",),
    limit=20, offset=0, outer=True,
)
```

Это отдельный пример: для него необходимы таблица `categories`, поле `category_uid` и зарегистрированные правила для обоих типов. Не передавайте имена таблиц или полей напрямую из непроверенного пользовательского ввода. Поддерживаются только возможности и операторы, реализованные адаптером; неподдерживаемые условия отклоняются. Для связей многие-ко-многим убедитесь, что API приложения проверяет доступ к каждому связываемому объекту.

## Своя транзакция и прямой SQL

Если транзакция уже открыта, используйте `native_repository_from_runtime(runtime, tx, "documents", context=context, placeholder="?")`. Несколько операций в ней зафиксируются или откатятся вместе. Для `object_type` фабрика использует регистрацию runtime; явно переданный конфликтующий тип приводит к ошибке.

Прямые `tx.execute`, `fetch_one` и `fetch_all` **не применяют политики и не публикуют события автоматически**. Они подходят для доверенных миграций, обслуживающего кода и обработчиков, но не для незащищённых CRUD-запросов. Для особых SQL-запросов `compile_operation_scope` возвращает параметризованное условие и параметры; вызывающий код обязан применить условие к фактическому запросу, в том числе ко всем связанным данным. Сам вызов компилятора ничего не защищает. Проверка `CREATE` выполняется отдельно через `validate_create_predicates`; не подставляйте пользовательский SQL вместо предикатов. Если нужна самостоятельная отправка события, вызывайте `tx.emit_object_event(runtime.object_lifecycle, event)` внутри транзакции и обеспечьте те же проверки и семантику, что у репозитория.

Например, для доверенного составного запроса к PostgreSQL:

```python
from muscles.data.policies import DataOperationKind, DataOperationRequest
from muscles.data.sql.native_policy import compile_operation_scope

operation = DataOperationRequest(
    DataOperationKind.READ, "documents", context, object_type="demo:document",
)
clause, parameters = compile_operation_scope(
    runtime.policies, operation, target_alias="d", placeholder="%s",
)
# "d" — тот же фиксированный alias, который передан компилятору.
query = 'SELECT "d".* FROM "documents" AS "d"'
if clause:
    query += f" WHERE {clause}"
rows = tx.fetch_all(query, parameters)
```

Пример предполагает открытую транзакцию `tx` и зарегистрированную политику. Если в запрос добавляются `JOIN`, агрегаты или ограничения пользователя, примените политики ко всем затронутым объектам и сохраните порядок параметров. Приоритетно используйте готовые методы репозитория: они выполняют эти проверки для поддерживаемых операций.

## Миграция существующих данных

Зарегистрированные политики и события не создают задним числом ссылки для существующих объектов. Приложение должно перенести и проверить их до включения ограничения видимости; используйте транзакции, проверки ссылочной целостности и повторяемый перенос. Удаление объекта должно удалять его ссылки в той же транзакции. Не удаляйте исходные данные до проверки результата.

При согласованном изменении уже применённой миграции `compatible_checksums` может содержать кортеж **точных** старых контрольных сумм. Это средство для проверенной совместимости путей установки, а не способ игнорировать произвольное изменение истории; другие суммы и изменение файла после обнаружения отклоняются. Подробности — в [README](../README.md#native-migrations-in-muscles-applications).
