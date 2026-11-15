"""Stable fragment updates for SimpleSession; experiments are not runtime imports.

A linguistic reference supplies context. Shared-decision evidence identifies
which unanswered choices may share an answer. Neither directly clears missing
state or grants access. The analysis model rechecks the affected region.
"""
from copy import deepcopy

from ..model import QueryFrame
from .analysis import indexes, interpretation, validated_call
from .prompts import FLAT_COMPACT_ANALYZE, STABLE_UPDATE, SHARED_DECISIONS, SHARED_UPDATE
from .question_types import protocol

def validate_update(raw, local, ids, context, *, confirmed_target=None, confirmed_columns=()):
    if (not isinstance(raw, dict) or set(raw) != {'fallback_reason', 'items'}
            or not isinstance(raw['fallback_reason'], str) or not isinstance(raw['items'], list)):
        raise ValueError('Expected fallback_reason string and items array')
    if raw['fallback_reason'].strip():
        if raw['items']:
            raise ValueError('Fallback must return no partial items')
        return {'fallback_reason': raw['fallback_reason'], 'items': []}
    cols = indexes(context)[2]
    candidates, stripped = {}, []
    for row in raw['items']:
        if not isinstance(row, dict) or not isinstance(row.get('field_candidates'), list):
            raise ValueError('Expected analysis row with field_candidates')
        r = deepcopy(row)
        iid = r.get('item_id')
        if not isinstance(iid, str) or iid not in ids or iid in candidates:
            raise ValueError('Unknown or duplicate update ID')
        options = r.pop('field_candidates')
        parsed, seen = [], set()
        for option in options:
            if (not isinstance(option, dict) or set(option) != {'column', 'label'}
                    or not isinstance(option['column'], str) or option['column'].casefold() not in cols
                    or not isinstance(option['label'], str) or not option['label'].strip()):
                raise ValueError('Candidate must have a schema column and business label')
            column = cols[option['column'].casefold()]
            if column not in seen:
                parsed.append({'column': column, 'label': option['label']})
                seen.add(column)
        if options and 'field' not in r.get('missing', []):
            raise ValueError('Only an undecided field may have candidates')
        if r.get('grounded') is False and r.get('required_columns'):
            r['grounded'] = True
        candidates[iid] = parsed
        stripped.append(r)
    if set(candidates) != set(ids):
        raise ValueError('Missing update IDs: ' + ', '.join(sorted(set(ids) - set(candidates))))
    frame = QueryFrame(local['original_request'],
        tuple({'item_id': i, 'span': local['items'][i]['span']} for i in ids if local['items'][i]['role'] == 'output'),
        tuple({'item_id': i, 'span': local['items'][i]['span']} for i in ids if local['items'][i]['role'] == 'modifier'))
    rows = interpretation(frame, {'items': stripped}, context)['items']
    for row in rows:
        if 'question_focus' not in row or (row['status'] == 'clear' and not row['bindings'] and row['kind'] != 'instruction'):
            raise ValueError('Each row needs question_focus; clear data requires dependencies')
        row['field_candidates'] = candidates[row['item_id']]
        if row['item_id'] == confirmed_target:
            dependencies = {c for binding in row['bindings'] for c in binding}
            omitted = set(confirmed_columns) - dependencies
            if omitted:
                raise ValueError('Retain explicitly selected columns for ' + confirmed_target
                                 + ': ' + ', '.join(sorted(omitted)))
    return {'fallback_reason': '', 'items': rows}


def reference_contexts(local):
    """Materialize references from current providers, without overwriting nodes.

    Attachment supplies context source -> target; anaphora supplies it target ->
    source. Transitive references have explicit provenance. Cycles never produce
    facts, and unanswered provider decisions are not copied into consumer missing.
    These are linguistic contexts, not assertions of schema-equivalent columns.
    """
    adjacency = {i: [] for i in local['items']}
    for e in local['relations']['edges']:
        a, b = e['source_id'], e['target_id']
        provider, consumer = (a, b) if e['kind'] == 'applies_to' else (b, a)
        adjacency[provider].append((consumer, e['kind']))
    result = {i: [] for i in local['items']}
    for provider, n in local['items'].items():
        queue, seen = [(provider, [])], {provider}
        for current, path in queue:
            for receiver, kind in adjacency[current]:
                if receiver in seen:
                    continue
                seen.add(receiver)
                route = path + [{'provider': current, 'consumer': receiver, 'kind': kind}]
                result[receiver].append({'provider_id': provider, 'path': route,
                    'requirement': n['requirement'], 'required_columns': n['required_columns'],
                    'provider_missing': n['missing']})
                queue.append((receiver, route))
    return result


def validate_groups(raw, session):
    if not isinstance(raw, dict) or set(raw) != {'groups'} or not isinstance(raw['groups'], list):
        raise ValueError('Expected groups array')
    slots = {f'{i}/{d}' for i, n in session['view']['items'].items() for d in n['missing']}
    seen = set()
    for g in raw['groups']:
        if (not isinstance(g, dict) or set(g) != {'slots', 'kind', 'evidence', 'reason'}
                or not isinstance(g['slots'], list) or len(g['slots']) < 2
                or any(not isinstance(s, str) or s not in slots for s in g['slots'])
                or len(set(g['slots'])) != len(g['slots'])
                or len({s.split('/')[0] for s in g['slots']}) < 2
                or g['kind'] not in ('entity', 'measure', 'value')
                or not isinstance(g['evidence'], list) or len(g['evidence']) < 2
                or any(not isinstance(q, str) or not q or q not in session['current_request']
                       for q in g['evidence'])
                or not isinstance(g['reason'], str) or not g['reason'].strip()):
            raise ValueError('Group must use missing slots of distinct fragments; evidence must be an array of at least two separate exact request substrings')
        key = tuple(sorted(g['slots']))
        if key in seen:
            raise ValueError('Duplicate group')
        seen.add(key)
    return deepcopy(raw['groups'])


def reconcile_permissions(session):
    """Replace evidence for a stable fragment, retaining undischarged denials.

    Within an inventory, IDs identify contributions. After a full fallback only
    a unique, identical span and role can reconnect an older permission task.
    Missing model dependencies never discharge a detected restriction.
    """
    from .simple_session import signature
    view, issues = session['view'], session['permission_issues']
    inventory_id = view['inventory_id']
    items = view['items']
    for iid, item in items.items():
        current_key = signature('permission', inventory_id, iid)
        matches = [k for k, old in issues.items()
                   if (old.get('inventory_id') == inventory_id and old['item_id'] == iid)
                   or (old.get('inventory_id') != inventory_id
                       and (old['role'], old['span']) == (item['role'], item['span'])
                       and sum((n['role'], n['span']) == (item['role'], item['span'])
                               for n in items.values()) == 1)]
        old_denied = {c for k in matches for c in issues[k]['denied_columns']}
        denied = old_denied | set(item['denied_columns'])
        if not denied:
            continue
        evidence = {**deepcopy(item), 'inventory_id': inventory_id,
                    'denied_columns': sorted(denied),
                    'required_columns': sorted(set(item['required_columns']) | old_denied)}
        # Keep the old task key so a deferred/refused task cannot be resurrected
        # merely by changing the model's dependency bundle.
        key = matches[0] if matches else current_key
        for obsolete in matches:
            issues.pop(obsolete)
        issues[key] = evidence


def finish_view(session, context):
    """Derive questions and code-checked candidates from one inventory snapshot."""
    from .simple_session import signature
    from .flat_balance import plan_balance
    view = session['view']
    if session.get('strict_menus'):
        for item in view['items'].values():
            # No field domain can be narrowed without a business scope. Repair
            # an omitted scope decision rather than exposing a database-wide menu.
            if (('field' in item['missing'] or item['denied_columns'])
                    and not item['scope_tables'] and 'scope' not in item['missing']):
                item['missing'].insert(0, 'scope')
                item['question_focus']['scope'] = 'Which business entity or population does this part concern?'
    from .answer_effects import resolve_permission_choice
    resolve_permission_choice(session, context)
    reconcile_permissions(session)
    questions = []
    decision_rows = (view.get('progressive', {}).get('decision_ledger')
                     if view.get('progressive', {}).get('question_policy') in ('answer_delta', 'answer_snapshot') else None)
    for iid, item in view['items'].items():
        for dim in item['missing']:
            decisions = [d for d in decision_rows or [] if d['owner_id'] == iid and d['dimension'] == dim]
            for decision in decisions or [None]:
                q = {'item': item, 'dimension': dim,
                    'focus': decision['question'] if decision else item['question_focus'][dim],
                    'decision_type': protocol(item, dim, strict=session.get('strict_menus', False))['type'],
                    'key': signature('slot', view['inventory_id'], iid, dim),
                    'requested_slots': [iid + '/' + dim]}
                if decision:
                    q['decision_ids'] = [decision['decision_id']]
                    q['key'] = signature('slot', view['inventory_id'], iid, dim, decision['decision_id'])
                questions.append(q)
    for key, item in session['permission_issues'].items():
        questions.append({'item': item, 'dimension': 'permission',
            'focus': ('Which permitted fields should be used for this part?' if session.get('strict_menus')
                      else 'Which permitted requirement would you like for this part?'),
            'key': key, 'decision_type': protocol(item, 'permission', strict=session.get('strict_menus', False))['type']})
    view['questions'] = questions
    view['balance'] = plan_balance(session, context)
    if session.get('local_bundle'):
        bundle_questions(session, context)
    return session


def bundle_questions(session, context):
    """Ask coupled open decisions together, staying inside one source fragment."""
    from .simple_session import signature
    from .flat_balance import heuristic_key, plan_balance
    from .gain_policy import eligible_questions
    view, buckets = session['view'], {}
    unavailable = {slot for turn in session['turns']
                   if turn['answer']['action'] == 'unanswerable'
                   for slot in turn.get('requested_slots', [])}
    for q in eligible_questions(session):
        if session.get('strict_menus'):
            from .hierarchical_questions import ready
            if q['dimension'] not in ('operation', 'value') or not ready(session, q):
                continue
        if q['dimension'] == 'permission' or set(q['requested_slots']) & unavailable:
            continue
        menu = q['dimension'] == 'field' and view['balance']['options'].get(q['key'])
        if not menu:
            buckets.setdefault(q['item']['item_id'], []).append(q)
    removed, combined = set(), []
    for iid, qs in buckets.items():
        if len(qs) < 2:
            continue
        qs.sort(key=lambda q: heuristic_key(session, q))
        q = deepcopy(qs[0])
        q['requested_slots'] = sorted({s for candidate in qs for s in candidate['requested_slots']})
        q['requested_dimensions'] = [candidate['dimension'] for candidate in qs]
        if any(candidate.get('decision_ids') for candidate in qs):
            q['decision_ids'] = list(dict.fromkeys(d for candidate in qs for d in candidate.get('decision_ids', [])))
        q['key'] = signature('bundle', view['inventory_id'], iid, q['requested_slots'])
        q['focus'] = 'Please clarify this part: ' + ' '.join(candidate['focus'] for candidate in qs)
        q['decision_type'], q['open_bundle'] = 'fragment_details', True
        combined.append(q)
        removed.update(candidate['key'] for candidate in qs)
    view['questions'] = [q for q in view['questions']
                         if q['key'] not in removed and not set(q.get('requested_slots', [])) & unavailable] + combined
    view['balance'] = plan_balance(session, context)


def annotate_shared(call, session):
    """One annotation per inventory; private references never enter this input."""
    view = session['view']
    pending = [n for n in view['items'].values() if n['missing']]
    groups = []
    if len(pending) >= 2:
        payload = {'request': session['current_request'], 'frame': view['frame'],
            'relations': view['relations'], 'items': [{k: n[k] for k in
                ('item_id', 'role', 'requirement', 'scope_tables', 'required_columns',
                 'missing', 'question_focus')} for n in pending]}
        groups = validated_call(call, 'shared_decisions', SHARED_DECISIONS, payload,
                                lambda raw: validate_groups(raw, session))
    view['shared_decisions'] = groups


def affected_region(view, target):
    """Directed context closure plus shared-decision links; cycles terminate."""
    adjacency = {iid: set() for iid in view['items']}
    for edge in view['relations']['edges']:
        a, b = edge['source_id'], edge['target_id']
        provider, consumer = (a, b) if edge['kind'] == 'applies_to' else (b, a)
        adjacency[provider].add(consumer)
    for group in view.get('shared_decisions', []):
        ids = {s.split('/')[0] for s in group['slots']}
        for iid in ids:
            adjacency[iid].update(ids - {iid})
    seen, queue = {target}, [target]
    for provider in queue:
        for consumer in sorted(adjacency[provider] - seen):
            seen.add(consumer)
            queue.append(consumer)
    return sorted(seen)


def update_view(call, session, context):
    """Reanalyze one affected region once; topology changes request full fallback."""
    from .simple_session import public_history
    view = session['update_base']
    turn = session['turns'][-1]
    target = turn['target_id']
    item = view['items'].get(target)
    if item is None or (item['span'], item['role']) != (turn['target_fragment'], turn['target_role']):
        return None, {'fallback_reason': 'Target no longer belongs to this inventory'}
    ids = affected_region(view, target)
    local = {'items': view['items'], 'relations': view['relations'],
             'original_request': view['frame']['original_text']}
    payload = {'db_name': session['db_name'],
        'schema': {k: context[k] for k in ('tables', 'foreign_keys')},
        'column_meanings': context.get('column_language', {}),
        'current_request': session['current_request'],
        'public_conversation': public_history(session),
        'previous_nodes': [{'item_id': iid, 'source_span': n['span'], 'role': n['role'],
                           **{k: deepcopy(n[k]) for k in ('requirement', 'kind', 'scope_tables',
                              'required_columns', 'missing', 'question_focus', 'field_candidates')}}
                          for iid, n in view['items'].items()],
        'relations': view['relations'], 'target_id': target, 'update_ids': ids,
        'required_fragments': [{'item_id': iid, 'span': view['items'][iid]['span'],
                               'role': view['items'][iid]['role']} for iid in ids],
        'inherited_contexts': reference_contexts(local),
        'shared_decisions_from_text': view.get('shared_decisions', [])}
    confirmed = {c for option in turn.get('selected_options', [])
                 for c in option['required_columns']} if turn.get('selection_mode') in (
                     'return_fields', 'operand', 'complete') else set()
    parsed = validated_call(call, 'propagation_analysis',
        FLAT_COMPACT_ANALYZE + STABLE_UPDATE + SHARED_UPDATE, payload,
        lambda raw: validate_update(raw, local, ids, context,
                                   confirmed_target=target, confirmed_columns=confirmed))
    audit = {'updated_ids': ids, 'kept_ids': sorted(set(view['items']) - set(ids)),
             'fallback_reason': parsed['fallback_reason']}
    if parsed['fallback_reason']:
        return None, audit
    new = deepcopy(session)
    new['view'] = deepcopy(view)
    # Concrete accepted choices supersede temporary category-domain constraints.
    # The affected analysis above already used the effective answer and history.
    new['view'].pop('navigation_constraints', None)
    new['view'].pop('candidate_restrictions', None)
    new['view'].pop('gain', None)
    for row in parsed['items']:
        iid = row['item_id']
        deps = sorted({c for b in row['bindings'] for c in b})
        new['view']['items'][iid] = {**row, 'span': view['items'][iid]['span'],
            'role': view['items'][iid]['role'], 'required_columns': deps,
            'denied_columns': sorted(set(deps) - set(new['allowed']))}
    # A new answer can justify recomputing a previously empty menu in this region.
    invalidated = {q['key'] for q in view['questions'] if q['item']['item_id'] in ids}
    new['view']['deferred'] = [k for k in view['deferred'] if k not in invalidated]
    new['menu_blockers'] = {k: iid for k, iid in new.get('menu_blockers', {}).items() if iid not in ids}
    after = {'items': new['view']['items'], 'relations': view['relations']}
    new['view']['inherited_contexts'] = reference_contexts(after)
    new['view']['update_audit'] = audit
    new.pop('update_base', None)
    from .answer_effects import record_dependency_update
    return record_dependency_update(finish_view(new, context), view), audit
