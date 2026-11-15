"""Fixed-output user answers for contract-based clarification.

The model may use private evaluation evidence to locate the requested fact,
but it cannot write the final business answer.  It returns an opaque value or
option ID and deterministic code renders the user-facing text from the active
question.  This prevents ``bond_type`` and similar identifiers from leaking
back through the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping, Protocol


class ContractUserClient(Protocol):
    def complete(self, *, system_prompt: str, user_payload: dict[str, Any], response_schema: dict[str, Any] | None = None) -> Any:
        ...


CONTRACT_USER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "answer_kind": {"type": "string", "enum": ["select_option", "value_id", "confirm", "deny", "refuse"]},
        "selected_option_ids": {"type": "array", "items": {"type": "string"}},
        "value_id": {"type": ["string", "null"]},
    },
    "required": ["answer_kind", "selected_option_ids", "value_id"],
}

CONTRACT_ALTERNATIVE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["accept", "reject"]},
        "selected_alternative_id": {"type": ["string", "null"]},
    },
    "required": ["decision", "selected_alternative_id"],
}


CONTRACT_USER_PROMPT = """Answer one contract clarification question using hidden task evidence.
Return exactly one JSON object with exactly these three keys:
{"answer_kind":"select_option|value_id|confirm|deny|refuse","selected_option_ids":[],"value_id":null}

Use one of these literal shapes:
- {"answer_kind":"select_option","selected_option_ids":["OPTION_ID"],"value_id":null}
- {"answer_kind":"value_id","selected_option_ids":[],"value_id":"VALUE_ID"}
- {"answer_kind":"confirm","selected_option_ids":[],"value_id":null}
- {"answer_kind":"deny","selected_option_ids":[],"value_id":null}
- {"answer_kind":"refuse","selected_option_ids":[],"value_id":null}

For select_option, use exactly one offered option ID. For value_id, use an
opaque semantic value ID supplied by the evaluator. Never return a table name,
column name, SQL fragment, natural-language answer, extra key, markdown fence,
or explanation. Never repeat the original request and never ask a question back.""".strip()

CONTRACT_ALTERNATIVE_PROMPT = """Decide whether to accept one permission-safe semantic alternative.
Preserve the hidden original request and user attitude. Return exactly one JSON
object with exactly these keys:
{"decision":"accept|reject","selected_alternative_id":null}

If accepting, select exactly one offered opaque alternative ID:
{"decision":"accept","selected_alternative_id":"ALTERNATIVE_ID"}
If rejecting or uncertain, return:
{"decision":"reject","selected_alternative_id":null}

Do not describe SQL, schema, or the hidden request. Do not add any other key or
markdown fence.""".strip()


@dataclass(frozen=True)
class ContractUserQuestion:
    text: str
    answer_shape: str = "free_text"
    options: tuple[tuple[str, str], ...] = ()
    value_labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractUserAnswer:
    answer: str
    decision: str
    answer_kind: str
    selected_option_ids: tuple[str, ...] = ()
    value_id: str | None = None


class ContractUserSimulator:
    """Canonical evaluator-side simulator for the v2 contract runtime.

    The runtime talks to this small interface only.  The older
    ``LLMUserSimulator`` remains available as a compatibility adapter for
    stage-6 callers, but it is not part of the active contract path.
    """

    def __init__(self, client: ContractUserClient):
        self.client = client

    def answer_question(self, question: ContractUserQuestion, *, private_task: Mapping[str, Any]) -> ContractUserAnswer:
        return answer_contract_question(self.client, question, private_task=private_task)

    def decide_alternative(self, alternatives: Iterable[Mapping[str, str]], *, private_task: Mapping[str, Any]) -> tuple[str, str | None]:
        return decide_contract_alternative(self.client, alternatives, private_task=private_task)


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
        raise ValueError("contract user simulator must return a JSON object")
    return value


def answer_contract_question(client: ContractUserClient, question: ContractUserQuestion, *, private_task: Mapping[str, Any]) -> ContractUserAnswer:
    shape = question.answer_shape.casefold()
    fixed_boolean = any(token in shape for token in ("confirm", "yes", "no", "boolean"))
    if not question.options and not (question.value_labels or {}) and not fixed_boolean:
        # Free text is intentionally outside the active v2 protocol.  Return
        # the equivalent of BIRD-Interact's unanswerable action without even
        # asking the model to invent a value.
        return ContractUserAnswer("I cannot answer that question.", "refuse", "refuse")
    payload = {"task": dict(private_task), "question": {"text": question.text, "answer_shape": question.answer_shape, "options": [{"id": item[0], "label": item[1]} for item in question.options], "value_ids": sorted((question.value_labels or {}).keys())}}
    # A malformed simulator response is equivalent to BIRD-Interact's
    # ``unanswerable()`` action.  It must not become a parser/runtime error or
    # cause the runner to trust an arbitrary free-form answer.
    # Keep transport failures visible to the smoke/evaluator; only malformed
    # model content is normalized to ``refuse``.
    raw_response = client.complete(system_prompt=CONTRACT_USER_PROMPT, user_payload=payload, response_schema=CONTRACT_USER_RESPONSE_SCHEMA)
    try:
        raw = _json(raw_response)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ContractUserAnswer("I cannot answer that question.", "refuse", "refuse")
    kind = raw.get("answer_kind")
    option_ids = raw.get("selected_option_ids")
    value_id = raw.get("value_id")
    allowed = {item[0] for item in question.options}
    if not isinstance(option_ids, list) or any(not isinstance(item, str) or item not in allowed for item in option_ids):
        return ContractUserAnswer("I cannot answer that question.", "refuse", "refuse")
    if kind == "select_option" and len(option_ids) == 1:
        labels = [label for ident, label in question.options if ident in option_ids]
        return ContractUserAnswer("I mean " + "; ".join(labels) + ".", "answer", "select_option", tuple(option_ids))
    labels = question.value_labels or {}
    if kind == "value_id" and isinstance(value_id, str) and value_id in labels:
        return ContractUserAnswer(str(labels[value_id]), "answer", "value_id", value_id=value_id)
    if kind == "confirm" and "confirm" in question.answer_shape.casefold():
        return ContractUserAnswer("Yes.", "answer", "confirm")
    if kind == "deny" and any(token in question.answer_shape.casefold() for token in ("confirm", "yes", "no", "boolean")):
        return ContractUserAnswer("No.", "answer", "deny")
    return ContractUserAnswer("I cannot answer that question.", "refuse", "refuse")


def decide_contract_alternative(client: ContractUserClient, alternatives: Iterable[Mapping[str, str]], *, private_task: Mapping[str, Any]) -> tuple[str, str | None]:
    offered = tuple(item for item in alternatives if isinstance(item, Mapping) and isinstance(item.get("id"), str))
    payload = {"task": dict(private_task), "alternatives": [{"id": item["id"], "summary": str(item.get("summary", ""))} for item in offered]}
    raw_response = client.complete(system_prompt=CONTRACT_ALTERNATIVE_PROMPT, user_payload=payload, response_schema=CONTRACT_ALTERNATIVE_RESPONSE_SCHEMA)
    try:
        raw = _json(raw_response)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "reject", None
    decision = raw.get("decision")
    selected = raw.get("selected_alternative_id")
    ids = {item["id"] for item in offered}
    if decision == "accept" and selected in ids:
        return "accept", selected
    return "reject", None
