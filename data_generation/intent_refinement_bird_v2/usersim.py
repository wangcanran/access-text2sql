"""Legacy user-simulator adapters.

The active v2 contract path uses ``ContractRuntime`` and
``ContractUserSimulator``.  The classes in this module remain only for stage-6
compatibility and deterministic policy tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .alternative_generator import AlternativeCandidate
from .contracts import EvaluationCase
from .profile_policy import AlternativeDecision, ProfileName, choose_alternative


@dataclass(frozen=True)
class UserResponse:
    answer: str
    decision: str
    referenced_node_ids: tuple[str, ...] = ()
    selected_transformation_id: str | None = None


@dataclass(frozen=True)
class SimulatorPayload:
    """Only this public view may be sent to an agent-facing component."""

    case_id: str
    initial_request: str
    role: str
    schema: dict[str, Any]
    foreign_keys: tuple[dict[str, str], ...]
    policy: dict[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "initial_request": self.initial_request,
            "role": self.role,
            "schema": self.schema,
            "foreign_keys": [dict(item) for item in self.foreign_keys],
            "policy": {table: list(columns) for table, columns in self.policy.items()},
        }


class OfflineUserSimulator:
    """Deterministic simulator used only by offline evaluation.

    ``EvaluationCase`` and its private fields never leave this object.  The
    simulator can answer a question from the hidden original request, while
    the public payload is separately constructed for the agent.
    """

    def __init__(self, case: EvaluationCase, *, profile: ProfileName | None = None):
        self._case = case
        self.profile: ProfileName = profile or (case.profile if case.profile in {"flexible_user", "preference_constrained_user", "strict_original_user"} else "flexible_user")
        self._turn = 0

    @property
    def case_id(self) -> str:
        return self._case.public.case_id

    def agent_payload(self) -> dict[str, Any]:
        """Return the public case; assert private fields are absent."""
        payload = self._case.public.agent_payload()
        forbidden = ("original_request", "gold_sql", "pseudo_sql", "profile", "private_metadata", "evaluation_only")
        if any(key in payload for key in forbidden):
            raise AssertionError("private evaluation field crossed agent boundary")
        return payload

    def public_view(self) -> SimulatorPayload:
        public = self._case.public
        return SimulatorPayload(public.case_id, public.initial_request, public.role, public.schema, public.foreign_keys, public.policy)

    def answer_clarification(
        self,
        question: str,
        *,
        referenced_node_ids: Iterable[str] = (),
        mode: str = "answer",
    ) -> UserResponse:
        """Answer based on the hidden original request, never on SQL labels."""
        self._turn += 1
        if mode == "refuse":
            return UserResponse("I cannot provide more detail.", "refuse", tuple(referenced_node_ids))
        if mode == "vague":
            return UserResponse("Please use the interpretation closest to my request.", "answer", tuple(referenced_node_ids))
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must not be empty")
        # The original request is the sole semantic backing for a normal
        # answer.  The question text is included only to make the transcript
        # auditable; it cannot alter the hidden target.
        answer = self._case.original_request.strip()
        if not answer:
            return UserResponse("I cannot clarify that request.", "refuse", tuple(referenced_node_ids))
        return UserResponse(answer, "answer", tuple(referenced_node_ids))

    def decide_alternative(self, candidates: Iterable[AlternativeCandidate]) -> UserResponse:
        candidates = tuple(candidates)
        transformations = tuple(item.transformation for item in candidates)
        decision: AlternativeDecision = choose_alternative(self.profile, transformations, private_metadata=self._case.private_metadata)
        if decision.decision == "reject":
            return UserResponse("I do not accept these changes to my request.", "reject", selected_transformation_id=None)
        selected = next(item for item in candidates if item.id == decision.transformation_id)
        return UserResponse(f"I accept this alternative: {selected.summary}", "accept", selected_transformation_id=selected.id)


def build_user_simulator(case: EvaluationCase, *, profile: ProfileName | None = None, client: Any | None = None, renderer: Any | None = None):
    """Build the legacy evaluation user.

    A model client is required for the production/offline-evaluation path;
    the deterministic implementation remains available only for legacy unit
    tests and policy checks.  New v2 code must use ``ContractRuntime`` instead
    of this factory.
    """
    if client is not None:
        from .llm_usersim import build_llm_user_simulator
        return build_llm_user_simulator(case, client, profile=profile, renderer=renderer)
    return OfflineUserSimulator(case, profile=profile)
