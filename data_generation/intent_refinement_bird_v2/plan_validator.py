"""Composition of binding, connectivity and permission diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .binding import BindingDiagnostic, validate_graph_bindings
from .intent_graph import HypothesisSet, IntentGraph
from .permission_checker import PermissionDiagnostic, check_permissions


@dataclass(frozen=True)
class PlanValidation:
    graph: IntentGraph
    status: str
    diagnostics: tuple[BindingDiagnostic | PermissionDiagnostic, ...]

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"


def _connected_tables(graph: IntentGraph, context: Mapping[str, Any]) -> bool:
    tables: set[str] = set()
    for node in graph.nodes:
        for binding in node.schema_bindings:
            tables.add(binding.table)
    if len(tables) <= 1:
        return True
    adjacency: dict[str, set[str]] = {table: set() for table in tables}
    schema = context.get("schema", context)
    fks = context.get("foreign_keys") or (schema.get("foreign_keys", ()) if isinstance(schema, Mapping) else ())
    for item in fks or ():
        if not isinstance(item, Mapping):
            continue
        a, b = item.get("from_table"), item.get("to_table")
        if isinstance(a, str) and isinstance(b, str) and a in adjacency and b in adjacency:
            adjacency[a].add(b); adjacency[b].add(a)
    seen = {next(iter(tables))}
    stack = list(seen)
    while stack:
        for neighbour in adjacency[stack.pop()]:
            if neighbour not in seen:
                seen.add(neighbour); stack.append(neighbour)
    return seen == tables


def validate_plan(graph: IntentGraph, public_context: Mapping[str, Any]) -> PlanValidation:
    diagnostics = list(validate_graph_bindings(graph, public_context))
    diagnostics.extend(check_permissions(graph, public_context))
    if not _connected_tables(graph, public_context):
        diagnostics.append(BindingDiagnostic("unconnected_tables", None, None, "graph tables are not connected by declared foreign keys"))
    codes = {item.code for item in diagnostics}
    if codes & {"permission_denied", "permission_denied_fk_endpoint"}:
        status = "permission_denied"
    elif "invalid_fk_path" in codes:
        status = "invalid_fk_path"
    elif "unconnected_tables" in codes:
        status = "unconnected_tables"
    elif codes & {"invalid_schema_binding", "missing_binding", "invalid_field_reference"}:
        status = "invalid_schema"
    else:
        status = "allowed"
    return PlanValidation(graph, status, tuple(diagnostics))


def validate_hypotheses(hypotheses: HypothesisSet, public_context: Mapping[str, Any]) -> dict[str, PlanValidation]:
    return {item.hypothesis_id: validate_plan(item.graph, public_context) for item in hypotheses.hypotheses}
