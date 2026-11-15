"""Plan block retention; legacy local replacement helpers remain for old audits."""
import math
import re
from clause_frame_experiment.relations import validate_relations


def build_inventory(frame, partition=None):
    """Bind each existing item to one unique, non-overlapping original interval."""
    pending = []; seen = set(); bound = {}
    for kind, entries in (("output", frame.outputs), ("modifier", frame.modifiers)):
        for entry in entries:
            iid, span = entry['item_id'], entry['span']
            if iid in seen: raise ValueError('duplicate item ID')
            seen.add(iid)
            if not span: raise ValueError('empty item span')
            positions = []; start = frame.original_text.find(span)
            while start >= 0:
                positions.append((start, start + len(span)))
                start = frame.original_text.find(span, start + 1)
            pending.append({'item_id': iid, 'kind': kind, 'span': span, 'positions': positions})
    ordered_ids = [x['item_id'] for x in pending]
    while pending:
        progress = False
        for item in list(pending):
            candidates = [(a,b) for a,b in item['positions'] if not any(
                a < x['end'] and x['start'] < b for x in bound.values())]
            if not candidates: raise ValueError('missing or overlapping stage-one item positions')
            if len(candidates) != 1: continue
            a,b = candidates[0]
            bound[item['item_id']] = {k:v for k,v in item.items() if k != 'positions'}
            bound[item['item_id']].update(start=a, end=b)
            pending.remove(item); progress = True
        if not progress: raise ValueError('ambiguous stage-one item positions')
    if partition is None:
        return {'items': [bound[i] for i in ordered_ids]}
    if not isinstance(partition, dict) or set(partition) != {'partitions'} or not isinstance(partition['partitions'], list):
        raise ValueError('expected partitions only')
    by_id = {}; covered_by = {}
    for row in partition['partitions']:
        if (not isinstance(row, dict) or not {'item_id', 'parts'} <= set(row)
                or set(row) - {'item_id', 'parts', 'covered_by'}):
            raise ValueError('expected item_id, parts and optional covered_by')
        iid, parts = row['item_id'], row['parts']
        if not isinstance(iid, str) or iid not in bound or iid in by_id:
            raise ValueError('unknown or repeated parent item')
        if not isinstance(parts, list) or not parts or any(not isinstance(p, str) or not p.strip() for p in parts):
            raise ValueError('parts must be nonempty text blocks')
        # Exact partition: no parent/child overlap, gaps, reordering or repetition.
        if ''.join(parts) != bound[iid]['span']:
            raise ValueError('parts must concatenate exactly to the complete parent span')
        by_id[iid] = parts
        if 'covered_by' in row:
            refs = row['covered_by']
            if (not isinstance(refs, list) or not refs or
                    any(not isinstance(ref, str) or ref not in bound or ref == iid for ref in refs)
                    or len(set(refs)) != len(refs) or len(parts) != 1):
                raise ValueError('context item needs one intact part and distinct covering item IDs')
            covered_by[iid] = refs
    if set(by_id) != set(bound):
        raise ValueError('every parent item must have exactly one partition')
    if any(ref in covered_by for refs in covered_by.values() for ref in refs):
        raise ValueError('context coverage must point directly to counted items')
    leaves = []; contexts = []
    for index, iid in enumerate(ordered_ids, 1):
        parent = bound[iid]; start = parent['start']
        if iid in covered_by:
            contexts.append({**parent, 'covered_by': covered_by[iid]})
            continue
        for number, part in enumerate(by_id[iid], 1):
            leaves.append({'item_id': f'leaf_{index}_{number}', 'parent_item_id': iid,
                           'kind': parent['kind'], 'span': part,
                           'start': start, 'end': start + len(part)})
            start += len(part)
    result = {'items': leaves, 'partition': partition}
    if contexts: result['context_items'] = contexts
    return result


def validate_inventory(frame, inventory):
    if not isinstance(inventory, dict) or inventory != build_inventory(frame, inventory.get('partition')):
        raise ValueError('inventory must match original items and their exact partition')


def plan_disclosure(frame, relations, inventory, ratios=(0, 1/3, 2/3, 1)):
    validate_inventory(frame, inventory)
    relations = validate_relations(frame, relations)
    ratios = tuple(float(r) for r in ratios)
    if not ratios or any(not math.isfinite(r) or not 0 <= r <= 1 for r in ratios) or list(ratios) != sorted(ratios):
        raise ValueError('ratios must be finite, increasing and in [0,1]')
    pending = {x['item_id'] for x in frame.outputs + frame.modifiers}
    dependents = {i: set() for i in pending}
    for edge in relations['edges']:
        dependents[edge['target_id']].add(edge['source_id'])
    rank = {}; depth = 0
    while pending:
        leaves = sorted(i for i in pending if not dependents[i] & pending)
        for i in leaves: rank[i] = depth
        pending.difference_update(leaves); depth += 1
    items = sorted(inventory['items'], key=lambda x: (rank[x.get('parent_item_id', x['item_id'])], x['start'], x['item_id']))
    ids = [x['item_id'] for x in items]; n = len(ids); levels = []
    for level, ratio in enumerate(ratios):
        keep = min(range(n+1), key=lambda k: (abs(k-ratio*n), k))
        levels.append({'level': level, 'target_ratio': ratio,
                       'actual_ratio': keep/n if n else None,
                       'planned_retention_ratio': keep/n if n else None,
                       'numerator': keep, 'denominator': n,
                       'retained_item_ids': ids[n-keep:], 'hidden_item_ids': ids[:n-keep],
                       'status': 'ok' if n else 'no_disclosable_items'})
    return {'weight_basis': 'one active leaf block; replaced parents and covered context excluded; unsegmented text has weight zero',
            'prune_order': ids, 'relations': relations, 'levels': levels,
            'distinct_states': len({tuple(x['hidden_item_ids']) for x in levels}),
            'limitation': 'Planned item retention, not measured language information. Edges order pruning, not semantic co-reference closure.'}


def render_level_prompt_payload(frame, relations, inventory, level):
    validate_inventory(frame, inventory)
    relations = validate_relations(frame, relations)
    ids = level['hidden_item_ids']
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise ValueError('hidden IDs must be a list of item IDs')
    known = {x['item_id'] for x in inventory['items']}
    if len(set(ids)) != len(ids) or not set(ids) <= known:
        raise ValueError('duplicate or unknown hidden item')
    if 'retained_item_ids' in level:
        retained = level['retained_item_ids']
        if (not isinstance(retained, list) or any(not isinstance(i, str) for i in retained)
                or len(set(retained)) != len(retained) or set(retained) != known - set(ids)):
            raise ValueError('retained IDs must be the exact complement of hidden IDs')
    return {'frame': frame.to_json(), 'relations': relations,
            'replacement_items': inventory['items'],
            'context_items': inventory.get('context_items', []),
            'hidden_item_ids': ids,
            'retained_item_ids': [x['item_id'] for x in inventory['items'] if x['item_id'] not in ids]}


_GRAMMAR_WORDS = frozenset('a an the of for to in on at by from with as who whom whose which that is are was were be been being do does did has have had'.split())


def _check_grammar_text(value):
    # A lexical bound, not proof of semantic equivalence (prepositions carry meaning).
    if not isinstance(value, str): raise ValueError('grammar text must be a string')
    tokens = re.findall(r'[A-Za-z]+|[^\s]', value)
    if len(tokens) > 6 or sum(t.isalpha() for t in tokens) > 3:
        raise ValueError('boundary edit is too large')
    if any(t.lower() not in _GRAMMAR_WORDS and t not in ',.;:?!' for t in tokens):
        raise ValueError('boundary edit must contain only supported grammar words or punctuation')


def materialize_level(frame, inventory, level, rendered):
    payload = render_level_prompt_payload(frame, {'edges': []}, inventory, level)
    if not isinstance(rendered, dict) or set(rendered) != {'replacements'} or not isinstance(rendered['replacements'], list):
        raise ValueError('expected replacements only')
    replacements = {}
    for row in rendered['replacements']:
        if not isinstance(row, dict) or not {'item_id', 'replacement'} <= set(row) or set(row) - {'item_id', 'replacement', 'boundary_edits'}:
            raise ValueError('expected item_id, replacement and optional boundary_edits')
        iid, value = row['item_id'], row['replacement']
        if not isinstance(iid, str) or iid in replacements or not isinstance(value, str) or not value.strip():
            raise ValueError('duplicate item or invalid replacement')
        replacements[iid] = row
    if set(replacements) != set(payload['hidden_item_ids']):
        raise ValueError('must replace exactly the selected items once each')
    text = frame.original_text
    edits = []
    for item in inventory['items']:
        iid = item['item_id']
        if iid not in replacements: continue
        row = replacements[iid]
        value = row['replacement']
        if 'partition' in inventory:
            span = item['span']
            leading = span[:len(span)-len(span.lstrip())]
            trailing = span[len(span.rstrip()):]
            value = leading + value.strip() + trailing
        if value == item['span']: raise ValueError('unchanged replacement')
        edits.append((item['start'], item['end'], value))
        boundaries = row.get('boundary_edits', [])
        if not isinstance(boundaries, list) or len(boundaries) > 2:
            raise ValueError('expected at most one edit on each boundary')
        sides = set()
        for edit in boundaries:
            if not isinstance(edit, dict) or set(edit) != {'side', 'original', 'replacement'}:
                raise ValueError('invalid boundary edit fields')
            side, before, after = edit['side'], edit['original'], edit['replacement']
            if not isinstance(side, str) or side not in ('left', 'right') or side in sides:
                raise ValueError('invalid or duplicate boundary side')
            sides.add(side)
            _check_grammar_text(before); _check_grammar_text(after)
            if not before or before == after:
                raise ValueError('boundary edit must replace nonempty original text')
            start, end = ((item['start'] - len(before), item['start']) if side == 'left'
                          else (item['end'], item['end'] + len(before)))
            if start < 0 or end > len(text) or text[start:end] != before:
                raise ValueError('boundary original must exactly match adjacent text')
            if ((start > 0 and text[start-1].isalnum() and text[start].isalnum()) or
                    (end < len(text) and text[end-1].isalnum() and text[end].isalnum())):
                raise ValueError('boundary edit must not cut a word')
            if any(start < other['end'] and other['start'] < end for other in inventory['items']):
                raise ValueError('boundary edit overlaps an item; use its replacement instead')
            edits.append((start, end, after))
    edits.sort(key=lambda e: (e[0], e[1]))
    if any(a[1] > b[0] for a,b in zip(edits, edits[1:])):
        raise ValueError('overlapping replacement or boundary edits')
    for start, end, value in reversed(edits):
        text = text[:start] + value + text[end:]
    return text
