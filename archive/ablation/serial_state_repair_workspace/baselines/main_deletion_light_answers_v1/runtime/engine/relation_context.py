"""Direct, public answer context; no copied bindings or automatic resolution."""
from copy import deepcopy


def direct_neighbors(session, target):
    current = session['view']['items'].get(target['item_id'])
    if not current or (current['span'], current['role']) != (target['span'], target['role']):
        return []
    result = []
    for edge in session['view']['relations']['edges']:
        if edge['source_id'] == target['item_id']:
            other, direction = edge['target_id'], 'outgoing'
        elif edge['target_id'] == target['item_id']:
            other, direction = edge['source_id'], 'incoming'
        else:
            continue
        neighbor = session['view']['items'][other]
        result.append({'span': neighbor['span'], 'role': neighbor['role'],
                       'relation': edge['kind'], 'direction': direction})
    return result


def affected(neighbor):
    """A context antecedent or attached instruction supplies downstream context."""
    return ((neighbor['relation'], neighbor['direction']) in
            (('context', 'incoming'), ('applies_to', 'outgoing')))


def capture_neighbors(session, target):
    snapshots = []
    for n in direct_neighbors(session, target):
        if not affected(n):
            continue
        matches = [i for i in session['view']['items'].values()
                   if (i['role'], i['span']) == (n['role'], n['span'])]
        if len(matches) != 1:
            continue
        item = matches[0]
        snapshots.append({**n, 'missing_before': list(item['missing']),
                          'candidate_columns_before': [c['column'] for c in item.get('field_candidates', [])]})
    return snapshots


def neighbor_context(turns, fragments):
    """Match quotes and roles across rounds, never reuse historical IDs.

    Rewritten or ambiguous neighbors are skipped; full conversation remains the
    source of meaning. Only accepted replies are forwarded, one hop at a time.
    """
    matched, skipped = [], []
    for index, turn in enumerate(turns):
        if turn['answer']['action'] not in ('answer', 'choose'):
            continue
        for neighbor in turn.get('neighbor_snapshots', []):
            if not affected(neighbor):
                continue
            matches = [i for i in fragments.values()
                       if (i['role'], i['span']) == (neighbor['role'], neighbor['span'])]
            if len(matches) != 1:
                skipped.append({'turn_index': index, 'neighbor_quote': neighbor['span'],
                    'neighbor_role': neighbor['role'],
                    'reason': 'quote_changed_or_absent' if not matches else 'ambiguous_quote'})
                continue
            record = {'item_id': matches[0]['item_id'], 'neighbor_quote': neighbor['span'],
                      'neighbor_role': neighbor['role'], 'turn_index': index,
                      'relation': neighbor['relation'], 'direction_from_answered_target': neighbor['direction'],
                      'answered_target': turn['target_fragment'], 'answered_role': turn['target_role'],
                      'asked_dimension': turn['asked_dimension'], 'question': deepcopy(turn['question']),
                      'answer': deepcopy(turn['answer']),
                      'selected_options': deepcopy(turn.get('selected_options', [])),
                      'selection_mode': turn.get('selection_mode')}
            matched.append(record)
    return matched, skipped


def change_audit(turns, evidence, items):
    """Report observed changes; no causal gain claim or state transition."""
    result = []
    for record in evidence:
        turn = turns[record['turn_index']]
        before = next(n for n in turn['neighbor_snapshots']
                      if (n['span'], n['role'], n['relation'], n['direction']) ==
                         (record['neighbor_quote'], record['neighbor_role'], record['relation'],
                          record['direction_from_answered_target']))
        after = items[record['item_id']]
        old, new = set(before['candidate_columns_before']), {c['column'] for c in after.get('field_candidates', [])}
        result.append({'turn_index': record['turn_index'], 'item_id': record['item_id'],
            'span': after['span'], 'role': after['role'],
            'missing_before': list(before['missing_before']), 'missing_after': list(after['missing']),
            'removed_missing': sorted(set(before['missing_before']) - set(after['missing'])),
            'added_missing': sorted(set(after['missing']) - set(before['missing_before'])),
            'candidate_columns_before': sorted(old), 'candidate_columns_after': sorted(new),
            'removed_candidates': sorted(old - new), 'added_candidates': sorted(new - old),
            'meaning': 'observed reanalysis change, not proof of answer entailment or causal graph benefit'})
    return result
