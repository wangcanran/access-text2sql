"""Stage-8 offline evaluation harness.

The harness deliberately keeps three boundaries visible: the public payload
sent to the agent, the private payload sent only to the user simulator, and
the result returned by the system under evaluation.  It does not make model
calls itself; a caller supplies a deterministic or live case runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Union

from .contracts import EvaluationCase, PublicCase


_PRIVATE_KEYS = {
    "original_request", "gold_sql", "pseudo_sql", "profile", "user_profile",
    "private_metadata", "evaluation_only", "private", "original_questions",
}


def _public_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _public_copy(item)
            for key, item in value.items()
            if str(key).casefold() not in _PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_public_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_public_copy(item) for item in value)
    return value


@dataclass(frozen=True)
class TraceEvent:
    """One auditable turn in the public interaction trace."""

    turn: int
    action: str
    state: str | None = None
    question: str | None = None
    answer_kind: str | None = None
    target_node_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    public_payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "action": self.action,
            "state": self.state,
            "question": self.question,
            "answer_kind": self.answer_kind,
            "target_node_ids": list(self.target_node_ids),
            "diagnostics": list(self.diagnostics),
            "public_payload": _public_copy(dict(self.public_payload)),
        }


@dataclass(frozen=True)
class CaseEvaluation:
    """Normalized result expected from a stage-8 case runner."""

    case_id: str
    role: str
    profile: str
    status: str
    turns: int = 0
    semantic_exact: bool | None = None
    semantic_loss: float | None = None
    permission_violation: bool = False
    sql_executable: bool = False
    result_correct: bool | None = None
    termination_reason: str = ""
    trace: tuple[TraceEvent, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def safe(self) -> bool:
        return not self.permission_violation

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "role": self.role,
            "profile": self.profile,
            "status": self.status,
            "turns": self.turns,
            "semantic_exact": self.semantic_exact,
            "semantic_loss": self.semantic_loss,
            "permission_violation": self.permission_violation,
            "sql_executable": self.sql_executable,
            "result_correct": self.result_correct,
            "termination_reason": self.termination_reason,
            "trace": [event.to_dict() for event in self.trace],
            "metadata": _public_copy(dict(self.metadata)),
        }


CaseRunner = Callable[[PublicCase, Mapping[str, Any], Mapping[str, Any], str], Union[CaseEvaluation, Mapping[str, Any]]]


def _normalize_result(result: CaseEvaluation | Mapping[str, Any], case: EvaluationCase, profile: str) -> CaseEvaluation:
    if isinstance(result, CaseEvaluation):
        return result
    if not isinstance(result, Mapping):
        raise TypeError("case runner must return CaseEvaluation or a mapping")
    raw_trace = result.get("trace", ())
    trace: list[TraceEvent] = []
    for item in raw_trace if isinstance(raw_trace, (list, tuple)) else ():
        if isinstance(item, TraceEvent):
            trace.append(item)
        elif isinstance(item, Mapping):
            trace.append(TraceEvent(
                turn=int(item.get("turn", len(trace) + 1)),
                action=str(item.get("action", "unknown")),
                state=str(item["state"]) if item.get("state") is not None else None,
                question=str(item["question"]) if item.get("question") is not None else None,
                answer_kind=str(item["answer_kind"]) if item.get("answer_kind") is not None else None,
                target_node_ids=tuple(str(value) for value in item.get("target_node_ids", ())),
                diagnostics=tuple(str(value) for value in item.get("diagnostics", ())),
                public_payload=_public_copy(item.get("public_payload", {})),
            ))
    return CaseEvaluation(
        case_id=str(result.get("case_id", case.public.case_id)),
        role=str(result.get("role", case.public.role)),
        profile=str(result.get("profile", profile)),
        status=str(result.get("status", "unknown")),
        turns=int(result.get("turns", len(trace))),
        semantic_exact=result.get("semantic_exact"),
        semantic_loss=float(result["semantic_loss"]) if result.get("semantic_loss") is not None else None,
        permission_violation=bool(result.get("permission_violation", False)),
        sql_executable=bool(result.get("sql_executable", False)),
        result_correct=result.get("result_correct"),
        termination_reason=str(result.get("termination_reason", "")),
        trace=tuple(trace),
        metadata=_public_copy(dict(result.get("metadata", {}))) if isinstance(result.get("metadata", {}), Mapping) else {},
    )


def evaluate_case(case: EvaluationCase, runner: CaseRunner, *, profile: str | None = None) -> CaseEvaluation:
    """Run one case with explicit public/private payload separation."""
    selected_profile = profile or case.profile or "strict_original_user"
    result = runner(
        case.public,
        _public_copy(case.public.agent_payload()),
        # This payload is intentionally private and is only handed to the
        # offline user simulator.  The runner must never forward it to the
        # agent or Text2SQL boundary.
        case.simulator_payload(),
        selected_profile,
    )
    return _normalize_result(result, case, selected_profile)


def evaluate_matrix(
    cases: Iterable[EvaluationCase],
    runner: CaseRunner,
    *,
    profiles: Iterable[str] = ("strict_original_user", "preference_constrained_user", "flexible_user"),
) -> tuple[CaseEvaluation, ...]:
    """Run the case x profile matrix in stable input order."""
    output: list[CaseEvaluation] = []
    selected_profiles = tuple(str(profile) for profile in profiles)
    for case in cases:
        for profile in selected_profiles:
            output.append(evaluate_case(case, runner, profile=profile))
    return tuple(output)
