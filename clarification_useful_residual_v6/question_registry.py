"""Code-owned registration and publication checks for every question route."""
from copy import deepcopy

DIMENSIONS = frozenset({'scope', 'field', 'permission', 'operation', 'value'})


def question_dimensions(question):
    dims = set(question.get('requested_dimensions', []))
    dims.add(question['dimension'])
    dims.update(s.rsplit('/', 1)[-1] for s in question.get('requested_slots', []))
    if not dims <= DIMENSIONS:
        raise ValueError('Unknown question dimension')
    owner = question['item']['item_id']
    if any(s.rsplit('/', 1)[0] != owner for s in question.get('requested_slots', [])):
        raise ValueError('Question slots must belong to its owner')
    return dims


def bind_question(question, rows):
    """Never accept a partial, stale, duplicate or cross-owner identity list."""
    owner, dims = question['item']['item_id'], question_dimensions(question)
    eligible = {r['decision_id']: r for r in rows
                if r['owner_id'] == owner and r['dimension'] in dims}
    ids = question.get('decision_ids')
    if ids:
        if (not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
                or len(set(ids)) != len(ids) or not set(ids) <= eligible.keys()
                or {eligible[i]['dimension'] for i in ids} != dims):
            raise ValueError('Question IDs must cover exactly its current owner and purposes')
    else:
        if ids is not None and not isinstance(ids, list):
            raise ValueError('Question identities must be a list')
        ids = []
        for dim in sorted(dims):
            candidates = [i for i, r in eligible.items() if r['dimension'] == dim]
            if len(candidates) != 1:
                raise ValueError('Question has no unique registered decision')
            ids.extend(candidates)
    question['decision_ids'] = list(ids)
    return ids


def register_questions(session):
    """Run after queue construction, before any scheduler/menu can consume it.

    Only initial analysis may introduce missing identities here. Answer updates
    carry their own authoritative ledger; their hints cannot reopen decisions.
    """
    view = session.get('view')
    if not view:
        return session
    info = view.setdefault('progressive', {})
    if 'decision_ledger' not in info:
        raise ValueError('Question publication requires a canonical decision ledger')
    rows = deepcopy(info['decision_ledger'])
    ids = [r['decision_id'] for r in rows]
    if len(set(ids)) != len(ids) or any(r['owner_id'] not in view['items'] for r in rows):
        raise ValueError('Decision IDs must be unique and owned by active nodes')
    counter = max([info.get('next_decision_id', 1)] +
                  [int(i[1:]) + 1 for i in ids if i.startswith('d') and i[1:].isdigit()])
    for q in view.get('questions', []):
        owner = q['item']['item_id']
        if owner not in view['items']:
            raise ValueError('Question owner is not active')
        for dim in sorted(question_dimensions(q)):
            if any(r['owner_id'] == owner and r['dimension'] == dim for r in rows):
                continue
            if session.get('turns'):
                raise ValueError('Answer updates cannot mint unregistered question purposes')
            rows.append({'decision_id': f'd{counter}', 'owner_id': owner,
                         'dimension': dim, 'question': q['item'].get('question_focus', {}).get(dim, q['focus']),
                         'source_span': q['item']['span']})
            counter += 1
        bind_question(q, rows)
    info['decision_ledger'], info['next_decision_id'] = rows, counter
    view['segmentation']['inventory']['progressive'] = deepcopy(info)
    return session
