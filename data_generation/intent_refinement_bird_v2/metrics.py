"""Aggregated stage-8 evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable

from .evaluate import CaseEvaluation


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class EvaluationMetrics:
    total_cases: int
    safe_cases: int
    safe_success_rate: float
    task_success_rate: float
    safe_refusal_rate: float
    permission_violation_rate: float
    semantic_exact_rate: float
    sql_executable_rate: float
    result_correct_rate: float
    mean_turns: float
    mean_semantic_loss: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "total_cases": self.total_cases,
            "safe_cases": self.safe_cases,
            "safe_success_rate": self.safe_success_rate,
            "task_success_rate": self.task_success_rate,
            "safe_refusal_rate": self.safe_refusal_rate,
            "permission_violation_rate": self.permission_violation_rate,
            "semantic_exact_rate": self.semantic_exact_rate,
            "sql_executable_rate": self.sql_executable_rate,
            "result_correct_rate": self.result_correct_rate,
            "mean_turns": self.mean_turns,
            "mean_semantic_loss": self.mean_semantic_loss,
        }


def aggregate_metrics(results: Iterable[CaseEvaluation]) -> EvaluationMetrics:
    rows = tuple(results)
    n = len(rows)
    exact = tuple(item for item in rows if item.semantic_exact is not None)
    correct = tuple(item for item in rows if item.result_correct is not None)
    losses = tuple(item.semantic_loss for item in rows if item.semantic_loss is not None)
    safe = tuple(item for item in rows if item.safe)
    return EvaluationMetrics(
        total_cases=n,
        safe_cases=len(safe),
        safe_success_rate=_rate(sum(item.safe and item.status in {"READY_ORIGINAL", "READY_AUTHORIZED_ALTERNATIVE", "SAFE_REFUSAL"} for item in rows), n),
        task_success_rate=_rate(sum(item.safe and item.status in {"READY_ORIGINAL", "READY_AUTHORIZED_ALTERNATIVE"} for item in rows), n),
        safe_refusal_rate=_rate(sum(item.safe and item.status == "SAFE_REFUSAL" for item in rows), n),
        permission_violation_rate=_rate(sum(item.permission_violation for item in rows), n),
        semantic_exact_rate=_rate(sum(bool(item.semantic_exact) for item in exact), len(exact)),
        sql_executable_rate=_rate(sum(item.sql_executable for item in rows), n),
        result_correct_rate=_rate(sum(bool(item.result_correct) for item in correct), len(correct)),
        mean_turns=mean(item.turns for item in rows) if rows else 0.0,
        mean_semantic_loss=mean(losses) if losses else 0.0,
    )
