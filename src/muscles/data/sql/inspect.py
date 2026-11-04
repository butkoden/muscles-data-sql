def inspect_sql_layer(engine_manager) -> dict:
    from time import perf_counter
    from sqlalchemy import inspect, text

    started = perf_counter()
    with engine_manager.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    latency_ms = round((perf_counter() - started) * 1000, 3)
    inspector = inspect(engine_manager.engine)
    tables = []
    for table_name in inspector.get_table_names():
        tables.append(
            {
                "name": table_name,
                "columns": [column["name"] for column in inspector.get_columns(table_name)],
                "indexes": [index["name"] for index in inspector.get_indexes(table_name)],
                "pk": inspector.get_pk_constraint(table_name).get("constrained_columns", []),
            }
        )
    return {
        "status": "ok",
        "dialect": engine_manager.engine.dialect.name,
        "driver": engine_manager.engine.dialect.driver,
        "latency_ms": latency_ms,
        "tables": tables,
    }
