"""Check the dependency effects of an answer without a second model call."""
from copy import deepcopy


def resolve_permission_choice(session, context):
    """A chosen field is evidence; only a checked updated node clears its issue.

    Never discharge restrictions merely on category navigation or answer commit.
    A resplit needs an unambiguous source match; reused IDs are not provenance.
    """
    if not session.get('strict_menus') or not session['turns']:
        return
    turn = session['turns'][-1]
    if not turn.get('permission_field_choice'):
        return
    key = turn['permission_issue_key']
    old = session['permission_issues'].get(key)
    if old is None:
        return
    view = session['view']
    if turn.get('target_inventory_id') == view['inventory_id']:
        candidates = [view['items'][turn['target_id']]] if turn['target_id'] in view['items'] else []
    else:
        candidates = [n for n in view['items'].values()
                      if (n['span'], n['role']) == (turn['target_fragment'], turn['target_role'])]
    if len(candidates) != 1:
        return
    node = candidates[0]
    selected = {c for o in turn['selected_options'] for c in o['required_columns']}
    deps = set(node['required_columns'])
    from .simple_session import connected_options
    if (not selected or not selected <= deps or not deps <= set(session['allowed'])
            or set(node['missing']) & {'scope', 'field'}
            or not connected_options(session, context, node, deps)):
        return
    session['permission_issues'].pop(key)
    session.setdefault('permission_resolution_audit', []).append({
        'turn': len(session['turns']) - 1, 'issue_key': key,
        'item_id': node['item_id'], 'previous_denied_columns': old['denied_columns'],
        'checked_columns': sorted(deps)})


def record_dependency_update(session, previous):
    """Audit newly mapped fields and the next route derived from current state.

    Open text is not restricted to the dimension asked. Its mapping is model-owned;
    the dependency delta and access decisions below are deterministic.
    """
    if not session.get('strict_menus') or not session['turns'] or previous is None:
        return session
    turn = session['turns'][-1]
    view = session['view']
    stable = previous.get('inventory_id') == view.get('inventory_id')
    rows = []
    for iid, node in view['items'].items():
        old = previous['items'].get(iid) if stable else None
        if old is None and not stable:
            matches = [n for n in previous['items'].values()
                       if (n['span'], n['role']) == (node['span'], node['role'])]
            old = matches[0] if len(matches) == 1 else None
        before = set(old['required_columns']) if old else set()
        after = set(node['required_columns'])
        added = after - before
        pending = set(node['missing']) & {'scope', 'field'}
        denied = after - set(session['allowed'])
        if not added and not pending:
            continue
        route = ('scope_menu' if 'scope' in pending else 'permission_menu' if denied
                 or any(n['item_id'] == iid for n in session['permission_issues'].values())
                 else 'field_menu' if 'field' in pending else 'mapped_authorized')
        rows.append({'item_id': iid, 'new_columns': sorted(added),
                     'new_denied_columns': sorted(added & denied),
                     'pending_field_decisions': sorted(pending), 'route': route})
    view['answer_dependency_audit'] = {'answer_action': turn['answer']['action'],
        'asked_dimension': turn.get('asked_dimension'), 'stable_inventory': stable,
        'items': rows, 'user_answer': deepcopy(turn['answer'])}
    return session
