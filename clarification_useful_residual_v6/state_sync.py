"""Deterministic post-transition state coordination.

This module does not infer meaning.  It keeps the existing semantic graph,
claims, question ledger and deferred projections synchronized after a
transition.  The frozen runtime remains the executor; this is its single
post-commit consistency boundary for the pool experiment.
"""
from copy import deepcopy

from clarification_deferred_deletion.baseline import light
from .state_contract import validate_projection


def _view(session):
    return session.get('view') if isinstance(session.get('view'), dict) else None


def _ledger(view):
    return (view.get('progressive') or {}).get('decision_ledger', [])


def _active_and_blocked(session):
    view = _view(session) or {}
    active = set(view.get('items', {}))
    blocked = set((session.get('deferred_state') or {}).get('blocked_nodes', {}))
    return active, blocked


def reconcile_state(session, *, event='state_reconcile', strict=True):
    """Synchronize deterministic projections and return a new session.

    ``strict`` controls whether detected drift raises or is recorded as an
    unresolved contract.  No semantic field is invented or rewritten here.
    """
    result = deepcopy(session)
    view = _view(result)
    if not view:
        return result
    progressive = view.setdefault('progressive', {})
    ledger = progressive.setdefault('decision_ledger', [])
    ids = [d.get('decision_id') for d in ledger if isinstance(d, dict)]
    owners = set(view.get('items', {}))
    active, blocked = _active_and_blocked(result)
    progressive_parents = set(progressive.get('parents', {})) | {
        p for p in progressive.get('parents', {}).values() if isinstance(p, str)}
    retired = set(progressive.get('retired', {}))
    errors = []
    if len(ids) != len(set(ids)):
        errors.append('decision ledger contains duplicate IDs')
    if any(not isinstance(d, dict) or d.get('owner_id') not in owners for d in ledger):
        errors.append('decision ledger contains an inactive owner')
    claims = result.get('binding_claims', {})
    if not isinstance(claims, dict):
        errors.append('binding_claims must be an owner map')
    else:
        known = active | blocked | progressive_parents | retired
        unknown = set(claims) - known
        if unknown:
            # Historical claims are retained for lineage and deferred review.
            # They are not an active semantic owner, but they are valid state.
            result.setdefault('state_sync_audit', []).append(dict(
                event=event, warning='historical claim owners retained',
                owners=sorted(unknown)))
    # The segmentation inventory is a read-only projection of the ledger.
    segmentation = view.setdefault('segmentation', {})
    inventory = segmentation.setdefault('inventory', {})
    inventory['progressive'] = deepcopy(progressive)
    # current_request is the active graph's semantic projection.  Deferred
    # canonical text is maintained by sync_canonical and is not overwritten.
    items = view.get('items', {})
    relations = view.get('relations', {})
    edges = relations.get('edges', []) if isinstance(relations, dict) else []
    if items and all(isinstance(n, dict) and 'role' in n and 'requirement' in n
                     for n in items.values()):
        result['current_request'] = light.projection(items, edges)
    elif not items:
        result['current_request'] = ''
    if result.get('obligation_pool') and not errors:
        from . import obligations
        try:
            projected = obligations.refresh(result, event)
            result['obligation_pool'] = projected['obligation_pool']
        except (ValueError, KeyError, TypeError) as exc:
            errors.append('obligation pool projection failed: ' + str(exc))
    # Run after deterministic projections have been refreshed. This catches
    # stale question summaries before the next scheduler/consumer sees them.
    errors.extend(validate_projection(result))
    errors = list(dict.fromkeys(errors))
    if errors:
        reason = 'State coordination failed: ' + '; '.join(errors)
        if strict:
            raise ValueError(reason)
        result['contract_unresolved_reason'] = reason
    result.setdefault('state_sync_audit', []).append(dict(event=event, errors=errors,
        active_nodes=sorted(active), blocked_nodes=sorted(blocked), decision_ids=sorted(i for i in ids if i)))
    return result
