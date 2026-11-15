"""Render an authorized intent graph into the final user-facing request.

This is a deterministic projection.  It reads only the graph and public
description catalog; evaluation-only original requests and SQL never enter
the Text2SQL payload.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .intent_graph import IntentGraph, IntentNode
from .user_renderer import UserRenderer


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _field(node: IntentNode, renderer: UserRenderer) -> str:
    bindings = [binding.qualified_name for binding in node.schema_bindings if binding.valid]
    if not bindings:
        return renderer.text(node.business_meaning)
    label = renderer.binding(bindings[0])
    # A missing BIRD description yields a generic label.  The parser's
    # business meaning is safer and more useful in that case, while the
    # renderer still scrubs any physical identifiers it may contain.
    return renderer.text(node.business_meaning) if label == "the requested field" else label


def _node_text(node: IntentNode, renderer: UserRenderer) -> str:
    field = _field(node, renderer)
    meaning = renderer.text(node.business_meaning)
    if node.semantic_type == "TargetEntity":
        return meaning
    if node.semantic_type == "OutputExpression":
        return field or meaning
    if node.semantic_type == "FilterPredicate":
        operator = node.operator or "matches"
        value = _value(node.value)
        return f"where {field or meaning} {operator} {value}".strip()
    if node.semantic_type == "Aggregation":
        return f"calculate {node.operator or 'the aggregate'} of {field or meaning}"
    if node.semantic_type == "MeasureExpression":
        aggregation = str(node.operator or node.attributes.get("aggregation", "aggregate")).upper()
        group = node.attributes.get("group_by")
        suffix = f" per {renderer.text(str(group))}" if group and not str(group).startswith("target_") else " per entity" if group else ""
        return f"{aggregation}({field or meaning}){suffix}"
    if node.semantic_type == "Grouping":
        group_field = field or meaning
        group_field = group_field.removeprefix("group by ").removeprefix("Group by ")
        return f"group by {group_field}"
    if node.semantic_type == "Ordering":
        return f"order by {field or meaning} {node.operator or node.attributes.get('direction', '')}".strip()
    if node.semantic_type == "Ranking":
        direction = str(node.operator or node.attributes.get("direction", "DESC")).upper()
        return "highest first" if direction == "DESC" else "lowest first"
    if node.semantic_type == "TopK":
        return f"keep the top {_value(node.value or node.attributes.get('k') or 1)} result"
    if node.semantic_type == "ResultScope":
        return f"limit the results to {_value(node.value) or meaning}"
    if node.semantic_type == "JoinRelation":
        tables = []
        for binding in node.schema_bindings:
            if binding.table not in tables:
                tables.append(renderer.table(binding.table))
        return "join " + " and ".join(tables or [meaning])
    if node.semantic_type == "Subquery":
        return f"with the related condition {meaning}"
    return meaning


def render_final_request(graph: IntentGraph, *, renderer: UserRenderer | None = None) -> str:
    """Produce concise natural language from the current execution graph."""
    renderer = renderer or UserRenderer()
    nodes = tuple(graph.nodes)
    targets = [node for node in nodes if node.semantic_type == "TargetEntity"]
    outputs = [node for node in nodes if node.semantic_type == "OutputExpression"]
    clauses = [node for node in nodes if node.semantic_type not in {"TargetEntity", "OutputExpression"}]
    subject = renderer.text(targets[0].business_meaning) if targets else "the requested records"
    if subject.casefold().endswith(" records records"):
        subject = subject[: -len(" records")]
    selected = ", ".join(_node_text(node, renderer) for node in outputs) or "the requested information"
    request = f"Show {selected} for {subject}"
    # Preserve the business dependency as one grammatical clause.  This keeps
    # SUM + grouping + ranking + top-k visible to Text2SQL even when the graph
    # nodes arrive in an arbitrary order.
    measures = [node for node in clauses if node.semantic_type == "MeasureExpression"]
    rankings = [node for node in clauses if node.semantic_type == "Ranking"]
    topks = [node for node in clauses if node.semantic_type == "TopK"]
    for node in clauses:
        if node.semantic_type in {"MeasureExpression", "Ranking", "TopK"}:
            continue
        text = _node_text(node, renderer)
        if text:
            request += "; " + text
    if measures:
        measure = measures[0]
        aggregation = str(measure.operator or measure.attributes.get("aggregation", "aggregate")).upper()
        request += f"; rank by {aggregation} of {_field(measure, renderer)}"
        if rankings:
            direction = str(rankings[0].operator or rankings[0].attributes.get("direction", "DESC")).upper()
            request += " from highest to lowest" if direction == "DESC" else " from lowest to highest"
        if topks:
            request += f" and keep the top {_value(topks[0].value or topks[0].attributes.get('k') or 1)}"
    return renderer.text(request).strip().rstrip(".") + "."


class FinalRequestRenderer:
    def __init__(self, renderer: UserRenderer | None = None):
        self.renderer = renderer or UserRenderer()

    def render(self, graph: IntentGraph) -> str:
        return render_final_request(graph, renderer=self.renderer)
