"""Conservative relation-aware routing for the compact flat experiment.

The relation graph is a linguistic dependency, not a SQL join or an estimated
probability distribution over user answers. Scores are auditable priorities only.
"""
from .analysis import indexes
from .question_types import classify


QUESTION_PRIORITY = {'scope': 0, 'field': 1, 'permission': 1,
                     'operation': 2, 'value': 3}


def plan_balance(session, context, *, max_options=5):
    """Bound menus by relevant, authorized columns; rank potential scope unlocks."""
    # Import at call time to avoid a module import cycle.
    from .simple_session import connected_options

    view = session['view']
    columns = indexes(context)[1]
    options_by_key, audit, candidate_tables = {}, {}, {}
    for q in view['questions']:
        if q['dimension'] != 'field':
            continue
        item = q['item']
        iid = item['item_id']
        decision_type = classify(item, q['dimension'])
        raw = item.get('field_candidates', [])
        if session.get('strict_menus'):
            from .category_propagation import restrict_candidates
            raw = restrict_candidates(session, iid, raw)
        permitted = []
        for option in raw:
            col = option['column']
            if (col in session['allowed']
                    and connected_options(session, context, item, [col])):
                permitted.append({'id': chr(65 + len(permitted)), 'text': option['label'],
                                  'required_columns': [col]})
        candidate_tables[iid] = {columns[o['required_columns'][0]] for o in permitted}
        reason = ('scope_unresolved' if 'scope' in item['missing'] or not item['scope_tables']
                  else 'permission_issue' if item['denied_columns']
                  else 'calculation_unresolved' if decision_type == 'calculation_specification'
                  else 'no_relevant_authorized_choice' if not permitted
                  else 'too_many_relevant_choices' if len(permitted) > max_options
                  else 'indistinguishable_business_labels' if len({
                      o['text'].casefold() for o in permitted}) != len(permitted)
                  else 'bounded_relevant_choices')
        options_by_key[q['key']] = permitted if reason == 'bounded_relevant_choices' else []
        audit[q['key']] = {'item_id': iid, 'relevant_count': len(raw),
                           'anchor_tables': list(item['scope_tables']),
                           'candidate_tables': sorted(candidate_tables[iid]),
                           'authorized_connected_count': len(permitted),
                           'decision_type': decision_type,
                           'mode': 'hierarchical_menu' if session.get('strict_menus') else
                                   'menu' if options_by_key[q['key']] else 'open',
                           'reason': reason}

    # Count only direct, still-pending scope dependencies. Context points from
    # the dependent to its antecedent; attachment points from the instruction
    # to its affected expression. Neither transfers schema bindings.
    pending = {q['item']['item_id'] for q in view['questions']
               if q['dimension'] != 'permission'}
    unlocks = {q['key']: set() for q in view['questions']}
    for edge in view['relations']['edges']:
        source, target = edge['source_id'], edge['target_id']
        if source not in pending or target not in pending:
            continue
        source_item = view['items'][source]
        if 'scope' not in source_item['missing'] and source_item['scope_tables']:
            continue
        provider, dependent = ((target, source) if edge['kind'] == 'context'
                               else (source, target))
        for q in view['questions']:
            if q['item']['item_id'] != provider:
                continue
            if (q['dimension'] == 'scope' or (edge['kind'] == 'context'
                    and q['dimension'] == 'field' and len(candidate_tables.get(provider, set())) > 1)):
                unlocks[q['key']].add(dependent)

    scores = {}
    for q in view['questions']:
        key = q['key']
        scores[key] = {'potential_scope_unlocks': len(unlocks[key]),
                       'dependent_items': sorted(unlocks[key]),
                       'interaction': 'menu' if (options_by_key.get(key) or
                            (session.get('strict_menus') and q['dimension'] in ('scope', 'field', 'permission'))) else 'open',
                       'candidate_count': len(options_by_key.get(key, []))}
    return {'max_options': max_options, 'options': options_by_key,
            'field_audit': audit, 'scores': scores,
            'meaning': 'direct scope dependencies and bounded authorized choices; not expected information gain'}


def question_key(session, q):
    """Higher potential scope unlock first, then dimension and menu feasibility."""
    baseline = heuristic_key(session, q)
    if session.get('question_policy', 'heuristic') == 'expected_gain':
        from .gain_policy import score_for
        gain = score_for(session, q)
        if gain is not None:
            # Binary summation noise is not evidence for a different question.
            return (-round(gain, 12), *baseline)
    return (0, *baseline) if session.get('question_policy') == 'expected_gain' else baseline


def heuristic_key(session, q):
    """Original priority, preserved as an explicit baseline and tie-breaker."""
    from .simple_session import requirement_position
    score = session['view']['balance']['scores'][q['key']]
    return (-score['potential_scope_unlocks'], QUESTION_PRIORITY[q['dimension']],
            0 if score['interaction'] == 'menu' else 1,
            score['candidate_count'] if score['interaction'] == 'menu' else 0,
            requirement_position(session, q['item']))
