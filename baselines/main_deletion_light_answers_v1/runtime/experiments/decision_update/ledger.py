"""Decision identity survives local fragment replacement, without storing intent twice."""
from copy import deepcopy


def pending(view):
    info = view.get('progressive', {})
    if 'decision_ledger' in info:
        return deepcopy(info['decision_ledger'])
    return discover(view['items'], info.get('source_open_choices', {}), [], 1)[0]


def discover(items, choices, existing, next_id):
    rows = deepcopy(existing)
    covered = {(r['owner_id'], r['dimension']) for r in existing}
    for owner, item in items.items():
        entries = choices.get(owner, []) + [
            {'dimension': dim, 'span': item['span'], 'question': item['question_focus'][dim]}
            for dim in item['missing']
            if not any(e['dimension'] == dim for e in choices.get(owner, []))]
        for entry in entries:
            if (owner, entry['dimension']) in covered:
                continue
            if any(r['owner_id'] == owner and r['dimension'] == entry['dimension']
                   and (r['question'] == entry['question'] or r['source_span'] == entry['span']) for r in rows):
                continue
            rows.append({'decision_id': f'd{next_id}', 'owner_id': owner,
                         'dimension': entry['dimension'], 'question': entry['question'],
                         'source_span': entry['span']})
            next_id += 1
    return rows, next_id


def answer_witnesses(event):
    if not event or event['kind'] != 'answer':
        return []
    turn = event['turn']
    return [s for s in [turn.get('answer', {}).get('text', '')] +
            [o['text'] for o in turn.get('selected_options', [])] if s]


def apply_effects(decisions, effects, event, children, editable):
    """Validate total accounting, then return surviving records and audit effects.

    children maps temporary piece IDs to (new stable ID, previous parent ID).
    Coverage/witnesses are mechanical; semantic removal within a revised compound
    parent is still a model judgment, recorded with its reason.
    """
    active = {r['decision_id']: r for r in decisions if r['owner_id'] in editable}
    if not isinstance(effects, list) or any(not isinstance(e, dict) for e in effects):
        raise ValueError('decision_effects must be an array')
    ids = [e.get('decision_id') for e in effects]
    if len(ids) != len(active) or set(ids) != set(active):
        raise ValueError('Account for every pending editable decision exactly once: ' + ', '.join(active))
    surviving = [deepcopy(r) for r in decisions if r['owner_id'] not in editable]
    for effect in effects:
        if set(effect) != {'decision_id', 'action', 'piece_ids', 'witness', 'reason'}:
            raise ValueError('Decision effects need decision_id, action, piece_ids, witness, reason')
        row = active[effect['decision_id']]
        if not isinstance(effect['reason'], str) or not effect['reason'].strip():
            raise ValueError('Decision effect needs a reason')
        targets = effect['piece_ids']
        if (not isinstance(targets, list) or any(not isinstance(t, str) for t in targets)
                or len(set(targets)) != len(targets) or any(t not in children for t in targets)):
            raise ValueError('Unknown or duplicate decision piece IDs')
        if not isinstance(effect['witness'], str):
            raise ValueError('Decision witness must be a string')
        action = effect['action']
        if action == 'keep':
            if not targets or effect['witness']:
                raise ValueError('Kept decisions need current pieces and empty witness')
            for index, target in enumerate(targets):
                new_id, parent = children[target]
                if parent != row['owner_id']:
                    raise ValueError('A decision must stay in its own parent descendants')
                carried = deepcopy(row)
                carried['owner_id'] = new_id
                # Stable ancestry, with distinct IDs if a decision branches.
                if index:
                    carried['decision_id'] += '/' + target
                surviving.append(carried)
        elif action == 'answered':
            turn = event.get('turn', {}) if event else {}
            slots = set(turn.get('requested_slots', []))
            asked = row['dimension'] == turn.get('asked_dimension') or (
                row['owner_id'] + '/' + row['dimension'] in slots)
            if (targets or not asked or row['owner_id'] != turn.get('target_id')
                    or effect['witness'] not in answer_witnesses(event)):
                raise ValueError('Only the actual asked decision can be answered, with an exact current answer/selected-label witness')
        elif action == 'removed':
            receipt = event.get('receipt', {}) if event and event['kind'] == 'removal' else {}
            explicit = receipt.get('receipt', {})
            owners = set(explicit.get('drop_ids', []) + explicit.get('revise_ids', []))
            witness = receipt.get('scoped_permission_decision', {}).get('quote') or receipt.get('target_fragment')
            if targets or row['owner_id'] not in owners or not witness or effect['witness'] != witness:
                raise ValueError('Removal needs an explicitly dropped/revised owner and the exact removal quote')
            if row['source_span'] not in witness and witness not in row['source_span']:
                raise ValueError('Removal quote does not cover this pending contribution')
        else:
            raise ValueError('Decision action must be keep, answered or removed')
    return surviving


def force_pending(mapping, decisions):
    for item in mapping['items']:
        for row in decisions:
            if row['owner_id'] != item['item_id']:
                continue
            dim = row['dimension']
            if dim not in item['missing']:
                item['missing'].append(dim)
            existing = item['question_focus'].get(dim, '')
            if row['question'] not in existing:
                item['question_focus'][dim] = (existing + ' ' + row['question']).strip()
    return mapping
