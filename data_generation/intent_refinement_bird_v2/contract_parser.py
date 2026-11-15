"""Strict JSON boundary for the contract-based parser migration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .contract_coverage import CoverageReport, audit_contract_coverage
from .intent_contract import IntentContract, parse_intent_contract


class ContractParserClient(Protocol):
    def complete(self, *, system_prompt: str, user_payload: dict[str, Any], response_schema: dict[str, Any] | None = None) -> Any:
        ...


_STRING_LIST = {"type": "array", "items": {"type": "string"}}
_MEASURE_LEAF_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "operation": {"type": "string"}, "meaning": {"type": "string"},
        "field_refs": _STRING_LIST, "inner_measure": {"type": "null"},
        "scope_id": {"type": ["string", "null"]}, "distinct": {"type": "boolean"},
        "attributes": {"type": "object", "additionalProperties": True},
    },
    "required": ["operation", "meaning", "field_refs", "inner_measure", "scope_id", "distinct", "attributes"],
}
_MEASURE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "operation": {"type": "string"}, "meaning": {"type": "string"},
        "field_refs": _STRING_LIST, "inner_measure": {"anyOf": [_MEASURE_LEAF_SCHEMA, {"type": "null"}]},
        "scope_id": {"type": ["string", "null"]}, "distinct": {"type": "boolean"},
        "attributes": {"type": "object", "additionalProperties": True},
    },
    "required": ["operation", "meaning", "field_refs", "inner_measure", "scope_id", "distinct", "attributes"],
}
_SCOPE_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}, "meaning": {"type": "string"}, "entity": {"type": ["string", "null"]}, "parent_scope_id": {"type": ["string", "null"]}}, "required": ["id", "meaning", "entity", "parent_scope_id"]}
_FILTER_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}, "meaning": {"type": "string"}, "operator": {"type": ["string", "null"]}, "value": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]}, "field_refs": _STRING_LIST, "relation_ids": _STRING_LIST, "scope_id": {"type": ["string", "null"]}, "source_span": {"type": "string"}}, "required": ["id", "meaning", "operator", "value", "field_refs", "relation_ids", "scope_id", "source_span"]}
_GROUPING_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}, "meaning": {"type": "string"}, "field_refs": _STRING_LIST, "relation_ids": _STRING_LIST, "scope_id": {"type": ["string", "null"]}, "source_span": {"type": "string"}}, "required": ["id", "meaning", "field_refs", "relation_ids", "scope_id", "source_span"]}
_RELATION_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}, "meaning": {"type": "string"}, "relation_kind": {"type": "string"}, "field_refs": _STRING_LIST, "source_span": {"type": "string"}}, "required": ["id", "meaning", "relation_kind", "field_refs", "source_span"]}
_CONSTRAINT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}, "meaning": {"type": "string"}, "subject_ids": _STRING_LIST, "required": {"type": "boolean"}, "source_span": {"type": "string"}}, "required": ["id", "meaning", "subject_ids", "required", "source_span"]}
_EVIDENCE_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"source_span": {"type": "string"}, "item_id": {"type": "string"}, "confidence": {"type": "number"}}, "required": ["source_span", "item_id", "confidence"]}
_RANKING_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}, "meaning": {"type": "string"}, "measure_output_id": {"type": ["string", "null"]}, "direction": {"type": "string", "enum": ["ASC", "DESC"]}, "limit": {"type": ["integer", "null"]}, "scope_id": {"type": ["string", "null"]}, "source_span": {"type": "string"}}, "required": ["id", "meaning", "measure_output_id", "direction", "limit", "scope_id", "source_span"]}
_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "version": {"type": "string"},
        "subject": {"type": "object", "additionalProperties": False, "properties": {"meaning": {"type": "string"}, "candidate_bindings": _STRING_LIST, "scope_id": {"type": ["string", "null"]}}, "required": ["meaning", "candidate_bindings", "scope_id"]},
        "outputs": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}, "meaning": {"type": "string"}, "role": {"type": "string"}, "measure": {"anyOf": [_MEASURE_SCHEMA, {"type": "null"}]}, "field_refs": _STRING_LIST, "scope_id": {"type": ["string", "null"]}, "shared_scope_id": {"type": ["string", "null"]}, "distinct": {"type": "boolean"}, "required": {"type": "boolean"}, "candidate_bindings": _STRING_LIST, "relation_ids": _STRING_LIST, "source_span": {"type": "string"}}, "required": ["id", "meaning", "role", "measure", "field_refs", "scope_id", "shared_scope_id", "distinct", "required", "candidate_bindings", "relation_ids", "source_span"]}},
        "filters": {"type": "array", "items": _FILTER_SCHEMA}, "groupings": {"type": "array", "items": _GROUPING_SCHEMA},
        "ranking": {"anyOf": [_RANKING_SCHEMA, {"type": "null"}]}, "scopes": {"type": "array", "items": _SCOPE_SCHEMA},
        "relations": {"type": "array", "items": _RELATION_SCHEMA}, "constraints": {"type": "array", "items": _CONSTRAINT_SCHEMA},
        "unresolved": _STRING_LIST, "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA}, "metadata": {"type": "object", "additionalProperties": True},
    },
    "required": ["version", "subject", "outputs", "filters", "groupings", "ranking", "scopes", "relations", "constraints", "unresolved", "evidence", "metadata"],
}

CONTRACT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "contract": _CONTRACT_SCHEMA,
        "unresolved": _STRING_LIST,
        "evidence": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["contract", "unresolved", "evidence"],
}


CONTRACT_PARSER_PROMPT = """Extract a minimal user-intent contract from the request.

Return exactly one JSON object with exactly these top-level keys:
`contract`, `unresolved`, and `evidence`. The value of `contract` must contain
exactly the fields in the JSON example below. Do not use alternate names such
as `semantic_scope`, `output_id`, `entity`, `measure`, or `group_by` at the
contract level. In particular, do not return the old graph format and do not
return a bare `outputs` array. The gateway may not enforce the JSON schema, so
the example is a literal output template, not merely documentation.

Represent requested outputs, filters, grouping, ranking, semantic scopes, and
shared constraints. Preserve nested business grain such as 'average number per
molecule'. Do not generate SQL, a join plan, permission decisions, or a final
status. Do not delete a requested output because it is inconvenient. Include
source evidence for high-impact phrases. Use `null` for absent nullable
objects, `[]` for absent lists, and `{}` for absent metadata/attributes.
Every required text field must be a non-empty string. In particular, every
scope object must have a non-empty `id` and `meaning`; never emit a scope with
an empty meaning. Every output must have a non-empty `id` and `meaning`. Every
measure must have non-empty `operation` and `meaning`, plus `field_refs`,
`inner_measure`, `scope_id`, `distinct`, and `attributes`.
Never use an empty string as a placeholder for a required text field. If the
meaning is genuinely unknown, put the item ID in `unresolved` and still give
the item a concise non-empty business meaning such as "requested value".

The following is a complete structural example. Copy its shape, replace its
values with facts from the current request, and do not copy its meanings when
they are not applicable:

Request: "Show employee names."
Output:
{
  "contract": {
    "version": "intent-contract-v1",
    "subject": {"meaning": "employee records", "candidate_bindings": ["employee.name"], "scope_id": null},
    "outputs": [
      {"id": "employee_names", "meaning": "employee names", "role": "set", "measure": null, "field_refs": ["employee.name"], "scope_id": null, "shared_scope_id": null, "distinct": false, "required": true, "candidate_bindings": [], "relation_ids": [], "source_span": "employee names"}
    ],
    "filters": [],
    "groupings": [],
    "ranking": null,
    "scopes": [],
    "relations": [],
    "constraints": [],
    "unresolved": [],
    "evidence": [{"source_span": "employee names", "item_id": "employee_names", "confidence": 0.99}],
    "metadata": {}
  },
  "unresolved": [],
  "evidence": []
}

When scopes, ranking, filters, relations, or nested measures are present, use
this exact nested shape (values are illustrative):
{
  "contract": {
    "version": "intent-contract-v1",
    "subject": {"meaning": "customer records", "candidate_bindings": ["customer.id"], "scope_id": "selected_customer"},
    "outputs": [{"id": "total_sales", "meaning": "total sales per customer", "role": "scalar", "measure": {"operation": "SUM", "meaning": "sales amount", "field_refs": ["sale.amount"], "inner_measure": null, "scope_id": "selected_customer", "distinct": false, "attributes": {}}, "field_refs": [], "scope_id": "selected_customer", "shared_scope_id": "selected_customer", "distinct": false, "required": true, "candidate_bindings": [], "relation_ids": ["customer_sale"], "source_span": "total sales"}],
    "filters": [{"id": "filter_year", "meaning": "sales in 2023", "operator": "=", "value": 2023, "field_refs": ["sale.year"], "relation_ids": [], "scope_id": "selected_customer", "source_span": "in 2023"}],
    "groupings": [{"id": "group_customer", "meaning": "group by customer", "field_refs": ["customer.id"], "relation_ids": [], "scope_id": "selected_customer", "source_span": "per customer"}],
    "ranking": {"id": "rank_sales", "meaning": "highest total sales", "measure_output_id": "total_sales", "direction": "DESC", "limit": 1, "scope_id": "selected_customer", "source_span": "highest"},
    "scopes": [{"id": "selected_customer", "meaning": "one customer at a time", "entity": "customer", "parent_scope_id": null}],
    "relations": [{"id": "customer_sale", "meaning": "customer linked to sale", "relation_kind": "required", "field_refs": ["customer.id", "sale.customer_id"], "source_span": "customer sales"}],
    "constraints": [], "unresolved": [], "evidence": [{"source_span": "highest total sales", "item_id": "rank_sales", "confidence": 0.95}], "metadata": {}
  },
  "unresolved": [], "evidence": []
}

For a request such as "For the most common bond type, give total occurrences
and the average number of such bonds per molecule", create a `scopes` entry,
make `ranking.measure_output_id` point to the total-occurrences output, and
represent the average output with `measure.operation` = `AVG` and a nested
`measure.inner_measure` whose `operation` is `COUNT`. Set `shared_scope_id` on
both outputs. The outer AVG is a semantic operation; do not emit SQL or invent
a CTE.""".strip()


CONTRACT_REPAIR_NOTE = """The previous response did not match the contract protocol.
Return the exact top-level shape {\"contract\": {...}, \"unresolved\": [], \"evidence\": []}.
Never return semantic_scope, output_id, entity, or a bare outputs array. Every
contract field and every output field shown in the example is required; use
null, [], or {} where appropriate. Preserve every requested output."""


@dataclass(frozen=True)
class ContractParseRequest:
    request: str
    public_context: dict[str, Any]
    history: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.request, str) or not self.request.strip():
            raise ValueError("request must not be empty")
        _assert_public(self.public_context, "public_context")
        for index, item in enumerate(self.history):
            _assert_public(item, f"history[{index}]")

    def to_payload(self) -> dict[str, Any]:
        return {"request": self.request, "public_context": parser_public_context(self.public_context), "history": [parser_public_context(item) for item in self.history]}


@dataclass(frozen=True)
class ContractParseResult:
    contract: IntentContract
    coverage: CoverageReport


def _json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines.pop()
            text = "\n".join(lines).strip()
        value = json.loads(text)
    if not isinstance(value, Mapping):
        raise ValueError("contract parser must return a JSON object")
    return value


_PRIVATE_KEYS = {"original_request", "original_questions", "gold_sql", "pseudo_sql", "evaluation_only", "private", "private_metadata", "profile", "user_profile"}
_POLICY_KEYS = {"policy", "accessible_columns", "role_access_policy"}


def _assert_public(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _PRIVATE_KEYS:
                raise ValueError(f"private field is not allowed in contract parser payload: {path}.{key}")
            _assert_public(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_public(item, f"{path}[{index}]")


def parser_public_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Remove internal access policy before the contract parser boundary."""
    def scrub(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): scrub(item) for key, item in value.items() if str(key).casefold() not in _POLICY_KEYS}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, tuple):
            return tuple(scrub(item) for item in value)
        return value
    result = scrub(dict(context))
    # The parser may use the full schema and declared FK metadata to resolve
    # semantic candidates; authorization remains a runner concern.
    return result


def parse_contract_response(request: str, value: Any) -> ContractParseResult:
    payload = _json(value)
    raw_contract = payload.get("contract")
    if not isinstance(raw_contract, Mapping):
        raise ValueError("contract response must contain an object under contract")
    # Top-level unresolved/evidence are compatibility conveniences.  The
    # canonical contract remains the only semantic source of truth.
    merged = dict(raw_contract)
    if payload.get("unresolved") and not merged.get("unresolved"):
        merged["unresolved"] = payload["unresolved"]
    if payload.get("evidence") and not merged.get("evidence"):
        merged["evidence"] = payload["evidence"]
    contract = parse_intent_contract(merged)
    return ContractParseResult(contract, audit_contract_coverage(request, contract))


def parse_contract(client: ContractParserClient, request: ContractParseRequest) -> ContractParseResult:
    value = client.complete(system_prompt=CONTRACT_PARSER_PROMPT, user_payload=request.to_payload(), response_schema=CONTRACT_RESPONSE_SCHEMA)
    return parse_contract_response(request.request, value)
