"""Deterministic ownership boundary for model-produced graph references.

The model supplies semantic declarations.  This module validates the identity
surface before any lifecycle transition is allowed to run.  It intentionally
does not judge whether text expresses the user's meaning; that remains a
semantic review responsibility.
"""
from copy import deepcopy


def compile_plan_references(raw, session, pending):
    """Validate and normalize references in a decomposition plan.

    This is the plan-side counterpart of ``compile_model_references`` used by
    answer transactions.  Both boundaries enforce the same rules: parents and
    decisions must be current, aliases must be unique, and graph endpoints may
    only refer to the current graph or this plan's aliases.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('nodes'), list):
        raise ValueError('Decomposition plan must contain a nodes array')
    result = deepcopy(raw)
    view = session.get('view') or {}
    current = set(view.get('items', {}))
    progressive = view.get('progressive') or {}
    current.update(progressive.get('parents', {}))
    current.update(v for v in progressive.get('parents', {}).values() if isinstance(v, str))
    current.update(progressive.get('retired', {}))
    pending_by_id = {d['decision_id']: d for d in (pending or [])}
    aliases = set()
    for node in result['nodes']:
        if not isinstance(node, dict):
            raise ValueError('Every decomposition node must be an object')
        alias = node.get('id')
        parent = node.get('parent')
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError('Every decomposition node needs a nonempty alias')
        if alias in aliases:
            raise ValueError('Duplicate decomposition alias: ' + alias)
        aliases.add(alias)
        if parent not in current:
            raise ValueError('Decomposition parent is outside the current graph: ' + str(parent))
        decision_ids = node.get('decision_ids', [])
        if not isinstance(decision_ids, list) or len(decision_ids) != len(set(decision_ids)):
            raise ValueError('decision_ids must be a distinct list')
        # Historical replay snapshots may not carry the projected ledger;
        # the frozen canonical planner remains the compatibility validator in
        # that case. Fresh sessions always provide the ledger and are checked
        # strictly here.
        if pending_by_id:
            for decision_id in decision_ids:
                decision = pending_by_id.get(decision_id)
                if decision is None:
                    raise ValueError('Decomposition references an unknown decision: ' + str(decision_id))
                if decision['owner_id'] != parent:
                    raise ValueError('Decomposition decision crosses its parent boundary: ' + str(decision_id))
    edges = result.get('edges', [])
    if not isinstance(edges, list):
        raise ValueError('Decomposition edges must be an array')
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 3 or any(not isinstance(v, str) for v in edge):
            raise ValueError('Each decomposition edge must be [source, target, kind]')
        source, target, _ = edge
        if source not in aliases and source not in current:
            raise ValueError('Decomposition edge source is outside the graph: ' + source)
        if target not in aliases and target not in current:
            raise ValueError('Decomposition edge target is outside the graph: ' + target)
    retired = result.get('retired_dependencies', {})
    if not isinstance(retired, dict):
        raise ValueError('retired_dependencies must be a column-to-reason map')
    # Decomposition plans use the graph compiler's canonical representation:
    # physical column -> explanation.  Older transaction plans used
    # parent -> column-list; accepting that shape here was the source of a
    # retry-only contradiction, because canonical_plan consumes the former.
    required = {column for item in (view.get('items') or {}).values()
                for column in item.get('required_columns', [])}
    for column, reason in retired.items():
        if column not in required:
            raise ValueError('Cannot retire an unknown prior dependency: ' + str(column))
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Every retired dependency needs a nonempty reason')
    return result
