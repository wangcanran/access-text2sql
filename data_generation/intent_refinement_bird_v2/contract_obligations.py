"""Compile an intent contract into field and relationship obligations.

The compiler does not choose joins or emit SQL.  It only exposes the physical
resources required to honour each semantic item so the rule-based permission
layer can make a decision without asking the LLM to invent a query plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .intent_contract import IntentContract, MeasureSpec


@dataclass(frozen=True)
class ContractObligation:
    semantic_item_id: str
    role: str
    field_refs: tuple[str, ...] = ()
    relation_ids: tuple[str, ...] = ()
    aggregation: str | None = None
    distinct: bool = False
    scope_ids: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class ContractObligations:
    items: tuple[ContractObligation, ...] = ()

    def for_item(self, semantic_item_id: str) -> tuple[ContractObligation, ...]:
        return tuple(item for item in self.items if item.semantic_item_id == semantic_item_id)

    def fields(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(field for item in self.items for field in item.field_refs))


def _measure_obligations(item_id: str, measure: MeasureSpec, scope_ids: tuple[str, ...], role: str = "measure", relation_ids: tuple[str, ...] = ()) -> list[ContractObligation]:
    # A composed outer aggregate (for example AVG(COUNT(...))) can have no
    # direct field reference.  Its required physical resource is represented
    # by the inner measure, so do not manufacture a binding obligation here.
    result = [ContractObligation(item_id, role, measure.field_refs, relation_ids=relation_ids, aggregation=measure.operation, distinct=measure.distinct, scope_ids=scope_ids, required=not (measure.inner_measure and not measure.field_refs))]
    if measure.inner_measure:
        inner = measure.inner_measure
        inner_scope = (inner.scope_id,) if inner.scope_id else ()
        result.extend(_measure_obligations(item_id, inner, inner_scope, "inner_measure", relation_ids))
    return result


def compile_contract_obligations(contract: IntentContract) -> ContractObligations:
    """Compile all explicitly referenced resources without inventing fields."""
    result: list[ContractObligation] = []
    for output in contract.outputs:
        scopes = tuple(item for item in (output.scope_id, output.shared_scope_id) if item)
        if output.measure:
            result.extend(_measure_obligations(output.id, output.measure, scopes, relation_ids=output.relation_ids))
        elif output.field_refs:
            result.append(ContractObligation(output.id, "output", output.field_refs, relation_ids=output.relation_ids, distinct=output.distinct, scope_ids=scopes, required=output.required))
        elif output.candidate_bindings:
            # Candidates are hypotheses for later binding resolution, not
            # simultaneous execution dependencies.
            result.append(ContractObligation(output.id, "output", (), relation_ids=output.relation_ids, distinct=output.distinct, scope_ids=scopes, required=output.required))
        else:
            result.append(ContractObligation(output.id, "output", (), relation_ids=output.relation_ids, distinct=output.distinct, scope_ids=scopes, required=output.required))
    for item in contract.filters:
        result.append(ContractObligation(item.id, "filter", item.field_refs, relation_ids=item.relation_ids, scope_ids=(item.scope_id,) if item.scope_id else ()))
    for item in contract.groupings:
        result.append(ContractObligation(item.id, "grouping", item.field_refs, relation_ids=item.relation_ids, scope_ids=(item.scope_id,) if item.scope_id else ()))
    if contract.ranking:
        result.append(ContractObligation(contract.ranking.id, "ranking", (), relation_ids=(), aggregation="rank", scope_ids=(contract.ranking.scope_id,) if contract.ranking.scope_id else ()))
    for item in contract.relations:
        result.append(ContractObligation(item.id, "relation", item.field_refs, relation_ids=()))
    return ContractObligations(tuple(result))
