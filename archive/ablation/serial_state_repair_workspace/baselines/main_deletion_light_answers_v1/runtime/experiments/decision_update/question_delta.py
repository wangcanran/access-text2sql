"""Answer updates preserve pending decisions unless the event changes them.

Graph reachability grants review access, never permission to silently close a
question. The model explains semantic effects in the existing transaction call;
code owns identities, default retention and descendant routing.
"""
from copy import deepcopy

from .ledger import pending


def asked_ids(base, event, dimensions):
    if event['kind'] != 'answer' or event.get('turn', {}).get('answer', {}).get('action') == 'unanswerable':
        return set()
    turn = event.get('turn', {})
    rows = [d for d in pending(base) if d['owner_id'] == event['target'] and d['dimension'] in dimensions]
    ids = turn.get('decision_ids')
    if ids is None:
        q = next((q for q in base.get('questions', []) if q['key'] == turn.get('decision_key')), {})
        ids = q.get('decision_ids')
    if ids is not None:
        return {d['decision_id'] for d in rows if d['decision_id'] in ids}
    # Older saved events have no decision IDs. A dimension identifies a decision
    # only when it is unique; never consume several purposes by type alone.
    return {d['decision_id'] for d in rows
            if sum(r['dimension'] == d['dimension'] for r in rows) == 1}


def apply_questions(base, event, raw, mapping, replacements, aliases, dimensions):
    old = pending(base)
    by_id = {d['decision_id']: d for d in old}
    changes = raw.get('question_changes', {})
    if not isinstance(changes, dict) or set(changes) - set(by_id):
        raise ValueError('question_changes must reference current decision IDs')
    asked = asked_ids(base, event, dimensions)
    # A completed answer owns the decision identity, not the wording produced
    # by the updater.  A child hint with the same dimension cannot reopen that
    # decision under a paraphrase (for example, "subset" -> "coverage").
    asked_dimensions = {by_id[did]['dimension'] for did in asked if did in by_id}
    rows = {n['item_id']: n for n in mapping}
    additions = {i: deepcopy(n['question_focus']) for i, n in rows.items()}
    carried, audit = [], []
    for did, change in changes.items():
        if by_id[did]['owner_id'] not in replacements:
            raise ValueError('Question changes need an updated owner inside the review boundary')
        if not isinstance(change, dict) or not change or set(change) - {'resolved', 'to', 'question'}:
            raise ValueError('Question change needs resolved or to/question')
        if 'resolved' in change:
            if set(change) != {'resolved'} or not isinstance(change['resolved'], str) or not change['resolved'].strip():
                raise ValueError('Resolving a related question needs an answer-based reason')
            turn = event.get('turn', {})
            answer = turn.get('answer', {})
            if answer.get('action') == 'unanswerable' or not (answer.get('text', '').strip() or turn.get('selected_options')):
                raise ValueError('Related questions cannot resolve without current answer evidence')
        if 'question' in change and (not isinstance(change['question'], str) or not change['question'].strip()):
            raise ValueError('Reworded questions must be nonempty')
    for decision in old:
        did, parent, dim = (decision[k] for k in ('decision_id', 'owner_id', 'dimension'))
        change = changes.get(did, {})
        if 'resolved' in change or did in asked and not change:
            for target in replacements.get(parent, []):
                if additions[target].get(dim) == decision['question']:
                    additions[target].pop(dim)
            audit.append({'decision_id': did, 'effect': 'resolved',
                          'reason': change.get('resolved', 'Directly answered decision')})
            continue
        if parent not in replacements:
            carried.append(deepcopy(decision))
            continue
        children = replacements[parent]
        targets = change.get('to')
        if targets is not None:
            if (not isinstance(targets, list) or not targets or any(not isinstance(t, str) for t in targets)
                    or len(set(targets)) != len(targets)):
                raise ValueError('Pending question needs distinct surviving child IDs')
            targets = [aliases.get(t, t) for t in targets]
        else:
            routed = [aliases[row['id']] for row in raw['updates'][parent] if did in row.get('pending', [])]
            matches = [i for i in children if dim in additions[i]]
            targets = routed or (children if len(children) == 1 else matches)
            if len(targets) != 1 and not routed:
                raise ValueError('Route pending decision ' + did + ' to its surviving children with question_changes.to')
        if any(t not in children for t in targets):
            raise ValueError('A pending decision must stay in its parent descendants')
        for index, target in enumerate(targets):
            question = change.get('question', decision['question'])
            carried.append({**deepcopy(decision), 'decision_id': did if not index else did + '/' + target,
                            'owner_id': target, 'question': question, 'source_span': rows[target]['requirement']})
            if additions[target].get(dim) == question:
                additions[target].pop(dim)
        audit.append({'decision_id': did, 'effect': 'retained', 'owners': targets})
    serial = base.get('progressive', {}).get('next_decision_id', 1)
    used = set(by_id)
    for owner, questions in additions.items():
        for dim, question in questions.items():
            if dim in asked_dimensions:
                # The answer closed this dimension for this local event. Any
                # new hint of the same type is normally a stale re-description,
                # not a new user requirement. An explicitly marked independent
                # or additional purpose is the narrow exception; this keeps
                # legacy compound-node behavior without accepting paraphrased
                # duplicates such as "subset" -> "coverage".
                marker = question.casefold()
                explicit_new = any(token in marker for token in
                                   ('independent', 'additional', 'another', 'separate'))
                if not explicit_new:
                    continue
            # Child hints cannot mint a second identity (or change the type) of
            # an existing question. This also covers a just-resolved decision.
            # Compare only identical question text within its parent lineage;
            # do not guess whether paraphrases have equivalent meaning.
            parent = next(p for p, children in replacements.items() if owner in children)
            known = [d['question'] for d in old if d['owner_id'] == parent]
            known.extend(d['question'] for d in carried if d['owner_id'] == owner)
            if question in known:
                continue
            while f'd{serial}' in used:
                serial += 1
            carried.append({'decision_id': f'd{serial}', 'owner_id': owner, 'dimension': dim,
                            'question': question, 'source_span': rows[owner]['requirement']})
            used.add(f'd{serial}')
            serial += 1
    for owner, row in rows.items():
        questions = {}
        for d in carried:
            if d['owner_id'] != owner:
                continue
            questions.setdefault(d['dimension'], [])
            if d['question'] not in questions[d['dimension']]:
                questions[d['dimension']].append(d['question'])
        row['question_focus'] = {dim: ' '.join(qs) for dim, qs in questions.items()}
        row['missing'] = list(questions)
    return carried, serial, audit
