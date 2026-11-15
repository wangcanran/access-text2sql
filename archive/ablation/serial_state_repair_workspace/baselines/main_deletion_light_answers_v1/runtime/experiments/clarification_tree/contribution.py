"""Staged local contributions and code-owned answer/cancel transactions."""
from copy import deepcopy
from .state import require, text, frontier, business_role, add_issue, validate
from .permissions import check_columns
from .transaction import commit, cancel, DIMENSIONS


def content(raw, alias, context):
    require(isinstance(raw, dict) and {'meaning', 'granularity'} <= set(raw)
            <= {'meaning', 'granularity', 'role', 'columns', 'scope_columns', 'computation', 'output_exclusions'}, 'Invalid contribution content')
    require(text(raw['meaning']), 'Missing contribution meaning')
    role = business_role(raw['granularity'], raw.get('role'))
    columns = deepcopy(raw.get('columns', []))
    scope_columns = deepcopy(raw.get('scope_columns', []))
    computation = deepcopy(raw.get('computation', {}))
    require(isinstance(computation, dict), 'Invalid computation')
    exclusions = deepcopy(raw.get('output_exclusions', []))
    require(isinstance(exclusions, list) and all(text(x) for x in exclusions), 'Invalid output exclusions')
    check_columns(columns, context)
    check_columns(scope_columns, context)
    return dict(alias=alias, meaning=raw['meaning'], granularity=raw['granularity'], role=role,
                columns=columns, scope_columns=scope_columns, computation=computation, output_exclusions=exclusions)


def prepare(state, target, raw, context):
    """No tree mutation. Every existing obligation gets a staged owner."""
    require(target in frontier(state), 'Focus requires an active leaf')
    require(isinstance(raw, dict) and {'focus', 'remainders', 'question', 'coverage'} <= set(raw)
            <= {'focus', 'remainders', 'question', 'coverage', 'issue_owners', 'relations'}, 'Invalid focus plan')
    require(isinstance(raw['remainders'], list) and text(raw['coverage']), 'Invalid remainder coverage')
    parts = [content(raw['focus'], 'focus', context)] + [content(c, 'r' + str(i + 1), context)
                                                       for i, c in enumerate(raw['remainders'])]
    aliases = {c['alias'] for c in parts}
    for p in parts:
        p['output_exclusions'] = list(dict.fromkeys(state['nodes'][target].get('output_exclusions', [])
                                                   + p['output_exclusions']))
    old = {i: q for i, q in state['issues'].items() if q['owner'] == target and q['status'] == 'pending'}
    owners = deepcopy(raw.get('issue_owners', {}))
    require(isinstance(owners, dict), 'issue_owners must be an object')
    if len(parts) == 1:
        for iid in old:
            owners.setdefault(iid, 'focus')
        if 'columns' not in raw['focus']:
            parts[0]['columns'] = deepcopy(state['nodes'][target]['columns'])
    require(set(owners) == set(old),
            'Every pending issue requires exactly one contribution owner: '
            f'target={target}; allowed={sorted(old)}; unexpected={sorted(set(owners)-set(old))}; '
            f'missing={sorted(set(old)-set(owners))}. External issues are read-only; '
            'use {} when allowed is empty, and omit question.issue_id for a new question.')
    require(all(v in aliases for v in owners.values()), 'Invalid contribution alias in issue_owners')
    established = set(state['nodes'][target]['columns'])
    established_scope = set(state['nodes'][target].get('scope_columns', []))
    proposed_scope = established_scope | {c for p in parts if p['granularity'] == 'atomic' for c in p.get('scope_columns', [])}
    # A compound contribution is still a semantic container. Any fields the
    # model lists for it are only guesses until a descendant becomes atomic;
    # retain previously established parent dependencies but discard new
    # candidate bindings at this boundary.
    for p in parts:
        if p['granularity'] != 'atomic':
            # Bind compound business conditions only when locally refined;
            # a sibling's new binding is not established scope here.
            p['scope_columns'] = sorted(set(p['scope_columns']) & established_scope)
        if p['granularity'] != 'atomic' and proposed_scope:
            p['columns'] = sorted(set(p['columns']) & established)
    require(established <= {c for p in parts for c in p['columns']}, 'Plan dropped established dependency')
    for p in parts:
        if p['granularity'] != 'atomic':
            require(set(p['columns']) <= established, 'Compound plan introduces speculative bindings')
        require(set(p.get('scope_columns', [])) <= proposed_scope,
                'Contribution introduces speculative scope bindings')
    q = deepcopy(raw['question'])
    require(isinstance(q, dict) and {'dimension', 'question'} <= set(q)
            <= {'dimension', 'question', 'issue_id', 'options', 'multiple'}, 'Invalid focused question')
    require(q['dimension'] in DIMENSIONS and text(q['question']), 'Invalid focused question content')
    if q.get('issue_id'):
        require(q['issue_id'] in old and owners[q['issue_id']] == 'focus'
                and old[q['issue_id']]['dimension'] == q['dimension'], 'Question must belong to focus')
    q.setdefault('options', [])
    q.setdefault('multiple', q['dimension'] == 'field')
    require(isinstance(q['options'], list) and type(q['multiple']) is bool, 'Invalid menu options')
    labels = set()
    for o in q['options']:
        require(isinstance(o, dict) and 'label' in o and set(o) <= {'label', 'columns'}
                and text(o['label']) and o['label'] not in labels, 'Content options cannot carry actions')
        labels.add(o['label'])
        o.setdefault('columns', [])
        check_columns(o['columns'], context)
        if q['dimension'] != 'field':
            o['columns'] = []
    relations = deepcopy(raw.get('relations', []))
    require(isinstance(relations, list), 'Relations must be a list')
    external = set(frontier(state)) - {target}
    for r in relations:
        require(isinstance(r, dict) and set(r) == {'source', 'target', 'kind'}
                and r['source'] in aliases | external and r['target'] in aliases | external
                and r['source'] != r['target'] and r['kind'] in ('applies_to', 'depends_on')
                and bool({r['source'], r['target']} & aliases), 'Invalid staged relationship')
        r['status'] = 'unresolved'
    plan = dict(revision=state['revision'], target=target, parts=parts, question=q,
                owners=owners, relations=relations, coverage=raw['coverage'])
    # Exercise the real transaction on private state before accepting a model
    # plan. Do not maintain a second, weaker copy of commit's invariants here.
    # Defer needs no fabricated answer and validates the entire partition.
    apply(state, plan, {'action': 'defer'}, context)
    return plan


def apply(state, plan, event, context, update=None):
    """An answered focus with independent remainders MUST become a child."""
    require(plan['revision'] == state['revision'] and plan['target'] in frontier(state), 'Stale focus plan')
    require(event['action'] in ('answer', 'cancel', 'defer'), 'Unsupported focus action')
    staged = deepcopy(state)
    question = plan['question']
    asked = question.get('issue_id')
    if not asked:
        asked = add_issue(staged, plan['target'], question['dimension'], question['question'])
    owners = {**plan['owners'], asked: 'focus'}
    require(set(owners) <= set(staged['issues']), 'Focus plan contains unknown issue owner IDs')
    parts = deepcopy(plan['parts'])
    # Old field dependencies are distributed in the approved staged partition.
    # The interpreter receives focus only and cannot alter remainder content.
    old_focus = set(parts[0]['columns'])
    retired, reason = [], ''
    resolved = False
    new_issues = []
    resolutions = {}
    if event['action'] == 'answer':
        require(isinstance(update, dict) and {'meaning', 'granularity', 'resolved'} <= set(update)
                <= {'meaning', 'granularity', 'role', 'columns', 'computation', 'resolved', 'remaining_question',
                    'issues', 'issue_resolutions', 'retired_columns', 'retirement_reason',
                    'output_exclusions', 'binding_suggestions'}, 'Interpreter may edit focus content only')
        require(type(update['resolved']) is bool, 'resolved must be boolean')
        resolutions = update.get('issue_resolutions', {})
        require(isinstance(resolutions, dict), 'issue_resolutions must be an object')
        sources = [event.get('answer', {}).get('text', '')] + event.get('selected_labels', [])
        for iid, resolution in resolutions.items():
            require(iid != asked and owners.get(iid) == 'focus', 'Issue resolution must belong to this focus, excluding current issue')
            require(isinstance(resolution, dict) and {'status', 'evidence'} <= set(resolution)
                    <= {'status', 'evidence', 'remaining_question'}
                    and resolution['status'] in ('answered', 'partial', 'pending'), 'Invalid local issue resolution')
            if resolution['status'] != 'pending':
                evidence = resolution['evidence']
                require(text(evidence) and any(evidence in source for source in sources),
                        'Issue resolution needs an exact quote from the current answer or selected label')
            require(resolution['status'] != 'partial' or text(resolution.get('remaining_question')),
                    'Partial issue resolution needs a remaining question')
        raw_content = {k: update[k] for k in ('meaning', 'granularity', 'role', 'columns', 'computation') if k in update}
        suggestions = deepcopy(update.get('binding_suggestions', update.get('columns', [])))
        check_columns(suggestions, context)
        # Model bindings are suggestions. Containers carry semantics, not new
        # established field dependencies. Omission cannot retire old bindings.
        raw_content['columns'] = sorted(old_focus | set(event.get('selected_columns', [])) |
                                        (set(suggestions) if update['granularity'] == 'atomic' else set()))
        exclusions = update.get('output_exclusions', [])
        require(isinstance(exclusions, list) and all(text(x) for x in exclusions), 'Invalid output exclusions')
        raw_content['output_exclusions'] = list(dict.fromkeys(parts[0].get('output_exclusions', []) + exclusions))
        if update['granularity'] != 'atomic':
            raw_content['computation'] = deepcopy(parts[0].get('computation', {}))
        if 'columns' not in raw_content:
            raw_content['columns'] = sorted(old_focus | set(event.get('selected_columns', [])))
        raw_content['scope_columns'] = deepcopy(parts[0].get('scope_columns', []))
        raw_content.setdefault('computation', deepcopy(parts[0].get('computation', {})))
        parts[0] = content(raw_content, 'focus', context)
        # Legacy retirement proposals remain audit-only. Excluding an output
        # identifier never removes the same column's join/filter obligation.
        retired, reason = [], ''
        require((old_focus - set(retired)) | set(event.get('selected_columns', [])) <= set(parts[0]['columns']),
                'Focus lost established/selected dependency')
        resolved = update['resolved']
        require(resolved or text(update.get('remaining_question')), 'Partial answer needs remaining question')
        issues = update.get('issues', [])
        require(isinstance(issues, list), 'Focus issues must be an array')
        for q in issues:
            require(isinstance(q, dict) and set(q) == {'dimension', 'question'}
                    and text(q['question']), 'Each focus issue requires dimension and a nonempty question')
            require(q['dimension'] in DIMENSIONS,
                    f"Invalid focus issue dimension {q['dimension']!r}; allowed: {', '.join(DIMENSIONS)}")
        # The current issue is already represented by remaining_question. Models
        # often echo it in issues as well; treating that echo as a new issue
        # creates a duplicate pending obligation. Reuse the existing issue and
        # keep only genuinely additional local questions.
        if not resolved:
            remaining = update.get('remaining_question')
            issues = [q for q in issues
                      if not (isinstance(q, dict) and q.get('dimension') == question['dimension']
                              and q.get('question') == remaining)]
        new_issues = [{**q, 'owner': 'focus'} for q in issues if isinstance(q, dict)]
        require(len(new_issues) == len(issues), 'Invalid focus issue')
        if update['granularity'] == 'atomic' and ('binding_suggestions' in update or 'columns' in update) and old_focus - set(suggestions):
            new_issues.append(dict(owner='focus', dimension='field',
                                   question='Clarify the data needed for the revised result while preserving required subject and join dependencies.'))
    changes = {}
    for iid, owner in owners.items():
        changes[iid] = dict(owner=owner, status='answered' if iid == asked and resolved else 'pending',
                            question=update.get('remaining_question') if iid == asked and update and not resolved
                            else staged['issues'][iid]['question'],
                            reason='Actual public answer interpreted for this focus' if iid == asked and resolved else '')
    # Retiring a column still used by a remainder cannot remove that remainder's obligation.
    for iid, resolution in resolutions.items():
        if resolution['status'] == 'pending':
            continue
        changes[iid].update(status='answered' if resolution['status'] == 'answered' else 'pending',
                            reason=resolution['evidence'])
        if resolution['status'] == 'partial':
            changes[iid]['question'] = resolution['remaining_question']
    retired_global = sorted(set(retired) & set(state['nodes'][plan['target']]['columns']))
    raw = dict(children=parts, issue_updates=changes, new_issues=new_issues,
               relations=plan['relations'], retired_columns=retired_global, retirement_reason=reason,
               coverage=plan['coverage'])
    tx_event = dict(revision=staged['revision'], target=plan['target'], issue_id=asked,
                    answer=event.get('answer', {}), selected_columns=event.get('selected_columns', []))
    tx_event['local_answered_issues'] = [iid for iid, r in resolutions.items() if r['status'] == 'answered']
    # Contributions may introduce definite columns at planning time; record them
    # as established in the private staging state before the atomic commit.
    staged['nodes'][plan['target']]['columns'] = sorted({c for p in plan['parts'] for c in p['columns']})
    staged['nodes'][plan['target']]['scope_columns'] = sorted({c for p in plan['parts'] for c in p.get('scope_columns', [])})
    staged['nodes'][plan['target']]['computation'] = deepcopy(plan['parts'][0].get('computation', {}))
    raw['retired_columns'] = retired
    result = commit(staged, tx_event, raw, context)
    focus_id = result['audit'][-1]['mapping']['focus']
    if event['action'] == 'cancel':
        uncertain_dependents = set()
        for r in result['relations']:
            if r['status'] == 'unresolved':
                if r['kind'] == 'applies_to' and r['source'] == focus_id:
                    uncertain_dependents.add(r['target'])
                if r['kind'] == 'depends_on' and r['target'] == focus_id:
                    uncertain_dependents.add(r['source'])
        result = cancel(result, focus_id, automatic=event.get('reason') == 'no_authorized_local_plan')
        for iid in uncertain_dependents:
            if iid not in frontier(result):
                continue
            issue_id = add_issue(result, iid, 'relation', 'Clarify only this surviving contribution: '
                      + result['nodes'][iid]['meaning']
                      + '. Preserve its necessary subject association without recreating the cancelled output: '
                      + plan['parts'][0]['meaning'])
            result['issues'][issue_id]['cancelled_node_id'] = focus_id
            result['nodes'][iid]['assessed'] = False
    elif event['action'] == 'defer':
        result['nodes'][focus_id]['blocked'] = True
    result['audit'].append({'action': 'focused_commit', 'focus_id': focus_id,
                            'plan': deepcopy(plan), 'event': deepcopy(event),
                            'interpretation': deepcopy(update)})
    return validate(result)
