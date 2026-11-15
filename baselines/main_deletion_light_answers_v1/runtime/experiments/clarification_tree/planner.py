"""Interleaved local question planning, not a global decomposition pipeline."""
from copy import deepcopy
from .state import require, text, frontier, add_issue, validate, business_role
from .permissions import check_columns, dependencies, safe, node_dependencies
from .transaction import DIMENSIONS, candidates
from .response_format import plan as normalize_plan


def assess(state, target, raw, context):
    raw, menus, normalization = normalize_plan(raw, state['nodes'][target])
    require(isinstance(raw, dict) and {'granularity', 'columns', 'issues', 'focus', 'options'} <= set(raw)
            <= {'role', 'granularity', 'columns', 'computation', 'issues', 'focus', 'options'}, 'Invalid plan')
    role = business_role(raw['granularity'], raw.get('role'))
    check_columns(raw['columns'], context)
    require(set(state['nodes'][target]['columns']) <= set(raw['columns']), 'Planning cannot retire dependencies')
    if raw['granularity'] != 'atomic':
        require(set(raw['columns']) == set(state['nodes'][target]['columns']),
                'Compound planning cannot add speculative bindings')
    require(isinstance(raw['issues'], list) and isinstance(raw['options'], list), 'Invalid plan arrays')
    require(raw['focus'] is None if not raw['issues'] else type(raw['focus']) is int and
            0 <= raw['focus'] < len(raw['issues']), 'Invalid focus')
    require(raw['issues'] or raw['granularity'] == 'atomic',
            'Unclear leaf requires a question')
    new = deepcopy(state)
    if normalization:
        new['audit'].append({'action': 'normalize_plan', 'target': target, 'rules': normalization})
    n = new['nodes'][target]
    # Analysis cannot silently reinterpret an already confirmed atomic role.
    require(n['role'] is None or n['role'] == role, 'Role change needs an answer')
    n.update(role=role, granularity=raw['granularity'], columns=raw['columns'],
             computation=deepcopy(raw.get('computation', n.get('computation', {}))), assessed=True)
    ids = []
    for q in raw['issues']:
        require(isinstance(q, dict) and {'dimension', 'question'} <= set(q) <= {'dimension', 'question', 'issue_id'} and
                q['dimension'] in DIMENSIONS and text(q['question']), 'Invalid planned issue')
        if 'issue_id' in q:
            old = new['issues'].get(q['issue_id'])
            require(old is not None and old['owner'] == target and old['status'] == 'pending'
                    and old['dimension'] == q['dimension'], 'Invalid existing pending identity')
            old['question'] = q['question']
            ids.append(q['issue_id'])
            continue
        require(not any(old['owner'] == target and old['status'] == 'answered'
                        and old['question'] == q['question'] for old in new['issues'].values()),
                'Cannot re-ask an answered question')
        found = next((i for i, old in new['issues'].items() if old['owner'] == target and
                      old['status'] == 'pending' and old['dimension'] == q['dimension'] and
                      old['question'] == q['question']), None)
        ids.append(found or add_issue(new, target, q['dimension'], q['question']))
    require(len(ids) == len(set(ids)), 'Duplicate planned issue')
    for index, options in menus.items():
        labels = set()
        for o in options:
            require(isinstance(o, dict) and set(o) == {'label', 'columns'} and text(o['label'])
                    and o['label'] not in labels, 'Invalid business option')
            labels.add(o['label'])
            check_columns(o['columns'], context)
            require(raw['issues'][index]['dimension'] != 'field' or o['columns'],
                    'Field option needs concrete dependencies')
        if index != raw['focus']:
            q = new['issues'][ids[index]]
            require(q['dimension'] != 'field' or options, 'Field question requires options')
            business = q['dimension'] != 'field'
            q['options'] = [dict(label=o['label'], columns=[] if business else o['columns']) for o in options]
            q['menu_kind'] = 'business' if business else 'binding'
    if ids:
        chosen = ids[raw['focus']]
        require(new['issues'][chosen]['dimension'] != 'field' or raw['options'], 'Field question requires options')
        business = new['issues'][chosen]['dimension'] != 'field'
        # Business alternatives are semantic choices, not permission-filtered
        # executable bindings. Do not silently turn model-supplied columns into
        # obligations from selecting a business meaning.
        new['issues'][chosen]['options'] = [dict(label=o['label'], columns=[] if business else o['columns'])
                                            for o in deepcopy(raw['options'])]
        new['issues'][chosen]['menu_kind'] = 'business' if business else 'binding'
        if business and any(o['columns'] for o in raw['options']):
            new['audit'].append({'action': 'business_option_binding_ignored', 'issue_id': chosen,
                                 'proposed_options': deepcopy(raw['options'])})
        n['planned_issue'] = chosen
    candidates(new)
    new['revision'] += 1
    return validate(new)


def next_step(state, context, allowed):
    leaves = frontier(state)
    if not leaves:
        return {'kind': 'refuse', 'reason': 'no_remaining_objective'}
    deferred = {i for i, n in leaves.items() if n.get('blocked')}
    # A possible dependence on a blocked anchor is not independence.
    for _ in range(len(leaves)):
        for r in state['relations']:
            if r['status'] not in ('confirmed', 'unresolved'):
                continue
            if r['kind'] == 'applies_to' and r['source'] in deferred:
                deferred.add(r['target'])
            if r['kind'] == 'depends_on' and r['target'] in deferred:
                deferred.add(r['source'])
    # Known local blockers are handled before spending calls on other leaves.
    for iid, n in leaves.items():
        if iid in deferred:
            continue
        if n['granularity'] == 'atomic' and not safe(dependencies(state, iid, context), context, allowed):
            return {'kind': 'permission', 'target': iid}
    for r in state['relations']:
        if r['status'] == 'unresolved' and not {r['source'], r['target']} & deferred:
            return {'kind': 'relation', 'relation': deepcopy(r)}
    for iid, n in leaves.items():
        if iid in deferred:
            continue
        if not n['assessed']:
            return {'kind': 'assess', 'target': iid}
        questions = sorted(state['issues'].values(), key=lambda q: q['id'] != n.get('planned_issue'))
        for q in questions:
            if q['owner'] == iid and q['status'] == 'pending':
                if q['dimension'] == 'field' and 'options' not in q:
                    return {'kind': 'assess', 'target': iid}
                return {'kind': 'question', 'target': iid, 'issue_id': q['id']}
        if n['granularity'] != 'atomic':
            return {'kind': 'unresolved', 'reason': 'unclear_leaf_without_question'}
    if deferred:
        return {'kind': 'unresolved', 'reason': 'permission_blocked'}
    if not any(n['role'] == 'output' for n in leaves.values()):
        return {'kind': 'unresolved', 'reason': 'remaining_conditions_without_output'}
    for iid, n in leaves.items():
        if n['role'] == 'output' and not n['columns']:
            return {'kind': 'unresolved', 'reason': 'output_without_binding'}
        # Computation is semantic enrichment for the Text2SQL prompt. A
        # proposed formula may be rendered as natural language; only an
        # unresolved issue still blocks closure.
        if any(q['owner'] == iid and q['status'] == 'pending'
               and q['dimension'] in ('operation', 'value') for q in state['issues'].values()):
            return {'kind': 'unresolved', 'reason': 'calculation_semantics_unresolved'}
        if n['role'] == 'modifier' and not any(r['source'] == iid and r['status'] == 'confirmed'
                                             for r in state['relations']):
            return {'kind': 'unresolved', 'reason': 'modifier_without_confirmed_scope'}
    if not safe({c for n in leaves.values() for c in node_dependencies(n, context)}, context, allowed):
        return {'kind': 'unresolved', 'reason': 'joint_connectivity'}
    return {'kind': 'deliver'}
