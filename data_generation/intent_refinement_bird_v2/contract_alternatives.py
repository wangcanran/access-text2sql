"""Permission-safe semantic alternatives for IntentContract."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from .contract_obligations import ContractObligations
from .contract_permission import ContractPermissionDiagnostic
from .intent_contract import IntentContract, OutputSpec


@dataclass(frozen=True)
class ContractAlternative:
    id: str
    summary: str
    affected_item_ids: tuple[str, ...]
    semantic_loss: str
    transform: str
    requires_authorization: bool = True


def generate_contract_alternatives(contract: IntentContract, diagnostics: Iterable[ContractPermissionDiagnostic], *, max_candidates: int = 8) -> tuple[ContractAlternative, ...]:
    """Generate finite alternatives from denied semantic items only.

    The generator never replaces a restricted field with an arbitrary public
    column.  It offers removal of an affected optional result, or removal of a
    whole restricted required result for explicit user authorization.
    """
    denied = {item.semantic_item_id for item in diagnostics if item.code == "permission_denied"}
    outputs = {item.id: item for item in contract.outputs}
    relation_to_outputs: dict[str, set[str]] = {}
    for output in contract.outputs:
        for relation_id in output.relation_ids:
            relation_to_outputs.setdefault(relation_id, set()).add(output.id)
    result: list[ContractAlternative] = []
    seen_output_sets: set[tuple[str, ...]] = set()
    for item_id in sorted(denied):
        output_ids = {item_id} if item_id in outputs else relation_to_outputs.get(item_id, set())
        if not output_ids or any(output_id not in outputs for output_id in output_ids):
            continue
        # Dropping a ranking measure would leave the ranking semantically
        # undefined.  Such a permission failure has no safe drop-only
        # alternative; the caller must reject or ask for a different request.
        if contract.ranking and contract.ranking.measure_output_id in output_ids:
            continue
        # A denied relation is only removable when every use of it belongs to
        # the proposed dropped outputs.  Filters/groupings are independent
        # semantic obligations and cannot be repaired by dropping an output.
        if item_id in relation_to_outputs:
            used_elsewhere = any(item_id in item.relation_ids for item in (*contract.filters, *contract.groupings))
            if used_elsewhere:
                continue
        output_id = sorted(output_ids)[0]
        output = outputs[output_id]
        output_key = tuple(sorted(output_ids))
        if output_key in seen_output_sets:
            continue
        seen_output_sets.add(output_key)
        result.append(ContractAlternative(
            f"drop_{output_id}",
            f"Return the other requested results but omit {output.meaning} because it is unavailable.",
            output_key, f"the result item '{output.meaning}' is omitted", "drop_output",
        ))
        if len(result) >= max_candidates:
            break
    return tuple(result)


def apply_contract_alternative(contract: IntentContract, alternative: ContractAlternative) -> IntentContract:
    if alternative.transform != "drop_output":
        raise ValueError(f"unsupported contract transformation: {alternative.transform}")
    affected = set(alternative.affected_item_ids)
    outputs = tuple(item for item in contract.outputs if item.id not in affected)
    if not outputs:
        raise ValueError("an alternative cannot remove every requested output")
    if contract.ranking and contract.ranking.measure_output_id in affected:
        raise ValueError("an alternative cannot remove the ranking measure")

    # Alternatives change the semantic contract, so remove only resources
    # whose complete dependency closure belonged to the dropped outputs.  A
    # relation may be referenced by several surviving outputs (or by a filter,
    # grouping, or constraint); those shared resources must remain intact.
    surviving_ids = {item.id for item in outputs}
    surviving_relation_ids = {
        relation_id
        for item in outputs
        for relation_id in item.relation_ids
    }
    surviving_relation_ids.update(
        relation_id
        for item in (*contract.filters, *contract.groupings)
        for relation_id in item.relation_ids
    )
    surviving_subject_ids = set(surviving_ids)
    surviving_subject_ids.update(item.id for item in contract.filters)
    surviving_subject_ids.update(item.id for item in contract.groupings)
    relations = tuple(item for item in contract.relations if item.id in surviving_relation_ids)
    surviving_subject_ids.update(item.id for item in relations)

    # Retain scopes that are still referenced by surviving outputs, measures,
    # filters, groupings, ranking, or constraints.  This avoids leaving
    # dangling scope metadata while preserving shared business grains.
    referenced_scopes: set[str] = set()
    for item in outputs:
        referenced_scopes.update(value for value in (item.scope_id, item.shared_scope_id) if value)
        if item.measure:
            measure = item.measure
            while measure:
                if measure.scope_id:
                    referenced_scopes.add(measure.scope_id)
                measure = measure.inner_measure
    referenced_scopes.update(item.scope_id for item in contract.filters if item.scope_id)
    referenced_scopes.update(item.scope_id for item in contract.groupings if item.scope_id)
    if contract.ranking and contract.ranking.scope_id:
        referenced_scopes.add(contract.ranking.scope_id)
    # Keep parent scopes needed to interpret a retained child scope.
    scope_by_id = {item.id: item for item in contract.scopes}
    pending_scopes = list(referenced_scopes)
    while pending_scopes:
        scope_id = pending_scopes.pop()
        scope = scope_by_id.get(scope_id)
        if scope and scope.parent_scope_id and scope.parent_scope_id not in referenced_scopes:
            referenced_scopes.add(scope.parent_scope_id)
            pending_scopes.append(scope.parent_scope_id)
    scopes = tuple(item for item in contract.scopes if item.id in referenced_scopes)
    surviving_subject_ids.update(item.id for item in scopes)

    constraints = []
    for item in contract.constraints:
        subjects = tuple(subject_id for subject_id in item.subject_ids if subject_id in surviving_subject_ids)
        if item.subject_ids and not subjects:
            continue
        constraints.append(item if subjects == item.subject_ids else replace(item, subject_ids=subjects))
    constraints = tuple(constraints)
    valid_ids = {"subject", *(item.id for item in outputs), *(item.id for item in contract.filters),
                 *(item.id for item in contract.groupings), *(item.id for item in scopes),
                 *(item.id for item in relations), *(item.id for item in constraints),
                 *(item.id for item in (contract.ranking,) if item is not None)}
    evidence = tuple(item for item in contract.evidence if item.item_id in valid_ids)
    metadata = dict(contract.metadata)
    metadata["accepted_alternative_id"] = alternative.id
    metadata["semantic_loss"] = alternative.semantic_loss
    # The accepted transformation intentionally removes the affected source
    # phrase from the public request used for the next coverage audit.  The
    # original private request remains untouched in the evaluation case.
    phrases = list(metadata.get("suppressed_semantic_phrases", ()))
    phrases.extend(item.meaning for item in contract.outputs if item.id in affected)
    metadata["suppressed_semantic_phrases"] = tuple(dict.fromkeys(phrases))
    unresolved = tuple(item for item in contract.unresolved if item not in affected)
    return IntentContract(contract.subject, outputs, contract.filters, contract.groupings, contract.ranking, scopes, relations, constraints, unresolved, evidence, metadata, contract.version)
