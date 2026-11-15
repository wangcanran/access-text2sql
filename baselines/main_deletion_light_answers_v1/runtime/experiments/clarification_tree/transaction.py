"""Atomic answer-driven tree expansion and explicit issue ownership."""
from copy import deepcopy
from .state import require, text, node, frontier, validate, add_issue, business_role, attachment_possible
from .permissions import check_columns
from .response_format import transaction as normalize_transaction

DIMENSIONS = ('boundary', 'relation', 'field', 'operation', 'value')


def descriptor(raw, context):
    require(isinstance(raw, dict) and {'alias', 'meaning', 'granularity', 'columns'} <= set(raw)
            <= {'alias', 'meaning', 'role', 'granularity', 'columns', 'scope_columns', 'computation', 'output_exclusions'},
            'Invalid child descriptor')
    require(text(raw['alias']) and text(raw['meaning']) and raw['granularity'] in
            ('compound', 'atomic', 'undetermined'), 'Invalid child semantics')
    business_role(raw['granularity'], raw.get('role'))
    check_columns(raw['columns'], context)
    check_columns(raw.get('scope_columns', []), context)


def candidates(state):
    """New modifiers leave attachment scope explicitly unresolved."""
    leaves = frontier(state)
    known = {(r['source'], r['target'], r['kind']) for r in state['relations'] if r['status'] != 'retired'}
    for m in leaves.values():
        if m['role'] != 'modifier':
            continue
        for o in leaves.values():
            key = (m['id'], o['id'], 'applies_to')
            if o['id'] != m['id'] and attachment_possible(m, o) and key not in known:
                state['relations'].append(dict(source=m['id'], target=o['id'],
                                              kind='applies_to', status='unresolved'))
                known.add(key)


def commit(state, event, raw, context):
    require(event['revision'] == state['revision'], 'Stale answer')
    target = event['target']
    require(target in frontier(state), 'Target is not an active leaf')
    raw, normalization = normalize_transaction(raw, state, event)
    require(isinstance(raw, dict) and set(raw) == {'action', 'children', 'issue_updates',
        'new_issues', 'relations', 'retired_columns', 'retirement_reason', 'coverage'}, 'Invalid transaction')
    action = raw['action']
    require(action in ('revise', 'expand', 'retain') and isinstance(raw['children'], list), 'Invalid action')
    require(len(raw['children']) >= 2 if action == 'expand' else len(raw['children']) == 1,
            'Expansion needs multiple children; revision needs one')
    require(text(raw['coverage']), 'Missing remainder/coverage declaration')
    for child in raw['children']:
        descriptor(child, context)
    aliases = [c['alias'] for c in raw['children']]
    require(len(set(aliases)) == len(aliases) and not set(aliases) & set(state['nodes']), 'Ambiguous child alias')
    check_columns(raw['retired_columns'], context)
    old_columns = set(state['nodes'][target]['columns'])
    retired = set(raw['retired_columns'])
    require(retired <= old_columns and (not retired or text(raw['retirement_reason'])), 'Invalid retirement')
    require(isinstance(raw['issue_updates'], dict) and isinstance(raw['new_issues'], list)
            and isinstance(raw['relations'], list), 'Invalid transaction collections')
    old_issues = {i: q for i, q in state['issues'].items() if q['owner'] == target and q['status'] == 'pending'}
    require(set(raw['issue_updates']) == set(old_issues), 'Every old issue needs an owner/disposition')
    required = (old_columns - retired) | set(event.get('selected_columns', []))
    old_scope = set(state['nodes'][target].get('scope_columns', []))
    proposed_scope = old_scope | {c for child in raw['children'] if child['granularity'] == 'atomic' for c in child.get('scope_columns', [])}
    for child in raw['children']:
        if child['granularity'] != 'atomic':
            child['scope_columns'] = sorted(set(child.get('scope_columns', [])) & old_scope)
        if child['granularity'] != 'atomic' and proposed_scope:
            child['columns'] = sorted(set(child['columns']) & old_columns)
    for child in raw['children']:
        if child['granularity'] != 'atomic':
            require(set(child['columns']) <= required,
                    'Compound remainder cannot introduce speculative bindings; materialize a local contribution first')
        require(set(child.get('scope_columns', [])) <= proposed_scope,
                'Compound remainder cannot introduce speculative scope bindings')
    require(required <= {c for n in raw['children'] for c in n['columns']}, 'Lost retained/selected dependency')
    new = deepcopy(state)
    if normalization:
        new['audit'].append({'action': 'normalize_transaction', 'target': target, 'rules': normalization})
    mapping = {}
    for child in raw['children']:
        if action == 'expand':
            iid = 'f' + str(new['next_node'])
            new['next_node'] += 1
            n = node(iid, child['meaning'], target)
            new['nodes'][iid] = n
        else:
            iid = target
            n = new['nodes'][iid]
        mapping[child['alias']] = iid
        n.update({k: deepcopy(child[k]) for k in ('meaning', 'granularity', 'columns')})
        n['scope_columns'] = deepcopy(child.get('scope_columns', []))
        n['computation'] = deepcopy(child.get('computation', {}))
        n['output_exclusions'] = deepcopy(child.get('output_exclusions', []))
        n['role'] = business_role(child['granularity'], child.get('role'))
        n.pop('blocked', None)
        n['assessed'] = False
        # Full answers remain in the event audit. Copying the whole parent's
        # dialogue into every child would reintroduce sibling/retired meaning
        # at rendering time. The transaction supplies each child's local form.
        n['answers'] = [{'issue_id': event['issue_id'], 'answer': child['meaning']}]
    if action == 'expand':
        new['nodes'][target]['children'] = list(mapping.values())
        new['nodes'][target].update(role=None, granularity='compound')
    for iid, change in raw['issue_updates'].items():
        require(isinstance(change, dict) and set(change) == {'status', 'owner', 'question', 'reason'}, 'Invalid issue change')
        require(change['owner'] in mapping and change['status'] in ('pending', 'answered')
                and text(change['question']), 'Invalid issue assignment')
        require(change['status'] != 'answered' or ((iid == event['issue_id'] or
                (iid in event.get('local_answered_issues', []) and
                 change['owner'] == raw['issue_updates'][event['issue_id']]['owner'])) and text(change['reason'])),
                'Answer cannot close another issue')
        new['issues'][iid].update(owner=mapping[change['owner']], status=change['status'], question=change['question'])
        new['issues'][iid].pop('options', None)
    for q in raw['new_issues']:
        require(isinstance(q, dict) and set(q) == {'owner', 'dimension', 'question'}
                and q['owner'] in mapping and q['dimension'] in DIMENSIONS, 'Invalid new issue')
        owner = mapping[q['owner']]
        require(not any(x['owner'] == owner and x['status'] == 'pending' and x['question'] == q['question']
                        for x in new['issues'].values()), 'Duplicate pending question')
        add_issue(new, owner, q['dimension'], q['question'])
    # Never copy confirmed scope to all children. Reopen every old incident
    # relationship as candidates and keep its old source as audit provenance.
    if action == 'expand':
        for r in list(new['relations']):
            if r['status'] == 'retired' or target not in (r['source'], r['target']):
                continue
            old = deepcopy(r)
            r['status'] = 'retired'
            for child in mapping.values():
                candidate = {**old, 'source': child if old['source'] == target else old['source'],
                             'target': child if old['target'] == target else old['target'], 'status': 'unresolved'}
                leaves = frontier(new)
                if candidate['kind'] == 'applies_to' and not attachment_possible(
                    leaves[candidate['source']], leaves[candidate['target']]):
                    add_issue(new, child, 'relation',
                        'After refinement, clarify how this contribution relates to the previous condition: ' +
                        state['nodes'][old['source']]['meaning'])
                    continue
                new['relations'].append(candidate)
    for r in raw['relations']:
        require(isinstance(r, dict) and set(r) == {'source', 'target', 'kind', 'status'}
                and r['status'] == 'unresolved', 'New edges require user confirmation')
        candidate = {**r, 'source': mapping.get(r['source'], r['source']), 'target': mapping.get(r['target'], r['target'])}
        require(candidate['source'] in mapping.values() or candidate['target'] in mapping.values(), 'Outside relation edit')
        if candidate not in new['relations']:
            new['relations'].append(candidate)
    candidates(new)
    # A post-answer plan must not regenerate already answered questions. Mark
    # well-described atomic children assessed; new field issues still request
    # option preparation through the scheduler.
    for iid in mapping.values():
        n = new['nodes'][iid]
        n['assessed'] = n['role'] in ('output', 'modifier') and n['granularity'] == 'atomic'
        if not n['assessed'] and not any(q['owner'] == iid and q['status'] == 'pending'
                                       for q in new['issues'].values()):
            n['needs_local_focus'] = True
    new['revision'] += 1
    new['audit'].append({'event': deepcopy(event), 'transaction': deepcopy(raw), 'mapping': mapping})
    return validate(new)


def cancel(state, target, *, automatic=False):
    require(target in frontier(state), 'Only a current leaf can be explicitly cancelled')
    require(not automatic or state['nodes'][target]['granularity'] == 'atomic',
            'Automatic pruning requires an atomic contribution; refine containers first')
    new = deepcopy(state)
    new['nodes'][target]['lifecycle'] = 'cancelled'
    for q in new['issues'].values():
        if q['owner'] == target and q['status'] == 'pending':
            q['status'] = 'cancelled'
    affected = set()
    for r in new['relations']:
        if r['status'] == 'retired' or target not in (r['source'], r['target']):
            continue
        if r['status'] == 'confirmed':
            if r['kind'] == 'applies_to' and r['source'] == target:
                affected.add(r['target'])
            if r['kind'] == 'depends_on' and r['target'] == target:
                affected.add(r['source'])
        r['status'] = 'retired'
    for iid in affected:
        if iid not in frontier(new):
            continue
        issue_id = add_issue(new, iid, 'boundary', 'The shared requirement was cancelled. Clarify the remaining subject or scope without restoring it.')
        new['issues'][issue_id]['cancelled_node_id'] = target
        new['nodes'][iid]['assessed'] = False
    # Orphan modifiers are kept for explicit resolution, not silently discarded.
    new['revision'] += 1
    new['audit'].append({'action': 'explicit_cancel', 'target': target, 'affected': sorted(affected), 'automatic': automatic})
    return validate(new)
