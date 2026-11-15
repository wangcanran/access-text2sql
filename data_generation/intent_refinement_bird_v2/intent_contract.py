"""Minimal, verifiable representation of a user's query intent.

This module deliberately stops before SQL planning.  An :class:`IntentContract`
records what the user asked for, the scope in which each item is meaningful,
and the evidence/ambiguity needed to clarify it.  Physical schema bindings are
optional candidates; they are not the source of truth for user semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, tuple):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, set):
        return frozenset(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, frozenset, list)):
        return [_thaw(v) for v in value]
    return value


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ScopeSpec:
    """A business grain, independent of a SQL subquery implementation."""

    id: str
    meaning: str
    entity: str | None = None
    parent_scope_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.id, "scope id")
        _text(self.meaning, "scope meaning")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "meaning": self.meaning, "entity": self.entity, "parent_scope_id": self.parent_scope_id}


@dataclass(frozen=True)
class MeasureSpec:
    """A business measure, optionally composed from an inner measure."""

    operation: str
    meaning: str
    field_refs: tuple[str, ...] = ()
    inner_measure: "MeasureSpec | None" = None
    scope_id: str | None = None
    distinct: bool = False
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.operation, "measure operation")
        _text(self.meaning, "measure meaning")
        if any(not isinstance(item, str) or not item.strip() for item in self.field_refs):
            raise ValueError("measure field_refs must contain non-empty strings")
        object.__setattr__(self, "attributes", _freeze(self.attributes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "meaning": self.meaning,
            "field_refs": list(self.field_refs),
            "inner_measure": self.inner_measure.to_dict() if self.inner_measure else None,
            "scope_id": self.scope_id,
            "distinct": self.distinct,
            "attributes": _thaw(self.attributes),
        }


@dataclass(frozen=True)
class SubjectSpec:
    meaning: str
    candidate_bindings: tuple[str, ...] = ()
    scope_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.meaning, "subject meaning")

    def to_dict(self) -> dict[str, Any]:
        return {"meaning": self.meaning, "candidate_bindings": list(self.candidate_bindings), "scope_id": self.scope_id}


@dataclass(frozen=True)
class OutputSpec:
    id: str
    meaning: str
    role: str = "scalar"
    measure: MeasureSpec | None = None
    field_refs: tuple[str, ...] = ()
    scope_id: str | None = None
    shared_scope_id: str | None = None
    distinct: bool = False
    required: bool = True
    candidate_bindings: tuple[str, ...] = ()
    relation_ids: tuple[str, ...] = ()
    source_span: str = ""

    def __post_init__(self) -> None:
        _text(self.id, "output id")
        _text(self.meaning, "output meaning")
        if self.measure is None and not self.field_refs and not self.candidate_bindings:
            # A business-only output is valid while its binding is unresolved;
            # the parser must still provide a meaningful description.
            return
        if any(not isinstance(item, str) or not item.strip() for item in self.field_refs + self.candidate_bindings):
            raise ValueError("output field references must contain non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "meaning": self.meaning,
            "role": self.role,
            "measure": self.measure.to_dict() if self.measure else None,
            "field_refs": list(self.field_refs),
            "scope_id": self.scope_id,
            "shared_scope_id": self.shared_scope_id,
            "distinct": self.distinct,
            "required": self.required,
            "candidate_bindings": list(self.candidate_bindings),
            "relation_ids": list(self.relation_ids),
            "source_span": self.source_span,
        }


@dataclass(frozen=True)
class FilterSpec:
    id: str
    meaning: str
    operator: str | None = None
    value: Any = None
    field_refs: tuple[str, ...] = ()
    relation_ids: tuple[str, ...] = ()
    scope_id: str | None = None
    source_span: str = ""

    def __post_init__(self) -> None:
        _text(self.id, "filter id")
        _text(self.meaning, "filter meaning")
        object.__setattr__(self, "value", _freeze(self.value))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "meaning": self.meaning, "operator": self.operator, "value": _thaw(self.value), "field_refs": list(self.field_refs), "relation_ids": list(self.relation_ids), "scope_id": self.scope_id, "source_span": self.source_span}


@dataclass(frozen=True)
class GroupingSpec:
    id: str
    meaning: str
    field_refs: tuple[str, ...] = ()
    relation_ids: tuple[str, ...] = ()
    scope_id: str | None = None
    source_span: str = ""

    def __post_init__(self) -> None:
        _text(self.id, "grouping id")
        _text(self.meaning, "grouping meaning")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "meaning": self.meaning, "field_refs": list(self.field_refs), "relation_ids": list(self.relation_ids), "scope_id": self.scope_id, "source_span": self.source_span}


@dataclass(frozen=True)
class RankingSpec:
    id: str
    meaning: str
    measure_output_id: str | None = None
    direction: str = "DESC"
    limit: int | None = None
    scope_id: str | None = None
    source_span: str = ""

    def __post_init__(self) -> None:
        _text(self.id, "ranking id")
        _text(self.meaning, "ranking meaning")
        if self.direction.upper() not in {"ASC", "DESC"}:
            raise ValueError("ranking direction must be ASC or DESC")
        if self.limit is not None and (isinstance(self.limit, bool) or self.limit <= 0):
            raise ValueError("ranking limit must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "meaning": self.meaning, "measure_output_id": self.measure_output_id, "direction": self.direction.upper(), "limit": self.limit, "scope_id": self.scope_id, "source_span": self.source_span}


@dataclass(frozen=True)
class RelationSpec:
    id: str
    meaning: str
    relation_kind: str = "required"
    field_refs: tuple[str, ...] = ()
    source_span: str = ""

    def __post_init__(self) -> None:
        _text(self.id, "relation id")
        _text(self.meaning, "relation meaning")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "meaning": self.meaning, "relation_kind": self.relation_kind, "field_refs": list(self.field_refs), "source_span": self.source_span}


@dataclass(frozen=True)
class SemanticConstraint:
    id: str
    meaning: str
    subject_ids: tuple[str, ...] = ()
    required: bool = True
    source_span: str = ""

    def __post_init__(self) -> None:
        _text(self.id, "constraint id")
        _text(self.meaning, "constraint meaning")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "meaning": self.meaning, "subject_ids": list(self.subject_ids), "required": self.required, "source_span": self.source_span}


@dataclass(frozen=True)
class SemanticEvidence:
    source_span: str
    item_id: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _text(self.source_span, "evidence source_span")
        _text(self.item_id, "evidence item_id")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("evidence confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {"source_span": self.source_span, "item_id": self.item_id, "confidence": self.confidence}


@dataclass(frozen=True)
class ContractValidation:
    complete: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentContract:
    subject: SubjectSpec
    outputs: tuple[OutputSpec, ...] = ()
    filters: tuple[FilterSpec, ...] = ()
    groupings: tuple[GroupingSpec, ...] = ()
    ranking: RankingSpec | None = None
    scopes: tuple[ScopeSpec, ...] = ()
    relations: tuple[RelationSpec, ...] = ()
    constraints: tuple[SemanticConstraint, ...] = ()
    unresolved: tuple[str, ...] = ()
    evidence: tuple[SemanticEvidence, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    version: str = "intent-contract-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        ids: list[str] = []
        for collection in (self.outputs, self.filters, self.groupings, self.scopes, self.relations, self.constraints):
            ids.extend(item.id for item in collection)
        if self.ranking:
            ids.append(self.ranking.id)
        if len(ids) != len(set(ids)):
            raise ValueError("intent contract item ids must be unique")

    def validate(self) -> ContractValidation:
        errors: list[str] = []
        warnings: list[str] = []
        scope_ids = {item.id for item in self.scopes}
        output_ids = {item.id for item in self.outputs}
        relation_ids = {item.id for item in self.relations}
        if not self.outputs:
            errors.append("missing_required_output")
        if self.unresolved:
            errors.extend(f"unresolved:{item}" for item in self.unresolved)
        for output in self.outputs:
            if output.scope_id and output.scope_id not in scope_ids:
                errors.append(f"unknown_output_scope:{output.id}:{output.scope_id}")
            if output.shared_scope_id and output.shared_scope_id not in scope_ids:
                errors.append(f"unknown_output_shared_scope:{output.id}:{output.shared_scope_id}")
            unknown_relations = set(output.relation_ids) - relation_ids
            if unknown_relations:
                errors.append(f"unknown_output_relation:{output.id}:{','.join(sorted(unknown_relations))}")
            if output.required is False:
                warnings.append(f"optional_output:{output.id}")
            if output.measure:
                if output.measure.inner_measure and not output.measure.inner_measure.scope_id:
                    errors.append(f"nested_measure_missing_inner_scope:{output.id}")
            if output.measure and output.measure.scope_id and output.measure.scope_id not in scope_ids:
                errors.append(f"unknown_measure_scope:{output.id}:{output.measure.scope_id}")
        for item in self.filters + self.groupings:
            unknown_relations = set(item.relation_ids) - relation_ids
            if unknown_relations:
                errors.append(f"unknown_relation:{item.id}:{','.join(sorted(unknown_relations))}")
        if self.ranking:
            if self.ranking.measure_output_id and self.ranking.measure_output_id not in output_ids:
                errors.append(f"ranking_unknown_measure:{self.ranking.id}")
            if self.ranking.scope_id and self.ranking.scope_id not in scope_ids:
                errors.append(f"unknown_ranking_scope:{self.ranking.id}:{self.ranking.scope_id}")
            if not self.ranking.measure_output_id:
                warnings.append(f"ranking_without_explicit_measure:{self.ranking.id}")
            if self.ranking.scope_id:
                for output in self.outputs:
                    if output.shared_scope_id != self.ranking.scope_id:
                        errors.append(f"output_not_shared_with_ranking:{output.id}:{self.ranking.id}")
        for constraint in self.constraints:
            unknown = set(constraint.subject_ids) - output_ids - scope_ids - {item.id for item in self.filters} - {item.id for item in self.groupings}
            if unknown:
                errors.append(f"constraint_unknown_subject:{constraint.id}:{','.join(sorted(unknown))}")
        return ContractValidation(not errors, tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "subject": self.subject.to_dict(),
            "outputs": [item.to_dict() for item in self.outputs],
            "filters": [item.to_dict() for item in self.filters],
            "groupings": [item.to_dict() for item in self.groupings],
            "ranking": self.ranking.to_dict() if self.ranking else None,
            "scopes": [item.to_dict() for item in self.scopes],
            "relations": [item.to_dict() for item in self.relations],
            "constraints": [item.to_dict() for item in self.constraints],
            "unresolved": list(self.unresolved),
            "evidence": [item.to_dict() for item in self.evidence],
            "metadata": _thaw(self.metadata),
        }


def _tuple_strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name)


def _parse_measure(value: Any, path: str) -> MeasureSpec:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    inner = value.get("inner_measure")
    return MeasureSpec(
        operation=_text(value.get("operation"), f"{path}.operation"),
        meaning=_text(value.get("meaning"), f"{path}.meaning"),
        field_refs=_tuple_strings(value.get("field_refs"), f"{path}.field_refs"),
        inner_measure=_parse_measure(inner, f"{path}.inner_measure") if inner is not None else None,
        scope_id=_optional_text(value.get("scope_id"), f"{path}.scope_id"),
        distinct=bool(value.get("distinct", False)),
        attributes=value.get("attributes") or {},
    )


def parse_intent_contract(value: Mapping[str, Any]) -> IntentContract:
    """Parse an agent contract without trusting completion/status fields."""
    if not isinstance(value, Mapping):
        raise ValueError("contract must be an object")
    allowed = {"version", "subject", "outputs", "filters", "groupings", "ranking", "scopes", "relations", "constraints", "unresolved", "evidence", "metadata"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"contract contains unsupported fields: {sorted(unknown)}")
    raw_subject = value.get("subject")
    if not isinstance(raw_subject, Mapping):
        raise ValueError("contract.subject must be an object")
    subject = SubjectSpec(_text(raw_subject.get("meaning"), "subject.meaning"), _tuple_strings(raw_subject.get("candidate_bindings"), "subject.candidate_bindings"), _optional_text(raw_subject.get("scope_id"), "subject.scope_id"))
    def _objects(name: str) -> tuple[Mapping[str, Any], ...]:
        raw = value.get(name, ())
        if not isinstance(raw, (list, tuple)) or any(not isinstance(item, Mapping) for item in raw):
            raise ValueError(f"contract.{name} must be a list of objects")
        return tuple(raw)

    scopes_raw = _objects("scopes")
    scopes = tuple(ScopeSpec(_text(item.get("id"), "scope.id"), _text(item.get("meaning"), "scope.meaning"), _optional_text(item.get("entity"), "scope.entity"), _optional_text(item.get("parent_scope_id"), "scope.parent_scope_id")) for item in scopes_raw)
    outputs: list[OutputSpec] = []
    output_items = _objects("outputs")
    for index, item in enumerate(output_items):
        measure = _parse_measure(item["measure"], f"outputs[{index}].measure") if item.get("measure") is not None else None
        outputs.append(OutputSpec(
            _text(item.get("id"), f"outputs[{index}].id"), _text(item.get("meaning"), f"outputs[{index}].meaning"),
            str(item.get("role", "scalar")), measure, _tuple_strings(item.get("field_refs"), f"outputs[{index}].field_refs"),
            _optional_text(item.get("scope_id"), f"outputs[{index}].scope_id"), _optional_text(item.get("shared_scope_id"), f"outputs[{index}].shared_scope_id"),
            bool(item.get("distinct", False)), bool(item.get("required", True)), _tuple_strings(item.get("candidate_bindings"), f"outputs[{index}].candidate_bindings"), _tuple_strings(item.get("relation_ids"), f"outputs[{index}].relation_ids"), str(item.get("source_span", "")),
        ))
    # Use keyword arguments here.  These records are part of the parser
    # boundary and adding a field must never silently shift an older
    # positional value (for example ``scope_id`` into ``relation_ids``).
    filters = tuple(FilterSpec(
        id=_text(item.get("id"), "filter.id"),
        meaning=_text(item.get("meaning"), "filter.meaning"),
        operator=item.get("operator"), value=item.get("value"),
        field_refs=_tuple_strings(item.get("field_refs"), "filter.field_refs"),
        relation_ids=_tuple_strings(item.get("relation_ids"), "filter.relation_ids"),
        scope_id=_optional_text(item.get("scope_id"), "filter.scope_id"),
        source_span=str(item.get("source_span", "")),
    ) for item in _objects("filters"))
    groupings = tuple(GroupingSpec(
        id=_text(item.get("id"), "grouping.id"),
        meaning=_text(item.get("meaning"), "grouping.meaning"),
        field_refs=_tuple_strings(item.get("field_refs"), "grouping.field_refs"),
        relation_ids=_tuple_strings(item.get("relation_ids"), "grouping.relation_ids"),
        scope_id=_optional_text(item.get("scope_id"), "grouping.scope_id"),
        source_span=str(item.get("source_span", "")),
    ) for item in _objects("groupings"))
    raw_ranking = value.get("ranking")
    ranking = None
    if raw_ranking is not None:
        if not isinstance(raw_ranking, Mapping):
            raise ValueError("ranking must be an object or null")
        ranking = RankingSpec(_text(raw_ranking.get("id"), "ranking.id"), _text(raw_ranking.get("meaning"), "ranking.meaning"), _optional_text(raw_ranking.get("measure_output_id"), "ranking.measure_output_id"), str(raw_ranking.get("direction", "DESC")), raw_ranking.get("limit"), _optional_text(raw_ranking.get("scope_id"), "ranking.scope_id"), str(raw_ranking.get("source_span", "")))
    relations = tuple(RelationSpec(_text(item.get("id"), "relation.id"), _text(item.get("meaning"), "relation.meaning"), str(item.get("relation_kind", "required")), _tuple_strings(item.get("field_refs"), "relation.field_refs"), str(item.get("source_span", ""))) for item in _objects("relations"))
    constraints = tuple(SemanticConstraint(_text(item.get("id"), "constraint.id"), _text(item.get("meaning"), "constraint.meaning"), _tuple_strings(item.get("subject_ids"), "constraint.subject_ids"), bool(item.get("required", True)), str(item.get("source_span", ""))) for item in _objects("constraints"))
    evidence = tuple(SemanticEvidence(_text(item.get("source_span"), "evidence.source_span"), _text(item.get("item_id"), "evidence.item_id"), float(item.get("confidence", 1.0))) for item in _objects("evidence"))
    return IntentContract(subject, tuple(outputs), filters, groupings, ranking, scopes, relations, constraints, _tuple_strings(value.get("unresolved"), "contract.unresolved"), evidence, value.get("metadata") or {}, str(value.get("version", "intent-contract-v1")))


def contract_ids(contract: IntentContract) -> tuple[str, ...]:
    """Return stable semantic item ids for coverage and dialogue logs."""
    values: list[str] = ["subject"]
    for collection in (contract.outputs, contract.filters, contract.groupings, contract.scopes, contract.relations, contract.constraints):
        values.extend(item.id for item in collection)
    if contract.ranking:
        values.append(contract.ranking.id)
    return tuple(values)
