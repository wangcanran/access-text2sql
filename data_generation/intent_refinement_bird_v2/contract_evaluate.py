"""Evaluation adapter for the contract migration path."""

from __future__ import annotations

from typing import Any, Mapping

from .evaluate import CaseEvaluation, TraceEvent


def contract_result_to_evaluation(result: Mapping[str, Any], *, role: str, profile: str, case_id: str) -> CaseEvaluation:
    """Normalize contract smoke output without treating failures as refusals."""
    status = str(result.get("status", "unknown"))
    trace_items = result.get("trace", ())
    trace: list[TraceEvent] = []
    for index, item in enumerate(trace_items if isinstance(trace_items, (list, tuple)) else ()):
        if not isinstance(item, Mapping):
            continue
        diagnostics = []
        if item.get("error"):
            diagnostics.append(str(item["error"]))
        if item.get("mapping_error"):
            diagnostics.append(str(item["mapping_error"]))
        if item.get("parser_failure"):
            diagnostics.append("parser_failure")
        trace.append(TraceEvent(int(item.get("turn", index + 1)), str(item.get("kind", "contract_turn")), state=str(item.get("status")) if item.get("status") is not None else None, question=str(item.get("question")) if item.get("question") else None, answer_kind=str(item.get("answer_kind")) if item.get("answer_kind") else None, diagnostics=tuple(diagnostics), public_payload={"alternatives": item.get("alternatives", ())}))
    safe_refusal = status in {"SAFE_REFUSAL", "contract_permission_denied", "contract_binding_required", "INVALID_SCHEMA_BINDING"}
    permission_violation = bool(result.get("permission_violation", False)) or status == "permission_violation"
    sql = result.get("sql")
    return CaseEvaluation(
        case_id=str(result.get("case_id", case_id)), role=role, profile=profile, status=status,
        turns=len(trace), semantic_exact=result.get("semantic_exact"), semantic_loss=float(result["semantic_loss"]) if result.get("semantic_loss") is not None else None,
        permission_violation=permission_violation, sql_executable=bool(result.get("sql_executable", bool(sql and status == "success"))),
        result_correct=result.get("result_correct"), termination_reason="safe_refusal" if safe_refusal else status,
        trace=tuple(trace), metadata={"final_request": result.get("final_request", ""), "sql": sql or ""},
    )

