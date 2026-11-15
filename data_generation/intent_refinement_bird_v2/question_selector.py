"""Deterministic information-gain question selection for stage 4."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable, Mapping

from .intent_graph import HypothesisSet, IntentGraph, IntentNode
from .user_renderer import UserRenderer
from .semantic_readiness import assess_semantic_readiness


@dataclass(frozen=True)
class QuestionCandidate:
    question_id: str
    text: str
    target_node_ids: tuple[str, ...]
    distinguishing_hypothesis_ids: tuple[str, ...] = ()
    expected_answer_shape: str = "free_text"
    impact_score: float = 0.0
    reason: str = ""

    @property
    def fingerprint(self) -> str:
        return question_fingerprint(self.text, self.target_node_ids)


_IMPACT = {
    "FilterPredicate": 5.0,
    "Aggregation": 4.8,
    "MeasureExpression": 5.2,
    "Grouping": 4.8,
    "JoinRelation": 4.6,
    "OutputExpression": 4.0,
    "Subquery": 4.0,
    "Ordering": 3.0,
    "Ranking": 3.2,
    "TopK": 2.8,
    "ResultScope": 2.5,
    "TargetEntity": 3.5,
}


def question_fingerprint(text: str, target_node_ids: Iterable[str] = ()) -> str:
    normalized = " ".join(text.casefold().split()) + "|" + ",".join(sorted(set(target_node_ids)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _node_signature(node: IntentNode | None) -> tuple:
    if node is None:
        return (None,)
    return (
        node.semantic_type,
        node.business_meaning.casefold().strip(),
        tuple(binding.qualified_name for binding in node.schema_bindings if binding.valid),
        node.operator,
        repr(node.value),
    )


def _question_for(node: IntentNode, alternatives: tuple[IntentNode, ...], renderer: UserRenderer) -> tuple[str, str]:
    subject = renderer.text(node.business_meaning)
    meanings = [renderer.text(item.business_meaning) for item in alternatives if item.business_meaning.strip()]
    options = "; ".join(dict.fromkeys(meanings))
    if node.semantic_type == "FilterPredicate":
        return f"Which condition should apply to {subject}? Possible interpretations: {options}.", "choice_or_free_text"
    if node.semantic_type == "Aggregation":
        return f"Which measure or aggregation do you want for {subject}? Possible interpretations: {options}.", "choice_or_free_text"
    if node.semantic_type == "MeasureExpression":
        return f"What quantity should be measured and how should it be aggregated for {subject}? Possible interpretations: {options}.", "choice_or_free_text"
    if node.semantic_type == "Grouping":
        return f"What should the results be grouped by for {subject}? Possible interpretations: {options}.", "choice_or_free_text"
    if node.semantic_type == "JoinRelation":
        return f"Which relationship should connect {subject}? Possible interpretations: {options}.", "choice_or_free_text"
    if node.semantic_type == "Ordering":
        return f"How should {subject} be ordered? Possible interpretations: {options}.", "choice_or_free_text"
    if node.semantic_type == "Ranking":
        return f"Should {subject} be ranked from highest to lowest or lowest to highest? Possible interpretations: {options}.", "choice_or_free_text"
    if node.semantic_type == "TopK":
        return f"How many top results should be returned for {subject}? Possible interpretations: {options}.", "number"
    if node.semantic_type == "ResultScope":
        return f"What result limit or scope do you want for {subject}? Possible interpretations: {options}.", "free_text"
    return f"Please clarify what you mean by {subject}. Possible interpretations: {options}.", "choice_or_free_text"


def select_questions(
    graph: IntentGraph,
    hypotheses: HypothesisSet,
    *,
    asked_fingerprints: Iterable[str] = (),
    limit: int = 1,
    renderer: UserRenderer | None = None,
) -> tuple[QuestionCandidate, ...]:
    """Return high-impact questions that distinguish remaining hypotheses.

    The selector never chooses a hypothesis or changes a graph.  A question
    is omitted once its stable fingerprint has been asked.
    """
    if limit <= 0:
        return ()
    asked = set(asked_fingerprints)
    renderer = renderer or UserRenderer(context={})
    # A single, high-confidence interpretation with complete bindings is
    # already semantically clear.  The permission gate still runs afterwards;
    # this only prevents an artificial confirmation question.
    if assess_semantic_readiness(graph, hypotheses).ready:
        return ()
    by_id: dict[str, list[tuple[str, IntentNode | None]]] = {}
    all_hypotheses = hypotheses.hypotheses
    for hypothesis in all_hypotheses:
        for node_id in {node.node_id for node in hypothesis.graph.nodes}:
            by_id.setdefault(node_id, []).append((hypothesis.hypothesis_id, next((node for node in hypothesis.graph.nodes if node.node_id == node_id), None)))
    candidates: list[QuestionCandidate] = []
    primary_unresolved = {node.node_id: node for node in graph.unresolved_nodes()}
    for node_id, primary in primary_unresolved.items():
        variants = by_id.get(node_id, [])
        signatures = {(_node_signature(node)) for _, node in variants}
        # A single interpretation is not a clarification target.  If it is
        # structurally incomplete, the readiness check above has already made
        # that explicit; ask only when there are actual competing meanings.
        if len(signatures) <= 1 and primary.semantic_type not in {"FilterPredicate", "Aggregation", "MeasureExpression", "Grouping", "JoinRelation", "OutputExpression", "Ranking", "TopK"}:
            continue
        # When readiness failed because this node is incomplete (rather than
        # because the whole graph is clear), retain the existing focused
        # question path.  The early return above is what suppresses questions
        # for complete high-confidence level-3 semantics.
        variant_nodes = tuple(node for _, node in variants if node is not None and _node_signature(node) != _node_signature(primary))
        question, shape = _question_for(primary, variant_nodes or (primary,), renderer)
        targets = (node_id,)
        fingerprint = question_fingerprint(question, targets)
        if fingerprint in asked:
            continue
        distinguished = tuple(hid for hid, node in variants if node is not None and _node_signature(node) != _node_signature(primary))
        impact = _IMPACT.get(primary.semantic_type, 1.0) + min(len(distinguished), 5) * 1.5
        candidates.append(QuestionCandidate(
            question_id=f"q_{fingerprint}", text=question, target_node_ids=targets,
            distinguishing_hypothesis_ids=distinguished, expected_answer_shape=shape,
            impact_score=impact, reason=f"unresolved_{primary.semantic_type}",
        ))
    # A question asked for one unresolved node should not be replaced in the
    # same turn by unrelated low-impact questions. The caller can request a
    # new question after incorporating the answer into the graph.
    candidates.sort(key=lambda item: (-item.impact_score, item.question_id))
    return tuple(candidates[:limit])
