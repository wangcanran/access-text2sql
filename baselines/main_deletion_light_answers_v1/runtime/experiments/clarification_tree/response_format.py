"""Lossless, local response normalization before semantic contract validation.

No model calls, semantic matching, role inference or automatic answer closure.
Explicit contradictory values are rejected rather than overwritten.
"""
from copy import deepcopy
from .state import require


def plan(raw, current):
    require(isinstance(raw, dict), 'Plan must be an object')
    result = deepcopy(raw)
    audit = []
    issues = result.get('issues')
    require(isinstance(issues, list), 'Plan issues must be an array')
    if 'focus' not in result:
        require(len(issues) <= 1, 'Multiple issues require an explicit focus')
        result['focus'] = 0 if issues else None
        audit.append('derived_single_issue_focus')
    focus = result['focus']
    require(focus is None if not issues else type(focus) is int and 0 <= focus < len(issues), 'Invalid focus')
    if 'columns' not in result:
        result['columns'] = list(current['columns'])
        audit.append('inherited_established_columns')
    nested = {}
    for i, q in enumerate(issues):
        require(isinstance(q, dict), 'Issue must be an object')
        if 'options' in q:
            nested[i] = q.pop('options')
    top = result.get('options', [])
    require(isinstance(top, list), 'Top-level options must be an array')
    for i, options in nested.items():
        require(isinstance(options, list), 'Issue options must be an array')
    if focus in nested:
        require(not top or not nested[focus] or top == nested[focus],
                'Conflicting top-level and focused issue options')
        top = nested[focus] or top
        audit.append('focused_issue_options_to_menu')
    result['options'] = top
    # Preserve nonfocused menus as well; normalization must not throw content away.
    menus = {i: values for i, values in nested.items() if i != focus}
    menus[focus] = result['options'] if focus is not None else []
    for i, options in menus.items():
        if i is None:
            require(not options, 'Options without a question')
            continue
        for option in options:
            require(isinstance(option, dict), 'Option must be an object')
            if 'columns' not in option and issues[i].get('dimension') != 'field':
                option['columns'] = []
                audit.append('empty_business_option_binding')
    return result, {i: opts for i, opts in menus.items() if i is not None}, audit


def transaction(raw, state, event):
    require(isinstance(raw, dict), 'Transaction must be an object')
    result = deepcopy(raw)
    children = result.get('children')
    require(isinstance(children, list) and children, 'Transaction needs nonempty children')
    audit = []
    if 'action' not in result:
        result['action'] = 'expand' if len(children) > 1 else 'revise'
        audit.append('derived_action_from_child_count')
    for key, default in [('issue_updates', {}), ('new_issues', []), ('relations', []),
                         ('retired_columns', []), ('retirement_reason', '')]:
        if key not in result:
            result[key] = deepcopy(default)
            audit.append('default_' + key)
    for i, child in enumerate(children):
        require(isinstance(child, dict), 'Child must be an object')
        if 'alias' not in child:
            child['alias'] = 'p' + str(i + 1)
            audit.append('positional_child_alias')
        if 'columns' not in child:
            # Omission in one-to-one revision preserves existing obligations.
            # Expansion requires the semantic writer to allocate dependencies.
            child['columns'] = sorted(set(state['nodes'][event['target']]['columns']) |
                                      set(event.get('selected_columns', []))) if len(children) == 1 else []
            audit.append('default_child_columns')
        if 'scope_columns' not in child:
            child['scope_columns'] = list(state['nodes'][event['target']].get('scope_columns', [])) if len(children) == 1 else []
    changes = result['issue_updates']
    require(isinstance(changes, dict) and isinstance(result['new_issues'], list)
            and isinstance(result['relations'], list), 'Invalid transaction collections')
    old = {i: q for i, q in state['issues'].items()
           if q['owner'] == event['target'] and q['status'] == 'pending'}
    single_owner = children[0]['alias'] if len(children) == 1 else None
    if single_owner is not None:
        for iid, q in old.items():
            if iid not in changes:
                changes[iid] = {'status': 'pending', 'owner': single_owner, 'question': q['question'], 'reason': ''}
                audit.append('preserved_omitted_pending_' + iid)
    for iid, change in changes.items():
        require(isinstance(change, dict), 'Issue disposition must be an object')
        if iid not in old:
            continue  # Existing semantic validator supplies unknown-ID diagnostic.
        if single_owner is not None:
            change.setdefault('owner', single_owner)
        change.setdefault('question', old[iid]['question'])
        change.setdefault('status', 'pending')
        change.setdefault('reason', '')  # answered still requires explicit evidence.
    for q in result['new_issues']:
        require(isinstance(q, dict), 'New issue must be an object')
        if single_owner is not None:
            q.setdefault('owner', single_owner)
    for r in result['relations']:
        require(isinstance(r, dict), 'Relation must be an object')
        r.setdefault('status', 'unresolved')
    return result, audit
