from typing import Any

TYPE_MAP: dict[str, str] = {
    "String": "String",
    "Text": "Text",
    "Integer": "Integer",
    "Numeric": "Numeric",
    "Float": "Float",
    "Boolean": "Boolean",
    "Date": "Date",
    "DateTime": "DateTime",
}


def _sa():
    from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, MetaData, Numeric, String, Table, Text

    return {
        "Boolean": Boolean,
        "Column": Column,
        "Date": Date,
        "DateTime": DateTime,
        "Float": Float,
        "Integer": Integer,
        "MetaData": MetaData,
        "Numeric": Numeric,
        "String": String,
        "Table": Table,
        "Text": Text,
    }


def _resolve_type(column_type: Any):
    sa = _sa()
    if isinstance(column_type, type):
        name = column_type.__name__
    else:
        name = getattr(column_type, "__class__", type(column_type)).__name__
    return sa.get(TYPE_MAP.get(name, "String"), sa["String"])


def map_model(model_cls: type, table_name: str | None = None):
    sa = _sa()
    metadata = sa["MetaData"]()
    columns = []
    for field_name, field_def in model_cls.__dict__.items():
        if field_name.startswith("_"):
            continue
        field_type = getattr(field_def, "field_type", None)
        if field_type is None:
            continue
        sql_type = _resolve_type(field_type)
        is_pk = bool(getattr(field_def, "primary_key", False))
        nullable = bool(getattr(field_def, "nullable", not is_pk))
        autoincrement = bool(
            getattr(field_def, "autoincrement", False)
            or (is_pk and sql_type is sa["Integer"])
        )
        columns.append(
            sa["Column"](
                field_name,
                sql_type,
                primary_key=is_pk,
                nullable=nullable,
                autoincrement=autoincrement,
            )
        )

    if not columns:
        raise ValueError(f"No mappable columns found in model {model_cls.__name__}")
    return sa["Table"](table_name or model_cls.__name__.lower(), metadata, *columns, extend_existing=True)
