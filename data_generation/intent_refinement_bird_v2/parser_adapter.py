"""Safe adapter from parser output to immutable intent snapshots."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Protocol

from .evidence import Evidence
from .intent_graph import Hypothesis, HypothesisSet, IntentEdge, IntentGraph, IntentNode, SchemaBinding
from .parser_contract import (
    ALTERNATIVE_RESPONSE_SCHEMA, ASK_RESPONSE_SCHEMA, PARSE_UPDATE_RESPONSE_SCHEMA,
    PARSER_RESPONSE_SCHEMA, ParseRequest, parse_agent_action_response,
)
from .parse_errors import ParseAttempt, ParseFailure
from .user_renderer import UserRenderer


class ParserClient(Protocol):
    def complete(self, *, system_prompt: str, user_payload: dict[str, Any], response_schema: dict[str, Any] | None = None) -> Any:
        ...


_STRICT_OUTPUT_NOTE = """
When using the strict schema, include every declared property: use null for an absent
value/operator, [] for absent lists, and {} for absent attributes. Do not add undeclared
properties.
""".strip()


SYSTEM_PROMPT = """You are a semantic parser for a database request.
For parse_update return JSON with action=parse_update, a graph object, and optional hypotheses.
For ask return only a question, target_node_ids, and expected_answer_shape.
For propose_alternative return only an alternative object with summary, source_node_ids,
changes, and rationale. These dialogue actions never need a graph and never authorize
or confirm a transformation.
Extract business meaning and possible schema bindings from the public request.
Represent each requested result field as an OutputExpression node. Represent
the entity/table being searched as TargetEntity, and do not bind a TargetEntity
to a leaf data column. Represent every stated filter, aggregate, grouping,
ordering, and result limit as its own node. Do not collapse output semantics
into an entity node.
Use MeasureExpression for a business measure and make its aggregation and
grouping explicit in attributes. Use Ranking for comparison direction and
TopK for a requested number of winners. Ranking must depend on a
MeasureExpression, and TopK must depend on Ranking. Do not represent
"most", "highest", "largest", or "top" as a bare Ordering node. Preserve
source_span/evidence_span attributes for important phrases when possible.
Do not claim user confirmation, permission approval, authorization, or final readiness.
Never invent private evaluation fields. Every node status is interpreted as unresolved
by the runner; user evidence can only be added by the dialogue runner.

The graph must use exactly this shape:
{"action":"parse_update","graph":{"nodes":[{"id":"target_1","semantic_type":"TargetEntity","business_meaning":"student records","schema_bindings":[],"dependencies":[],"confidence":0.8}],"edges":[]},"hypotheses":[]}
Each node must use one of TargetEntity, OutputExpression, FilterPredicate, Aggregation,
MeasureExpression, Grouping, Ordering, Ranking, TopK, ResultScope, JoinRelation, Subquery. Do not use SQL AST labels such as
select, table, or columns. Always include nodes as a JSON list and edges as a JSON list.
If the request asks for names of students, emit a TargetEntity for student records and
an OutputExpression for student names bound to student.name. Do not return only the
TargetEntity. If multiple readings are plausible, emit separate hypotheses with a graph
for each reading; each hypothesis graph must carry its own semantic difference.

Use the following complete examples as the output contract. They are semantic examples,
not text to copy into unrelated requests.

EXAMPLE 1 — simple output
Input request: "Show the names of students."
Public schema: {"tables":{"student":["id","name"]}}
Output:
{"action":"parse_update","graph":{"nodes":[{"id":"target_student","semantic_type":"TargetEntity","business_meaning":"student records","schema_bindings":[],"dependencies":[],"confidence":0.98},{"id":"output_student_name","semantic_type":"OutputExpression","business_meaning":"student names","schema_bindings":[{"table":"student","column":"name","confidence":0.99}],"dependencies":["target_student"],"confidence":0.99}],"edges":[{"id":"depends_output_student_name","source":"target_student","target":"output_student_name","kind":"dependency"}]},"hypotheses":[]}

EXAMPLE 2 — output plus filter
Input request: "Show student names with scores above 90."
Public schema: {"tables":{"student":["id","name","score"]}}
Output:
{"action":"parse_update","graph":{"nodes":[{"id":"target_student","semantic_type":"TargetEntity","business_meaning":"student records","schema_bindings":[],"dependencies":[],"confidence":0.98},{"id":"output_student_name","semantic_type":"OutputExpression","business_meaning":"student names","schema_bindings":[{"table":"student","column":"name","confidence":0.99}],"dependencies":["target_student"],"confidence":0.99},{"id":"filter_score","semantic_type":"FilterPredicate","business_meaning":"students with scores above 90","schema_bindings":[{"table":"student","column":"score","confidence":0.95}],"operator":">","value":90,"dependencies":["target_student"],"confidence":0.91}],"edges":[{"id":"depends_output_student_name","source":"target_student","target":"output_student_name","kind":"dependency"},{"id":"depends_filter_score","source":"target_student","target":"filter_score","kind":"dependency"}]},"hypotheses":[]}

EXAMPLE 3 — two plausible readings
Input request: "Show the highest score for each student."
Public schema: {"tables":{"student":["id","name","score"],"exam_result":["student_id","total_score"]}}
Output:
{"action":"parse_update","graph":{"nodes":[{"id":"target_student","semantic_type":"TargetEntity","business_meaning":"student records","schema_bindings":[],"dependencies":[],"confidence":0.9},{"id":"output_highest_score","semantic_type":"OutputExpression","business_meaning":"highest score for each student","schema_bindings":[],"dependencies":["target_student"],"confidence":0.65}],"edges":[{"id":"depends_output_highest_score","source":"target_student","target":"output_highest_score","kind":"dependency"}]},"hypotheses":[{"id":"h_student_score","score":0.55,"graph":{"nodes":[{"id":"target_student","semantic_type":"TargetEntity","business_meaning":"student records","schema_bindings":[],"dependencies":[],"confidence":0.9},{"id":"aggregate_student_score","semantic_type":"Aggregation","business_meaning":"maximum student score per student","schema_bindings":[{"table":"student","column":"score","confidence":0.75}],"operator":"MAX","dependencies":["target_student"],"confidence":0.55}],"edges":[{"id":"depends_aggregate_student_score","source":"target_student","target":"aggregate_student_score","kind":"dependency"}]},"unresolved_node_ids":[]},{"id":"h_exam_total_score","score":0.45,"graph":{"nodes":[{"id":"target_student","semantic_type":"TargetEntity","business_meaning":"student records","schema_bindings":[],"dependencies":[],"confidence":0.9},{"id":"aggregate_exam_total","semantic_type":"Aggregation","business_meaning":"maximum exam total score per student","schema_bindings":[{"table":"exam_result","column":"total_score","confidence":0.7}],"operator":"MAX","dependencies":["target_student"],"confidence":0.45}],"edges":[{"id":"depends_aggregate_exam_total","source":"target_student","target":"aggregate_exam_total","kind":"dependency"}]},"unresolved_node_ids":[]}]}

In every example, status is omitted or unresolved; user_evidence is absent; permission is
unknown. The runner adds user evidence only after an actual user answer. Never copy a
schema binding from an example when the current public schema does not contain it.

EXAMPLE 4 — measure, grouping, ranking, and top-k
Input request: "Which customers, paying in CZK, consumed the most gas in 2011?"
Public schema: {"tables":{"customers":["CustomerID","Currency"],"transactions_1k":["CustomerID","Consumption","Year"]}}
Output:
{"action":"parse_update","graph":{"nodes":[
 {"id":"target_customer","semantic_type":"TargetEntity","business_meaning":"customers","schema_bindings":[],"dependencies":[],"confidence":0.98,"attributes":{"source_span":"customers"}},
 {"id":"filter_currency","semantic_type":"FilterPredicate","business_meaning":"customers paying in CZK","schema_bindings":[{"table":"customers","column":"Currency","confidence":0.98}],"value":"CZK","operator":"=","dependencies":["target_customer"],"confidence":0.98,"attributes":{"source_span":"paying in CZK"}},
 {"id":"filter_year","semantic_type":"FilterPredicate","business_meaning":"transactions in 2011","schema_bindings":[{"table":"transactions_1k","column":"Year","confidence":0.98}],"value":2011,"operator":"=","dependencies":["target_customer"],"confidence":0.98,"attributes":{"source_span":"in 2011"}},
 {"id":"measure_consumption","semantic_type":"MeasureExpression","business_meaning":"total gas consumed per customer","schema_bindings":[{"table":"transactions_1k","column":"Consumption","confidence":0.97}],"operator":"SUM","dependencies":["target_customer"],"confidence":0.96,"attributes":{"aggregation":"SUM","measure":"gas consumed","group_by":"target_customer","source_span":"consumed"}},
 {"id":"group_customer","semantic_type":"Grouping","business_meaning":"group by customer","schema_bindings":[{"table":"transactions_1k","column":"CustomerID","confidence":0.97}],"dependencies":["target_customer","measure_consumption"],"confidence":0.96,"attributes":{"group_by":"target_customer","source_span":"customers"}},
 {"id":"ranking_consumption","semantic_type":"Ranking","business_meaning":"highest total consumption","schema_bindings":[],"operator":"DESC","dependencies":["measure_consumption","group_customer"],"confidence":0.95,"attributes":{"direction":"DESC","source_span":"most"}},
 {"id":"top_one","semantic_type":"TopK","business_meaning":"the top customer or tied customers","schema_bindings":[],"value":1,"dependencies":["ranking_consumption"],"confidence":0.95,"attributes":{"k":1,"source_span":"the most"}},
 {"id":"output_customer","semantic_type":"OutputExpression","business_meaning":"customer identifiers","schema_bindings":[{"table":"customers","column":"CustomerID","confidence":0.98}],"dependencies":["target_customer","top_one"],"confidence":0.98,"attributes":{"source_span":"Which customers"}}
 ],"edges":[]},"hypotheses":[]}
""".strip()
SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n" + _STRICT_OUTPUT_NOTE


class ParseAdapter:
    def __init__(self, client: ParserClient, *, max_repair_attempts: int = 1, renderer: UserRenderer | None = None):
        self.client = client
        self.renderer = renderer
        if max_repair_attempts < 0 or max_repair_attempts > 1:
            raise ValueError("max_repair_attempts must be 0 or 1")
        self.max_repair_attempts = max_repair_attempts

    def parse(self, request: ParseRequest) -> "ParseResult":
        payload = request.to_payload()
        diagnostics: list[str] = []
        last_error: Exception | None = None
        raw = None
        for attempt in range(self.max_repair_attempts + 1):
            try:
                raw = self.client.complete(system_prompt=SYSTEM_PROMPT, user_payload=payload, response_schema=PARSE_UPDATE_RESPONSE_SCHEMA)
            except TypeError:
                # Test doubles and older compatible clients may not accept the
                # optional schema keyword; they still receive the same prompt.
                raw = self.client.complete(system_prompt=SYSTEM_PROMPT, user_payload=payload)
            try:
                if isinstance(raw, str):
                    import json
                    raw = json.loads(raw)
                action_response = parse_agent_action_response(raw)
                if action_response.action != "parse_update" or action_response.graph is None:
                    raise ValueError("ParseAdapter.parse requires action=parse_update; use parse_action for dialogue actions")
                graph = action_response.graph
                raw_hypotheses = action_response.hypotheses
                parsed_diagnostics = action_response.diagnostics
                diagnostics.extend(parsed_diagnostics)
                diagnostics.append(f"parser_attempts:{attempt + 1}")
                break
            except (TypeError, ValueError) as exc:
                last_error = exc
                if attempt >= self.max_repair_attempts:
                    raise ValueError(f"parser validation failed after {attempt + 1} attempt(s): {exc}") from exc
                diagnostics.append(f"repair_requested_after:{type(exc).__name__}")
                payload = {
                    **payload,
                    "repair_instruction": f"Return only the strict schema. Correct this validation error: {exc}. Preserve every requested semantic fact; add explicit MeasureExpression, Grouping, Ranking, or TopK dependencies when the request expresses ranking.",
                }
        if raw is None or last_error is not None and 'graph' not in locals():
            raise ValueError("parser did not return a usable response")
        merged, merge_diagnostics = merge_graph(request.prior_graph, graph)
        binding_graph, binding_diagnostics = validate_public_bindings(merged, request.public_context)
        all_diagnostics = tuple(diagnostics) + merge_diagnostics + binding_diagnostics
        hypotheses = build_hypothesis_set(binding_graph, raw_hypotheses, all_diagnostics, request.public_context)
        coverage = validate_semantic_coverage(request.request, binding_graph)
        # A syntactically valid graph can still lose a relation such as
        # "most" -> measure -> ranking -> top-k.  Ask the parser to repair the
        # graph once before exposing a clarification question.  This restores
        # the missing semantics instead of merely blocking execution.
        critical = tuple(item for item in coverage if item.startswith("missing_required_relation:"))
        if critical and self.max_repair_attempts > 0 and "semantic_repair_attempted" not in diagnostics:
            repair_payload = {
                **payload,
                "repair_instruction": (
                    "Semantic coverage audit found: " + ", ".join(critical) + ". "
                    "Repair the graph by adding explicit MeasureExpression, Grouping, Ranking, "
                    "and TopK nodes with dependencies. Preserve all existing facts; return only parse_update."
                ),
            }
            try:
                repaired_raw = self.client.complete(system_prompt=SYSTEM_PROMPT, user_payload=repair_payload, response_schema=PARSE_UPDATE_RESPONSE_SCHEMA)
                if isinstance(repaired_raw, str):
                    import json
                    repaired_raw = json.loads(repaired_raw)
                repaired = parse_agent_action_response(repaired_raw)
                if repaired.action == "parse_update" and repaired.graph is not None:
                    graph = repaired.graph
                    merged, merge_diagnostics = merge_graph(request.prior_graph, graph)
                    binding_graph, binding_diagnostics = validate_public_bindings(merged, request.public_context)
                    raw_hypotheses = repaired.hypotheses
                    hypotheses = build_hypothesis_set(binding_graph, raw_hypotheses, all_diagnostics, request.public_context)
                    coverage = validate_semantic_coverage(request.request, binding_graph)
                    diagnostics.append("semantic_repair_attempted")
            except (TypeError, ValueError):
                diagnostics.append("semantic_repair_failed")
        return ParseResult(binding_graph, hypotheses, tuple(diagnostics) + merge_diagnostics + binding_diagnostics + coverage)

    def parse_safe(self, request: ParseRequest) -> ParseAttempt:
        try:
            return ParseAttempt(result=self.parse(request))
        except ValueError as exc:
            return ParseAttempt(failure=ParseFailure("invalid_parser_response", str(exc), recoverable=True))
        except Exception:
            return ParseAttempt(failure=ParseFailure("parser_transport_or_runtime_error", "parser call failed", recoverable=True))

    def parse_action(self, request: ParseRequest, *, expected_action: str | None = None):
        """Obtain and validate an action response without forcing a graph.

        The runner may use this for a clarification question or an
        alternative proposal.  It deliberately does not mutate a graph or
        infer user authorization.
        """
        payload = request.to_payload()
        schema = {
            None: PARSER_RESPONSE_SCHEMA,
            "ask": ASK_RESPONSE_SCHEMA,
            "propose_alternative": ALTERNATIVE_RESPONSE_SCHEMA,
            "parse_update": PARSE_UPDATE_RESPONSE_SCHEMA,
        }.get(expected_action)
        if expected_action is not None and schema is None:
            raise ValueError(f"unsupported expected action: {expected_action}")
        raw = self.client.complete(system_prompt=SYSTEM_PROMPT, user_payload=payload, response_schema=schema)
        if isinstance(raw, str):
            import json
            raw = json.loads(raw)
        action = parse_agent_action_response(raw)
        return self.renderer.action(action) if self.renderer is not None else action


class ParseResult:
    __slots__ = ("graph", "hypotheses", "diagnostics")

    def __init__(self, graph: IntentGraph, hypotheses: HypothesisSet, diagnostics: tuple[str, ...] = ()):
        self.graph = graph
        self.hypotheses = hypotheses
        self.diagnostics = diagnostics


def merge_graph(prior: IntentGraph | None, incoming: IntentGraph) -> tuple[IntentGraph, tuple[str, ...]]:
    """Merge parser suggestions without losing prior facts or user evidence."""
    if prior is None:
        return incoming, ()
    diagnostics: list[str] = []
    incoming_by_id = {node.node_id: node for node in incoming.nodes}
    merged_nodes: list[IntentNode] = []
    for old in prior.nodes:
        new = incoming_by_id.pop(old.node_id, None)
        if new is None:
            merged_nodes.append(old)
            diagnostics.append(f"preserved_omitted_node:{old.node_id}")
            continue
        if old.confirmed_by_user:
            # A parser update cannot overwrite a user-confirmed fact.
            merged_nodes.append(old)
            if new.business_meaning != old.business_meaning or new.schema_bindings != old.schema_bindings:
                diagnostics.append(f"ignored_change_to_confirmed_node:{old.node_id}")
            continue
        merged_nodes.append(new)
    merged_nodes.extend(incoming_by_id.values())
    node_ids = {node.node_id for node in merged_nodes}
    edges = tuple(edge for edge in (*prior.edges, *incoming.edges) if edge.source_id in node_ids and edge.target_id in node_ids)
    unique_edges = {edge.edge_id: edge for edge in edges}
    return IntentGraph(nodes=tuple(merged_nodes), edges=tuple(unique_edges.values())), tuple(diagnostics)


def validate_public_bindings(graph: IntentGraph, public_context: Mapping[str, Any]) -> tuple[IntentGraph, tuple[str, ...]]:
    """Mark each binding against the public schema without silently deleting it."""
    schema = public_context.get("schema", public_context)
    tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    if not isinstance(tables, Mapping):
        return graph, ("public_schema_unavailable",)
    diagnostics: list[str] = []
    updated_nodes: list[IntentNode] = []
    for node in graph.nodes:
        bindings = []
        for binding in node.schema_bindings:
            columns = tables.get(binding.table)
            valid = isinstance(columns, (list, tuple, set)) and binding.column in columns
            if valid:
                bindings.append(binding)
            else:
                reason = f"unknown_public_schema_binding:{binding.qualified_name}"
                bindings.append(binding.with_update(valid=False, validation_reason=reason))
                diagnostics.append(f"binding_invalid:{node.node_id}:{binding.qualified_name}")
        updated_nodes.append(node.with_update(schema_bindings=tuple(bindings)))
    return IntentGraph(tuple(updated_nodes), graph.edges, graph.version), tuple(diagnostics)


def build_hypothesis_set(graph: IntentGraph, raw_hypotheses: tuple[dict[str, Any], ...], diagnostics: tuple[str, ...] = (), public_context: Mapping[str, Any] | None = None) -> HypothesisSet:
    """Build hypotheses from parser suggestions, falling back to the primary graph."""
    result = HypothesisSet()
    if not raw_hypotheses:
        unresolved = tuple(node.node_id for node in graph.unresolved_nodes())
        return result.add(Hypothesis("h0", graph, score=1.0, unresolved_node_ids=unresolved, diagnostics=diagnostics))
    for index, item in enumerate(raw_hypotheses):
        hypothesis_id = str(item.get("id", f"h{index}"))
        score = item.get("score", 0.0)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
            raise ValueError(f"hypothesis {hypothesis_id} has invalid score")
        raw_graph = item.get("graph")
        hypothesis_graph = graph
        if isinstance(raw_graph, Mapping):
            from .parser_contract import parse_graph
            hypothesis_graph, _ = merge_graph(graph, parse_graph(raw_graph))
        elif isinstance(item.get("business_meaning"), str) and item["business_meaning"].strip():
            # Compatibility with concise models that put one interpretation
            # directly on the hypothesis object instead of nesting a graph.
            binding = item.get("schema_binding")
            new_nodes = list(graph.nodes)
            new_edges = list(graph.edges)
            if isinstance(binding, Mapping) and isinstance(binding.get("table"), str):
                columns = binding.get("columns")
                if not isinstance(columns, list):
                    columns = [binding.get("column")] if isinstance(binding.get("column"), str) else []
                target_nodes = [node for node in graph.nodes if node.semantic_type == "TargetEntity"]
                for column_index, column in enumerate(columns):
                    if not isinstance(column, str) or not column.strip():
                        continue
                    output_id = f"{hypothesis_id}_output_{column_index}"
                    new_nodes.append(IntentNode(
                        output_id, "OutputExpression", item["business_meaning"],
                        schema_bindings=(SchemaBinding(str(binding["table"]), column.strip(), float(score)),),
                        dependencies=tuple(node.node_id for node in target_nodes),
                        confidence=float(score), status="unresolved",
                    ))
                    for target_node in target_nodes:
                        new_edges.append(IntentEdge(f"{hypothesis_id}_edge_{column_index}_{target_node.node_id}", target_node.node_id, output_id, "dependency"))
            if len(new_nodes) != len(graph.nodes):
                hypothesis_graph = IntentGraph(tuple(new_nodes), tuple(new_edges))
        if public_context is not None:
            hypothesis_graph, hypothesis_binding_diagnostics = validate_public_bindings(hypothesis_graph, public_context)
        else:
            hypothesis_binding_diagnostics = ()
        unresolved = tuple(str(value) for value in item.get("unresolved_node_ids", ()))
        unresolved = tuple(dict.fromkeys(unresolved + tuple(node.node_id for node in hypothesis_graph.unresolved_nodes())))
        result = result.add(Hypothesis(hypothesis_id, hypothesis_graph, float(score), unresolved, tuple(diagnostics) + tuple(hypothesis_binding_diagnostics)))
    return result


def validate_semantic_coverage(request: str, graph: IntentGraph) -> tuple[str, ...]:
    """Report obvious missing requested components; never auto-confirms them."""
    import re
    text = request.casefold()
    diagnostics: list[str] = []
    requirements = (
        ("OutputExpression", r"\b(name|names|title|titles|column|columns|field|fields|information|details|show|list|display|return)\b"),
        ("FilterPredicate", r"\b(where|with|without|only|exclude|greater|less|above|below|over|under|between|after|before|recent|highest|lowest|at least|at most)\b"),
        ("Aggregation", r"\b(count|sum|average|avg|mean|total|minimum|maximum)\b"),
        ("Grouping", r"\b(group|grouped|per each|by department|by category|by type)\b"),
        ("Ordering", r"\b(order|sort|sorted|ranking|rank|top|bottom|highest|lowest)\b"),
        ("ResultScope", r"\b(top \d+|first \d+|limit \d+|at most \d+|latest|earliest)\b"),
    )
    for semantic_type, pattern in requirements:
        present_types = {node.semantic_type for node in graph.nodes}
        satisfied = semantic_type == "Ordering" and bool(present_types & {"Ordering", "Ranking"})
        satisfied = satisfied or semantic_type == "ResultScope" and bool(present_types & {"ResultScope", "TopK"})
        if re.search(pattern, text) and not satisfied and not any(node.semantic_type == semantic_type for node in graph.nodes):
            diagnostics.append(f"possible_missing_semantic_component:{semantic_type}")
    ranking_terms = r"\b(most|highest|largest|top|lowest|least)\b"
    if re.search(ranking_terms, text):
        types = {node.semantic_type for node in graph.nodes}
        if not types & {"MeasureExpression", "Aggregation"}:
            diagnostics.append("missing_required_relation:ranking_requires_measure")
        if "Ranking" not in types:
            diagnostics.append("missing_required_relation:explicit_ranking")
        if re.search(r"\b(top\s+\d+|the most|highest|lowest|largest|least)\b", text) and "TopK" not in types and "ResultScope" not in types:
            diagnostics.append("missing_required_relation:ranking_requires_topk")
        if re.search(r"\b(which|what)\s+(customers?|users?|students?|departments?|categories?)\b", text) and "Grouping" not in types:
            diagnostics.append("missing_required_relation:ranking_requires_entity_grouping")
    if re.search(r"\b(per|each|by)\b", text) and any(node.semantic_type in {"MeasureExpression", "Aggregation"} for node in graph.nodes) and "Grouping" not in {node.semantic_type for node in graph.nodes}:
        diagnostics.append("missing_required_relation:measure_requires_grouping")
    # Phrase-level evidence prevents a parser from claiming a broad filter or
    # measure while silently dropping a concrete value from the request.
    if re.search(r"\b20\d{2}\b", text) and not any(node.semantic_type == "FilterPredicate" and re.search(r"\b20\d{2}\b", " ".join((str(node.value or ""), node.business_meaning, str(node.attributes.get("source_span", ""))))) for node in graph.nodes):
        diagnostics.append("missing_required_relation:time_filter")
    if re.search(r"\b[A-Z]{3}\b", request) and not any(node.semantic_type == "FilterPredicate" and re.search(r"\b[A-Z]{3}\b", " ".join((str(node.value or ""), node.business_meaning, str(node.attributes.get("source_span", "")))), flags=re.I) for node in graph.nodes):
        diagnostics.append("missing_required_relation:categorical_filter")
    if re.search(r"\b(consum|spend|amount|revenue|sales|score|count|total|average|mean|sum)\w*\b", text) and not any(node.semantic_type in {"MeasureExpression", "Aggregation", "OutputExpression"} and re.search(r"\b(consum|spend|amount|revenue|sales|score|count|total|average|mean|sum)\w*\b", " ".join((node.business_meaning, str(node.attributes.get("source_span", "")))), flags=re.I) for node in graph.nodes):
        diagnostics.append("missing_required_relation:measure_evidence")
    if any(node.semantic_type == "TargetEntity" and node.schema_bindings for node in graph.nodes):
        diagnostics.append("target_entity_has_column_binding_review_required")
    return tuple(diagnostics)
