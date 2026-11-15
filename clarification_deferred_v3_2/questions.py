"""Valid answers settle asked identities; other obligations survive by identity."""
from copy import deepcopy

from clarification_deferred_deletion.baseline import module

ledger = module('experiments.decision_update.ledger')
legacy_asked_ids = module('experiments.decision_update.question_delta').asked_ids
DIMENSIONS = {'scope', 'field', 'operation', 'value'}
POLICY = 'valid_answer_closes_current_question'


def asked_ids(base, event, dimensions):
    """Explicit protocol identities are authoritative, never partially accepted."""
    turn = event.get('turn', {})
    ids = turn.get('decision_ids')
    if ids is None:
        q = next((q for q in base.get('questions', []) if q['key'] == turn.get('decision_key')), {})
        ids = q.get('decision_ids')
    if ids is not None:
        allowed = {d['decision_id'] for d in ledger.pending(base)
                   if d['owner_id'] == event['target'] and d['dimension'] in dimensions}
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
                or len(ids) != len(set(ids)) or not set(ids) <= allowed):
            raise ValueError('Asked decision IDs must be distinct current IDs for this owner and purpose')
        return set(ids)
    return legacy_asked_ids(base, event, dimensions)


def contract(value):
    if not isinstance(value, dict) or set(value) != {'dimension'} or value['dimension'] not in DIMENSIONS:
        raise ValueError('clarification must contain exactly dimension: scope|field|operation|value')
    return deepcopy(value)


def apply_questions(base, event, raw, mapping, replacements, aliases, dimensions):
    """Route old independent questions; answer updates never mint questions."""
    old = ledger.pending(base)
    by_id = {d['decision_id']: d for d in old}
    if len(by_id) != len(old):
        raise ValueError('Pending decision identities must be unique')
    turn = event.get('turn', {})
    answer = turn.get('answer', {})
    valid = (answer.get('action') == 'answer' and bool(answer.get('text', '').strip())
             or answer.get('action') == 'choose' and bool(turn.get('selected_options')))
    asked = asked_ids(base, event, dimensions) if valid else set()
    extra_settled = raw.get('settled_decisions', [])
    if (not isinstance(extra_settled, list) or any(not isinstance(i, str) for i in extra_settled)
            or len(extra_settled) != len(set(extra_settled))):
        raise ValueError('settled_decisions must be a distinct decision ID list')
    if valid and not asked:
        raise ValueError('An effective answer must identify the actual pending question IDs')
    changes = raw.get('question_changes', {})
    if not isinstance(changes, dict) or set(changes) - set(by_id):
        raise ValueError('question_changes must reference current decision IDs')
    if not set(extra_settled) <= set(by_id):
        raise ValueError('settled_decisions must reference current decision IDs')
    if any(by_id[i]['owner_id'] != event.get('target') for i in extra_settled):
        raise ValueError('settled_decisions must stay within the answered owner')
    if valid:
        # Dense answers may explicitly settle independent decisions in the
        # same compound node. Coverage review validates the answer evidence.
        asked |= set(extra_settled)
    for change in changes.values():
        if not isinstance(change, dict) or set(change) - {'resolved', 'to', 'question'}:
            raise ValueError('Question changes may only route existing decisions')
    rows = {n['item_id']: n for n in mapping}
    hints = {iid: deepcopy(n['question_focus']) for iid, n in rows.items()}
    carried, audit = [], []
    for d in old:
        did, parent, dim = d['decision_id'], d['owner_id'], d['dimension']
        if did in asked:
            # Even an explicit model request to retain this ID cannot reopen it.
            audit.append(dict(decision_id=did, effect='resolved', reason=POLICY,
                              dimension=dim, owner=parent, answer=deepcopy(answer)))
            continue
        if parent not in replacements:
            carried.append(deepcopy(d))
            continue
        children = replacements[parent]
        change = changes.get(did, {})
        targets = change.get('to')
        if targets is None:
            routed = [aliases[r['id']] for r in raw['updates'][parent] if did in r.get('pending', [])]
            exact = [i for i in children if hints[i].get(dim) == d['question']]
            same_dimension = [i for i in children if dim in hints[i]]
            targets = routed or (children if len(children) == 1 else exact or same_dimension)
            if len(targets) != 1 and not routed:
                raise ValueError('Route independent pending decision ' + did + ' to surviving descendants')
        else:
            if (not isinstance(targets, list) or not targets or any(not isinstance(t, str) for t in targets)
                    or len(targets) != len(set(targets))):
                raise ValueError('Pending question needs distinct surviving child IDs')
            targets = [aliases.get(t, t) for t in targets]
        if len(set(targets)) != len(targets):
            raise ValueError('Pending targets must remain distinct after alias resolution')
        if not set(targets) <= set(children):
            raise ValueError('A pending decision must stay inside its parent descendants')
        for index, child in enumerate(targets):
            carried.append({**deepcopy(d), 'decision_id': did if not index else did + '/' + child,
                            'owner_id': child, 'source_span': rows[child]['requirement']})
        audit.append(dict(decision_id=did, effect='retained', owners=targets))
    for owner, row in rows.items():
        if hints[owner]:
            audit.append(dict(effect='question_hints_not_authoritative', owner=owner, hints=hints[owner]))
        focus = {}
        for d in carried:
            if d['owner_id'] == owner:
                focus.setdefault(d['dimension'], [])
                if d['question'] not in focus[d['dimension']]:
                    focus[d['dimension']].append(d['question'])
        row['question_focus'] = {dim: ' '.join(qs) for dim, qs in focus.items()}
        row['missing'] = list(focus)
    return carried, base.get('progressive', {}).get('next_decision_id', 1), audit
