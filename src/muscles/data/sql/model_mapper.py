"""Explicit mapping between Muscles models and SQL result records."""
from __future__ import annotations

from typing import Any, Generic, Iterable, Mapping, TypeVar

T = TypeVar("T")


class ModelMapper(Generic[T]):
    def __init__(self, model_type: type[T], fields: Mapping[str, str]):
        if not fields or len(set(fields.values())) != len(fields):
            raise ValueError("fields must contain unique record names")
        self.model_type = model_type
        self.fields = dict(fields)

    def from_record(self, record: Mapping[str, Any]) -> T:
        model = self.model_type(**{field: record[column] for field, column in self.fields.items()})
        unknown = self.fields.keys() - set(model.fields())
        if unknown:
            raise ValueError(f"Unknown model fields: {', '.join(sorted(unknown))}")
        return model

    def from_records(self, records: Iterable[Mapping[str, Any]]) -> list[T]:
        return [self.from_record(record) for record in records]

    def to_record(self, model: T) -> dict[str, Any]:
        if not isinstance(model, self.model_type):
            raise TypeError("model has an unexpected type")
        values = model.as_dict()
        return {column: values[field] for field, column in self.fields.items()}
