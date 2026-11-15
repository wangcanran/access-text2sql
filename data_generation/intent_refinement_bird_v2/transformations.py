"""Application of auditable semantic transformations.

This module contains no candidate-generation policy.  It only applies a
validated transformation to a graph, preserving the original snapshot.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .intent_graph import IntentEdge, IntentGraph, IntentNode, SchemaBinding
from .transformation import SemanticTransformation


def node_semantics(node: IntentNode) -> dict[str, Any]:
    """Return the semantic fields a transformation is allowed to change."""
    return {
        "semantic_type": node.semantic_type,
        "business_meaning": node.business_meaning,
        "schema_bindings": [binding.to_dict() for binding in node.schema_bindings],
        "value": node.to_dict()["value"],
        "operator": node.operator,
        "attributes": node.to_dict()["attributes"],
    }


def graph_semantics(graph: IntentGraph, node_ids: tuple[str, ...]) -> dict[str, Any]:
    return {node_id: node_semantics(graph.node(node_id)) for node_id in node_ids}


def _binding(value: Mapping[str, Any]) -> SchemaBinding:
    return SchemaBinding(
        str(value["table"]), str(value["column"]), float(value.get("confidence", 0.0)),
        valid=bool(value.get("valid", True)),
        validation_reason=str(value.get("validation_reason", "")),
    )


def _same_semantics(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    # A transformation must be based on the exact original semantic snapshot;
    # this prevents applying a stale offer to a changed conversation.
    def canonical(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): canonical(item) for key, item in value.items()}
        if isinstance(value, (tuple, list, set, frozenset)):
            return [canonical(item) for item in value]
        return value
    return canonical(actual) == canonical(expected)


def _replace_node(node: IntentNode, change: Mapping[str, Any]) -> IntentNode:
    updates: dict[str, Any] = {}
    if "business_meaning" in change:
        updates["business_meaning"] = str(change["business_meaning"])
    if "schema_bindings" in change:
        bindings = change["schema_bindings"]
        if not isinstance(bindings, (list, tuple)):
            raise ValueError("new schema_bindings must be a list")
        updates["schema_bindings"] = tuple(_binding(item) for item in bindings)
    for key in ("value", "operator", "attributes"):
        if key in change:
            updates[key] = change[key]
    return node.with_update(**updates) if updates else node


def _remove_node(graph: IntentGraph, node_id: str) -> IntentGraph:
    if any(node_id in node.dependencies for node in graph.nodes if node.node_id != node_id):
        raise ValueError(f"cannot remove node {node_id}: another node depends on it")
    return IntentGraph(
        nodes=tuple(node for node in graph.nodes if node.node_id != node_id),
        edges=tuple(edge for edge in graph.edges if edge.source_id != node_id and edge.target_id != node_id),
        version=graph.version,
    )


def apply_transformation(
    graph: IntentGraph,
    transformation: SemanticTransformation,
    *,
    strict: bool = True,
) -> IntentGraph:
    """Apply a proposed transformation and return a new immutable graph.

    ``strict`` checks that every affected node still has the old semantics
    captured in the offer.  No authorization is inferred here; callers must
    use :mod:`authorization` for user authorization.
    """
    affected = tuple(transformation.affected_nodes)
    old_nodes = transformation.old_semantics.get("nodes", {}) if isinstance(transformation.old_semantics, Mapping) else {}
    new_nodes = transformation.new_semantics.get("nodes", {}) if isinstance(transformation.new_semantics, Mapping) else {}
    if not isinstance(old_nodes, Mapping) or not isinstance(new_nodes, Mapping):
        raise ValueError("transformation semantics must contain nodes mappings")
    result = graph
    for node_id in affected:
        if node_id not in old_nodes or node_id not in new_nodes:
            raise ValueError(f"transformation is missing semantic snapshot for {node_id}")
        try:
            current = result.node(node_id)
        except KeyError:
            if new_nodes[node_id].get("removed") is True:
                continue
            raise ValueError(f"transformation references missing node {node_id}")
        if strict and not _same_semantics(node_semantics(current), old_nodes[node_id]):
            raise ValueError(f"stale transformation for node {node_id}")
        change = new_nodes[node_id]
        if change.get("removed") is True:
            result = _remove_node(result, node_id)
        else:
            result = result.update_node(_replace_node(current, change))
    return result


def make_transformation(
    graph: IntentGraph,
    *,
    transformation_id: str,
    transform_type: str,
    affected_nodes: tuple[str, ...],
    new_nodes: Mapping[str, Any],
    semantic_loss: str,
    permission_reason: str,
) -> SemanticTransformation:
    """Construct a transformation with an exact old semantic snapshot."""
    return SemanticTransformation(
        transformation_id=transformation_id,
        transform_type=transform_type,
        old_semantics={"nodes": graph_semantics(graph, affected_nodes)},
        new_semantics={"nodes": {str(key): dict(value) for key, value in new_nodes.items()}},
        affected_nodes=affected_nodes,
        semantic_loss=semantic_loss,
        permission_reason=permission_reason,
        user_authorization_required=True,
    )
