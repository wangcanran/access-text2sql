"""Deterministic schema and foreign-key validation for intent graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .dependencies import ExecutionObligation, collect_execution_obligations
from .intent_graph import IntentGraph


@dataclass(frozen=True)
class BindingDiagnostic:
    code: str
    node_id: str | None
    field: str | None
    detail: str


def _schema_tables(public_context: Mapping[str, Any]) -> Mapping[str, Any]:
    schema = public_context.get("schema", public_context)
    tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    return tables if isinstance(tables, Mapping) else {}


def validate_graph_bindings(graph: IntentGraph, public_context: Mapping[str, Any]) -> tuple[BindingDiagnostic, ...]:
    tables = _schema_tables(public_context)
    diagnostics: list[BindingDiagnostic] = []
    for obligation in collect_execution_obligations(graph):
        if "." not in obligation.field:
            diagnostics.append(BindingDiagnostic("invalid_field_reference", obligation.node_id, obligation.field, "field must use table.column form"))
            continue
        table, column = obligation.field.split(".", 1)
        columns = tables.get(table)
        if not isinstance(columns, (list, tuple, set)) or column not in columns:
            diagnostics.append(BindingDiagnostic("invalid_schema_binding", obligation.node_id, obligation.field, "table or column is absent from public schema"))
    for node in graph.nodes:
        if node.semantic_type in {"OutputExpression", "FilterPredicate", "Aggregation", "MeasureExpression", "Grouping", "Ordering", "JoinRelation", "Subquery"} and not node.schema_bindings and not list(_attribute_fields_safe(node)):
            diagnostics.append(BindingDiagnostic("missing_binding", node.node_id, None, "execution-relevant node has no schema binding"))
    return tuple(diagnostics)


def _attribute_fields_safe(node):
    from .dependencies import _attribute_fields
    return _attribute_fields(node.attributes)
