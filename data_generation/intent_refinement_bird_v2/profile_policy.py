"""Policies for offline evaluation user profiles.

Profiles describe only how a user reacts to an authorized semantic change.
They never change the hidden ``original_request`` or the user's clarification
answers.  This separation is essential: preference is an acceptance policy,
not a second ground-truth intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from .transformation import SemanticTransformation


ProfileName = Literal["flexible_user", "preference_constrained_user", "strict_original_user"]


@dataclass(frozen=True)
class AlternativeDecision:
    decision: Literal["accept", "reject"]
    transformation_id: str | None = None
    reason: str = ""


def _preferred_ids(metadata: Mapping[str, Any]) -> set[str]:
    values = metadata.get("preferred_alternative_ids") or metadata.get("preferred_transformations") or ()
    if isinstance(values, str):
        return {values}
    return {str(value) for value in values} if isinstance(values, (list, tuple, set)) else set()


def _preferred_types(metadata: Mapping[str, Any]) -> set[str]:
    values = metadata.get("preferred_transform_types") or metadata.get("preferred_types") or ()
    if isinstance(values, str):
        return {values}
    return {str(value) for value in values} if isinstance(values, (list, tuple, set)) else set()


def _preferred_fields(metadata: Mapping[str, Any]) -> set[str]:
    values = metadata.get("preferred_fields") or metadata.get("preferred_columns") or ()
    if isinstance(values, str):
        return {values}
    return {str(value) for value in values} if isinstance(values, (list, tuple, set)) else set()


def _contains_preferred_field(transformation: SemanticTransformation, fields: set[str]) -> bool:
    if not fields:
        return False
    text = repr(transformation.new_semantics)
    return any(field in text for field in fields)


def choose_alternative(
    profile: ProfileName,
    candidates: tuple[SemanticTransformation, ...],
    *,
    private_metadata: Mapping[str, Any] | None = None,
) -> AlternativeDecision:
    """Choose an offered transformation without changing the true request."""
    metadata = private_metadata or {}
    if not candidates:
        return AlternativeDecision("reject", reason="no_permission_safe_alternative")
    if profile == "strict_original_user":
        return AlternativeDecision("reject", reason="original_semantics_are_required")
    if profile == "preference_constrained_user":
        ids = _preferred_ids(metadata)
        types = _preferred_types(metadata)
        fields = _preferred_fields(metadata)
        matching = tuple(item for item in candidates if item.transformation_id in ids or item.transform_type in types or _contains_preferred_field(item, fields))
        if not matching:
            return AlternativeDecision("reject", reason="no_candidate_matches_private_preference")
        selected = min(matching, key=lambda item: (len(item.semantic_loss), item.transformation_id))
        return AlternativeDecision("accept", selected.transformation_id, "selected_candidate_matches_private_preference")
    if profile == "flexible_user":
        selected = min(candidates, key=lambda item: (len(item.semantic_loss), item.transformation_id))
        return AlternativeDecision("accept", selected.transformation_id, "selected_lowest_loss_permission_safe_candidate")
    raise ValueError(f"unsupported user profile: {profile}")
