"""Compile policy predicates into parameterized native SQL fragments."""

from __future__ import annotations

import re

from muscles.data.errors import DataPolicyDeniedError, DataUnsupportedOperationError
from muscles.data.policies import DataOperationKind, DataPredicateOperator, DataRelatedExists, like_matches, like_regex_body


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_create_predicates(values, predicates):
    """Check supported scalar constraints against an object before insertion."""
    for predicate in predicates:
        if predicate.field not in values:
            raise DataPolicyDeniedError("Create data lacks a field required by policy")
        actual, expected, op = values[predicate.field], predicate.value, predicate.operator
        if op is DataPredicateOperator.IS_NULL:
            if type(expected) is not bool:
                raise ValueError("IS_NULL requires a boolean")
            allowed = (actual is None) is expected
        elif op in (DataPredicateOperator.EQ, DataPredicateOperator.NE) and expected is None:
            allowed = (actual is None) if op is DataPredicateOperator.EQ else (actual is not None)
        elif op is DataPredicateOperator.IN:
            if not isinstance(expected, (tuple, list)):
                raise ValueError("IN requires a list or tuple")
            allowed = actual is not None and any(type(actual) is type(item) and actual == item for item in expected)
        elif op is DataPredicateOperator.LIKE:
            allowed = like_matches(actual, expected)
        elif op in (DataPredicateOperator.EQ, DataPredicateOperator.NE, DataPredicateOperator.GT,
                    DataPredicateOperator.GTE, DataPredicateOperator.LT, DataPredicateOperator.LTE):
            if actual is None or expected is None or type(actual) is not type(expected):
                allowed = False
            else:
                try:
                    allowed = {DataPredicateOperator.EQ: lambda: actual == expected,
                               DataPredicateOperator.NE: lambda: actual != expected,
                               DataPredicateOperator.GT: lambda: actual > expected,
                               DataPredicateOperator.GTE: lambda: actual >= expected,
                               DataPredicateOperator.LT: lambda: actual < expected,
                               DataPredicateOperator.LTE: lambda: actual <= expected}[op]()
                except TypeError:
                    allowed = False
        else:
            raise DataUnsupportedOperationError("Unsupported create policy condition")
        if not allowed:
            raise DataPolicyDeniedError("Create data violates a data policy")


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid SQL identifier")
    return '"' + value + '"'


def compile_related_exists(predicate, *, target_alias: str, placeholder: str = "%s"):
    """Return a parameterized EXISTS fragment and its parameters."""
    if placeholder not in ("%s", "?"):
        raise ValueError("Unsupported SQL placeholder")
    if predicate.operator is not DataPredicateOperator.RELATED_EXISTS or not isinstance(predicate.value, DataRelatedExists):
        raise DataUnsupportedOperationError("Expected a related-record condition")
    related = predicate.value
    parts = [
        f'{_identifier("policy_link")}.{_identifier(related.related_field)} = '
        f'{_identifier(target_alias)}.{_identifier(predicate.field)}'
    ]
    parameters = []
    for field, value in related.filters.items():
        column = f'{_identifier("policy_link")}.{_identifier(field)}'
        if value is None:
            parts.append(f"{column} IS NULL")
        else:
            parts.append(f"{column} = {placeholder}")
            parameters.append(value)
    sql = f'SELECT 1 FROM {_identifier(related.table)} AS {_identifier("policy_link")} WHERE ' + " AND ".join(parts)
    return f"EXISTS ({sql})", tuple(parameters)


def compile_policy_predicates(predicates, *, target_alias: str, placeholder: str = "%s"):
    """Compile mandatory policy conditions; unsupported operators fail closed."""
    if placeholder not in ("%s", "?"):
        raise ValueError("Unsupported SQL placeholder")
    clauses, parameters = [], []
    operators = {
        DataPredicateOperator.EQ: "=", DataPredicateOperator.NE: "<>",
        DataPredicateOperator.GT: ">", DataPredicateOperator.GTE: ">=",
        DataPredicateOperator.LT: "<", DataPredicateOperator.LTE: "<=",
    }
    for predicate in predicates:
        if predicate.operator is DataPredicateOperator.RELATED_EXISTS:
            clause, values = compile_related_exists(predicate, target_alias=target_alias, placeholder=placeholder)
            clauses.append(clause)
            parameters.extend(values)
            continue
        column = f'{_identifier(target_alias)}.{_identifier(predicate.field)}'
        if predicate.operator is DataPredicateOperator.IS_NULL:
            if type(predicate.value) is not bool:
                raise ValueError("IS_NULL requires a boolean")
            clauses.append(f"{column} IS {'NULL' if predicate.value else 'NOT NULL'}")
        elif predicate.operator in (DataPredicateOperator.EQ, DataPredicateOperator.NE) and predicate.value is None:
            clauses.append(f"{column} IS {'NULL' if predicate.operator is DataPredicateOperator.EQ else 'NOT NULL'}")
        elif predicate.operator is DataPredicateOperator.IN:
            if not isinstance(predicate.value, (list, tuple)):
                raise ValueError("IN requires a list or tuple")
            if not predicate.value:
                clauses.append("1 = 0")
            else:
                clauses.append(f"{column} IN ({', '.join(placeholder for _ in predicate.value)})")
                parameters.extend(predicate.value)
        elif predicate.operator is DataPredicateOperator.LIKE:
            like_regex_body(predicate.value)
            if placeholder == "?":
                replacements = {"%": "*", "_": "?", "*": "[*]", "?": "[?]", "[": "[[]"}
                pattern = "".join(replacements.get(char, char) for char in predicate.value)
                clauses.append(f"{column} GLOB {placeholder}")
                parameters.append(pattern)
            else:
                clauses.append(f"{column} LIKE {placeholder} ESCAPE ''")
                parameters.append(predicate.value)
        elif predicate.operator in operators:
            clauses.append(f"{column} {operators[predicate.operator]} {placeholder}")
            parameters.append(predicate.value)
        else:
            raise DataUnsupportedOperationError("Unsupported native SQL policy condition")
    return " AND ".join(f"({clause})" for clause in clauses), tuple(parameters)


def compile_operation_scope(policies, operation, *, target_alias: str, placeholder: str = "%s", extra_predicates=()):
    """Evaluate policy and compile an AND condition for a structured SQL operation."""
    if operation.kind not in (DataOperationKind.READ, DataOperationKind.UPDATE, DataOperationKind.DELETE):
        raise DataUnsupportedOperationError("Only read, update and delete scopes can be compiled")
    predicates = (*policies.evaluate(operation), *extra_predicates)
    if operation.kind is DataOperationKind.UPDATE and any(predicate.field in operation.values for predicate in predicates):
        raise DataPolicyDeniedError("Cannot change a field constrained by a data policy")
    return compile_policy_predicates((*operation.filters, *predicates), target_alias=target_alias, placeholder=placeholder)
