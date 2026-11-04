# Muscles SQL examples

From `projects/libraries/muscles-data-sql`:

```bash
export PYTHONPATH="src:../muscles/src:../muscles-data/src"
python3 examples/native_resources.py
python3 examples/queries_and_mapper.py
python3 examples/repository_policies.py
python3 examples/native_migrations.py
```

These demonstrate named resources and driver registration, SQL JOIN/model
mapping and transactions, and repository operations with policies. The last
example requires `muscles-data-sql[legacy]` dependencies.
The fourth covers package migration discovery, planning, upgrade and rollback.

See the [Muscles Data examples](../../muscles-data/examples/README.en.md) for
the full port, SQL bridge and external adapter index.
