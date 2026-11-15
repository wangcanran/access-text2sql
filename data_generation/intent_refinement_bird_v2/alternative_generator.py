"""Finite, schema-evidenced alternative generation for stage 5."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from .intent_graph import IntentGraph, IntentNode
from .permission_checker import _accessible, _exists
from .transformation import SemanticTransformation
from .transformations import make_transformation, node_semantics
from .user_renderer import UserRenderer


@dataclass(frozen=True)
class AlternativeCandidate:
    transformation: SemanticTransformation
    summary: str
    risk: str

    @property
    def id(self) -> str:
        return self.transformation.transformation_id


def _id(kind: str, node_id: str, replacement: str) -> str:
    digest = hashlib.sha256(f"{kind}|{node_id}|{replacement}".encode()).hexdigest()[:12]
    return f"tx_{kind}_{digest}"


def _public_columns(context: Mapping[str, Any], *, table: str | None = None, exclude: set[str] = set()) -> list[str]:
    schema = context.get("schema", context)
    tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    output: list[str] = []
    for table_name, columns in tables.items() if isinstance(tables, Mapping) else ():
        if table is not None and table != table_name:
            continue
        for column in columns if isinstance(columns, (list, tuple, set)) else ():
            field = f"{table_name}.{column}"
            if field in exclude or not _accessible(context, field):
                continue
            output.append(field)
    return output


def _node_change(node: IntentNode, *, field: str | None = None, removed: bool = False) -> dict[str, Any]:
    if removed:
        return {"removed": True}
    change: dict[str, Any] = {
        "business_meaning": node.business_meaning,
        "value": node.to_dict()["value"],
        "operator": node.operator,
        "attributes": node.to_dict()["attributes"],
    }
    if field:
        table, column = field.split(".", 1)
        change["schema_bindings"] = [{"table": table, "column": column, "confidence": 0.5}]
    return change


def _fk_pairs(context: Mapping[str, Any]) -> list[tuple[str, str]]:
    schema = context.get("schema", context)
    raw = context.get("foreign_keys") or (schema.get("foreign_keys", ()) if isinstance(schema, Mapping) else ())
    pairs: list[tuple[str, str]] = []
    for item in raw or ():
        if not isinstance(item, Mapping):
            continue
        if all(isinstance(item.get(key), str) for key in ("from_table", "from_column", "to_table", "to_column")):
            pairs.append((f"{item['from_table']}.{item['from_column']}", f"{item['to_table']}.{item['to_column']}"))
        for pair in item.get("pairs", ()) if isinstance(item.get("pairs"), (list, tuple)) else ():
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(value, str) for value in pair):
                pairs.append((pair[0], pair[1]))
    return pairs


def generate_alternatives(
    graph: IntentGraph,
    public_context: Mapping[str, Any],
    *,
    max_candidates: int = 8,
    max_semantic_distance: int = 1,
    renderer: UserRenderer | None = None,
) -> tuple[AlternativeCandidate, ...]:
    """Generate only permission-safe, schema-backed transformations.

    Required filters, aggregates and groupings are never silently removed.
    Output and ordering alternatives may include explicit removal, while all
    candidates remain pending user authorization.
    """
    if max_candidates <= 0 or max_semantic_distance < 1:
        return ()
    candidates: list[AlternativeCandidate] = []
    renderer = renderer or UserRenderer(context=public_context)
    for node in graph.nodes:
        restricted = [binding.qualified_name for binding in node.schema_bindings if _exists(public_context, binding.qualified_name) and not _accessible(public_context, binding.qualified_name)]
        if not restricted:
            continue
        # A field replacement stays on the same table by default.  This is a
        # conservative semantic guard: cross-table replacements require an
        # explicit FK-aware transformation and are not inferred here.
        replacement_fields = _public_columns(public_context, table=restricted[0].split(".", 1)[0], exclude=set(restricted))
        if node.semantic_type in {"OutputExpression", "Ordering", "MeasureExpression", "Grouping"}:
            for field in replacement_fields:
                transform_type = {
                    "OutputExpression": "replace_output",
                    "Ordering": "replace_ordering",
                    "MeasureExpression": "replace_measure",
                    "Grouping": "replace_grouping",
                }[node.semantic_type]
                transformation = make_transformation(
                    graph, transformation_id=_id("replace", node.node_id, field),
                    transform_type=transform_type, affected_nodes=(node.node_id,),
                    new_nodes={node.node_id: _node_change(node, field=field)},
                    semantic_loss=f"replace restricted {node.semantic_type} field {restricted[0]} with {field}",
                    permission_reason=f"{restricted[0]} is inaccessible for the current role",
                )
                candidates.append(AlternativeCandidate(transformation, renderer.alternative(f"Use {renderer.binding(field)} for {node.business_meaning}."), "replacement"))
                if len(candidates) >= max_candidates:
                    return tuple(candidates)
            if node.semantic_type == "OutputExpression":
                transformation = make_transformation(
                    graph, transformation_id=_id("drop", node.node_id, "drop"),
                    transform_type="drop_output", affected_nodes=(node.node_id,),
                    new_nodes={node.node_id: _node_change(node, removed=True)},
                    semantic_loss=f"omit restricted output {restricted[0]}",
                    permission_reason=f"{restricted[0]} is inaccessible for the current role",
                )
                candidates.append(AlternativeCandidate(transformation, renderer.alternative(f"Omit {node.business_meaning} from the result."), "lossy_removal"))
        elif node.semantic_type == "FilterPredicate":
            # Relaxing a predicate is allowed only as an explicit, lossy
            # proposal. It is never emitted as a silent graph edit.
            transformation = make_transformation(
                graph, transformation_id=_id("relax_filter", node.node_id, "remove"),
                transform_type="relax_filter", affected_nodes=(node.node_id,),
                new_nodes={node.node_id: _node_change(node, removed=True)},
                semantic_loss=f"remove filter based on restricted field {restricted[0]}",
                permission_reason=f"{restricted[0]} is inaccessible for the current role",
            )
            candidates.append(AlternativeCandidate(transformation, renderer.alternative(f"Remove the condition {node.business_meaning}."), "lossy_filter_relaxation"))
        elif node.semantic_type == "Ordering":
            transformation = make_transformation(
                graph, transformation_id=_id("drop_ordering", node.node_id, "drop"),
                transform_type="drop_ordering", affected_nodes=(node.node_id,),
                new_nodes={node.node_id: _node_change(node, removed=True)},
                semantic_loss=f"remove ordering based on restricted field {restricted[0]}",
                permission_reason=f"{restricted[0]} is inaccessible for the current role",
            )
            candidates.append(AlternativeCandidate(transformation, renderer.alternative(f"Return results without ordering by {node.business_meaning}."), "lossy_ordering_removal"))
        elif node.semantic_type == "JoinRelation":
            for left, right in _fk_pairs(public_context):
                if not (_exists(public_context, left) and _exists(public_context, right)):
                    continue
                if not (_accessible(public_context, left) and _accessible(public_context, right)):
                    continue
                table_left, column_left = left.split(".", 1)
                table_right, column_right = right.split(".", 1)
                change = {
                    "business_meaning": node.business_meaning,
                    "value": node.to_dict()["value"],
                    "operator": node.operator,
                    "attributes": node.to_dict()["attributes"],
                    "schema_bindings": [
                        {"table": table_left, "column": column_left, "confidence": 0.5},
                        {"table": table_right, "column": column_right, "confidence": 0.5},
                    ],
                }
                transformation = make_transformation(
                    graph, transformation_id=_id("replace_join", node.node_id, left + "=" + right),
                    transform_type="replace_join_path", affected_nodes=(node.node_id,), new_nodes={node.node_id: change},
                    semantic_loss=f"replace inaccessible join endpoints with declared FK {left}={right}",
                    permission_reason="original join endpoint is inaccessible for the current role",
                )
                candidates.append(AlternativeCandidate(transformation, renderer.alternative(f"Use the declared relationship between {renderer.table(table_left)} and {renderer.table(table_right)}."), "fk_replacement"))
                if len(candidates) >= max_candidates:
                    return tuple(candidates)
        # Filter, aggregation, grouping and join endpoint changes require a
        # domain-specific user choice; no generic deletion is offered.
    unique: dict[str, AlternativeCandidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.id, candidate)
    return tuple(unique.values())
