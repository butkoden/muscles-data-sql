from __future__ import annotations

from typing import Any

from muscles.data.policies import (
    DataOperationContext,
    DataOperationKind,
    DataOperationRequest,
    DataPolicyPipeline,
    DataPredicate,
    DataPredicateOperator,
)
from muscles.data.errors import DataPolicyDeniedError

from .query import FilterClause, JoinClause, QuerySpec


class SqlRepository:
    def __init__(
        self,
        session,
        table,
        *,
        policies: DataPolicyPipeline | None = None,
        context: DataOperationContext | None = None,
    ):
        self.session = session
        self.table = table
        self.policies = policies
        self.context = context or DataOperationContext()

    @property
    def target(self) -> str:
        return str(getattr(self.table, "fullname", self.table.name))

    def explain(
        self,
        kind: DataOperationKind,
        *,
        filters: tuple[DataPredicate, ...] = (),
        values: dict | None = None,
    ):
        """Describe policy constraints for a repository operation without SQL I/O."""
        filter_items = tuple(filters)
        request = DataOperationRequest(
            kind=kind,
            target=self.target,
            context=self.context,
            filters=filter_items,
            values=dict(values or {}),
        )
        predicates = self._policy_predicates(request)
        return {
            "operation": kind.value,
            "target": self.target,
            "filters": tuple(
                {"field": item.field, "operator": item.operator.value}
                for item in (*filter_items, *predicates)
            ),
        }

    def _policy_predicates(self, request: DataOperationRequest) -> tuple[DataPredicate, ...]:
        if self.policies is None:
            return ()
        return self.policies.evaluate(request)

    def _request(self, kind, *, filters=(), values=None) -> DataOperationRequest:
        return DataOperationRequest(
            kind=kind,
            target=self.target,
            context=self.context,
            filters=tuple(self._as_data_predicate(item) for item in filters),
            values=dict(values or {}),
        )

    @staticmethod
    def _as_data_predicate(item) -> DataPredicate:
        if isinstance(item, DataPredicate):
            return item
        return DataPredicate(
            field=item.field,
            operator=DataPredicateOperator(item.op.lower()),
            value=item.value,
        )

    def _assert_values_allowed(self, predicates: tuple[DataPredicate, ...], values: dict, *, require_fields: bool):
        for predicate in predicates:
            if predicate.field not in values:
                if require_fields:
                    raise DataPolicyDeniedError(
                        f"Create data must provide policy field '{predicate.field}'"
                    )
                continue
            if not _matches_predicate(values[predicate.field], predicate):
                raise DataPolicyDeniedError(
                    f"Write violates a data policy for field '{predicate.field}'"
                )

    def _append_policy_filters(self, spec: QuerySpec, predicates: tuple[DataPredicate, ...]) -> QuerySpec:
        if not predicates:
            return spec
        return QuerySpec(
            filters=list(spec.filters) + [
                FilterClause(field=item.field, op=item.operator.value, value=item.value)
                for item in predicates
            ],
            or_filters=list(spec.or_filters),
            joins=list(spec.joins),
            select_columns=list(spec.select_columns),
            order_by=list(spec.order_by),
            group_by=list(spec.group_by),
            having=list(spec.having),
            limit=spec.limit,
            offset=spec.offset,
            distinct=spec.distinct,
        )

    def create(self, data: dict):
        return self._create(data)

    def _create(self, data: dict, *, extra_predicates: tuple[DataPredicate, ...] = ()):
        from sqlalchemy import insert

        predicates = self._policy_predicates(
            self._request(DataOperationKind.CREATE, values=data)
        )
        predicates = (*predicates, *extra_predicates)
        self._assert_values_allowed(predicates, data, require_fields=True)
        result = self.session.execute(insert(self.table).values(**data))
        return result.inserted_primary_key

    def get(self, pk):
        from sqlalchemy import select

        pk_col = list(self.table.primary_key.columns)[0]
        filters = [FilterClause(field=pk_col.name, op="eq", value=pk)]
        predicates = self._policy_predicates(self._request(DataOperationKind.READ, filters=filters))
        stmt = select(self.table).where(pk_col == pk)
        for predicate in predicates:
            stmt = stmt.where(self._build_filter_expr(FilterClause(predicate.field, predicate.operator.value, predicate.value)))
        return self.session.execute(stmt).mappings().first()

    def list(self, limit: int = 100, offset: int = 0, order_by: Any = None):
        spec = QuerySpec(limit=limit, offset=offset, order_by=[order_by] if order_by is not None else [])
        predicates = self._policy_predicates(self._request(DataOperationKind.READ))
        stmt = self._build_select(self._append_policy_filters(spec, predicates))
        return self.session.execute(stmt).mappings().all()

    def find(self, filters: dict | None = None, limit: int = 100, offset: int = 0, order_by: Any = None):
        from sqlalchemy import select

        clauses = [FilterClause(field=field, op="eq", value=value) for field, value in (filters or {}).items()]
        spec = QuerySpec(filters=clauses, limit=limit, offset=offset, order_by=[order_by] if order_by is not None else [])
        predicates = self._policy_predicates(self._request(DataOperationKind.READ, filters=clauses))
        stmt = self._build_select(self._append_policy_filters(spec, predicates))
        return self.session.execute(stmt).mappings().all()

    def count(self, filters: dict | None = None) -> int:
        from sqlalchemy import func, select

        stmt = select(func.count()).select_from(self.table)
        filters = filters or {}
        clauses = [FilterClause(field=field, op="eq", value=value) for field, value in filters.items()]
        predicates = self._policy_predicates(self._request(DataOperationKind.READ, filters=clauses))
        for field, value in filters.items():
            stmt = stmt.where(getattr(self.table.c, field) == value)
        for predicate in predicates:
            stmt = stmt.where(self._build_filter_expr(FilterClause(predicate.field, predicate.operator.value, predicate.value)))
        return int(self.session.execute(stmt).scalar_one())

    def exists(self, filters: dict) -> bool:
        return self.count(filters) > 0

    def update(self, pk, data: dict):
        return self._update(pk, data)

    def _update(self, pk, data: dict, *, extra_predicates: tuple[DataPredicate, ...] = ()):
        from sqlalchemy import update

        pk_col = list(self.table.primary_key.columns)[0]
        filters = [FilterClause(field=pk_col.name, op="eq", value=pk)]
        predicates = self._policy_predicates(
            self._request(DataOperationKind.UPDATE, filters=filters, values=data)
        )
        predicates = (*predicates, *extra_predicates)
        self._assert_values_allowed(predicates, data, require_fields=False)
        stmt = update(self.table).where(pk_col == pk)
        for predicate in predicates:
            stmt = stmt.where(self._build_filter_expr(FilterClause(predicate.field, predicate.operator.value, predicate.value)))
        stmt = stmt.values(**data)
        return self.session.execute(stmt).rowcount

    def delete(self, pk):
        from sqlalchemy import delete

        pk_col = list(self.table.primary_key.columns)[0]
        filters = [FilterClause(field=pk_col.name, op="eq", value=pk)]
        predicates = self._policy_predicates(self._request(DataOperationKind.DELETE, filters=filters))
        stmt = delete(self.table).where(pk_col == pk)
        for predicate in predicates:
            stmt = stmt.where(self._build_filter_expr(FilterClause(predicate.field, predicate.operator.value, predicate.value)))
        return self.session.execute(stmt).rowcount

    def query(self, spec: QuerySpec):
        predicates = self._policy_predicates(
            self._request(DataOperationKind.READ, filters=spec.filters + spec.or_filters)
        )
        stmt = self._build_select(self._append_policy_filters(spec, predicates))
        return self.session.execute(stmt).mappings().all()

    def first(self, spec: QuerySpec):
        first_spec = QuerySpec(
            filters=list(spec.filters),
            or_filters=list(spec.or_filters),
            joins=list(spec.joins),
            select_columns=list(spec.select_columns),
            order_by=list(spec.order_by),
            group_by=list(spec.group_by),
            having=list(spec.having),
            limit=1,
            offset=spec.offset,
            distinct=spec.distinct,
        )
        rows = self.query(first_spec)
        return rows[0] if rows else None

    def scalar(self, spec: QuerySpec):
        predicates = self._policy_predicates(self._request(DataOperationKind.READ, filters=spec.filters + spec.or_filters))
        stmt = self._build_select(self._append_policy_filters(spec, predicates))
        return self.session.execute(stmt).scalar()

    def aggregate(self, spec: QuerySpec):
        predicates = self._policy_predicates(self._request(DataOperationKind.READ, filters=spec.filters + spec.or_filters))
        stmt = self._build_select(self._append_policy_filters(spec, predicates))
        return self.session.execute(stmt).mappings().all()

    def bulk_create(self, rows: list[dict]) -> int:
        from sqlalchemy import insert

        if not rows:
            return 0
        if self.policies is not None:
            with self.session.begin_nested():
                for row in rows:
                    self.create(row)
            return len(rows)
        self.session.execute(insert(self.table), rows)
        return len(rows)

    def bulk_update(self, pk_field: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        def apply_updates():
            updated_count = 0
            for row in rows:
                pk = row.get(pk_field)
                if pk is None:
                    continue
                data = {k: v for k, v in row.items() if k != pk_field}
                if not data:
                    continue
                updated_count += int(self.update(pk, data) or 0)
            return updated_count

        if self.policies is not None:
            with self.session.begin_nested():
                updated = apply_updates()
        else:
            updated = apply_updates()
        return updated

    def upsert(self, row: dict, conflict_fields: list[str]):
        from sqlalchemy import inspect
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from sqlalchemy.exc import OperationalError

        if not self._has_unique_conflict_constraint(conflict_fields):
            raise ValueError(
                "conflict_fields must match a PRIMARY KEY or UNIQUE constraint for safe upsert fallback"
            )
        if any(field not in row for field in conflict_fields):
            raise ValueError("upsert requires all conflict fields in the row")

        # Native upsert statements bypass policy-filtered update predicates.
        # Under policies use the same constrained read/update/create operations
        # as the rest of the repository instead.
        if self.policies is not None:
            filters = {field: row[field] for field in conflict_fields if field in row}
            filter_clauses = [FilterClause(field=field, op="eq", value=value) for field, value in filters.items()]
            upsert_predicates = self._policy_predicates(
                self._request(DataOperationKind.UPSERT, filters=filter_clauses, values=row)
            )
            self._assert_values_allowed(upsert_predicates, row, require_fields=True)
            existing = self.find(filters=filters, limit=1) if filters else []
            if existing:
                self._assert_values_allowed(upsert_predicates, dict(existing[0]), require_fields=True)
                pk_col = list(self.table.primary_key.columns)[0].name
                pk_val = existing[0][pk_col]
                return self._update(
                    pk_val,
                    {k: v for k, v in row.items() if k != pk_col},
                    extra_predicates=upsert_predicates,
                )
            self._create(row, extra_predicates=upsert_predicates)
            return 1

        dialect_name = inspect(self.session.bind).dialect.name
        if dialect_name == "sqlite":
            stmt = sqlite_insert(self.table).values(**row)
            update_data = {k: v for k, v in row.items() if k not in conflict_fields}
            stmt = stmt.on_conflict_do_update(index_elements=conflict_fields, set_=update_data)
            try:
                self.session.execute(stmt)
                return 1
            except OperationalError:
                # Fallback for tables where conflict_fields are not backed by unique/pk constraints.
                pass

        filters = {field: row[field] for field in conflict_fields if field in row}
        if filters:
            existing = self.find(filters=filters, limit=1)
            if existing:
                pk_col = list(self.table.primary_key.columns)[0].name
                pk_val = existing[0][pk_col]
                return self.update(pk_val, {k: v for k, v in row.items() if k != pk_col})

        pk_col = list(self.table.primary_key.columns)[0].name
        pk_val = row.get(pk_col)
        if pk_val is not None and self.get(pk_val):
            return self.update(pk_val, {k: v for k, v in row.items() if k != pk_col})
        self.create(row)
        return 1

    def _has_unique_conflict_constraint(self, conflict_fields: list[str]) -> bool:
        from sqlalchemy import UniqueConstraint

        if not conflict_fields:
            return False
        conflict_set = set(conflict_fields)
        pk_cols = {column.name for column in self.table.primary_key.columns}
        if conflict_set == pk_cols:
            return True

        for constraint in getattr(self.table, "constraints", []):
            columns = getattr(constraint, "columns", None)
            if columns is None:
                continue
            if isinstance(constraint, UniqueConstraint) and {column.name for column in columns} == conflict_set:
                return True

        for index in getattr(self.table, "indexes", []):
            if getattr(index, "unique", False) and {column.name for column in index.columns} == conflict_set:
                return True

        return False

    def _build_select(self, spec: QuerySpec):
        from sqlalchemy import and_, or_, select

        columns = spec.select_columns or [self.table]
        stmt = select(*columns)
        if spec.distinct:
            stmt = stmt.distinct()

        for join in spec.joins:
            if join.is_outer:
                stmt = stmt.outerjoin(join.table, join.on)
            else:
                stmt = stmt.join(join.table, join.on)

        if spec.filters:
            stmt = stmt.where(and_(*[self._build_filter_expr(clause) for clause in spec.filters]))
        if spec.or_filters:
            stmt = stmt.where(or_(*[self._build_filter_expr(clause) for clause in spec.or_filters]))
        if spec.group_by:
            stmt = stmt.group_by(*spec.group_by)
        if spec.having:
            stmt = stmt.having(*spec.having)
        if spec.order_by:
            stmt = stmt.order_by(*spec.order_by)
        if spec.limit is not None:
            stmt = stmt.limit(spec.limit)
        if spec.offset:
            stmt = stmt.offset(spec.offset)
        return stmt

    def _build_filter_expr(self, clause: FilterClause):
        column = getattr(self.table.c, clause.field)
        op = clause.op.lower()
        if op == "eq":
            return column == clause.value
        if op == "ne":
            return column != clause.value
        if op == "gt":
            return column > clause.value
        if op == "gte":
            return column >= clause.value
        if op == "lt":
            return column < clause.value
        if op == "lte":
            return column <= clause.value
        if op == "in":
            return column.in_(clause.value or [])
        if op == "like":
            return column.like(clause.value)
        if op == "is_null":
            return column.is_(None) if clause.value else column.is_not(None)
        raise ValueError(f"Unsupported filter operator: {clause.op}")


def _matches_predicate(actual: Any, predicate: DataPredicate) -> bool:
    """Evaluate supported scalar constraints before inserting/changing rows."""
    operator = predicate.operator.value
    expected = predicate.value
    try:
        if operator in ("eq", "ne") and expected is None:
            return (actual is None) if operator == "eq" else (actual is not None)
        if actual is None and operator != "is_null":
            return False
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        if operator == "gt":
            return actual is not None and actual > expected
        if operator == "gte":
            return actual is not None and actual >= expected
        if operator == "lt":
            return actual is not None and actual < expected
        if operator == "lte":
            return actual is not None and actual <= expected
        if operator == "in":
            return actual in (expected or [])
        if operator == "is_null":
            return (actual is None) is bool(expected)
    except (TypeError, ValueError):
        return False
    raise DataPolicyDeniedError(f"Policy operator '{operator}' cannot be validated against write data")
