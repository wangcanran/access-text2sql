"""Permission checks for contract-derived obligations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contract_obligations import ContractObligations, compile_contract_obligations
from .intent_contract import IntentContract


@dataclass(frozen=True)
class ContractPermissionDiagnostic:
    code: str
    semantic_item_id: str
    field: str | None
    role: str
    detail: str


def _tables(context: Mapping[str, Any]) -> Mapping[str, Any]:
    schema = context.get("schema", context)
    value = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def _policy(context: Mapping[str, Any]) -> Mapping[str, Any]:
    value = context.get("policy", {})
    return value if isinstance(value, Mapping) else {}


def _field_exists(context: Mapping[str, Any], field: str) -> bool:
    if "." not in field:
        return False
    table, column = field.split(".", 1)
    values = _tables(context).get(table, ())
    return isinstance(values, (list, tuple, set)) and column in values


def _field_allowed(context: Mapping[str, Any], field: str) -> bool:
    if "." not in field:
        return False
    table, column = field.split(".", 1)
    values = _policy(context).get(table, ())
    return isinstance(values, (list, tuple, set)) and column in values


def check_contract_permissions(contract: IntentContract, public_context: Mapping[str, Any], obligations: ContractObligations | None = None) -> tuple[ContractPermissionDiagnostic, ...]:
    """Check only resources explicitly required by the semantic contract.

    A missing candidate binding is reported as ``binding_required`` rather
    than permission denied.  The former means the semantic item still needs
    schema resolution; the latter means a known required field is inaccessible.
    """
    obligations = obligations or compile_contract_obligations(contract)
    diagnostics: list[ContractPermissionDiagnostic] = []
    relation_map = {item.id: item for item in contract.relations}
    for item in obligations.items:
        for field in item.field_refs:
            if not _field_exists(public_context, field):
                diagnostics.append(ContractPermissionDiagnostic("invalid_field", item.semantic_item_id, field, item.role, "field is absent from public schema"))
            elif not _field_allowed(public_context, field):
                diagnostics.append(ContractPermissionDiagnostic("permission_denied", item.semantic_item_id, field, item.role, "field is not accessible for current role"))
        for relation_id in item.relation_ids:
            relation = relation_map.get(relation_id)
            if relation is None:
                diagnostics.append(ContractPermissionDiagnostic("invalid_relation", item.semantic_item_id, None, item.role, f"relation {relation_id} is not declared"))
                continue
            for field in relation.field_refs:
                if not _field_exists(public_context, field):
                    diagnostics.append(ContractPermissionDiagnostic("invalid_field", item.semantic_item_id, field, "relation", "relation field is absent from public schema"))
                elif not _field_allowed(public_context, field):
                    diagnostics.append(ContractPermissionDiagnostic("permission_denied", item.semantic_item_id, field, "relation", "relation field is not accessible for current role"))
        if item.required and not item.field_refs and item.role in {"output", "measure", "inner_measure", "filter", "grouping", "relation"}:
            diagnostics.append(ContractPermissionDiagnostic("binding_required", item.semantic_item_id, None, item.role, "semantic item has no resolved candidate field"))
    unique: list[ContractPermissionDiagnostic] = []
    seen: set[tuple[str, str, str | None, str]] = set()
    for item in diagnostics:
        key = (item.code, item.semantic_item_id, item.field, item.role)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)
