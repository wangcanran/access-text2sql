"""Auditable semantic transformation records."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, tuple):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class SemanticTransformation:
    """A proposed or authorized change to one or more intent nodes."""

    transformation_id: str
    transform_type: str
    old_semantics: Any
    new_semantics: Any
    affected_nodes: tuple[str, ...]
    semantic_loss: str
    permission_reason: str
    user_authorization_required: bool = True
    authorization_id: str | None = None

    def __post_init__(self) -> None:
        if not self.transformation_id.strip() or not self.transform_type.strip():
            raise ValueError("transformation identifiers must not be empty")
        if not self.affected_nodes or any(not node.strip() for node in self.affected_nodes):
            raise ValueError("a transformation must name affected nodes")
        if not self.semantic_loss.strip() or not self.permission_reason.strip():
            raise ValueError("transformation must explain loss and permission reason")
        if not self.user_authorization_required and self.authorization_id is None:
            raise ValueError("non-user transformations require an authorization id")
        object.__setattr__(self, "old_semantics", _freeze(self.old_semantics))
        object.__setattr__(self, "new_semantics", _freeze(self.new_semantics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.transformation_id,
            "transform_type": self.transform_type,
            "old_semantics": _thaw(self.old_semantics),
            "new_semantics": _thaw(self.new_semantics),
            "affected_nodes": list(self.affected_nodes),
            "semantic_loss": self.semantic_loss,
            "permission_reason": self.permission_reason,
            "user_authorization_required": self.user_authorization_required,
            "authorization_id": self.authorization_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticTransformation":
        return cls(
            transformation_id=str(value["id"]),
            transform_type=str(value["transform_type"]),
            old_semantics=value.get("old_semantics"),
            new_semantics=value.get("new_semantics"),
            affected_nodes=tuple(str(item) for item in value.get("affected_nodes", ())),
            semantic_loss=str(value.get("semantic_loss", "")),
            permission_reason=str(value.get("permission_reason", "")),
            user_authorization_required=bool(value.get("user_authorization_required", True)),
            authorization_id=value.get("authorization_id"),
        )
