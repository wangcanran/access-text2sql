"""Propagate menu navigation as candidate constraints, never as chosen fields."""
from copy import deepcopy

from .analysis import validated_call
from .candidates import _meaning
from .prompts import CATEGORY_PROPAGATION
from .session_update import affected_region


def category_context(session, item_id):
    return [deepcopy(c) for c in session['view'].get('navigation_constraints', {}).values()
            if item_id in c['affected_ids']]


def restrict_candidates(session, item_id, candidates):
    restriction = session['view'].get('candidate_restrictions', {}).get(item_id)
    return candidates if restriction is None else [c for c in candidates if c['column'] in restriction]


def propagate(call, session, question, frontier, selected_categories, context):
    """Batch-prune existing neighbor domains after a category choice.

    Already selected leaves and ALL queued branches remain possible context.
    A neighbor with no materialized field domain receives the same context when
    its candidates are generated later. No analysis row or permission is changed.
    """
    from .simple_session import connected_options
    from .flat_balance import plan_balance
    new = deepcopy(session)
    view, target = new['view'], question['item']['item_id']
    ids = affected_region(view, target)
    remaining = {o['choice_id'] for o in frontier['selected']}
    remaining.update(c for branch in frontier['queue'] for c in branch['ids'])
    choices = [o for o in frontier['leaves'] if o['choice_id'] in remaining]
    constraints = view.setdefault('navigation_constraints', {})
    old = constraints.get(question['key'])
    constraint = {'target_id': target, 'dimension': question['dimension'],
                  'affected_ids': ids, 'selected_categories': deepcopy(selected_categories),
                  'possible_choices': deepcopy(choices)}
    constraints[question['key']] = constraint
    previous = old['possible_choices'] if old else frontier['leaves']
    narrowed = remaining != {o['choice_id'] for o in previous}
    restrictions = view.setdefault('candidate_restrictions', {})
    # On the source, code owns the exact selected union; it is not a model guess.
    if question['dimension'] in ('field', 'permission'):
        restrictions[target] = sorted({c for o in choices for c in o['required_columns']})
    neighbors = []
    for iid in ids:
        n = view['items'][iid]
        if iid == target or 'field' not in n['missing']:
            continue
        candidates = restrict_candidates(new, iid, n.get('field_candidates', []))
        candidates = [c for c in candidates if c['column'] in new['allowed']
                      and connected_options(new, context, n, [c['column']])]
        if candidates:
            neighbors.append({'item_id': iid, 'requirement': n['requirement'],
                'role': n['role'], 'question_focus': n['question_focus']['field'],
                'candidates': [{**c, 'description': _meaning(context, c['column'])} for c in candidates]})
    audit = {'target_id': target, 'affected_ids': ids, 'source_before': len(previous),
             'source_after': len(choices), 'neighbors': [], 'model_called': False}
    if narrowed and neighbors:
        domains = {n['item_id']: {c['column'] for c in n['candidates']} for n in neighbors}
        def check(raw):
            if not isinstance(raw, dict) or set(raw) != {'items'} or not isinstance(raw['items'], list):
                raise ValueError('Expected candidate constraint items')
            rows = {}
            for r in raw['items']:
                if (not isinstance(r, dict) or set(r) != {'item_id', 'columns', 'reason'}
                        or not isinstance(r['item_id'], str) or r['item_id'] not in domains
                        or r['item_id'] in rows or not isinstance(r['columns'], list)
                        or any(not isinstance(c, str) for c in r['columns'])
                        or len(r['columns']) != len(set(r['columns']))
                        or not set(r['columns']) <= domains[r['item_id']]
                        or not isinstance(r['reason'], str) or not r['reason'].strip()):
                    raise ValueError('Each affected domain needs a retained subset and reason; no additions')
                rows[r['item_id']] = r
            if set(rows) != set(domains):
                raise ValueError('Cover every supplied neighbor exactly once')
            return rows
        rows = validated_call(call, 'category_propagation', CATEGORY_PROPAGATION,
            {'current_request': new['current_request'], 'source_requirement': question['item']['requirement'],
             'category_constraints': list(constraints.values()), 'relations': view['relations'],
             'shared_decisions': view.get('shared_decisions', []),
             'context_nodes': [{'item_id': iid, 'requirement': view['items'][iid]['requirement']}
                               for iid in ids], 'neighbors': neighbors}, check)
        audit['model_called'] = True
        for iid, row in rows.items():
            restrictions[iid] = row['columns']
            audit['neighbors'].append({'item_id': iid, 'before': len(domains[iid]),
                                      'after': len(row['columns']), 'reason': row['reason']})
    view.setdefault('category_propagation_audit', []).append(audit)
    view['balance'] = plan_balance(new, context)
    return new
