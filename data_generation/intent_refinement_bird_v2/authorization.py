"""Explicit user authorization and rebuilding of transformed intent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .evidence import Authorization, Evidence
from .intent_graph import ConstraintLedger, IntentGraph
from .plan_validator import PlanValidation, validate_plan
from .transformation import SemanticTransformation
from .alternative_generator import AlternativeCandidate
from .transformations import apply_transformation


@dataclass(frozen=True)
class AuthorizationResult:
    graph: IntentGraph
    validation: PlanValidation
    ledger: ConstraintLedger
    authorization: Authorization


def present_transformations(
    ledger: ConstraintLedger,
    transformations: Iterable[SemanticTransformation | AlternativeCandidate],
) -> ConstraintLedger:
    """Register exactly the alternatives shown to the user."""
    result = ledger
    rejected = set(result.rejected_transformation_ids)
    for item in transformations:
        transformation = item.transformation if isinstance(item, AlternativeCandidate) else item
        if transformation.transformation_id in rejected:
            continue
        if transformation.transformation_id not in {entry.transformation_id for entry in result.transformations}:
            result = result.add_transformation(transformation)
    return result


def authorize_transformation(
    graph: IntentGraph,
    ledger: ConstraintLedger,
    transformation: SemanticTransformation,
    *,
    turn: int,
    quote: str,
    public_context: Mapping[str, object],
) -> AuthorizationResult:
    """Authorize exactly one offered transformation and revalidate it."""
    if not transformation.user_authorization_required:
        raise ValueError("transformation must require explicit user authorization")
    registered = next((item for item in ledger.transformations if item.transformation_id == transformation.transformation_id), None)
    if registered is None:
        raise ValueError("transformation was not presented to the user")
    if registered != transformation:
        raise ValueError("transformation differs from the presented offer")
    if transformation.transformation_id in ledger.rejected_transformation_ids:
        raise ValueError("transformation was rejected and cannot be authorized")
    evidence = (Evidence(f"auth_{transformation.transformation_id}_{turn}", turn, quote, "user", "authorization"),)
    authorization = Authorization(f"authorization_{transformation.transformation_id}_{turn}", transformation.transformation_id, turn, evidence)
    authorized_transformation = transformation
    authorized_transformation = SemanticTransformation(
        transformation_id=transformation.transformation_id,
        transform_type=transformation.transform_type,
        old_semantics=transformation.old_semantics,
        new_semantics=transformation.new_semantics,
        affected_nodes=transformation.affected_nodes,
        semantic_loss=transformation.semantic_loss,
        permission_reason=transformation.permission_reason,
        user_authorization_required=True,
        authorization_id=authorization.authorization_id,
    )
    rebuilt = apply_transformation(graph, authorized_transformation)
    validation = validate_plan(rebuilt, public_context)
    if transformation.transformation_id in {item.transformation_id for item in ledger.transformations}:
        next_ledger = ledger
    else:
        next_ledger = ledger.add_transformation(authorized_transformation)
    next_ledger = next_ledger.authorize(authorization)
    next_ledger = next_ledger.attach_authorization(authorized_transformation.transformation_id, authorization.authorization_id)
    return AuthorizationResult(rebuilt, validation, next_ledger, authorization)


def reject_transformation(ledger: ConstraintLedger, transformation: SemanticTransformation) -> ConstraintLedger:
    """Record a user rejection so the same offer cannot reappear."""
    if transformation.transformation_id not in {item.transformation_id for item in ledger.transformations}:
        ledger = ledger.add_transformation(transformation)
    return ledger.reject_transformation(transformation.transformation_id)


def filter_rejected(candidates: Iterable[SemanticTransformation], ledger: ConstraintLedger) -> tuple[SemanticTransformation, ...]:
    rejected = set(ledger.rejected_transformation_ids)
    return tuple(item for item in candidates if item.transformation_id not in rejected)
