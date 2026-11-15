"""Reproducible masking plans and source-grounded audit contracts."""
import math
import random
import re
from request_disclosure_experiment.disclosure import build_inventory


_SHARED_OPERATOR = re.compile(r'^(?:(?:the|a)\s+)?(?:(?:total|overall)\s+)?(?:number|count|sum|average)\s+of\s+', re.I)
_LIST_SEPARATOR = re.compile(r'^\s*,\s*(?:(?:and|or)\s+)?$|^\s+(?:and|or)\s+$', re.I)
_BARE_INTERROGATIVE = re.compile(r'^(?:which|what)\s+(.+)$', re.I)
_QUERY_INTERROGATIVE = re.compile(r'\b(?:what|which|how|who|where|when|why)\b', re.I)


def source_span_diagnostics(frame):
    """Identify unambiguous source-position conflicts for model contract repair."""
    positions, issues = [], []
    for item in frame.outputs + frame.modifiers:
        span = item['span']
        matches = [m.start() for m in re.finditer(re.escape(span),frame.original_text)] if span else []
        if not matches:
            issues.append({'item_id':item['item_id'],'problem':'not an exact source quote'})
        elif len(matches) == 1:
            positions.append((matches[0],matches[0]+len(span),item['item_id']))
    for index,(start,end,iid) in enumerate(positions):
        for other_start,other_end,other_id in positions[index+1:]:
            if max(start,other_start) < min(end,other_end):
                issues.append({'item_ids':[iid,other_id],'problem':'overlapping source positions',
                               'shared_text':frame.original_text[max(start,other_start):min(end,other_end)]})
    return issues


def requires_clause_rewrite(frame, directions):
    """A fixed copula cannot safely join two independently replaced clause parts."""
    hidden = {row['item_id'] for row in directions['directions'] if row['action'] == 'hide'}
    items = build_inventory(frame)['items']
    outputs = [item for item in items if item['kind'] == 'output'
               and item['item_id'] in hidden
               and re.match(r'^how many\b', item['span'].strip(), re.I)]
    modifiers = [item for item in items if item['kind'] == 'modifier'
                 and item['item_id'] in hidden]
    return any(modifier['start'] > output['end'] and re.fullmatch(
        r'\s+(?:is|are|was|were)\s+',
        frame.original_text[output['end']:modifier['start']], re.I)
        for output in outputs for modifier in modifiers)


def normalize_shared_operators(frame, obligations):
    """Leave an inherited list operator outside the first enumerated operand."""
    inventory = build_inventory(frame)
    positions = {item['item_id']: item for item in inventory['items']}
    requirements = {row['item_id']:row['requirement'].lower()
                    for row in obligations['obligations']}
    outputs = list(frame.outputs)
    normalized = []
    changes = []
    for index, item in enumerate(outputs):
        span = item['span']
        match = _SHARED_OPERATOR.match(span)
        if match and index + 1 < len(outputs):
            current = positions[item['item_id']]
            following = positions[outputs[index + 1]['item_id']]
            between = frame.original_text[current['end']:following['start']]
            operation = match.group().lower()
            shared_count = bool(re.search(r'\b(?:number|count)\s+of\b', operation)
                                and re.search(r'\b(?:number|count)\s+of\b', requirements[following['item_id']]))
            shared_other = any(word in operation and word in requirements[following['item_id']]
                               for word in ('sum of','average of'))
            if _LIST_SEPARATOR.fullmatch(between) and (shared_count or shared_other):
                span = span[match.end():]
                changes.append({'item_id':item['item_id'], 'old_span':item['span'],
                                'new_span':span, 'shared_operator':match.group().strip()})
        normalized.append({**item, 'span':span})
    if not changes:
        return frame, changes
    from clause_frame_experiment.model import QueryFrame
    updated = QueryFrame(original_text=frame.original_text, outputs=tuple(normalized),
                         modifiers=frame.modifiers)
    build_inventory(updated)
    return updated, changes


def apply_roles(frame, raw):
    if not isinstance(raw, dict) or set(raw) != {'roles'} or not isinstance(raw['roles'], list):
        raise ValueError('expected roles array')
    inventory = build_inventory(frame)
    items = {item['item_id']:item for item in inventory['items']}
    roles = {}
    for row in raw['roles']:
        if (not isinstance(row, dict) or set(row) != {'item_id','role'}
                or row['item_id'] not in items or row['item_id'] in roles
                or row['role'] not in ('output','modifier')):
            raise ValueError('invalid role assignment')
        roles[row['item_id']] = row['role']
    if set(roles) != set(items):
        raise ValueError('roles must cover every source item')
    numbered = {'output':0,'modifier':0}
    buckets = {'output':[],'modifier':[]}
    changes = []
    for item in sorted(items.values(),key=lambda x:x['start']):
        role = roles[item['item_id']]
        numbered[role] += 1
        new_id = ('o' if role == 'output' else 'm') + str(numbered[role])
        buckets[role].append({'item_id':new_id,'span':item['span']})
        if role != item['kind'] or new_id != item['item_id']:
            changes.append({'old_id':item['item_id'],'new_id':new_id,
                            'old_role':item['kind'],'new_role':role})
    from clause_frame_experiment.model import QueryFrame
    updated = QueryFrame(original_text=frame.original_text,
                         outputs=tuple(buckets['output']),
                         modifiers=tuple(buckets['modifier']))
    build_inventory(updated)
    return updated, changes


def counted_items(frame, inventory):
    ids = {i['item_id'] for i in frame.outputs + frame.modifiers}
    spans = {i['item_id']:i['span'] for i in frame.outputs + frame.modifiers}
    output_ids = {i['item_id'] for i in frame.outputs}
    if not isinstance(inventory, dict) or set(inventory) != {'context_items'} or not isinstance(inventory['context_items'], list):
        raise ValueError('expected context_items array')
    contexts = set()
    for row in inventory['context_items']:
        if (not isinstance(row, dict) or set(row) != {'item_id','source_span','covered_by','reason'}
                or not isinstance(row['item_id'], str) or row['item_id'] not in ids
                or row['item_id'] not in output_ids or row['item_id'] in contexts or not isinstance(row['reason'], str)
                or not row['reason'].strip() or not isinstance(row['covered_by'], list)
                or not row['covered_by'] or any(not isinstance(i,str) for i in row['covered_by'])
                or len(set(row['covered_by'])) != len(row['covered_by'])
                or not set(row['covered_by']) <= output_ids or row['item_id'] in row['covered_by']):
            raise ValueError('invalid context coverage')
        if row['source_span'] != spans[row['item_id']]:
            raise ValueError('excluded source_span must exactly match its supplied item_id')
        contexts.add(row['item_id'])
    if any(set(row['covered_by']) & contexts for row in inventory['context_items']):
        raise ValueError('context coverage must reference counted items directly')
    return [i for i in frame.outputs + frame.modifiers if i['item_id'] not in contexts]


def plan_levels(frame, ratios, seed=42, inventory=None):
    build_inventory(frame)
    ratios = list(ratios)
    if (not ratios or any(not math.isfinite(r) or not 0 <= r <= 1 for r in ratios)
            or ratios != sorted(ratios)):
        raise ValueError('ratios must be finite, nondecreasing values in [0,1]')
    items = counted_items(frame, inventory or {'context_items':[]})
    if not items:
        raise ValueError('no source specifications')
    order = [item['item_id'] for item in items]
    random.Random(seed).shuffle(order)
    levels = []
    for index, ratio in enumerate(ratios):
        retained = min(range(len(items) + 1), key=lambda n: (abs(n - ratio * len(items)), n))
        hidden = set(order[:len(items) - retained])
        levels.append({'level': index, 'target_retention_ratio': ratio,
                       'planned_retention_ratio': retained / len(items) if items else None,
                       'denominator': len(items), 'numerator': retained,
                       'selected_ids': [i['item_id'] for i in items if i['item_id'] in hidden],
                       'retained_ids': [i['item_id'] for i in items if i['item_id'] not in hidden]})
    return {'seed': seed, 'prune_order': order, 'levels': levels,
            'metric': 'Retained counted source fragments / counted source fragments; redundant context excluded; not measured semantic information.'}


def source_ledger(frame, selected):
    items = {item['item_id']: item for item in frame.outputs + frame.modifiers}
    if len(selected) != len(set(selected)) or not set(selected) <= set(items):
        raise ValueError('invalid selected IDs')
    return [{'item_id': iid, 'source_specification': items[iid]['span']} for iid in selected]


def protected_ledger(frame, selected):
    source_ledger(frame, selected)
    return [{'item_id': item['item_id'], 'span': item['span']}
            for item in frame.outputs + frame.modifiers
            if item['item_id'] not in selected]


def protected_text_issues(text, protected):
    """Verify wording preservation, not attachment or semantic equivalence."""
    issues = []
    required = {}
    for item in protected:
        required.setdefault(item['span'], []).append(item['item_id'])
    for span, ids in required.items():
        if text.count(span) < len(ids):
            issues.append('Protected source wording missing: ' + ', '.join(ids))
    return issues


def apply_replacements(frame, directions, raw):
    inventory = build_inventory(frame)
    expected = {d['item_id'] for d in directions['directions'] if d['action'] == 'hide'}
    if not isinstance(raw, dict) or set(raw) != {'replacements'} or not isinstance(raw['replacements'], list):
        raise ValueError('expected replacements array')
    replacements = {}
    for row in raw['replacements']:
        if (not isinstance(row, dict) or set(row) != {'item_id', 'replacement'}
                or not isinstance(row['item_id'], str) or row['item_id'] not in expected
                or row['item_id'] in replacements or not isinstance(row['replacement'], str)
                or not row['replacement'].strip()):
            raise ValueError('invalid replacement entry')
        replacements[row['item_id']] = row['replacement']
    if set(replacements) != expected:
        raise ValueError('replacements must cover exactly hide IDs')
    edits = [{**item, 'replacement': replacements[item['item_id']]}
             for item in inventory['items'] if item['item_id'] in replacements]
    text = frame.original_text
    for item in sorted(edits, key=lambda x: x['start'], reverse=True):
        text = text[:item['start']] + item['replacement'] + text[item['end']:]
    return {'rewritten_request': text, 'edits': edits}


def replacement_semantic_issues(edits, rewritten_request=''):
    """Catch edits that only rephrase a bare interrogative output."""
    issues = []
    for edit in edits:
        if edit['kind'] != 'output':
            continue
        source = _BARE_INTERROGATIVE.fullmatch(edit['span'].strip())
        replacement = _BARE_INTERROGATIVE.fullmatch(edit['replacement'].strip())
        if source and replacement:
            original_head = source.group(1).lower().split()
            new_head = replacement.group(1).lower().split()
            if original_head == new_head or (len(new_head) > len(original_head)
                                              and new_head[-len(original_head):] == original_head):
                issues.append('Bare interrogative still requests the same output class: '
                              + edit['item_id'])
    if (rewritten_request.endswith('?')
            and any(edit['kind'] == 'output' and
                    re.match(r'^(?:how|which|what|who)\b', edit['span'].strip(), re.I)
                    for edit in edits)
            and not _QUERY_INTERROGATIVE.search(rewritten_request)):
        issues.append('Wh-question lost its interrogative request for data')
    return issues


def validate_review(raw, directions, retained_ids):
    if not isinstance(raw, dict) or set(raw) != {'checks', 'issues'}:
        raise ValueError('expected checks and issues')
    if not isinstance(raw['checks'], list) or not isinstance(raw['issues'], list):
        raise ValueError('review arrays required')
    expected = {row['item_id']: ('hidden' if row['action'] == 'hide' else 'preserved')
                for row in directions['directions']}
    expected.update({iid: 'preserved' for iid in retained_ids})
    seen = set()
    for row in raw['checks']:
        if (not isinstance(row, dict) or set(row) != {'item_id', 'result', 'evidence'}
                or row['item_id'] not in expected or row['item_id'] in seen
                or row['result'] not in (expected[row['item_id']], 'fail', 'uncertain')
                or not isinstance(row['evidence'], str) or not row['evidence'].strip()):
            raise ValueError('invalid item review')
        seen.add(row['item_id'])
    if seen != set(expected):
        raise ValueError('review must cover every source item')
    if any(not isinstance(issue, str) or not issue.strip() for issue in raw['issues']):
        raise ValueError('issues must be nonempty strings')
    return not raw['issues'] and all(row['result'] not in ('fail', 'uncertain') for row in raw['checks'])
