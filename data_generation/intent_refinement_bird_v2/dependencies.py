"""Deterministic extraction of physical execution obligations from an intent graph."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from .intent_graph import IntentGraph, IntentNode, SchemaBinding


@dataclass(frozen=True)
class ExecutionObligation:
    node_id: str
    semantic_role: str
    field: str
    source: str = "schema_binding"
    relation_id: str | None = None
    path: tuple[str, ...] = ()


_QUALIFIED = re.compile(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b")


def _attribute_fields(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        if isinstance(value.get("table"), str) and isinstance(value.get("column"), str):
            yield f"{value['table']}.{value['column']}"
        for item in value.values():
            yield from _attribute_fields(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _attribute_fields(item)
    elif isinstance(value, str):
        for match in _QUALIFIED.finditer(value):
            yield f"{match.group(1)}.{match.group(2)}"


def collect_execution_obligations(graph: IntentGraph) -> tuple[ExecutionObligation, ...]:
    """Collect role-preserving field obligations without choosing a plan."""
    obligations: list[ExecutionObligation] = []
    for node in graph.nodes:
        role = node.semantic_type
        for index, binding in enumerate(node.schema_bindings):
            obligations.append(ExecutionObligation(node.node_id, role, binding.qualified_name, "schema_binding", path=(str(index),)))
        # Model attributes can carry fields for HAVING, windows, CASE and
        # subqueries. Keep them as separate obligations rather than flattening
        # them into a set, since the semantic role matters for authorization.
        for index, field in enumerate(_attribute_fields(node.attributes)):
            obligations.append(ExecutionObligation(node.node_id, role, field, "attribute", path=("attributes", str(index))))
        for index, field in enumerate(_attribute_fields(node.value)):
            obligations.append(ExecutionObligation(node.node_id, role, field, "value", path=("value", str(index))))
    return tuple(obligations)


def obligations_by_node(graph: IntentGraph) -> dict[str, tuple[ExecutionObligation, ...]]:
    result: dict[str, list[ExecutionObligation]] = {}
    for item in collect_execution_obligations(graph):
        result.setdefault(item.node_id, []).append(item)
    return {key: tuple(value) for key, value in result.items()}
