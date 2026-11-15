"""Small deterministic dialogue runner for stage 4.

The runner owns evidence, budgets and state derivation.  An LLM parser may
suggest a graph update, but it cannot mark a node confirmed or end a session.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from .evidence import Evidence
from .intent_graph import IntentGraph, IntentNode
from .plan_validator import PlanValidation, validate_plan
from .question_selector import QuestionCandidate, question_fingerprint, select_questions
from .state_deriver import DerivedState, derive_state
from .user_renderer import UserRenderer, renderer_for_context
from .alternative_generator import AlternativeCandidate, generate_alternatives
from .semantic_readiness import assess_semantic_readiness


@dataclass(frozen=True)
class DialogueTurn:
    turn: int
    question: QuestionCandidate
    answer: str
    touched_node_ids: tuple[str, ...]
    progress: bool


@dataclass(frozen=True)
class DialogueSnapshot:
    graph: IntentGraph
    validation: PlanValidation
    state: DerivedState
    turns: tuple[DialogueTurn, ...] = ()
    asked_fingerprints: tuple[str, ...] = ()
    no_progress_turns: int = 0
    alternatives: tuple[AlternativeCandidate, ...] = ()


class DialogueRunner:
    def __init__(self, public_context: Mapping[str, Any], *, max_turns: int = 8, max_no_progress_turns: int = 2, renderer: UserRenderer | None = None, bird_description_root: str | None = None):
        if max_turns <= 0 or max_no_progress_turns < 0:
            raise ValueError("dialogue budgets must be positive")
        self.public_context = dict(public_context)
        self.max_turns = max_turns
        self.max_no_progress_turns = max_no_progress_turns
        self.renderer = renderer or renderer_for_context(public_context, bird_description_root=bird_description_root)

    def start(self, graph: IntentGraph, hypotheses, *, alternatives_pending: bool = False) -> DialogueSnapshot:
        validation = validate_plan(graph, self.public_context)
        questions = select_questions(graph, hypotheses, renderer=self.renderer)
        selected = questions[0] if questions else None
        readiness = assess_semantic_readiness(graph, hypotheses)
        alternatives: tuple[AlternativeCandidate, ...] = ()
        # Once semantic uncertainty is gone, a permission failure enters
        # negotiation automatically if the deterministic generator can offer
        # at least one finite, schema/FK-backed transformation.
        semantic_ready = selected is None and readiness.ready
        if semantic_ready and validation.status == "permission_denied":
            alternatives = generate_alternatives(graph, self.public_context, renderer=self.renderer)
        pending = alternatives_pending or bool(alternatives)
        state = derive_state(graph, validation, selected_question=selected, alternatives_pending=pending, semantic_ready=readiness.ready)
        return DialogueSnapshot(
            graph, validation, state,
            asked_fingerprints=tuple(item.fingerprint for item in (selected,) if item),
            alternatives=alternatives,
        )

    def answer(self, snapshot: DialogueSnapshot, answer: str, *, touched_node_ids: Iterable[str] | None = None) -> DialogueSnapshot:
        if snapshot.state.selected_question is None:
            raise ValueError("there is no pending question")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("answer must not be empty")
        question = snapshot.state.selected_question
        requested_touched = question.target_node_ids if touched_node_ids is None else touched_node_ids
        touched = tuple(dict.fromkeys(str(item) for item in requested_touched))
        touched_set = set(touched)
        updated_nodes: list[IntentNode] = []
        for node in snapshot.graph.nodes:
            if node.node_id not in touched_set:
                updated_nodes.append(node)
                continue
            evidence = node.user_evidence + (Evidence(f"user_turn_{len(snapshot.turns) + 1}_{node.node_id}", len(snapshot.turns) + 1, answer.strip(), "user"),)
            updated_nodes.append(node.with_update(user_evidence=evidence, status="confirmed"))
        graph = IntentGraph(tuple(updated_nodes), snapshot.graph.edges, snapshot.graph.version)
        progress = bool(touched_set) and touched_set <= {node.node_id for node in snapshot.graph.nodes}
        no_progress = 0 if progress else snapshot.no_progress_turns + 1
        turn = DialogueTurn(len(snapshot.turns) + 1, question, answer.strip(), touched, progress)
        validation = validate_plan(graph, self.public_context)
        asked = tuple(dict.fromkeys((*snapshot.asked_fingerprints, question.fingerprint)))
        next_questions = select_questions(graph, _single_hypothesis_set(graph), asked_fingerprints=asked, renderer=self.renderer)
        selected = next_questions[0] if next_questions else None
        readiness = assess_semantic_readiness(graph, _single_hypothesis_set(graph))
        state = derive_state(graph, validation, selected_question=selected, semantic_ready=readiness.ready, turn=len(snapshot.turns) + 1, max_turns=self.max_turns, no_progress_turns=no_progress, max_no_progress_turns=self.max_no_progress_turns)
        return DialogueSnapshot(graph, validation, state, snapshot.turns + (turn,), asked, no_progress, snapshot.alternatives)


def _single_hypothesis_set(graph: IntentGraph):
    from .intent_graph import Hypothesis, HypothesisSet
    unresolved = tuple(node.node_id for node in graph.unresolved_nodes())
    return HypothesisSet().add(Hypothesis("dialogue_current", graph, 1.0, unresolved))
