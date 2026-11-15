"""Stage-2 contract for the semantic parser agent.

The contract is deliberately plain JSON so the LLM client can be replaced by
a deterministic parser or a test double without changing graph semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .intent_graph import IntentEdge, IntentGraph, IntentNode, SchemaBinding


ALLOWED_ACTIONS = {"parse_update", "ask", "propose_alternative"}
_NODE_TYPES = {"TargetEntity", "OutputExpression", "FilterPredicate", "Aggregation", "MeasureExpression", "Grouping", "Ordering", "Ranking", "TopK", "ResultScope", "JoinRelation", "Subquery"}
_EDGE_KINDS = {"dependency", "binding", "relation", "alternative"}

# Sent to OpenAI-compatible clients that support strict JSON-schema output.
# Every property is required so the model cannot silently omit a semantic
# field; nullable fields represent an intentional absence.
PARSER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
        "question": {"type": ["string", "null"]},
        "target_node_ids": {"type": "array", "items": {"type": "string"}},
        "expected_answer_shape": {"type": ["string", "null"]},
        "alternative": {
            "type": ["object", "null"], "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "source_node_ids": {"type": "array", "items": {"type": "string"}},
                "changes": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "rationale": {"type": "string"},
            },
            "required": ["summary", "source_node_ids", "changes", "rationale"],
        },
        "graph": {
            "type": ["object", "null"], "additionalProperties": False,
            "properties": {
                "version": {"type": "string"},
                "nodes": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "semantic_type": {"type": "string", "enum": sorted(_NODE_TYPES)},
                        "business_meaning": {"type": "string"},
                        "schema_bindings": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False,
                            "properties": {"table": {"type": "string"}, "column": {"type": "string"}, "confidence": {"type": "number"}},
                            "required": ["table", "column", "confidence"],
                        }},
                        "value": {"type": ["string", "number", "boolean", "object", "array", "null"]},
                        "operator": {"type": ["string", "null"]},
                        "dependencies": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                        "attributes": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["id", "semantic_type", "business_meaning", "schema_bindings", "value", "operator", "dependencies", "confidence", "attributes"],
                }},
                "edges": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"id": {"type": "string"}, "source": {"type": "string"}, "target": {"type": "string"}, "kind": {"type": "string", "enum": sorted(_EDGE_KINDS)}, "label": {"type": "string"}},
                    "required": ["id", "source", "target", "kind", "label"],
                }},
            },
            "required": ["version", "nodes", "edges"],
        },
        "hypotheses": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "id": {"type": "string"}, "score": {"type": "number"},
                "graph": {"type": ["object", "null"]},
                "unresolved_node_ids": {"type": "array", "items": {"type": "string"}},
                "diagnostics": {"type": "array", "items": {"type": "string"}},
                "business_meaning": {"type": ["string", "null"]},
                "schema_binding": {"type": ["object", "null"]},
            },
            "required": ["id", "score", "graph", "unresolved_node_ids", "diagnostics", "business_meaning", "schema_binding"],
        }},
    },
    "required": ["action", "question", "target_node_ids", "expected_answer_shape", "alternative", "graph", "hypotheses"],
}

# Action-specific schemas are used by runners that already know which action
# they are requesting.  Keeping these separate prevents an ``ask`` response
# from being forced to manufacture an empty graph, and prevents an
# alternative proposal from being mistaken for a parse update.
PARSE_UPDATE_RESPONSE_SCHEMA: dict[str, Any] = {
    **PARSER_RESPONSE_SCHEMA,
    "properties": {**PARSER_RESPONSE_SCHEMA["properties"], "action": {"type": "string", "enum": ["parse_update"]}, "graph": {**PARSER_RESPONSE_SCHEMA["properties"]["graph"], "type": "object"}},
    "required": ["action", "graph", "hypotheses"],
}

ASK_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["ask"]},
        "question": {"type": "string"},
        "target_node_ids": {"type": "array", "items": {"type": "string"}},
        "expected_answer_shape": {"type": "string"},
    },
    "required": ["action", "question", "target_node_ids", "expected_answer_shape"],
}

ALTERNATIVE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["propose_alternative"]},
        "alternative": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "source_node_ids": {"type": "array", "items": {"type": "string"}},
                "changes": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                "rationale": {"type": "string"},
            },
            "required": ["summary", "source_node_ids", "changes", "rationale"],
        },
    },
    "required": ["action", "alternative"],
}

_PRIVATE_KEYS = {
    "original_request", "original_questions", "gold_sql", "pseudo_sql",
    "evaluation_only", "private", "private_metadata", "profile", "user_profile",
}


def _assert_public_payload(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _PRIVATE_KEYS:
                raise ValueError(f"private field is not allowed in parser payload: {path}.{key}")
            _assert_public_payload(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_public_payload(item, f"{path}[{index}]")


@dataclass(frozen=True)
class ParseRequest:
    request: str
    public_context: dict[str, Any]
    history: tuple[dict[str, Any], ...] = ()
    prior_graph: IntentGraph | None = None

    def __post_init__(self) -> None:
        if not self.request.strip():
            raise ValueError("request must not be empty")
        _assert_public_payload(self.public_context, "public_context")
        for index, item in enumerate(self.history):
            _assert_public_payload(item, f"history[{index}]")

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "request": self.request,
            "public_context": self.public_context,
            "history": [dict(item) for item in self.history],
        }
        if self.prior_graph is not None:
            payload["prior_graph"] = self.prior_graph.to_dict()
        return payload


@dataclass(frozen=True)
class ParseResponse:
    graph: IntentGraph
    hypotheses: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClarificationRequest:
    """The parser's question action.

    This is deliberately not represented as an intent graph.  A question is
    an instruction to the dialogue runner and cannot assert a new fact,
    permission result, or user confirmation.
    """

    question: str
    target_node_ids: tuple[str, ...] = ()
    expected_answer_shape: str = "free_text"


@dataclass(frozen=True)
class AlternativeProposal:
    """A proposed semantic transformation requiring user authorization."""

    summary: str
    source_node_ids: tuple[str, ...] = ()
    changes: tuple[dict[str, Any], ...] = ()
    rationale: str = ""


@dataclass(frozen=True)
class AgentActionResponse:
    action: str
    graph: IntentGraph | None = None
    hypotheses: tuple[dict[str, Any], ...] = ()
    clarification: ClarificationRequest | None = None
    alternative: AlternativeProposal | None = None
    diagnostics: tuple[str, ...] = ()


def _required_string(value: Mapping[str, Any], key: str, path: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{path}.{key} must be a non-empty string")
    return item.strip()


def _parse_binding(value: Any, path: str) -> SchemaBinding:
    if isinstance(value, str):
        parts = value.strip().split(".", 1)
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError(f"{path} string binding must use table.column form")
        value = {"table": parts[0].strip(), "column": parts[1].strip()}
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object or table.column string")
    table = _required_string(value, "table", path)
    # Some OpenAI-compatible models pluralize this one field.  Accept it only
    # as a mechanical normalization; binding semantics remain unchanged.
    if (not isinstance(value.get("column"), str) or not value.get("column", "").strip()) and isinstance(value.get("columns"), list) and len(value["columns"]) == 1:
        value = {**value, "column": value["columns"][0]}
    column = _required_string(value, "column", path)
    confidence = value.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError(f"{path}.confidence must be numeric")
    return SchemaBinding(table, column, float(confidence))


def parse_node(value: Mapping[str, Any], index: int) -> IntentNode:
    path = f"nodes[{index}]"
    node_id = _required_string(value, "id", path)
    semantic_type = _required_string(value, "semantic_type", path)
    if semantic_type not in _NODE_TYPES:
        raise ValueError(f"{path}.semantic_type is unsupported: {semantic_type}")
    meaning = _required_string(value, "business_meaning", path)
    raw_dependencies = value.get("dependencies", [])
    if not isinstance(raw_dependencies, list) or any(not isinstance(item, str) or not item.strip() for item in raw_dependencies):
        raise ValueError(f"{path}.dependencies must be a list of non-empty strings")
    raw_bindings = value.get("schema_bindings", [])
    if raw_bindings is None:
        raw_bindings = []
    elif isinstance(raw_bindings, Mapping):
        raw_bindings = [raw_bindings]
    elif isinstance(raw_bindings, str):
        raw_bindings = [raw_bindings]
    if not isinstance(raw_bindings, list) or any(not isinstance(item, (Mapping, str)) for item in raw_bindings):
        raise ValueError(f"{path}.schema_bindings must be a list of binding objects or table.column strings")
    if semantic_type == "JoinRelation":
        expanded: list[Any] = []
        for binding in raw_bindings:
            if isinstance(binding, Mapping) and {"from_table", "from_column", "to_table", "to_column"} <= set(binding):
                expanded.extend((
                    {"table": binding["from_table"], "column": binding["from_column"], "confidence": binding.get("confidence", 0.0)},
                    {"table": binding["to_table"], "column": binding["to_column"], "confidence": binding.get("confidence", 0.0)},
                ))
            else:
                expanded.append(binding)
        raw_bindings = expanded
    confidence = value.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError(f"{path}.confidence must be numeric")
    # A parser cannot provide user confirmation.  Any model status is ignored
    # and normalized to unresolved/proposed by the adapter.
    attributes = value.get("attributes", {})
    if attributes is None:
        attributes = {}
    if not isinstance(attributes, Mapping):
        raise ValueError(f"{path}.attributes must be an object")
    # Compatibility: preserve a small set of known semantic fields emitted at
    # the node top level instead of silently dropping them.
    known_attribute_keys = {"direction", "limit", "offset", "distinct", "join_type", "having", "partition_by", "window", "aggregation", "group_by", "measure", "k", "source_span", "evidence_span"}
    attributes = {**dict(attributes), **{key: value[key] for key in known_attribute_keys if key in value}}
    return IntentNode(
        node_id=node_id,
        semantic_type=semantic_type,
        business_meaning=meaning,
        schema_bindings=tuple(_parse_binding(item, f"{path}.schema_bindings[{i}]") for i, item in enumerate(raw_bindings)),
        value=value.get("value"),
        operator=value.get("operator"),
        dependencies=tuple(raw_dependencies),
        confidence=float(confidence),
        permission_state="unknown",
        status="unresolved",
        attributes=attributes,
    )


def parse_edge(value: Mapping[str, Any], index: int) -> IntentEdge:
    path = f"edges[{index}]"
    edge_id = _required_string(value, "id", path)
    source = _required_string(value, "source", path)
    target = _required_string(value, "target", path)
    kind = _required_string(value, "kind", path)
    if kind not in _EDGE_KINDS:
        raise ValueError(f"{path}.kind is unsupported: {kind}")
    label = value.get("label", "")
    if not isinstance(label, str):
        raise ValueError(f"{path}.label must be a string")
    return IntentEdge(edge_id, source, target, kind, label)


def parse_graph(value: Mapping[str, Any]) -> IntentGraph:
    value = _canonicalize_graph_payload(value)
    raw_nodes = value.get("nodes", ())
    raw_edges = value.get("edges", ())
    if not isinstance(raw_nodes, list) or any(not isinstance(item, Mapping) for item in raw_nodes):
        raise ValueError("graph.nodes must be a list of objects")
    if not isinstance(raw_edges, list) or any(not isinstance(item, Mapping) for item in raw_edges):
        raise ValueError("graph.edges must be a list of objects")
    value = _canonicalize_edges(value)
    raw_nodes = value.get("nodes", ())
    raw_edges = value.get("edges", ())
    graph = IntentGraph(
        nodes=tuple(parse_node(item, i) for i, item in enumerate(raw_nodes)),
        edges=tuple(parse_edge(item, i) for i, item in enumerate(raw_edges)),
    )
    validate_graph_structure(graph)
    return graph


def validate_graph_structure(graph: IntentGraph) -> None:
    """Reject graphs that cannot preserve ranking dependencies.

    This is a parser-contract invariant, rather than an execution heuristic:
    a ranking is meaningless without a measure, and a TopK is meaningless
    without a ranking.  The adapter can therefore ask the parser to repair a
    malformed graph before dialogue or Text2SQL sees it.
    """
    by_id = {node.node_id: node for node in graph.nodes}
    for node in graph.nodes:
        deps = [by_id.get(item) for item in node.dependencies]
        if node.semantic_type == "Ranking" and not any(item and item.semantic_type in {"MeasureExpression", "Aggregation"} for item in deps):
            raise ValueError(f"ranking node {node.node_id} must depend on MeasureExpression or Aggregation")
        if node.semantic_type == "TopK" and not any(item and item.semantic_type == "Ranking" for item in deps):
            raise ValueError(f"TopK node {node.node_id} must depend on Ranking")
        if node.semantic_type == "MeasureExpression":
            aggregation = str(node.operator or node.attributes.get("aggregation", "")).strip()
            if not aggregation:
                raise ValueError(f"measure node {node.node_id} must declare aggregation")


def _canonicalize_edges(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize safe omissions in model edges without inventing relations."""
    nodes = {str(item.get("id")): str(item.get("semantic_type")) for item in value.get("nodes", ()) if isinstance(item, Mapping)}
    edges = value.get("edges", [])
    if not isinstance(edges, list):
        return value
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(edges):
        if not isinstance(item, Mapping):
            return value
        source, target = item.get("source"), item.get("target")
        if not isinstance(source, str) or not isinstance(target, str) or source not in nodes or target not in nodes:
            normalized.append(dict(item))
            continue
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            kind = "dependency"
        # The graph contract stores dependency edges parent -> dependent.
        if kind == "dependency" and nodes.get(source) != "TargetEntity" and nodes.get(target) == "TargetEntity":
            source, target = target, source
        normalized.append({"id": str(item.get("id") or f"edge_{index}_{source}_{target}"), "source": source, "target": target, "kind": kind, "label": str(item.get("label", ""))})
    value["edges"] = normalized
    return value


def _canonicalize_graph_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Accept one conservative legacy shape emitted by some chat models.

    A few models answer a request for a semantic graph with
    ``{"type":"select", "table":..., "columns":[...]}``.  This is
    converted into explicit target/output nodes; no permission or user
    confirmation is inferred during this compatibility step.
    """
    raw_nodes = value.get("nodes", ())
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return dict(value)
    if not all(isinstance(item, Mapping) and item.get("type") == "select" for item in raw_nodes):
        return _promote_target_bindings(dict(value))
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = list(value.get("edges", ())) if isinstance(value.get("edges", ()), list) else []
    for index, item in enumerate(raw_nodes):
        table = item.get("table")
        columns = item.get("columns", [])
        if not isinstance(table, str) or not table.strip() or not isinstance(columns, list):
            return dict(value)
        target_id = f"target_{table}"
        nodes.append({"id": target_id, "semantic_type": "TargetEntity", "business_meaning": f"{table} records"})
        for column in columns:
            if not isinstance(column, str) or not column.strip():
                return dict(value)
            output_id = f"output_{table}_{column}"
            nodes.append({
                "id": output_id,
                "semantic_type": "OutputExpression",
                "business_meaning": column,
                "schema_bindings": [{"table": table, "column": column}],
                "dependencies": [target_id],
            })
            edges.append({"id": f"depends_{output_id}", "source": target_id, "target": output_id, "kind": "dependency"})
    return _promote_target_bindings({"version": value.get("version", "semantic-intent-graph-v1"), "nodes": nodes, "edges": edges})


def _promote_target_bindings(value: dict[str, Any]) -> dict[str, Any]:
    """Promote leaf bindings accidentally attached to entities to outputs."""
    nodes = value.get("nodes")
    if not isinstance(nodes, list):
        return value
    additions: list[dict[str, Any]] = []
    edges = value.setdefault("edges", [])
    for item in list(nodes):
        if not isinstance(item, dict) or item.get("semantic_type") != "TargetEntity":
            continue
        bindings = item.get("schema_bindings") or []
        if not isinstance(bindings, list):
            continue
        target_id = str(item.get("id", "target"))
        item["schema_bindings"] = []
        for index, binding in enumerate(bindings):
            if not isinstance(binding, Mapping):
                continue
            table, column = binding.get("table"), binding.get("column")
            if not isinstance(column, str) or not column.strip():
                columns = binding.get("columns")
                column = columns[0] if isinstance(columns, list) and len(columns) == 1 else None
            if not isinstance(table, str) or not table.strip() or not isinstance(column, str) or not column.strip():
                continue
            output_id = f"{target_id}_output_{column}_{index}"
            additions.append({
                "id": output_id,
                "semantic_type": "OutputExpression",
                "business_meaning": str(column),
                "schema_bindings": [dict(binding, column=column)],
                "dependencies": [target_id],
                "confidence": binding.get("confidence", 0.0),
            })
            if isinstance(edges, list):
                edges.append({"id": f"depends_{output_id}", "source": target_id, "target": output_id, "kind": "dependency"})
    nodes.extend(additions)
    return value


def parse_agent_response(raw: Mapping[str, Any]) -> tuple[IntentGraph, tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Validate a ``parse_update`` response for backwards compatibility."""
    parsed = parse_agent_action_response(raw)
    if parsed.action != "parse_update" or parsed.graph is None:
        raise ValueError("parse_agent_response only accepts action=parse_update")
    return parsed.graph, parsed.hypotheses, parsed.diagnostics


def parse_agent_action_response(raw: Mapping[str, Any]) -> AgentActionResponse:
    """Validate one of the three independent parser actions.

    ``ask`` and ``propose_alternative`` intentionally do not require a graph;
    their payloads are dialogue instructions and transformation proposals.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("agent response must be an object")
    action = raw.get("action", "parse_update")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported parser action: {action}")
    diagnostics: list[str] = []
    if action == "ask":
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("ask action requires a non-empty question")
        ids = raw.get("target_node_ids", [])
        if not isinstance(ids, list) or any(not isinstance(item, str) or not item.strip() for item in ids):
            raise ValueError("ask.target_node_ids must be a list of non-empty strings")
        shape = raw.get("expected_answer_shape") or "free_text"
        if not isinstance(shape, str) or not shape.strip():
            raise ValueError("ask.expected_answer_shape must be a string")
        return AgentActionResponse(action, clarification=ClarificationRequest(question.strip(), tuple(ids), shape.strip()), diagnostics=tuple(diagnostics))
    if action == "propose_alternative":
        value = raw.get("alternative")
        if not isinstance(value, Mapping):
            raise ValueError("propose_alternative action requires alternative object")
        summary = value.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("alternative.summary must be a non-empty string")
        ids = value.get("source_node_ids", [])
        changes = value.get("changes", [])
        rationale = value.get("rationale", "")
        if not isinstance(ids, list) or any(not isinstance(item, str) or not item.strip() for item in ids):
            raise ValueError("alternative.source_node_ids must be a list of non-empty strings")
        if not isinstance(changes, list) or any(not isinstance(item, Mapping) for item in changes):
            raise ValueError("alternative.changes must be a list of objects")
        if not isinstance(rationale, str):
            raise ValueError("alternative.rationale must be a string")
        return AgentActionResponse(action, alternative=AlternativeProposal(summary.strip(), tuple(ids), tuple(dict(item) for item in changes), rationale), diagnostics=tuple(diagnostics))
    graph_payload = raw.get("graph")
    if not isinstance(graph_payload, Mapping):
        raise ValueError("parse_update action requires graph object")
    graph = parse_graph(graph_payload)
    hypotheses = raw.get("hypotheses", [])
    if not isinstance(hypotheses, list) or any(not isinstance(item, Mapping) for item in hypotheses):
        raise ValueError("hypotheses must be a list of objects")
    if any("user_evidence" in node for node in graph_payload.get("nodes", ()) if isinstance(node, Mapping)):
        diagnostics.append("agent_user_evidence_ignored")
    if any(node.get("status") in {"confirmed", "blocked", "rejected"} for node in graph_payload.get("nodes", ()) if isinstance(node, Mapping)):
        diagnostics.append("agent_node_status_normalized")
    return AgentActionResponse(action, graph=graph, hypotheses=tuple(dict(item) for item in hypotheses), diagnostics=tuple(diagnostics))
