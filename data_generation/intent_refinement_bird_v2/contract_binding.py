"""Resolve unambiguous contract candidate bindings against public schema."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .intent_contract import IntentContract, MeasureSpec, OutputSpec, RelationSpec


def _public_fields(context: Mapping[str, Any]) -> set[str]:
    schema = context.get("schema", context)
    tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
    return {f"{table}.{column}" for table, columns in tables.items() for column in columns if isinstance(columns, (list, tuple, set))}


def _resolve(candidates: tuple[str, ...], public: set[str]) -> tuple[str, ...]:
    valid = tuple(dict.fromkeys(item for item in candidates if item in public))
    return (valid[0],) if len(valid) == 1 else ()


def _measure(measure: MeasureSpec, public: set[str]) -> MeasureSpec:
    inner = _measure(measure.inner_measure, public) if measure.inner_measure else None
    refs = tuple(item for item in measure.field_refs if item in public)
    return replace(measure, field_refs=refs, inner_measure=inner)


def resolve_contract_bindings(contract: IntentContract, public_context: Mapping[str, Any]) -> IntentContract:
    """Resolve only a single valid candidate; never guess among alternatives."""
    public = _public_fields(public_context)
    outputs: list[OutputSpec] = []
    for output in contract.outputs:
        refs = tuple(item for item in output.field_refs if item in public)
        if not refs and output.candidate_bindings:
            refs = _resolve(output.candidate_bindings, public)
        measure = _measure(output.measure, public) if output.measure else None
        if measure is not None and not measure.field_refs and output.candidate_bindings:
            measure = replace(measure, field_refs=_resolve(output.candidate_bindings, public))
        outputs.append(replace(output, field_refs=refs, measure=measure))
    filters = tuple(replace(item, field_refs=tuple(field for field in item.field_refs if field in public)) for item in contract.filters)
    groupings = tuple(replace(item, field_refs=tuple(field for field in item.field_refs if field in public)) for item in contract.groupings)
    relations = tuple(replace(item, field_refs=tuple(field for field in item.field_refs if field in public)) for item in contract.relations)
    return replace(contract, outputs=tuple(outputs), filters=filters, groupings=groupings, relations=relations)
