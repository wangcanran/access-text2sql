"""Evidence and authorization records for semantic intent snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


EvidenceSource = Literal["user", "agent", "system"]


@dataclass(frozen=True)
class Evidence:
    """A quote or event that supports a semantic claim.

    User evidence is the only source that can establish a confirmed user
    requirement. Agent and system records remain useful for provenance and
    diagnostics but are never equivalent to user confirmation.
    """

    evidence_id: str
    turn: int
    quote: str
    source: EvidenceSource = "user"
    kind: str = "statement"

    def __post_init__(self) -> None:
        if not self.evidence_id.strip():
            raise ValueError("evidence_id must not be empty")
        if self.turn < 0:
            raise ValueError("turn must be non-negative")
        if not self.quote.strip():
            raise ValueError("quote must not be empty")
        if self.source not in {"user", "agent", "system"}:
            raise ValueError(f"unsupported evidence source: {self.source}")

    @property
    def is_user(self) -> bool:
        return self.source == "user"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "turn": self.turn,
            "quote": self.quote,
            "source": self.source,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Evidence":
        return cls(
            evidence_id=str(value["id"]),
            turn=int(value["turn"]),
            quote=str(value["quote"]),
            source=str(value.get("source", "user")),
            kind=str(value.get("kind", "statement")),
        )


@dataclass(frozen=True)
class Authorization:
    """Explicit user authorization for one semantic transformation."""

    authorization_id: str
    transformation_id: str
    turn: int
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not self.authorization_id.strip() or not self.transformation_id.strip():
            raise ValueError("authorization and transformation ids must not be empty")
        if self.turn < 0:
            raise ValueError("turn must be non-negative")
        if not self.evidence or not any(item.is_user for item in self.evidence):
            raise ValueError("authorization requires at least one user evidence record")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.authorization_id,
            "transformation_id": self.transformation_id,
            "turn": self.turn,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Authorization":
        return cls(
            authorization_id=str(value["id"]),
            transformation_id=str(value["transformation_id"]),
            turn=int(value["turn"]),
            evidence=tuple(Evidence.from_dict(item) for item in value.get("evidence", ())),
        )
