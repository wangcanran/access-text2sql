"""Deterministic typed dependency closure; no business-specific keyword rules."""
from copy import deepcopy


def canonical_plan(raw, base, context, decisions):
    if not isinstance(raw, dict) or set(raw) != {'nodes', 'edges', 'retired_dependencies'}:
        raise ValueError('Expected nodes, edges, retired_dependencies')
    raw = deepcopy(raw)
    canonical = {f"{t['name']}.{c['name']}".casefold(): f"{t['name']}.{c['name']}"
                 for t in context['tables'] for c in t['columns']}
    def column(c):
        if not isinstance(c, str) or c.casefold() not in canonical:
            raise ValueError('Unknown schema column: ' + str(c))
        return canonical[c.casefold()]
    if not isinstance(raw['nodes'], list) or not raw['nodes']:
        raise ValueError('Decompose all parents, including blocked contributions')
    ids, parents, routed = set(), set(), set()
    by_decision = {d['decision_id']: d for d in decisions}
    for n in raw['nodes']:
        if not isinstance(n, dict) or set(n) != {'id', 'parent', 'text', 'role', 'kind',
                                                'columns', 'questions', 'candidates', 'decision_ids'}:
            raise ValueError('Leaf fields must follow the stated node contract')
        if not isinstance(n['id'], str) or not n['id'] or n['id'] in ids or n['id'] in base['items']:
            raise ValueError('Leaf aliases must be distinct from active IDs')
        ids.add(n['id'])
        if n['parent'] not in base['items']:
            raise ValueError('Unknown parent')
        parents.add(n['parent'])
        old = base['items'][n['parent']]
        if not isinstance(n['text'], str) or not n['text'].strip():
            raise ValueError('Leaf text must be nonempty')
        if n['role'] not in ('output', 'modifier') or n['kind'] not in ('attribute', 'calculation', 'relation', 'instruction'):
            raise ValueError('Invalid leaf role/kind')
        if old['role'] == 'modifier' and n['role'] != 'modifier':
            raise ValueError('A modifier cannot become a returned output')
        if not isinstance(n['columns'], list) or not isinstance(n['candidates'], dict):
            raise ValueError('columns must be a list; candidates must be a map')
        n['columns'] = list(dict.fromkeys(column(c) for c in n['columns']))
        n['candidates'] = {column(c): label for c, label in n['candidates'].items()}
        if any(not isinstance(v, str) or not v.strip() or v in canonical.values() for v in n['candidates'].values()):
            raise ValueError('Candidates need business labels')
        if not isinstance(n['questions'], dict) or set(n['questions']) - {'scope', 'field', 'operation', 'value'}:
            raise ValueError('Invalid question dimensions')
        if any(not isinstance(q, str) or not q.strip() for q in n['questions'].values()):
            raise ValueError('Questions must be nonempty')
        if 'field' not in n['questions']:
            n['candidates'] = {}  # Unused menu metadata, same as baseline answers.
        if not isinstance(n['decision_ids'], list) or len(n['decision_ids']) != len(set(n['decision_ids'])):
            raise ValueError('decision_ids must be distinct')
        for did in n['decision_ids']:
            d = by_decision.get(did)
            if not d or d['owner_id'] != n['parent']:
                raise ValueError('Route every pending decision within its own parent: ' + str(did))
            # Routing an existing obligation cannot close it by omitting a hint.
            n['questions'].setdefault(d['dimension'], d['question'])
            routed.add(did)
    if parents != set(base['items']):
        raise ValueError('Every active parent must have descendants')
    if routed != set(by_decision):
        raise ValueError('Unrouted pending decisions: ' + ', '.join(sorted(set(by_decision) - routed)))
    if not isinstance(raw['edges'], list):
        raise ValueError('edges must be a list')
    seen = set()
    nodes = {n['id']: n for n in raw['nodes']}
    for e in raw['edges']:
        if not isinstance(e, list) or len(e) != 3 or any(not isinstance(x, str) for x in e):
            raise ValueError('Edges must be [source,target,kind]')
        a, b, k = e
        if a not in ids or b not in ids or a == b or k not in ('requires', 'applies_to') or tuple(e) in seen:
            raise ValueError('Invalid or duplicate edge')
        if k == 'applies_to' and nodes[a]['role'] != 'modifier':
            raise ValueError('Only a modifier can apply to another node')
        seen.add(tuple(e))
    retired = raw['retired_dependencies']
    if not isinstance(retired, dict):
        raise ValueError('retired_dependencies must be a map')
    raw['retired_dependencies'] = {column(c): reason for c, reason in retired.items()}
    if any(not isinstance(s, str) or not s.strip() for s in retired.values()):
        raise ValueError('Every retired dependency needs a reason')
    old_columns = {c for old in base['items'].values() for c in old['required_columns']}
    if set(raw['retired_dependencies']) - old_columns:
        raise ValueError('Cannot retire an unknown prior dependency')
    declared_all = {c for n in raw['nodes'] for c in n['columns']}
    overlap = set(raw['retired_dependencies']) & declared_all
    if overlap:
        raise ValueError('A retired dependency remains required by a surviving leaf: '
                         + ', '.join(sorted(overlap)))
    for parent, old in base['items'].items():
        declared = {c for n in raw['nodes'] if n['parent'] == parent for c in n['columns']}
        missing = set(old['required_columns']) - declared - set(raw['retired_dependencies'])
        if missing:
            raise ValueError('Account for prior dependencies of ' + parent + ': ' + ', '.join(sorted(missing)))
    return raw


def closure(plan, allowed, seeds=None):
    """A lost optional condition does not kill its target; a lost provider does."""
    allowed = set(allowed)
    nodes = {n['id']: n for n in plan['nodes']}
    dropped = deepcopy(seeds or {})
    for iid, n in nodes.items():
        denied = sorted(set(n['columns']) - allowed)
        if denied:
            dropped[iid] = {'rule': 'denied_dependency', 'columns': denied}
        elif n['candidates'] and not set(n['candidates']) & allowed:
            dropped[iid] = {'rule': 'empty_authorized_field_domain', 'columns': sorted(n['candidates'])}
    while True:
        previous = len(dropped)
        for a, b, kind in plan['edges']:
            if kind == 'requires' and b in dropped and a not in dropped:
                dropped[a] = {'rule': 'lost_required_provider', 'provider': b}
        # Reach all modifiers that still contribute to a live output. Cyclic
        # orphan components are removed too, not just zero-degree nodes.
        needed = {i for i, n in nodes.items() if n['role'] == 'output' and i not in dropped}
        while True:
            size = len(needed)
            for a, b, kind in plan['edges']:
                if a in dropped or b in dropped:
                    continue
                if kind == 'requires' and a in needed:
                    needed.add(b)
                if kind == 'applies_to' and b in needed:
                    needed.add(a)
            if len(needed) == size:
                break
        for i, n in nodes.items():
            if n['role'] == 'modifier' and i not in needed and i not in dropped:
                dropped[i] = {'rule': 'no_surviving_output_consumer'}
        if len(dropped) == previous:
            break
    return dropped


def required_columns(plan):
    columns = {n['id']: set(n['columns']) for n in plan['nodes']}
    while True:
        before = sum(map(len, columns.values()))
        for a, b, kind in plan['edges']:
            if kind == 'requires':
                columns[a].update(columns[b])
        if sum(map(len, columns.values())) == before:
            return columns
