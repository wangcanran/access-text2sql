"""Decision identity and explicit continuations, rather than dimension closure."""
from copy import deepcopy

from clarification_deferred_deletion.baseline import module

ledger = module('experiments.decision_update.ledger')
asked_ids = module('experiments.decision_update.question_delta').asked_ids
DIMENSIONS = {'scope', 'field', 'operation', 'value'}


def contract(value):
    if (not isinstance(value, dict) or {'dimension', 'followups'} - set(value)
            or set(value) - {'dimension', 'followups', 'continue_decisions'}):
        raise ValueError('clarification requires dimension, followups and optional continue_decisions')
    if value['dimension'] not in DIMENSIONS or not isinstance(value['followups'], dict):
        raise ValueError('Invalid clarification dimension/followups')
    if set(value['followups']) - DIMENSIONS or any(not isinstance(q, str) or not q.strip()
                                                  for q in value['followups'].values()):
        raise ValueError('Followups map dimensions to nonempty business questions')
    result = deepcopy(value)
    result.setdefault('continue_decisions', [])
    if (not isinstance(result['continue_decisions'], list)
            or any(not isinstance(d, str) for d in result['continue_decisions'])
            or len(set(result['continue_decisions'])) != len(result['continue_decisions'])):
        raise ValueError('continue_decisions must contain distinct existing decision IDs')
    return result


def apply_questions(base, event, raw, mapping, replacements, aliases, dimensions):
    old = ledger.pending(base)
    by_id = {d['decision_id']: d for d in old}
    asked = asked_ids(base, event, dimensions)
    changes = raw.get('question_changes', {})
    if not isinstance(changes, dict) or set(changes) - set(by_id):
        raise ValueError('question_changes must reference current decision IDs')
    turn = event.get('turn', {})
    clarification = turn.get('scoped_permission_decision', {}).get('clarification', {})
    answer_dimension = clarification.get('dimension', event['dimension'])
    continued = set(clarification.get('continue_decisions', [])) if turn.get('selected_options') else set()
    if continued - set(by_id):
        raise ValueError('Continuation refers to an unknown prior decision')
    asked -= continued
    for did, change in changes.items():
        if by_id[did]['owner_id'] not in replacements:
            raise ValueError('Question changes need an updated owner inside the review boundary')
        if not isinstance(change, dict) or not change or set(change) - {'resolved', 'to', 'question'}:
            raise ValueError('Question changes require resolved or to/question')
        if 'resolved' in change:
            if did in continued:
                raise ValueError('Menu contract explicitly preserves decision ' + did)
            if set(change) != {'resolved'} or not isinstance(change['resolved'], str) or not change['resolved'].strip():
                raise ValueError('Resolution requires an answer-based reason')
            answer = turn.get('answer', {})
            if answer.get('action') == 'unanswerable' or not (answer.get('text', '').strip() or turn.get('selected_options')):
                raise ValueError('Cannot resolve without actual answer evidence')
            if (not answer.get('text', '').strip() and by_id[did]['dimension'] != answer_dimension
                    and did not in asked):
                raise ValueError('A menu choice cannot close a different clarification dimension')
        if 'question' in change and (not isinstance(change['question'], str) or not change['question'].strip()):
            raise ValueError('Reworded questions must be nonempty')
    rows = {n['item_id']: n for n in mapping}
    additions = {i: deepcopy(n['question_focus']) for i, n in rows.items()}
    # Existing questions are routed by stable identity below. Copying either
    # their exact text or the parent's aggregate is not a new obligation.
    for owner, questions in additions.items():
        parent = next(p for p, children in replacements.items() if owner in children)
        previous = base.get('items', {}).get(parent, {}).get('question_focus', {})
        for dim, question in list(questions.items()):
            known = [d['question'] for d in old if d['owner_id'] == parent and d['dimension'] == dim]
            asked_text = [event.get('question'), turn.get('scoped_permission_decision', {}).get('question')]
            if question in known or question == previous.get(dim) or (
                    parent == event['target'] and dim == answer_dimension and question in asked_text):
                questions.pop(dim)
    carried, audit = [], []
    for d in old:
        did, parent, dim = d['decision_id'], d['owner_id'], d['dimension']
        change = changes.get(did, {})
        if 'resolved' in change or did in asked and not change:
            for child in replacements.get(parent, []):
                if additions[child].get(dim) == d['question']:
                    additions[child].pop(dim)
            audit.append(dict(decision_id=did, effect='resolved', reason=change.get('resolved', 'Directly answered decision')))
            continue
        if parent not in replacements:
            carried.append(deepcopy(d))
            continue
        children = replacements[parent]
        targets = change.get('to')
        if targets is None:
            routed = [aliases[r['id']] for r in raw['updates'][parent] if did in r.get('pending', [])]
            matches = [i for i in children if dim in rows[i]['question_focus']]
            targets = routed or (children if len(children) == 1 else matches)
            if len(targets) != 1 and not routed:
                raise ValueError('Route pending decision ' + did + ' to surviving descendants')
        else:
            if not isinstance(targets, list) or not targets or len(targets) != len(set(targets)):
                raise ValueError('Pending question needs distinct surviving child IDs')
            targets = [aliases.get(t, t) for t in targets]
        if not set(targets) <= set(children):
            raise ValueError('A pending decision stays inside its parent descendants')
        for index, child in enumerate(targets):
            question = change.get('question', d['question'])
            carried.append({**deepcopy(d), 'decision_id': did if not index else did + '/' + child,
                            'owner_id': child, 'question': question, 'source_span': rows[child]['requirement']})
            if additions[child].get(dim) == question:
                additions[child].pop(dim)
        audit.append(dict(decision_id=did, effect='retained', owners=targets))

    # Compiler-declared conditional obligations become mandatory after selection.
    # A split must explicitly route them to one descendant of the asked parent.
    followups = clarification.get('followups', {}) if turn.get('selected_options') else {}
    routes = raw.get('followup_routes', {})
    if not isinstance(routes, dict) or set(routes) - set(followups):
        raise ValueError('followup_routes must name declared continuation dimensions')
    forced = []
    for dim, question in followups.items():
        children = replacements[event['target']]
        target = aliases.get(routes.get(dim), routes.get(dim))
        if target is None and len(children) == 1:
            target = children[0]
        if target not in children:
            raise ValueError('Route mandatory ' + dim + ' followup to one asked-parent descendant')
        if not any(d['owner_id'] == target and d['dimension'] == dim and d['question'] == question for d in carried):
            additions[target][dim] = question
            forced.append((target, dim, question))
    serial = base.get('progressive', {}).get('next_decision_id', 1)
    used = set(by_id) | {d['decision_id'] for d in carried}
    for owner, questions in additions.items():
        parent = next(p for p, children in replacements.items() if owner in children)
        for dim, question in questions.items():
            if any(d['owner_id'] == owner and d['dimension'] == dim and d['question'] == question for d in carried):
                continue
            # Only the exact answered question is stale. A new question with the
            # same dimension is not evidence that its answer is already known.
            if ((owner, dim, question) not in forced and any(
                    d['decision_id'] in asked and d['owner_id'] == parent and d['question'] == question for d in old)):
                continue
            while f'd{serial}' in used:
                serial += 1
            carried.append(dict(decision_id=f'd{serial}', owner_id=owner, dimension=dim,
                                question=question, source_span=rows[owner]['requirement']))
            audit.append(dict(decision_id=f'd{serial}', effect='introduced', owner=owner,
                              source='menu_contract' if (owner, dim, question) in forced else 'child_question'))
            used.add(f'd{serial}')
            serial += 1
    for owner, row in rows.items():
        focus = {}
        for d in carried:
            if d['owner_id'] == owner:
                focus.setdefault(d['dimension'], [])
                if d['question'] not in focus[d['dimension']]:
                    focus[d['dimension']].append(d['question'])
        row['question_focus'] = {dim: ' '.join(qs) for dim, qs in focus.items()}
        row['missing'] = list(focus)
    return carried, serial, audit
