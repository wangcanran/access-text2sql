"""Conservative SQL dependency and permission validation."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .dependencies import collect_execution_obligations
from .intent_graph import IntentGraph
from .intent_contract import IntentContract
from .contract_obligations import compile_contract_obligations


# The trailing boundary used by the ordinary ``table.column`` form does not
# match ``table.*`` because ``*`` is a non-word character.  End the token at
# SQL punctuation/whitespace instead so qualified wildcards are expanded too.
_FIELD = re.compile(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*|\*)(?=\s|[,)]|$)")
_TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)(?:\s+(?:AS\s+)?([A-Za-z_][\w]*))?", re.I)
_COMMA_TABLE = re.compile(r",\s*([A-Za-z_][\w]*)", re.I)
_UNQUALIFIED = re.compile(r"(?<![.\w])([A-Za-z_][\w]*)(?!\s*\()")
_CLAUSE = re.compile(r"\b(SELECT|FROM|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|JOIN|LIMIT)\b", re.I)


@dataclass(frozen=True)
class SQLDiagnostic:
    code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class SQLValidation:
    sql: str
    diagnostics: tuple[SQLDiagnostic, ...]
    executable: bool = False
    result_columns: tuple[str, ...] = ()
    result_rows: tuple[tuple[Any, ...], ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.diagnostics and self.executable


def _table_aliases(sql: str, context: Mapping[str, Any]) -> tuple[dict[str, str], tuple[str, ...]]:
    schema = context.get("schema", context)
    tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    known = set(tables) if isinstance(tables, Mapping) else set()
    aliases: dict[str, str] = {}
    referenced: list[str] = []
    for table, alias in _TABLE_REF.findall(sql):
        if table not in known:
            continue
        aliases[table] = table
        aliases[alias or table] = table
        if table not in referenced:
            referenced.append(table)
    # A comma join is legal SQL and has no JOIN keyword. Add known table names
    # following commas; aliases are intentionally not inferred here.
    for table in _COMMA_TABLE.findall(sql):
        if table in known:
            aliases[table] = table
            if table not in referenced:
                referenced.append(table)
    return aliases, tuple(referenced)


def _fields(sql: str, context: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    aliases, tables = _table_aliases(sql, context)
    found: list[str] = []
    for qualifier, column in _FIELD.findall(sql):
        table = aliases.get(qualifier, qualifier)
        if column == "*":
            schema_tables = (context.get("schema", context) or {}).get("tables", {})
            for expanded in schema_tables.get(table, ()) if isinstance(schema_tables, Mapping) else ():
                field = f"{table}.{expanded}"
                if field not in found:
                    found.append(field)
            continue
        field = f"{table}.{column}"
        if field not in found:
            found.append(field)
    schema = context.get("schema", context)
    schema_tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    # SELECT * has no qualifier and expands against every referenced table.
    if re.search(r"\bSELECT\s+\*", sql, re.I):
        for table in tables:
            for expanded in schema_tables.get(table, ()) if isinstance(schema_tables, Mapping) else ():
                field = f"{table}.{expanded}"
                if field not in found:
                    found.append(field)
    # Resolve a bare column only when it maps to one and only one referenced
    # table. This catches alias-free SQL without guessing ambiguous columns.
    for column in _UNQUALIFIED.findall(sql):
        if column.upper() in {"SELECT", "FROM", "WHERE", "JOIN", "ON", "AS", "AND", "OR", "GROUP", "BY", "ORDER", "LIMIT", "HAVING", "ASC", "DESC", "DISTINCT"}:
            continue
        candidates = [f"{table}.{column}" for table in tables if column in schema_tables.get(table, ())]
        if len(candidates) == 1 and candidates[0] not in found:
            found.append(candidates[0])
    return tuple(found), tables


def _public_fields(context: Mapping[str, Any]) -> set[str]:
    schema = context.get("schema", context)
    tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    return {f"{table}.{column}" for table, columns in tables.items() for column in columns}


def _accessible_fields(context: Mapping[str, Any]) -> set[str]:
    policy = context.get("policy", {})
    return {f"{table}.{column}" for table, columns in policy.items() for column in columns}


def validate_sql(sql: str, graph: IntentGraph, public_context: Mapping[str, Any], *, connection: sqlite3.Connection | None = None, require_obligations: bool = True) -> SQLValidation:
    text = str(sql or "").strip()
    diagnostics: list[SQLDiagnostic] = []
    if not text:
        return SQLValidation(text, (SQLDiagnostic("empty_sql", "SQL is empty"),))
    if not re.match(r"^\s*(SELECT|WITH)\b", text, re.I):
        diagnostics.append(SQLDiagnostic("non_select_sql", "only SELECT/WITH queries are allowed"))
    if ";" in text.rstrip(";"):
        diagnostics.append(SQLDiagnostic("multiple_statements", "multiple SQL statements are not allowed"))
    public = _public_fields(public_context)
    accessible = _accessible_fields(public_context)
    referenced, referenced_tables = _fields(text, public_context)
    for field in referenced:
        if field not in public:
            diagnostics.append(SQLDiagnostic("unknown_field", f"field is absent from the public schema: {field}", field))
        elif field not in accessible:
            diagnostics.append(SQLDiagnostic("permission_denied", f"field is inaccessible: {field}", field))
    if require_obligations:
        present = set(referenced)
        obligations = collect_execution_obligations(graph)
        expected = {obligation.field for obligation in obligations}
        for obligation in obligations:
            if obligation.field in public and obligation.field not in present:
                diagnostics.append(SQLDiagnostic("missing_obligation", f"SQL omitted confirmed dependency {obligation.field}", obligation.field))
        for field in sorted(present - expected):
            if field in public:
                diagnostics.append(SQLDiagnostic("extra_semantic_field", f"SQL introduced an unconfirmed field dependency: {field}", field))
    if len(referenced_tables) > 1:
        fk_adjacency: dict[str, set[str]] = {table: set() for table in referenced_tables}
        raw_fks = public_context.get("foreign_keys", ())
        schema = public_context.get("schema", public_context)
        if not raw_fks and isinstance(schema, Mapping):
            raw_fks = schema.get("foreign_keys", ())
        for item in raw_fks or ():
            if not isinstance(item, Mapping):
                continue
            left, right = item.get("from_table"), item.get("to_table")
            if left in fk_adjacency and right in fk_adjacency:
                fk_adjacency[left].add(right); fk_adjacency[right].add(left)
        seen = {referenced_tables[0]}
        stack = list(seen)
        while stack:
            for neighbour in fk_adjacency[stack.pop()]:
                if neighbour not in seen:
                    seen.add(neighbour); stack.append(neighbour)
        if seen != set(referenced_tables):
            diagnostics.append(SQLDiagnostic("invalid_fk_connectivity", "referenced tables are not connected by declared foreign keys"))
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    executable = False
    if not diagnostics and connection is not None:
        try:
            cursor = connection.execute(text)
            columns = tuple(item[0] for item in cursor.description or ())
            rows = tuple(tuple(row) for row in cursor.fetchall())
            executable = True
        except sqlite3.Error as exc:
            diagnostics.append(SQLDiagnostic("sql_execution_error", str(exc)))
    return SQLValidation(text, tuple(diagnostics), executable, columns, rows)


def validate_contract_sql(sql: str, contract: IntentContract, public_context: Mapping[str, Any], *, connection: sqlite3.Connection | None = None) -> SQLValidation:
    """Validate generated SQL against semantic obligations from a contract.

    This intentionally checks only obligations that can be observed from SQL:
    required field dependencies and a few explicit operators (ranking, limit,
    distinct, and nested aggregation).  SQL structure remains the Text2SQL
    model's responsibility; omission of a required semantic resource remains a
    deterministic validator failure.
    """
    base = validate_sql(sql, IntentGraph(), public_context, connection=connection, require_obligations=False)
    if not base.allowed:
        return base
    text = str(sql or "")
    diagnostics = list(base.diagnostics)
    obligations = compile_contract_obligations(contract)
    present = set(_fields(text, public_context)[0])
    for item in obligations.items:
        if not item.required:
            continue
        for field in item.field_refs:
            if field in _public_fields(public_context) and field not in present:
                diagnostics.append(SQLDiagnostic("missing_contract_obligation", f"SQL omitted required semantic dependency {field} for {item.semantic_item_id}", field))
        if item.relation_ids:
            relation_fields = {
                field
                for relation_id in item.relation_ids
                for relation in contract.relations
                if relation.id == relation_id
                for field in relation.field_refs
            }
            for field in relation_fields:
                if field in _public_fields(public_context) and field not in present:
                    diagnostics.append(SQLDiagnostic("missing_contract_relation", f"SQL omitted required relation dependency {field} for {item.semantic_item_id}", field))
    if contract.ranking:
        if not re.search(r"\bORDER\s+BY\b", text, re.I):
            diagnostics.append(SQLDiagnostic("missing_contract_ranking", "contract requires ranking but SQL has no ORDER BY"))
        if contract.ranking.limit is not None and not re.search(r"\bLIMIT\b", text, re.I):
            diagnostics.append(SQLDiagnostic("missing_contract_limit", "contract requires a bounded ranking result but SQL has no LIMIT"))
    has_distinct_requirement = any(item.distinct or (item.measure and item.measure.distinct) for item in contract.outputs)
    if has_distinct_requirement and not re.search(r"\bDISTINCT\b", text, re.I):
        diagnostics.append(SQLDiagnostic("missing_contract_distinct", "contract requires a distinct result but SQL has no DISTINCT"))
    for output in contract.outputs:
        measure = output.measure
        if measure and measure.inner_measure:
            if not re.search(rf"\b{re.escape(measure.operation)}\s*\(", text, re.I):
                diagnostics.append(SQLDiagnostic("missing_contract_outer_aggregation", f"SQL omitted outer {measure.operation} aggregation for {output.id}"))
            if not re.search(rf"\b{re.escape(measure.inner_measure.operation)}\s*\(", text, re.I):
                diagnostics.append(SQLDiagnostic("missing_contract_inner_aggregation", f"SQL omitted inner {measure.inner_measure.operation} aggregation for {output.id}"))
    if diagnostics:
        return SQLValidation(base.sql, tuple(diagnostics), False, base.result_columns, base.result_rows)
    return base
