"""Rule-derived interaction states for stage 4."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .intent_graph import IntentGraph
from .plan_validator import PlanValidation
from .question_selector import QuestionCandidate


STATE_NEEDS_DISAMBIGUATION = "NEEDS_DISAMBIGUATION"
STATE_BLOCKED_BY_PERMISSION = "BLOCKED_BY_PERMISSION"
STATE_WAITING_FOR_ALTERNATIVE_AUTHORIZATION = "WAITING_FOR_ALTERNATIVE_AUTHORIZATION"
STATE_READY_ORIGINAL = "READY_ORIGINAL"
STATE_READY_AUTHORIZED_ALTERNATIVE = "READY_AUTHORIZED_ALTERNATIVE"
STATE_SAFE_REFUSAL = "SAFE_REFUSAL"
STATE_STOP_UNRESOLVED = "STOP_UNRESOLVED"


@dataclass(frozen=True)
class DerivedState:
    status: str
    reason: str
    selected_question: QuestionCandidate | None = None
    diagnostics: tuple[str, ...] = ()


def _has_user_evidence(graph: IntentGraph) -> bool:
    return all(node.confirmed_by_user for node in graph.nodes if node.semantic_type != "TargetEntity")


def derive_state(
    graph: IntentGraph,
    validation: PlanValidation,
    *,
    selected_question: QuestionCandidate | None = None,
    alternatives_pending: bool = False,
    alternative_authorized: bool = False,
    alternatives_rejected: bool = False,
    turn: int = 0,
    max_turns: int = 8,
    no_progress_turns: int = 0,
    max_no_progress_turns: int = 2,
    semantic_ready: bool | None = None,
) -> DerivedState:
    """Derive state solely from runner data and deterministic checks."""
    if alternatives_rejected and validation.status == "permission_denied":
        return DerivedState(STATE_SAFE_REFUSAL, "user_rejected_permission_alternative")
    if turn >= max_turns or no_progress_turns >= max_no_progress_turns:
        return DerivedState(STATE_STOP_UNRESOLVED, "interaction_budget_exhausted")
    # A permission decision must not preempt unresolved semantic questions.
    # First establish what the user means; only then negotiate a safe
    # permission-preserving transformation.
    if selected_question is not None:
        return DerivedState(STATE_NEEDS_DISAMBIGUATION, "unresolved_high_impact_question", selected_question)
    if semantic_ready is False or (semantic_ready is None and any(node.semantic_type != "TargetEntity" and node.status in {"proposed", "unresolved", "blocked"} for node in graph.nodes)):
        return DerivedState(STATE_NEEDS_DISAMBIGUATION, "semantic_nodes_unresolved")
    if validation.status == "permission_denied":
        if alternatives_pending:
            return DerivedState(STATE_WAITING_FOR_ALTERNATIVE_AUTHORIZATION, "permission_blocked_alternative_pending")
        return DerivedState(STATE_BLOCKED_BY_PERMISSION, "original_execution_requires_inaccessible_field")
    if validation.status != "allowed":
        return DerivedState(STATE_NEEDS_DISAMBIGUATION, f"plan_validation_{validation.status}")
    if semantic_ready is not True and not _has_user_evidence(graph):
        return DerivedState(STATE_NEEDS_DISAMBIGUATION, "required_semantics_lack_user_evidence")
    if alternative_authorized:
        return DerivedState(STATE_READY_AUTHORIZED_ALTERNATIVE, "authorized_transformation_revalidated")
    return DerivedState(STATE_READY_ORIGINAL, "original_intent_confirmed_and_allowed")
