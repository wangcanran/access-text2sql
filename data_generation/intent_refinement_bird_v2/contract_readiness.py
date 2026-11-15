"""Rule-derived readiness for the contract migration."""

from __future__ import annotations

from dataclasses import dataclass

from .contract_coverage import CoverageReport
from .contract_obligations import compile_contract_obligations
from .contract_permission import ContractPermissionDiagnostic
from .intent_contract import IntentContract


@dataclass(frozen=True)
class ContractReadiness:
    semantic_ready: bool
    executable_ready: bool
    ready: bool
    reasons: tuple[str, ...] = ()


def assess_contract_readiness(contract: IntentContract, *, coverage: CoverageReport | None = None, permission_diagnostics: tuple[ContractPermissionDiagnostic, ...] = ()) -> ContractReadiness:
    reasons: list[str] = []
    validation = contract.validate()
    reasons.extend(validation.errors)
    if coverage is not None and not coverage.complete:
        reasons.extend(f"coverage:{item.code}" for item in coverage.findings)
    semantic_ready = not reasons
    obligations = compile_contract_obligations(contract)
    for item in obligations.items:
        if item.required and item.role in {"output", "measure", "inner_measure", "filter", "grouping", "relation"} and not item.field_refs:
            reasons.append(f"binding_required:{item.semantic_item_id}")
    if any(item.code in {"binding_required", "invalid_field", "invalid_relation", "permission_denied"} for item in permission_diagnostics):
        reasons.extend(f"permission:{item.code}:{item.semantic_item_id}" for item in permission_diagnostics)
    executable_ready = semantic_ready and not any(reason.startswith("binding_required") or reason.startswith("permission:") for reason in reasons)
    return ContractReadiness(semantic_ready, executable_ready, semantic_ready and executable_ready, tuple(dict.fromkeys(reasons)))
