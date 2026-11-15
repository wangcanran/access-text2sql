"""Pure permission checks over execution obligations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .dependencies import ExecutionObligation, collect_execution_obligations
from .intent_graph import IntentGraph


@dataclass(frozen=True)
class PermissionDiagnostic:
    code: str
    node_id: str | None
    field: str | None
    semantic_role: str | None
    detail: str


def _policy(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = context.get("policy", {})
    return value if isinstance(value, Mapping) else {}


def _accessible(context: Mapping[str, Any], field: str) -> bool:
    if "." not in field:
        return False
    table, column = field.split(".", 1)
    allowed = _policy(context).get(table, ())
    return isinstance(allowed, (list, tuple, set)) and column in allowed


def _exists(context: Mapping[str, Any], field: str) -> bool:
    if "." not in field:
        return False
    table, column = field.split(".", 1)
    schema = context.get("schema", context)
    tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    columns = tables.get(table, ()) if isinstance(tables, Mapping) else ()
    return isinstance(columns, (list, tuple, set)) and column in columns


def _foreign_keys(context: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    schema = context.get("schema", context)
    values = context.get("foreign_keys") or (schema.get("foreign_keys", ()) if isinstance(schema, Mapping) else ())
    return tuple(item for item in values if isinstance(item, Mapping))


def check_permissions(graph: IntentGraph, public_context: Mapping[str, Any]) -> tuple[PermissionDiagnostic, ...]:
    diagnostics: list[PermissionDiagnostic] = []
    for obligation in collect_execution_obligations(graph):
        if not _exists(public_context, obligation.field):
            continue
        if _exists(public_context, obligation.field) and not _accessible(public_context, obligation.field):
            diagnostics.append(PermissionDiagnostic("permission_denied", obligation.node_id, obligation.field, obligation.semantic_role, "column is not accessible for current role"))
    for node in graph.nodes:
        if node.semantic_type != "JoinRelation":
            continue
        bindings = [item.qualified_name for item in node.schema_bindings]
        from .dependencies import _attribute_fields
        bindings.extend(field for field in _attribute_fields(node.attributes) if field not in bindings)
        if len(bindings) < 2:
            continue
        for field in bindings:
            if not _accessible(public_context, field):
                diagnostics.append(PermissionDiagnostic("permission_denied_fk_endpoint", node.node_id, field, "JoinRelation", "foreign-key endpoint is inaccessible"))
        if not any({a, b} == set(bindings[:2]) for item in _foreign_keys(public_context) for a, b in _fk_pair(item)):
            diagnostics.append(PermissionDiagnostic("invalid_fk_path", node.node_id, None, "JoinRelation", "join endpoints do not match a declared foreign key"))
    return tuple(diagnostics)


def _fk_pair(item: Mapping[str, Any]):
    if all(isinstance(item.get(key), str) for key in ("from_table", "from_column", "to_table", "to_column")):
        yield f"{item['from_table']}.{item['from_column']}", f"{item['to_table']}.{item['to_column']}"
    pairs = item.get("pairs")
    if isinstance(pairs, (list, tuple)):
        for pair in pairs:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, str) for x in pair):
                yield pair[0], pair[1]
