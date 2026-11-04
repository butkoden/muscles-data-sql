# Примеры Muscles SQL

Из каталога `projects/libraries/muscles-data-sql`:

```bash
export PYTHONPATH="src:../muscles/src:../muscles-data/src"
python3 examples/native_resources.py
python3 examples/queries_and_mapper.py
python3 examples/repository_policies.py
python3 examples/native_migrations.py
```

Первый пример показывает ресурсы и регистрацию драйвера, второй — SQL JOIN,
mapper и транзакции, третий — операции репозитория и политики. Для третьего
примера нужны зависимости `muscles-data-sql[legacy]`.
Четвёртый показывает обнаружение миграций пакета, план, применение и откат.

Полный список портов, SQL-моста и адаптеров внешних сервисов находится в
[примерах Muscles Data](../../muscles-data/examples/README.md).
