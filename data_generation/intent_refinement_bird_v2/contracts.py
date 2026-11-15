"""Explicit stage-0 data contracts.

The public and evaluation-only objects are deliberately separate types.  A
caller can construct an evaluation case for a simulator, but the public
payload has no private target fields or profile labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


_PRIVATE_PUBLIC_KEYS = {
    "original_request", "gold_sql", "pseudo_sql", "profile", "user_profile",
    "private_metadata", "evaluation_only", "private", "original_questions",
}


def _public_copy(value: Any) -> Any:
    """Recursively remove evaluation-only keys before an agent boundary."""
    if isinstance(value, Mapping):
        return {
            str(key): _public_copy(item)
            for key, item in value.items()
            if str(key).casefold() not in _PRIVATE_PUBLIC_KEYS
        }
    if isinstance(value, list):
        return [_public_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_public_copy(item) for item in value)
    return value


@dataclass(frozen=True)
class PublicCase:
    case_id: str
    initial_request: str
    schema: dict[str, Any]
    foreign_keys: tuple[dict[str, str], ...]
    role: str
    policy: dict[str, tuple[str, ...]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def agent_payload(self) -> dict[str, Any]:
        """Return only data allowed to cross the agent boundary."""
        return {
            "case_id": self.case_id,
            "initial_request": self.initial_request,
            "schema": _public_copy(self.schema),
            "foreign_keys": [_public_copy(dict(item)) for item in self.foreign_keys],
            "role": self.role,
            "policy": {table: list(columns) for table, columns in self.policy.items()},
            "metadata": _public_copy(self.metadata),
        }


@dataclass(frozen=True)
class EvaluationCase:
    """Public case plus private evaluation-only information."""

    public: PublicCase
    original_request: str
    gold_sql: str
    pseudo_sql: str
    profile: str | None = None
    private_metadata: dict[str, Any] = field(default_factory=dict)

    def simulator_payload(self) -> dict[str, Any]:
        return {
            "case_id": self.public.case_id,
            "original_request": self.original_request,
            "gold_sql": self.gold_sql,
            "pseudo_sql": self.pseudo_sql,
            "profile": self.profile,
            "private_metadata": self.private_metadata,
        }


def _copy_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    tables = schema.get("tables", schema)
    if not isinstance(tables, Mapping):
        raise TypeError("schema.tables must be a mapping of table to columns")
    copied: dict[str, Any] = {"tables": {str(t): [str(c) for c in cols] for t, cols in tables.items()}}
    if "foreign_keys" in schema:
        copied["foreign_keys"] = [dict(item) for item in schema["foreign_keys"]]
    return copied
