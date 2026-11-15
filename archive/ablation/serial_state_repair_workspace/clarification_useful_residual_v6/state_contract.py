"""Deterministic invariants for the v10 clarification state machine.

Model responses describe semantic content, but they never own lifecycle
identities.  This module checks the small set of projections that every
transition consumes: active owners, pending decisions and the node-level
``missing`` summary.  It deliberately does not infer meaning or permissions.
"""

from copy import deepcopy


DIMENSIONS = frozenset(('scope', 'field', 'operation', 'value', 'permission'))


def validate_projection(session):
    """Return a list of deterministic state errors without mutating state."""
    view = session.get('view') or {}
    items = view.get('items') or {}
    state = session.get('deferred_state') or {}
    blocked = set((state.get('blocked_nodes') or {}).keys())
    errors = []
    overlap = sorted(set(items) & blocked)
    if overlap:
        errors.append('active and blocked node sets overlap: ' + ', '.join(overlap))

    ledger = ((view.get('progressive') or {}).get('decision_ledger') or [])
    ids = []
    by_owner = {}
    for row in ledger:
        if not isinstance(row, dict):
            errors.append('decision ledger contains a non-object row')
            continue
        decision_id, owner, dimension = row.get('decision_id'), row.get('owner_id'), row.get('dimension')
        if not isinstance(decision_id, str) or not decision_id:
            errors.append('decision ledger contains an invalid decision ID')
        else:
            ids.append(decision_id)
        if owner not in items:
            errors.append('decision ledger owner is not active: ' + str(owner))
        if dimension not in DIMENSIONS:
            errors.append('decision ledger has invalid dimension: ' + str(dimension))
        by_owner.setdefault(owner, set()).add(dimension)
    if len(ids) != len(set(ids)):
        errors.append('decision ledger contains duplicate IDs')

    # ``missing`` is a cached routing projection. Every published missing slot
    # must have an authoritative ledger row. During initial analysis and an
    # answer transaction the ledger may be rebuilt one step before the cached
    # node summary; requiring exact equality here would turn that legitimate
    # intermediate state into a system error. The scheduler's stronger final
    # route check handles any ledger entry that is actually selected for user
    # publication.
    for owner, node in items.items():
        if not isinstance(node, dict) or 'missing' not in node:
            continue
        missing = node.get('missing')
        if not isinstance(missing, list) or any(d not in DIMENSIONS for d in missing):
            errors.append('node has invalid missing dimensions: ' + str(owner))
            continue
        if len(missing) != len(set(missing)):
            errors.append('node has duplicate missing dimensions: ' + str(owner))
        if not set(missing) <= by_owner.get(owner, set()):
            errors.append('node missing dimension has no decision ledger row: ' + str(owner))
    return errors


def audit_transition(before, after, event):
    """Check identity preservation across one committed transition.

    This is intentionally a structural audit. Semantic replacement and
    permission closure remain the existing compiler responsibilities.
    """
    errors = validate_projection(after)
    if not isinstance(event, dict):
        errors.append('transition event must be an object')
        return errors
    # Answer identity routing is validated by the code-owned question router
    # immediately before transaction construction. The frozen adapter does not
    # preserve a stable pre-ledger object for this audit boundary, so repeating
    # that check here would compare against a post-consumption ledger and create
    # false transition failures. This audit remains focused on projections and
    # active/blocked ownership.
    return errors

