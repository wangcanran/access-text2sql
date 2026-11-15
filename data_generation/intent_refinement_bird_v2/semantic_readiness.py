"""Rule-based semantic readiness checks.

Parser status is deliberately not trusted: new parser nodes are normally
marked ``unresolved`` to prevent a model from ending a dialogue by emitting a
status flag.  Readiness is therefore derived from structural evidence and
cross-hypothesis agreement instead.  A clear parser result can proceed to the
permission gate without an artificial user confirmation question.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .intent_graph import HypothesisSet, IntentGraph, IntentNode


@dataclass(frozen=True)
class SemanticReadiness:
    ready: bool
    reasons: tuple[str, ...] = ()


def _signature(node: IntentNode | None) -> tuple:
    if node is None:
        return (None,)
    return (
        node.semantic_type,
        node.business_meaning.casefold().strip(),
        tuple((binding.table, binding.column, binding.valid) for binding in node.schema_bindings),
        node.operator,
        repr(node.value),
    )


def _hypotheses_disagree(graph: IntentGraph, hypotheses: HypothesisSet) -> bool:
    alternatives = [item.graph for item in hypotheses.hypotheses if item.graph is not graph]
    if len(hypotheses.hypotheses) <= 1:
        return False
    baseline = {node.node_id: _signature(node) for node in graph.nodes}
    for alternative in alternatives:
        candidate = {node.node_id: _signature(node) for node in alternative.nodes}
        if candidate != baseline:
            return True
    return False


def assess_semantic_readiness(graph: IntentGraph, hypotheses: HypothesisSet, *, confidence_threshold: float = 0.75) -> SemanticReadiness:
    reasons: list[str] = []
    if _hypotheses_disagree(graph, hypotheses):
        reasons.append("competing_hypotheses")
    for node in graph.nodes:
        if node.semantic_type == "TargetEntity":
            continue
        if node.confidence < confidence_threshold and not node.confirmed_by_user:
            reasons.append(f"low_confidence:{node.node_id}")
        bindings = tuple(binding for binding in node.schema_bindings if binding.valid)
        # Every executable semantic node except the target must have a
        # concrete public binding.  This catches genuinely vague outputs while
        # allowing target entities to remain business-level concepts.
        # Ranking and TopK are relational operators over other semantic nodes;
        # they intentionally have no physical column of their own.
        if not bindings and node.semantic_type not in {"Ranking", "TopK"}:
            reasons.append(f"missing_binding:{node.node_id}")
    by_id = {node.node_id: node for node in graph.nodes}
    for node in graph.nodes:
        deps = [by_id.get(dep) for dep in node.dependencies]
        if node.semantic_type == "Ranking":
            if not any(item and item.semantic_type in {"MeasureExpression", "Aggregation"} for item in deps):
                reasons.append(f"ranking_without_measure:{node.node_id}")
            if not any(item and item.semantic_type == "Grouping" for item in deps) and any(token in node.business_meaning.casefold() for token in ("per", "each", "customer", "department")):
                reasons.append(f"ranking_without_grouping:{node.node_id}")
        if node.semantic_type == "TopK" and not any(item and item.semantic_type == "Ranking" for item in deps):
            reasons.append(f"topk_without_ranking:{node.node_id}")
        if node.semantic_type == "MeasureExpression":
            aggregation = str(node.operator or node.attributes.get("aggregation", "")).upper()
            if not aggregation:
                reasons.append(f"measure_without_aggregation:{node.node_id}")
            if node.attributes.get("group_by") and not any(item and item.semantic_type in {"TargetEntity", "Grouping"} for item in deps):
                reasons.append(f"measure_without_group_target:{node.node_id}")
        if node.semantic_type == "Ordering" and any(token in node.business_meaning.casefold() for token in ("most", "highest", "top", "largest")):
            reasons.append(f"missing_measure_for_ordering:{node.node_id}")
            reasons.append(f"legacy_bare_ordering_for_ranking:{node.node_id}")
    return SemanticReadiness(not reasons, tuple(dict.fromkeys(reasons)))
