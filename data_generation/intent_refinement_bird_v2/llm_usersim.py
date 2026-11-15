"""Compatibility user simulator for stage-6 callers.

The simulator is an evaluation-only component.  Its private task fields are
sent to the simulator model, never to the agent.  The model first locates the
question against hidden evidence (original request, pseudo SQL, or gold SQL)
and then returns a small typed answer.  Natural-language rendering is done by
code so a model cannot freely volunteer unrelated task information.  The
active v2 contract path uses the smaller single-protocol
``ContractUserSimulator`` in ``contract_usersim.py``; this locator/answer API
is retained only for compatibility.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from .alternative_generator import AlternativeCandidate
from .contracts import EvaluationCase
from .profile_policy import ProfileName
from .user_renderer import UserRenderer


class UserModelClient(Protocol):
    def complete(self, *, system_prompt: str, user_payload: dict[str, Any], response_schema: dict[str, Any] | None = None) -> Any:
        ...


LOCATOR_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "scope": {"type": "string", "enum": ["labeled", "unlabeled", "unanswerable"]},
        "evidence_id": {"type": ["string", "null"]},
    },
    "required": ["scope", "evidence_id"],
}

CLARIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "answer_kind": {"type": "string", "enum": ["select_option", "value", "confirm", "deny", "refuse"]},
        "selected_option_ids": {"type": "array", "items": {"type": "string"}},
        "value": {"type": ["string", "null"]},
    },
    "required": ["answer_kind", "selected_option_ids", "value"],
}

ALTERNATIVE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["accept", "reject"]},
        "selected_transformation_id": {"type": ["string", "null"]},
    },
    "required": ["decision", "selected_transformation_id"],
}

LOCATOR_PROMPT = """You are the hidden evaluator-side user simulator for a Text2SQL dialogue.
Classify which hidden evidence is relevant to the agent's question. Return only
the JSON schema. `labeled` means the question asks about a known part of the
original request; `unlabeled` means it asks about a useful semantic detail that
can be answered from the hidden task; `unanswerable` means it asks for schema,
SQL, implementation, or unrelated information. Never write SQL or a hidden
answer in the output. evidence_id must be one of the supplied opaque IDs or
null.
""".strip()

ANSWER_PROMPT = """You are a hidden user answering one clarification question.
Use the original request and SQL evidence only to determine the requested fact.
Answer only the question's target. Do not repeat the original request, SQL,
table names, column names, reasoning, or any other task detail. Return only the
fixed JSON schema. For a multiple-choice question select option IDs. For a
free-text question use a short value only when it is explicitly supported by
the hidden task; otherwise use refuse. Never ask a question back.
""".strip()

ALTERNATIVE_PROMPT = """You are the hidden user deciding whether to accept one of
the permission-safe alternatives offered by an agent. Preserve the original
request. Use the supplied user attitude and hidden task evidence. Return only
accept with exactly one offered transformation ID, or reject with null. Do not
describe schema, SQL, or the hidden request.
""".strip()


@dataclass(frozen=True)
class UserQuestion:
    text: str
    referenced_node_ids: tuple[str, ...] = ()
    expected_answer_shape: str = "free_text"
    options: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_parts(cls, text: str, referenced_node_ids: Iterable[str] = (), expected_answer_shape: str = "free_text", options: Iterable[Mapping[str, Any]] = ()) -> "UserQuestion":
        normalized = []
        for option in options:
            if not isinstance(option, Mapping) or not isinstance(option.get("id"), str) or not isinstance(option.get("label"), str):
                continue
            normalized.append((option["id"], option["label"]))
        return cls(str(text), tuple(dict.fromkeys(str(item) for item in referenced_node_ids)), str(expected_answer_shape), tuple(normalized))


@dataclass(frozen=True)
class LLMUserResponse:
    answer: str
    decision: str
    referenced_node_ids: tuple[str, ...] = ()
    selected_transformation_id: str | None = None
    answer_kind: str = "refuse"
    evidence_scope: str = "unanswerable"


def _json_result(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("user simulator returned invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("user simulator model must return a JSON object")
    return dict(value)


def _sql_segments(sql: str) -> dict[str, str]:
    """Expose coarse hidden evidence labels, never raw labels to the agent."""
    text = str(sql or "")
    segments: dict[str, str] = {}
    matches = list(re.finditer(r"\b(SELECT|FROM|WHERE|GROUP BY|HAVING|ORDER BY|LIMIT)\b", text, flags=re.I))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        key = match.group(1).casefold().replace(" ", "_")
        segments[f"gold_{key}_{index}"] = text[match.start():end].strip()
    return segments


class LLMUserSimulator:
    """Two-stage, schema-constrained simulator backed by a language model."""

    def __init__(self, case: EvaluationCase, client: UserModelClient, *, profile: ProfileName | None = None, renderer: UserRenderer | None = None):
        self._case = case
        self.client = client
        self.profile = profile or (case.profile if case.profile in {"flexible_user", "preference_constrained_user", "strict_original_user"} else "flexible_user")
        self.renderer = renderer or UserRenderer(context=case.public.agent_payload())
        self._turn = 0

    def agent_payload(self) -> dict[str, Any]:
        """The simulator's public payload remains identical to the agent view."""
        return self._case.public.agent_payload()

    def _private_payload(self) -> dict[str, Any]:
        return {
            "original_request": self._case.original_request,
            "pseudo_sql": self._case.pseudo_sql,
            "gold_sql": self._case.gold_sql,
            "profile": self.profile,
            "private_metadata": self._case.private_metadata,
        }

    def _complete(self, prompt: str, payload: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        return _json_result(self.client.complete(system_prompt=prompt, user_payload=payload, response_schema=schema))

    def _locate(self, question: UserQuestion) -> dict[str, Any]:
        gold_segments = _sql_segments(self._case.gold_sql)
        pseudo_segments = {key.replace("gold_", "pseudo_", 1): value for key, value in _sql_segments(self._case.pseudo_sql).items()}
        payload = {
            "task": {"original_request": self._case.original_request, "gold_sql": self._case.gold_sql, "pseudo_sql": self._case.pseudo_sql},
            "question": {"text": question.text, "referenced_node_ids": list(question.referenced_node_ids), "expected_answer_shape": question.expected_answer_shape},
            "evidence_ids": ["original_request", *gold_segments.keys(), *pseudo_segments.keys()],
        }
        result = self._complete(LOCATOR_PROMPT, payload, LOCATOR_SCHEMA)
        scope = result.get("scope")
        evidence_id = result.get("evidence_id")
        valid_ids = set(payload["evidence_ids"])
        if scope not in {"labeled", "unlabeled", "unanswerable"} or (evidence_id is not None and evidence_id not in valid_ids):
            return {"scope": "unanswerable", "evidence_id": None}
        return {"scope": scope, "evidence_id": evidence_id}

    def answer_clarification(self, question: str, *, referenced_node_ids: Iterable[str] = (), expected_answer_shape: str = "free_text", options: Iterable[Mapping[str, Any]] = ()) -> LLMUserResponse:
        self._turn += 1
        safe_options = []
        for option in options:
            if isinstance(option, Mapping):
                safe_options.append({**dict(option), "label": self.renderer.question(str(option.get("label", "")))})
        user_question = UserQuestion.from_parts(self.renderer.question(question), referenced_node_ids, expected_answer_shape, safe_options)
        location = self._locate(user_question)
        if location["scope"] == "unanswerable":
            return LLMUserResponse("I cannot answer that question.", "refuse", user_question.referenced_node_ids, answer_kind="refuse", evidence_scope="unanswerable")
        payload = {
            "task": self._private_payload(),
            "question": {"text": user_question.text, "expected_answer_shape": user_question.expected_answer_shape, "options": [{"id": key, "label": label} for key, label in user_question.options]},
            "located_scope": location,
        }
        result = self._complete(ANSWER_PROMPT, payload, CLARIFICATION_SCHEMA)
        kind = result.get("answer_kind")
        option_ids = result.get("selected_option_ids")
        value = result.get("value")
        allowed_ids = {key for key, _ in user_question.options}
        if not isinstance(option_ids, list) or any(item not in allowed_ids for item in option_ids):
            kind, option_ids, value = "refuse", [], None
        if kind == "select_option" and not option_ids:
            kind = "refuse"
        shape = user_question.expected_answer_shape.casefold()
        if kind == "select_option" and not ("choice" in shape or user_question.options):
            kind, option_ids = "refuse", []
        if kind in {"confirm", "deny"} and not any(token in shape for token in ("confirm", "yes", "no", "boolean")):
            kind = "refuse"
        if kind == "value" and any(token in shape for token in ("choice", "confirm", "yes", "no", "boolean")):
            kind, value = "refuse", None
        if kind == "value" and (not isinstance(value, str) or not value.strip() or len(value.strip()) > 120):
            kind, value = "refuse", None
        if kind == "value" and isinstance(value, str):
            candidate_value = value.strip()
            hidden_request = self._case.original_request.strip()
            # A free-text answer may be short, but it must never echo the
            # hidden request or a substantial SQL fragment.
            if candidate_value.casefold() == hidden_request.casefold() or candidate_value.casefold() in hidden_request.casefold() and len(candidate_value) > 24:
                kind, value = "refuse", None
            if any(fragment and fragment.casefold() in candidate_value.casefold() for fragment in (self._case.gold_sql, self._case.pseudo_sql) if len(fragment) > 20):
                kind, value = "refuse", None
        if kind == "refuse":
            return LLMUserResponse("I cannot answer that question.", "refuse", user_question.referenced_node_ids, answer_kind="refuse", evidence_scope=location["scope"])
        if kind == "select_option":
            labels = [label for key, label in user_question.options if key in option_ids]
            answer = "I mean " + "; ".join(labels) + "."
        elif kind == "value":
            answer = f"The value is {value.strip()}."
        elif kind == "confirm":
            answer = "Yes."
        elif kind == "deny":
            answer = "No."
        else:
            return LLMUserResponse("I cannot answer that question.", "refuse", user_question.referenced_node_ids, answer_kind="refuse", evidence_scope=location["scope"])
        # A user response must never become a SQL/schema side channel.
        public_schema = self._case.public.schema.get("tables", {})
        forbidden = [str(table) for table in public_schema] + [f"{table}.{column}" for table, columns in public_schema.items() for column in columns]
        if any(token.casefold() in answer.casefold() for token in forbidden) or re.search(r"\b(select|from|where|join|group by|order by)\b", answer, re.I):
            return LLMUserResponse("I cannot answer that question.", "refuse", user_question.referenced_node_ids, answer_kind="refuse", evidence_scope=location["scope"])
        return LLMUserResponse(answer, "answer", user_question.referenced_node_ids, answer_kind=kind, evidence_scope=location["scope"])

    def decide_alternative(self, candidates: Iterable[AlternativeCandidate]) -> LLMUserResponse:
        candidates = tuple(candidates)
        if not candidates:
            return LLMUserResponse("I do not accept these changes to my request.", "reject", selected_transformation_id=None, answer_kind="reject")
        payload = {
            "task": self._private_payload(),
            "alternatives": [{"id": item.id, "summary": self.renderer.alternative(item.summary)} for item in candidates],
        }
        result = self._complete(ALTERNATIVE_PROMPT, payload, ALTERNATIVE_SCHEMA)
        decision = result.get("decision")
        selected = result.get("selected_transformation_id")
        ids = {item.id for item in candidates}
        if decision == "accept" and selected in ids:
            return LLMUserResponse("I accept this alternative.", "accept", selected_transformation_id=selected, answer_kind="accept")
        return LLMUserResponse("I do not accept these changes to my request.", "reject", selected_transformation_id=None, answer_kind="reject")


def build_llm_user_simulator(case: EvaluationCase, client: UserModelClient, *, profile: ProfileName | None = None, renderer: UserRenderer | None = None) -> LLMUserSimulator:
    return LLMUserSimulator(case, client, profile=profile, renderer=renderer)
