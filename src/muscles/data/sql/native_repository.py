"""Policy-aware operations over a native SQL transaction."""
from __future__ import annotations

from contextlib import contextmanager

from muscles.data.object_lifecycle import ObjectLifecycleEvent
from muscles.data.policies import DataOperationKind, DataOperationRequest, DataPredicate, DataPredicateOperator
from muscles.data.ports import SqlTransactionPort
from .native_policy import _identifier, compile_operation_scope, compile_policy_predicates, validate_create_predicates


class NativePolicyRepository:
    def __init__(self, transaction, table: str, *, policies, context, placeholder: str = "%s",
                 object_lifecycle=None, object_type: str | None = None):
        self.transaction = transaction
        self.table = table
        self.policies = policies
        self.context = context
        self.placeholder = placeholder
        if placeholder not in ("%s", "?"):
            raise ValueError("Unsupported SQL placeholder")
        if not callable(getattr(policies, "evaluate", None)):
            raise TypeError("A policy pipeline is required")
        if (object_lifecycle is None) != (object_type is None):
            raise ValueError("object_lifecycle and object_type must be provided together")
        if object_lifecycle is not None and not callable(getattr(transaction, "emit_object_event", None)):
            raise TypeError("Transaction must support object lifecycle events")
        self.object_lifecycle = object_lifecycle
        self.object_type = object_type
        _identifier(table)

    def _where(self, kind, *, uid=None, values=None, filters=(), extra_predicates=()):
        filters = tuple(filters)
        if not all(isinstance(item, DataPredicate) for item in filters):
            raise TypeError("Filters must contain DataPredicate values")
        if uid is not None:
            filters += (DataPredicate("uid", DataPredicateOperator.EQ, uid),)
        request = DataOperationRequest(kind=kind, target=self.table, context=self.context,
                                       filters=filters, values=values or {}, object_type=self.object_type)
        clause, params = compile_operation_scope(self.policies, request, target_alias="target",
                                                  placeholder=self.placeholder, extra_predicates=extra_predicates)
        return (" WHERE " + clause if clause else ""), params

    def list(self, *, limit: int = 100, offset: int = 0, order_by: str | None = None, filters=()):
        if type(limit) is not int or type(offset) is not int or limit < 0 or offset < 0:
            raise ValueError("Invalid pagination")
        where, params = self._where(DataOperationKind.READ, filters=filters)
        order = f' ORDER BY "target".{_identifier(order_by)}' if order_by is not None else ""
        sql = f'SELECT "target".* FROM {_identifier(self.table)} AS "target"{where}{order} LIMIT {self.placeholder} OFFSET {self.placeholder}'
        return self.transaction.fetch_all(sql, params + (limit, offset))

    def count(self, *, filters=()):
        where, params = self._where(DataOperationKind.READ, filters=filters)
        row = self.transaction.fetch_one(f'SELECT COUNT(*) AS total FROM {_identifier(self.table)} AS "target"{where}', params)
        return int(row["total"])

    def list_joined(self, related_table: str, *, target_field: str, related_field: str,
                    filters=(), related_filters=(), related_fields=(), limit: int = 100,
                    offset: int = 0, outer: bool = False, order_by: str | None = None,
                    related_object_type: str | None = None):
        """Return target rows and selected related fields with policies on both tables."""
        if type(limit) is not int or type(offset) is not int or limit < 0 or offset < 0:
            raise ValueError("Invalid pagination")
        target_where, target_params = self._where(DataOperationKind.READ, filters=filters)
        related_filters = tuple(related_filters)
        if self.object_type is not None and related_object_type is None:
            raise ValueError("Related object type is required for a scoped join")
        if not all(isinstance(item, DataPredicate) for item in related_filters):
            raise TypeError("Filters must contain DataPredicate values")
        request = DataOperationRequest(kind=DataOperationKind.READ, target=related_table,
                                       context=self.context, filters=related_filters, object_type=related_object_type)
        related_predicates = self.policies.evaluate(request)
        related_where, related_params = compile_policy_predicates((*related_filters, *related_predicates),
                                                                   target_alias="related", placeholder=self.placeholder)
        if type(outer) is not bool:
            raise ValueError("outer must be a boolean")
        related_fields = tuple(related_fields)
        if len(set(related_fields)) != len(related_fields):
            raise ValueError("Related fields must be unique")
        related_columns = "".join(
            f', "related".{_identifier(field)} AS {_identifier("related__" + field)}'
            for field in related_fields
        )
        join_kind = "LEFT JOIN" if outer else "JOIN"
        join_policy = f" AND ({related_where})" if related_where else ""
        order = f' ORDER BY "target".{_identifier(order_by)}' if order_by is not None else ""
        sql = (f'SELECT DISTINCT "target".*{related_columns} FROM {_identifier(self.table)} AS "target" '
               f'{join_kind} {_identifier(related_table)} AS "related" '
               f'ON "target".{_identifier(target_field)} = "related".{_identifier(related_field)}'
               f'{join_policy}{target_where}{order} LIMIT {self.placeholder} OFFSET {self.placeholder}')
        return self.transaction.fetch_all(sql, related_params + target_params + (limit, offset))

    def aggregate(self, function: str, field: str, *, filters=()):
        """Apply a supported aggregate after mandatory policy constraints."""
        allowed = {"min": "MIN", "max": "MAX", "sum": "SUM", "avg": "AVG"}
        if function not in allowed:
            raise ValueError("Unsupported aggregate function")
        column = _identifier(field)
        where, params = self._where(DataOperationKind.READ, filters=filters)
        row = self.transaction.fetch_one(
            f'SELECT {allowed[function]}("target".{column}) AS value '
            f'FROM {_identifier(self.table)} AS "target"{where}', params
        )
        return row["value"]

    def aggregate_grouped(self, function: str, field: str, *, group_by, filters=()):
        """Aggregate accessible rows by validated target columns."""
        allowed = {"min": "MIN", "max": "MAX", "sum": "SUM", "avg": "AVG"}
        if function not in allowed:
            raise ValueError("Unsupported aggregate function")
        if not isinstance(group_by, (tuple, list)) or not group_by or len(set(group_by)) != len(group_by):
            raise ValueError("group_by requires unique column names")
        columns = [f'"target".{_identifier(name)}' for name in group_by]
        where, params = self._where(DataOperationKind.READ, filters=filters)
        sql = (f'SELECT {", ".join(columns)}, {allowed[function]}("target".{_identifier(field)}) AS value '
               f'FROM {_identifier(self.table)} AS "target"{where} GROUP BY {", ".join(columns)}')
        return self.transaction.fetch_all(sql, params)

    def exists(self, *, filters=()):
        where, params = self._where(DataOperationKind.READ, filters=filters)
        row = self.transaction.fetch_one(
            f'SELECT 1 AS present FROM {_identifier(self.table)} AS "target"{where} LIMIT 1', params
        )
        return row is not None

    def get(self, uid):
        if uid is None:
            raise ValueError("Object identifier is required")
        where, params = self._where(DataOperationKind.READ, uid=uid)
        return self.transaction.fetch_one(f'SELECT "target".* FROM {_identifier(self.table)} AS "target"{where}', params)

    def delete(self, uid):
        if uid is None:
            raise ValueError("Object identifier is required")
        where, params = self._where(DataOperationKind.DELETE, uid=uid)
        count = self.transaction.execute(f'DELETE FROM {_identifier(self.table)} AS "target"{where}', params)
        if count and self.object_lifecycle is not None:
            event = ObjectLifecycleEvent("deleted", self.object_type, str(uid), self.context)
            self.transaction.emit_object_event(self.object_lifecycle, event)
        return count

    def create(self, values):
        return self._create(values)

    def _create(self, values, *, extra_predicates=()):
        if not isinstance(values, dict) or not values or values.get("uid") is None:
            raise ValueError("Create requires an object identifier")
        request = DataOperationRequest(kind=DataOperationKind.CREATE, target=self.table,
                                       context=self.context, values=values, object_type=self.object_type)
        validate_create_predicates(values, (*self.policies.evaluate(request), *extra_predicates))
        fields = [(_identifier(field), value) for field, value in values.items()]
        columns = ", ".join(field for field, _ in fields)
        placeholders = ", ".join(self.placeholder for _ in fields)
        count = self.transaction.execute(f'INSERT INTO {_identifier(self.table)} ({columns}) VALUES ({placeholders})',
                                         tuple(value for _, value in fields))
        if count and self.object_lifecycle is not None:
            event = ObjectLifecycleEvent("created", self.object_type, str(values["uid"]), self.context)
            self.transaction.emit_object_event(self.object_lifecycle, event)
        return count

    def bulk_create(self, rows):
        """Create all rows atomically using the same checks as create."""
        if not isinstance(rows, list):
            raise TypeError("Rows must be a list")
        if not rows:
            return 0
        self.transaction.execute("SAVEPOINT native_bulk_create")
        try:
            count = sum(self.create(row) for row in rows)
        except BaseException:
            self.transaction.execute("ROLLBACK TO SAVEPOINT native_bulk_create")
            self.transaction.execute("RELEASE SAVEPOINT native_bulk_create")
            raise
        self.transaction.execute("RELEASE SAVEPOINT native_bulk_create")
        return count

    def upsert(self, values):
        """Insert or update by uid; policy constraints on upsert fail closed."""
        if not isinstance(values, dict) or values.get("uid") is None:
            raise ValueError("Upsert requires an object identifier")
        uid = values["uid"]
        filters = (DataPredicate("uid", DataPredicateOperator.EQ, uid),)
        request = DataOperationRequest(kind=DataOperationKind.UPSERT, target=self.table,
                                       context=self.context, filters=filters, values=values, object_type=self.object_type)
        upsert_predicates = self.policies.evaluate(request)
        changes = {field: value for field, value in values.items() if field != "uid"}
        if not changes:
            raise ValueError("Upsert requires at least one field to update")
        self.transaction.execute("SAVEPOINT native_upsert")
        try:
            count = self._update(uid, changes, extra_predicates=upsert_predicates)
            if not count:
                count = self._create(values, extra_predicates=upsert_predicates)
        except BaseException:
            self.transaction.execute("ROLLBACK TO SAVEPOINT native_upsert")
            self.transaction.execute("RELEASE SAVEPOINT native_upsert")
            raise
        self.transaction.execute("RELEASE SAVEPOINT native_upsert")
        return count

    def bulk_update(self, rows):
        """Update rows atomically, enforcing the policy for each object."""
        if not isinstance(rows, list):
            raise TypeError("Rows must be a list")
        if not rows:
            return 0
        self.transaction.execute("SAVEPOINT native_bulk_update")
        try:
            count = 0
            for row in rows:
                if not isinstance(row, dict) or row.get("uid") is None:
                    raise ValueError("Each row requires an object identifier")
                count += self.update(row["uid"], {key: value for key, value in row.items() if key != "uid"})
        except BaseException:
            self.transaction.execute("ROLLBACK TO SAVEPOINT native_bulk_update")
            self.transaction.execute("RELEASE SAVEPOINT native_bulk_update")
            raise
        self.transaction.execute("RELEASE SAVEPOINT native_bulk_update")
        return count

    def bulk_delete(self, uids):
        """Delete objects atomically, enforcing policies and emitting events."""
        if not isinstance(uids, list):
            raise TypeError("Object identifiers must be a list")
        if not uids:
            return 0
        self.transaction.execute("SAVEPOINT native_bulk_delete")
        try:
            count = sum(self.delete(uid) for uid in uids)
        except BaseException:
            self.transaction.execute("ROLLBACK TO SAVEPOINT native_bulk_delete")
            self.transaction.execute("RELEASE SAVEPOINT native_bulk_delete")
            raise
        self.transaction.execute("RELEASE SAVEPOINT native_bulk_delete")
        return count

    def update(self, uid, values):
        return self._update(uid, values)

    def _update(self, uid, values, *, extra_predicates=()):
        if uid is None:
            raise ValueError("Object identifier is required")
        if not isinstance(values, dict) or not values or "uid" in values:
            raise ValueError("Update requires values and cannot change the identifier")
        fields = [(_identifier(field), value) for field, value in values.items()]
        where, params = self._where(DataOperationKind.UPDATE, uid=uid, values=values,
                                    extra_predicates=extra_predicates)
        assignments = ", ".join(f"{field} = {self.placeholder}" for field, _ in fields)
        sql = f'UPDATE {_identifier(self.table)} AS "target" SET {assignments}{where}'
        return self.transaction.execute(sql, tuple(value for _, value in fields) + params)


def native_repository_from_runtime(runtime, transaction, table: str, *, context, object_type: str | None = None,
                                   placeholder: str = "%s"):
    """Build a native repository using the runtime's policies and lifecycle."""
    lookup = getattr(runtime, "object_type_for_target", None)
    registered_type = lookup(table) if callable(lookup) else None
    if registered_type is not None:
        if object_type is not None and object_type != registered_type:
            raise ValueError("Object type does not match the registered data target")
        object_type = registered_type
    return NativePolicyRepository(transaction, table, policies=runtime.policies, context=context,
                                  placeholder=placeholder, object_type=object_type,
                                  object_lifecycle=runtime.object_lifecycle if object_type is not None else None)


@contextmanager
def native_repository_transaction(runtime, resource: str, table: str, *, context,
                                  object_type: str | None = None, placeholder: str = "%s"):
    """Open a runtime resource transaction with its policies and handlers."""
    sql = runtime.connect(resource, SqlTransactionPort)
    with sql.transaction() as transaction:
        yield native_repository_from_runtime(runtime, transaction, table, context=context,
                                             object_type=object_type, placeholder=placeholder)
