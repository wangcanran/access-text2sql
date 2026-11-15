"""Code-owned structural invariants at state transition boundaries.

These checks do not judge business meaning or invent graph dependencies.
"""
from clarification_deferred_deletion.baseline import main


def check_structure(session):
    main.check_active_state(session)
    view = session.get('view')
    if not view:
        return session
    active = set(view['items'])
    blocked = set(session.get('deferred_state', {}).get('blocked_nodes', {}))
    if active & blocked:
        raise ValueError('Canonical blocked and eligible node identities must be disjoint')
    info = view.get('progressive', {})
    decisions = info.get('decision_ledger', [])
    ids = [d['decision_id'] for d in decisions]
    if len(ids) != len(set(ids)):
        raise ValueError('Pending decision identities must be unique')
    if any(d['owner_id'] not in active for d in decisions):
        raise ValueError('Pending decisions must belong to eligible nodes')
    if set(ids) & {d['decision_id'] for d in session.get('settled_questions', [])}:
        raise ValueError('Settled decisions cannot reenter the pending ledger')
    keys = [q['key'] for q in view.get('questions', [])]
    if len(keys) != len(set(keys)):
        raise ValueError('Current question keys must be unique')
    for q in view.get('questions', []):
        references = q.get('decision_ids', [])
        if len(references) != len(set(references)) or not set(references) <= set(ids):
            raise ValueError('Question routing must reference distinct pending decision identities')
        if any(d['owner_id'] != q['item']['item_id'] for d in decisions if d['decision_id'] in references):
            raise ValueError('A question cannot consume another owner decision')
    parents = info.get('parents', {})
    for child in parents:
        seen, current = set(), child
        while current in parents:
            if current in seen:
                raise ValueError('Node ancestry must be acyclic')
            seen.add(current)
            current = parents[current]
    for edge in view['relations']['edges']:
        if edge['source_id'] == edge['target_id']:
            raise ValueError('A relation cannot refer to its own node')
    return session
