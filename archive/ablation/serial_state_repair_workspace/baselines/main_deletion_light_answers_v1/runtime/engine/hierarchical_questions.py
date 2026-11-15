"""Progressively narrow authorized business choices before asking for details.

Navigation is not a field selection. A category owns a set of possible leaves,
not a set of query dependencies. Only explicit leaf choices enter intent history.
"""
from copy import deepcopy

from .analysis import indexes, validated_call
from .candidates import _meaning
from .question_types import protocol
from .prompts import STRICT_SCOPE_CHOICES, STRICT_CHOICE_GROUPS, STRICT_FIELD_LABELS, MENU_COLUMN_FILTER


def ready(session, question):
    """Scope precedes fields; unresolved scope/fields block downstream details."""
    if not session.get('strict_menus'):
        return True
    item, dim = question['item'], question['dimension']
    missing = set(item['missing'])
    if dim == 'scope':
        return True
    if 'scope' in missing:
        return False
    if dim == 'field' and any(n['item_id'] == item['item_id']
                             for n in session['permission_issues'].values()):
        return False  # One field decision, with access constraints, not two menus.
    if dim in ('field', 'permission'):
        return True
    return 'field' not in missing and not any(
        n['item_id'] == item['item_id'] for n in session['permission_issues'].values())


def _defer(session, q, reason, blocked=False):
    new = deepcopy(session)
    new['view']['deferred'].append(q['key'])
    new['view']['candidate_audit'].append({'decision_key': q['key'], 'reason': reason})
    if blocked:
        new.setdefault('menu_blockers', {})[q['key']] = q['item']['item_id']
    return new, None


def _choices(call, session, q, context):
    """Model proposes relevant meanings; code checks all displayed dependencies."""
    from .simple_session import connected_options, public_history
    from .category_propagation import category_context
    item = q['item']
    columns = indexes(context)[1]
    if q['dimension'] == 'scope':
        def check(raw):
            if not isinstance(raw, dict) or set(raw) != {'options'} or not isinstance(raw['options'], list):
                raise ValueError('Expected scope options array')
            parsed, labels = [], set()
            for o in raw['options']:
                if (not isinstance(o, dict) or set(o) != {'text', 'required_columns'}
                        or not isinstance(o['text'], str) or not o['text'].strip()
                        or not isinstance(o['required_columns'], list) or not o['required_columns']
                        or any(not isinstance(c, str) or c not in columns for c in o['required_columns'])):
                    raise ValueError('Each scope choice needs business text and actual supporting columns')
                if o['text'].strip().casefold() in labels:
                    raise ValueError('Scope labels must distinguish choices')
                labels.add(o['text'].strip().casefold())
                parsed.append({'text': o['text'].strip(), 'required_columns': sorted(set(o['required_columns']))})
            return parsed
        raw = validated_call(call, 'scope_menu_choices', STRICT_SCOPE_CHOICES,
            {'current_request': session['current_request'], 'public_conversation': public_history(session),
             'target': item, 'question_focus': q['focus'],
             'category_context': category_context(session, item['item_id']),
             'schema': {k: context[k] for k in ('tables', 'foreign_keys')},
             'column_meanings': context.get('column_language', {})}, check)
    else:
        permission = q['dimension'] == 'permission'
        raw = [] if permission else [
            {'text': o['label'], 'required_columns': [o['column']]}
            for o in item.get('field_candidates', [])]
        if not raw:
            from .execution import _authorized_graph, _connected
            # Scope tables identify the entity, not where its attributes must
            # be stored. Build the semantic search domain using verified schema
            # connections BEFORE permission filtering, including denied keys.
            graph, error = _authorized_graph(context, columns)
            if error:
                raise ValueError(error)
            domain = {c for c, table in columns.items() if item['scope_tables'] and
                      _connected(graph, set(item['scope_tables']) | {table})}
            if permission:
                domain = {c for c in domain if c in session['allowed']
                          and connected_options(session, context, item, [c])}
                if not domain:
                    return [], True  # Known access issue, not unsupported schema.
            # Relevance is independent of access: an empty authorized subset
            # must not be mistaken for an invitation to choose unrelated fields.
            def check(raw):
                if (not isinstance(raw, dict) or set(raw) != {'columns'}
                        or not isinstance(raw['columns'], list)
                        or any(not isinstance(c, str) or c not in domain for c in raw['columns'])
                        or len(raw['columns']) != len(set(raw['columns']))):
                    raise ValueError('Expected unique schema column IDs')
                return raw['columns']
            selected = validated_call(call, 'hierarchical_field_filter', MENU_COLUMN_FILTER,
                {'current_request': session['current_request'], 'target': item['span'],
                 'current_meaning': item['requirement'], 'role': item['role'], 'kind': item['kind'],
                 'scope_tables': item['scope_tables'],
                 'foreign_keys': context.get('foreign_keys', []),
                 'dimension': 'field', 'question_focus': q['focus'],
                 'permission_field_choice': permission,
                 'public_conversation': public_history(session),
                 'missing_dimensions': item['missing'],
                 'category_context': category_context(session, item['item_id']),
                 'candidate_domain': [{'column': c, 'meaning': _meaning(context, c)}
                                      for c in sorted(domain)]}, check)
            raw = [{'text': _meaning(context, c), 'required_columns': [c]} for c in selected]
        restriction = session['view'].get('candidate_restrictions', {}).get(item['item_id'])
        if restriction is not None:
            raw = [o for o in raw if set(o['required_columns']) <= set(restriction)]
    # An unresolved scope lists alternatives, not simultaneous table anchors.
    anchor = {**item, 'scope_tables': []} if q['dimension'] == 'scope' else item
    permitted, seen = [], set()
    for o in raw:
        cols = set(o['required_columns'])
        marker = (o['text'].casefold(), tuple(sorted(cols)))
        if (marker not in seen and cols <= set(session['allowed'])
                and connected_options(session, context, anchor, cols)):
            seen.add(marker)
            permitted.append({'choice_id': 'c' + str(len(permitted) + 1), **o})
    return permitted, bool(raw) or q['dimension'] == 'permission'


def _choice_evidence(choice, context):
    return {'choice_id': choice['choice_id'], 'meaning': choice['text'],
            'fields': [{'column': c, 'meaning': _meaning(context, c)}
                       for c in choice['required_columns']]}


def _field_labels(call, session, q, frontier, context):
    """Render only the displayed authorized leaves; bindings stay code-owned."""
    by_id = {o['choice_id']: o for o in frontier['leaves']}
    domain = frontier['active']
    def check(raw):
        if not isinstance(raw, dict) or set(raw) != {'options'} or not isinstance(raw['options'], list):
            raise ValueError('Expected business labels for exactly the supplied choice IDs')
        labels, result = set(), {}
        for o in raw['options']:
            if (not isinstance(o, dict) or set(o) != {'label', 'choice_id'}
                    or not isinstance(o['choice_id'], str) or o['choice_id'] not in domain
                    or o['choice_id'] in result
                    or not isinstance(o['label'], str) or not o['label'].strip()
                    or o['label'].strip().casefold() in labels):
                raise ValueError('Each supplied choice needs one distinct nonempty business label')
            labels.add(o['label'].strip().casefold())
            result[o['choice_id']] = o['label'].strip()
        if set(result) != set(domain):
            raise ValueError('Label every supplied choice ID exactly once')
        return result
    return validated_call(call, 'hierarchical_field_labels', STRICT_FIELD_LABELS,
        {'current_request': session['current_request'], 'target': q['item']['requirement'],
         'role': q['item']['role'], 'question_focus': q['focus'], 'category_path': frontier['path'],
         'choices': [_choice_evidence(by_id[c], context) for c in domain]}, check)


def prepare(call, session, plan, context, max_options=5):
    from .simple_session import decision
    q = decision(session, plan)
    if not 2 <= max_options <= 25:
        raise ValueError('Menu size must permit a strict partition and letter IDs')
    if session['pending'] and session['pending']['decision_key'] == q['key']:
        return session, deepcopy(session['pending']['public'])
    if q['dimension'] not in ('scope', 'field', 'permission'):
        raise ValueError('Hierarchical menus handle scope, field and permission choices')
    new = deepcopy(session)
    frontier = new.setdefault('menu_frontiers', {}).get(q['key'])
    if frontier is None:
        choices, supported = _choices(call, session, q, context)
        if not choices:
            return _defer(session, q, 'no_authorized_relevant_choices' if supported else
                          'no_schema_supported_choices', blocked=supported or q['dimension'] == 'permission')
        frontier = {'leaves': choices, 'active': [o['choice_id'] for o in choices],
                    'queue': [], 'selected': [], 'path': [], 'history': []}
        new['menu_frontiers'][q['key']] = frontier
    domain = frontier['active']
    by_id = {o['choice_id']: o for o in frontier['leaves']}
    answer_protocol = protocol(q['item'], q['dimension'], strict=True)
    multi = answer_protocol['multi_select']
    category = len(domain) > max_options
    if category:
        def check(raw):
            if (not isinstance(raw, dict) or set(raw) != {'options'} or not isinstance(raw['options'], list)
                    or not 2 <= len(raw['options']) <= max_options):
                raise ValueError('Expected 2..max_options categories')
            seen, labels = [], set()
            for o in raw['options']:
                if (not isinstance(o, dict) or set(o) != {'label', 'choice_ids'}
                        or not isinstance(o['label'], str) or not o['label'].strip()
                        or not isinstance(o['choice_ids'], list)
                        or not 1 <= len(o['choice_ids']) < len(domain)
                        or any(not isinstance(c, str) for c in o['choice_ids'])
                        or o['label'].strip().casefold() in labels):
                    raise ValueError('Each distinct business category must strictly shrink the domain')
                labels.add(o['label'].strip().casefold())
                seen.extend(o['choice_ids'])
            if len(seen) != len(set(seen)) or set(seen) != set(domain):
                raise ValueError('Partition every supplied choice exactly once, without additions or omissions')
            return raw['options']
        rows = validated_call(call, 'hierarchical_menu_groups', STRICT_CHOICE_GROUPS,
            {'current_request': session['current_request'], 'target': q['item']['requirement'],
             'question_focus': q['focus'], 'category_path': frontier['path'], 'max_options': max_options,
             'choices': [_choice_evidence(by_id[c], context) for c in domain]}, check)
        options = [{'id': chr(65 + i), 'text': r['label'].strip(), 'choice_ids': r['choice_ids']}
                   for i, r in enumerate(rows)]
    else:
        options = [{'id': chr(65 + i), **deepcopy(by_id[c])} for i, c in enumerate(domain)]
        # Scope meanings are already model-rendered in scope_menu_choices.
        # Fields are always rendered, even if analysis supplied usable labels.
        if q['dimension'] in ('field', 'permission'):
            labels = _field_labels(call, session, q, frontier, context)
            for o in options:
                o['text'] = labels[o['choice_id']]
    purpose = ('Choose the business scope; this does not select returned fields.' if q['dimension'] == 'scope'
               else 'Choose all relevant fields for this part only.')
    if q['dimension'] in ('field', 'permission') and answer_protocol['mode'] == 'operand':
        purpose += ' This selects the fields involved; their calculation or condition combination is decided separately.'
    if category:
        purpose = ('Choose the relevant business categories. Each selected category will be narrowed separately; '
                   'choosing a category does not select its fields.')
    none_id = chr(65 + len(options))
    public = {'question': 'Regarding "' + q['item']['span'] + '": ' + q['focus'] + ' ' + purpose +
              (' Current category: ' + ' / '.join(frontier['path']) + '.' if frontier['path'] else ''),
              'focus': q['focus'], 'multi_select': multi,
              'granularity': 'category' if category else 'field' if q['dimension'] == 'permission' else q['dimension'],
              'options': [{'id': o['id'], 'label': o['text']} for o in options] +
                         [{'id': none_id, 'label': 'None of these meets my requirement'}]}
    new['pending'] = {'turn': len(new['turns']), 'decision_key': q['key'], 'public': public,
                      'options': options, 'mode': 'hierarchical', 'none_id': none_id,
                      'category': category, 'multi': multi}
    new['view']['candidate_audit'].append({'decision_key': q['key'], 'granularity': public['granularity'],
        'domain_size': len(domain), 'category_path': list(frontier['path']),
        'business_option_count': len(options),
        'option_domain_sizes': [len(o['choice_ids']) if category else 1 for o in options]})
    return new, public


def commit(call, session, plan, question, answer, context):
    """Navigation retains the analysis; completed explicit choices update intent."""
    from . import simple_session as base
    q, menu = base.decision(session, plan), session['pending']
    if question != menu['public']:
        raise ValueError('Menu was not shown by this runner')
    if (not isinstance(answer, dict) or not {'action', 'text', 'choices'} <= set(answer)
            or not set(answer) <= {'action', 'text', 'choices', 'module_ids'}
            or not isinstance(answer['text'], str) or not isinstance(answer['choices'], list)
            or any(not isinstance(c, str) for c in answer['choices'])):
        raise ValueError('Invalid hierarchical answer')
    control = answer['action'] in ('unanswerable', 'refuse', 'submit')
    if control and (answer['text'] or answer['choices']):
        raise ValueError('Control answer must have empty payload')
    by_id = {o['id']: o for o in menu['options']}
    choices = answer['choices']
    if not control and (answer['action'] != 'choose' or answer['text'] or not choices
            or len(set(choices)) != len(choices) or not set(choices) <= set(by_id) | {menu['none_id']}
            or (not menu['multi'] and len(choices) != 1)):
        raise ValueError('This decision requires menu IDs, not an open answer')
    none = menu['none_id'] in choices
    if none and len(choices) != 1:
        raise ValueError('None-of-these is exclusive')
    new = deepcopy(session)
    record = {'question': deepcopy(question), 'answer': deepcopy(answer)}
    new.setdefault('public_interactions', []).append(record)
    f = new['menu_frontiers'][q['key']]
    f['history'].append(record)
    if control or none:
        if answer['action'] == 'refuse' or (answer['action'] == 'submit' and not f['selected']):
            new['turns'].append({**record, 'decision_key': q['key']})
            new['pending'] = None
            new['menu_frontiers'] = {}
            return new
        if answer['action'] == 'submit':
            # Preserve earlier concrete choices before honoring submission.
            new['submit_requested'] = True
            f['queue'] = []
        # A declined branch does not discard other requested branches.
    elif menu['category']:
        branches = [{'ids': by_id[c]['choice_ids'], 'path': f['path'] + [by_id[c]['text']]} for c in choices]
        f['queue'] = branches + f['queue']
        from .category_propagation import propagate
        new = propagate(call, new, q, f, [by_id[c]['text'] for c in choices], context)
        f = new['menu_frontiers'][q['key']]
    else:
        selected = [deepcopy(by_id[c]) for c in choices]
        cols = {c for o in f['selected'] + selected for c in o['required_columns']}
        anchor = {**q['item'], 'scope_tables': []} if q['dimension'] == 'scope' else q['item']
        if not cols <= set(session['allowed']) or not base.connected_options(session, context, anchor, cols):
            raise ValueError('Selected leaves are not jointly authorized and connected')
        f['selected'].extend(selected)
        f['last_leaf_exchange'] = deepcopy(record)
    if f['queue']:
        branch = f['queue'].pop(0)
        f['active'], f['path'] = branch['ids'], branch['path']
        new['pending'] = None
        return new
    if menu['category'] and not (control or none):
        raise ValueError('Category navigation must have a smaller next branch')
    selected = f['selected']
    if not selected:
        # Under the complete-menu contract, rejecting every offered branch
        # means no authorized choice was accepted for this required contribution.
        new['turns'].append({'question': deepcopy(question),
            'answer': {'action': 'unanswerable', 'text': '', 'choices': []},
            'decision_key': q['key'], 'target_id': q['item']['item_id'],
            'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
            'asked_dimension': q['dimension'], 'requested_slots': q.get('requested_slots', []),
            'menu_exchanges': deepcopy(f['history']), 'menu_rejected': True,
            'rejection_basis': 'no_authorized_choice_accepted'})
        new['pending'] = None
        new['menu_frontiers'].pop(q['key'])
        return _defer(new, q, 'no_authorized_choice_accepted', blocked=True)[0]
    # One effective turn represents all explicit leaf selections in this local
    # traversal. Preserve each actual exchange so letter choices stay auditable.
    mode = protocol(q['item'], q['dimension'], strict=True)['mode']
    turn = {**deepcopy(f['last_leaf_exchange']), 'decision_key': q['key'],
            'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
            'asked_dimension': q['dimension'], 'target_id': q['item']['item_id'],
            'target_inventory_id': session['view'].get('inventory_id'),
            'requested_slots': q.get('requested_slots', []),
            'selected_options': [{k: deepcopy(v) for k, v in o.items() if k in ('text', 'required_columns', 'choice_id')}
                                 for o in selected],
            'selection_mode': mode, 'menu_exchanges': deepcopy(f['history'])}
    if q.get('decision_ids'):
        turn['decision_ids'] = list(q['decision_ids'])
    if q['dimension'] == 'permission':
        # Keep the restriction until the reconstructed dependency set is checked.
        turn['permission_field_choice'] = True
        turn['permission_issue_key'] = q['key']
    new['turns'].append(turn)
    new['menu_frontiers'] = {}
    new['pending'] = None
    if new.get('update_mode') == 'propagation':
        new['update_base'] = deepcopy(session['view'])
    new['view'] = None
    return new
