"""Dialogue-owned intent with full or stable graph-guided analysis updates."""
from copy import deepcopy
import hashlib
import json

from ..model import QueryFrame
from .analysis import interpretation, indexes, validated_call
from .candidates import field_domain_explicit
from .execution import _authorized_graph, _connected, validate_sql_dependencies
from .prompts import HISTORY_REQUEST, ROUND_ANALYZE, SESSION_CANDIDATES, FLAT_RECONSTRUCT_GUIDANCE, MENU_NOTE_RECONSTRUCTION
from .question_types import protocol


def signature(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def public_history(session):
    return {'original_request': session['original_request'], 'turns': [
        {k: deepcopy(t[k]) for k in ('question', 'answer', 'selected_options', 'selection_mode',
                                    'target_fragment', 'target_role', 'asked_dimension',
                                    'related_fragments', 'requested_dimensions', 'menu_exchanges',
                                    'permission_field_choice') if k in t}
        for t in session['turns']]}


def reconstruction_history(session):
    """Unanswered assistant hypotheses are not evidence of user intent."""
    history = public_history(session)
    history['turns'] = [t for t in history['turns'] if t['answer']['action'] in ('answer', 'choose')]
    return history


def start_session(call, text, context, db_name, allowed, *, analysis_mode='joint', relation_context=False, menu_notes=False, question_policy='heuristic', update_mode='full', local_bundle=False, strict_menus=False):
    if analysis_mode not in ('joint', 'flat_compact'):
        raise ValueError('Unknown simple-session analysis mode')
    if type(relation_context) is not bool or (relation_context and analysis_mode != 'flat_compact'):
        raise ValueError('relation_context requires flat_compact analysis')
    if type(menu_notes) is not bool:
        raise ValueError('menu_notes must be boolean')
    if question_policy not in ('heuristic', 'expected_gain') or (question_policy != 'heuristic' and analysis_mode != 'flat_compact'):
        raise ValueError('expected_gain requires flat_compact analysis')
    if update_mode not in ('full', 'propagation') or (update_mode == 'propagation' and analysis_mode != 'flat_compact'):
        raise ValueError('propagation updates require flat_compact analysis')
    if type(local_bundle) is not bool or (local_bundle and update_mode != 'propagation'):
        raise ValueError('local_bundle requires propagation updates')
    if type(strict_menus) is not bool or (strict_menus and analysis_mode != 'flat_compact'):
        raise ValueError('strict_menus requires flat_compact analysis')
    if strict_menus and menu_notes:
        raise ValueError('Strict field menus accept choices only; details are asked afterwards')
    if update_mode == 'propagation' and (relation_context or question_policy != 'heuristic'):
        raise ValueError('propagation uses persistent contexts and heuristic scheduling')
    if not set(allowed) <= set(indexes(context)[1]):
        raise ValueError('Unknown policy column')
    return {'mode': 'simple', 'analysis_mode': analysis_mode,
            'relation_context': relation_context, 'menu_notes': menu_notes, 'question_policy': question_policy,
            'update_mode': update_mode, 'local_bundle': local_bundle, 'strict_menus': strict_menus,
            'menu_frontiers': {}, 'public_interactions': [], 'menu_blockers': {},
            'original_request': text, 'current_request': text,
            'db_name': db_name, 'allowed': sorted(set(allowed)), 'turns': [],
            'view': None, 'pending': None, 'permission_issues': {}}


def round_view(raw, text, context):
    """Adapt coarse requirements to existing routing; IDs belong to the runner.

    The frame is a compatibility view, not a verbatim segmentation. Completeness
    of meaning remains a model responsibility; schema/shape checks cannot prove it.
    """
    if (not isinstance(raw, dict) or set(raw) != {'items'}
            or not isinstance(raw['items'], list) or not raw['items']):
        raise ValueError('Expected a nonempty items array covering the current request')
    keys = {'requirement', 'role', 'kind', 'grounded', 'scope_tables',
            'required_columns', 'missing', 'question_focus', 'reason'}
    buckets = {'output': [], 'modifier': []}
    fragments, rows = {}, []
    for row in deepcopy(raw['items']):
        if (not isinstance(row, dict) or not keys - {'reason'} <= set(row)
                or not set(row) <= keys):
            raise ValueError('Expected the documented joint requirement fields; IDs are runner-assigned')
        role = row.pop('role')
        if not isinstance(role, str) or role not in buckets:
            raise ValueError('role must be output or modifier')
        description = row['requirement']
        if not isinstance(description, str) or not description.strip():
            raise ValueError('Each requirement needs a nonempty description')
        if row['kind'] not in ('attribute', 'calculation', 'relation', 'instruction'):
            raise ValueError('kind must be attribute, calculation, relation or instruction, not role')
        if (row['grounded'] and not row['required_columns'] and not row['missing']
                and row['kind'] != 'instruction'):
            raise ValueError('A grounded attribute/calculation/relation needs schema dependencies')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            row['reason'] = 'Model omitted the explanation; see raw response.'
        iid = ('o' if role == 'output' else 'm') + str(len(buckets[role]) + 1)
        fragment = {'item_id': iid, 'span': description.strip()}
        buckets[role].append(fragment)
        fragments[iid] = {**fragment, 'role': role}
        rows.append({**row, 'item_id': iid, 'requirement': description.strip()})
    frame = QueryFrame(text, tuple(buckets['output']), tuple(buckets['modifier']))
    result = interpretation(frame, {'items': rows}, context)
    return frame, fragments, result


def requirement_position(session, item):
    """Use source order when literal, otherwise stable analysis order."""
    position = session['current_request'].find(item['span'])
    if position >= 0:
        return position
    ids = list(session['view']['items'])
    return len(session['current_request']) + (ids.index(item['item_id'])
                                             if item['item_id'] in ids else len(ids))


def analyze_session(call, session, context, reconstruct=None):
    if session['view'] is not None:
        from .gain_policy import refresh_gain
        return refresh_gain(call, session)
    new = deepcopy(session)
    if any(t['answer']['action'] in ('answer', 'choose') for t in new['turns']):
        def request_check(raw):
            if (not isinstance(raw, dict) or set(raw) != {'request'}
                    or not isinstance(raw['request'], str) or not raw['request'].strip()):
                raise ValueError('Expected nonempty request')
            return raw['request'].strip()
        if reconstruct is None:
            prompt = (HISTORY_REQUEST + FLAT_RECONSTRUCT_GUIDANCE if
                new.get('analysis_mode') == 'flat_compact' else HISTORY_REQUEST)
            if any(t['answer']['action'] == 'choose' and t['answer']['text'].strip()
                   for t in new['turns']):
                prompt += MENU_NOTE_RECONSTRUCTION
            new['current_request'] = validated_call(call, 'round_reconstruct', prompt,
                                                    reconstruction_history(new), request_check)
        else:
            new['current_request'] = reconstruct(call, new)
            if not isinstance(new['current_request'], str) or not new['current_request'].strip():
                raise ValueError('Reconstructor must return a nonempty request')
    fallback_audit = None
    previous_view = new.get('update_base')
    if new.get('update_base') is not None:
        from .session_update import update_view
        updated, fallback_audit = update_view(call, new, context)
        if updated is not None:
            return updated
        new.pop('update_base')
    payload = {'db_name': new['db_name'],
               'schema': {k: context[k] for k in ('tables', 'foreign_keys')},
               'column_meanings': context.get('column_language', {}),
               'current_request': new['current_request'],
               'public_conversation': public_history(new)}
    if new.get('analysis_mode', 'joint') == 'flat_compact':
        from .compact_analysis import analyze_flat
        if new.get('relation_context'):
            payload['relation_context_turns'] = new['turns']
        frame, fragments, result, edges, source_inventory = analyze_flat(call, payload, context)
        metadata = {'segmentation': {'mode': 'flat_compact', 'verbatim': True,
                                     'inventory': source_inventory},
                    'analysis_contract': 'flat_compact_v3', 'relations': edges}
    else:
        frame, fragments, result = validated_call(
            call, 'round_analysis', ROUND_ANALYZE, payload,
            lambda raw: round_view(raw, new['current_request'], context))
        metadata = {'segmentation': {'mode': 'joint_requirements', 'verbatim': False},
                    'analysis_contract': 'joint_requirements_v1'}
    items, questions = {}, []
    for row in result['items']:
        iid = row['item_id']
        cols = sorted({c for b in row['bindings'] for c in b})
        item = {**row, **fragments[iid], 'required_columns': cols,
                'denied_columns': sorted(set(cols) - set(new['allowed']))}
        items[iid] = item
        for dimension in row['missing']:
            focus = row['question_focus'][dimension]
            key_parts = [dimension, ' '.join(focus.split()).casefold()]
            if new.get('analysis_mode') == 'flat_compact':
                key_parts += [item['role'], ' '.join(item['span'].split()).casefold()]
            questions.append({'item': item, 'dimension': dimension, 'focus': focus,
                              'decision_type': protocol(item, dimension)['type'],
                              'key': signature(*key_parts)})
        if item['denied_columns'] and new.get('update_mode') != 'propagation':
            key = signature(item['role'], item['span'], item['denied_columns'])
            new['permission_issues'][key] = item
    # Access evidence survives a reconstruction that omits a restricted part.
    # Only acceptance of an explicitly scoped replacement discharges that issue.
    for key, item in new['permission_issues'].items():
        questions.append({'item': item, 'dimension': 'permission', 'focus':
                          'Which permitted requirement would you like for this part?', 'key': key})
    # A fresh inventory invalidates domain proofs from the previous analysis.
    new['menu_blockers'] = {}
    new['menu_frontiers'] = {}
    new['view'] = {'frame': frame.to_json(), **metadata, 'items': items,
                   'questions': questions, 'deferred': [], 'candidate_audit': []}
    if new.get('analysis_mode') == 'flat_compact':
        from .flat_balance import plan_balance
        if new.get('update_mode') == 'propagation':
            from .session_update import finish_view, annotate_shared
            new['view']['inventory_id'] = signature('inventory', new['original_request'],
                                                    len(new['turns']), frame.to_json())
            new['view']['update_audit'] = fallback_audit or {'mode': 'initial_full'}
            new = finish_view(new, context)
            annotate_shared(call, new)
            from .answer_effects import record_dependency_update
            return record_dependency_update(new, previous_view)
        if new.get('relation_context'):
            from .relation_context import neighbor_context, change_audit
            evidence, skipped = neighbor_context(new['turns'], fragments)
            new['view']['relation_context'] = {'matched': evidence, 'skipped': skipped,
                'changes': change_audit(new['turns'], evidence, items)}
        new['view']['balance'] = plan_balance(new, context)
        if new.get('question_policy') == 'expected_gain':
            from .gain_policy import estimate_gain
            new['view']['gain'] = estimate_gain(call, new)
    return new


def next_step(session):
    turn = len(session['turns'])
    action = session['turns'][-1]['answer']['action'] if turn else None
    if action == 'refuse':
        return {'action': 'refuse', 'reason': 'user_refused', 'turn': turn}
    if any(t.get('menu_rejected') for t in session['turns']):
        return {'action': 'blocked', 'reason': 'no_authorized_choice_accepted', 'turn': turn}
    if session['view'] is None:
        return {'action': 'analyze', 'turn': turn}
    eligible = []
    from .hierarchical_questions import ready
    for q in session['view']['questions']:
        past = [t for t in session['turns'] if t['decision_key'] == q['key']]
        if (q['key'] in session['view']['deferred'] or (not session.get('strict_menus') and len(past) >= 2)
                or any(t['answer']['action'] == 'unanswerable' for t in past)):
            continue
        if ready(session, q):
            eligible.append(q)
    submitted = action == 'submit' or session.get('submit_requested', False)
    if eligible and not submitted:
        navigating = [q for q in eligible if q['key'] in session.get('menu_frontiers', {})]
        if navigating:
            q = navigating[0]
        elif session['view'].get('balance'):
            from .flat_balance import question_key
            q = min(eligible, key=lambda q: question_key(session, q))
        else:
            rank = {'scope': 0, 'field': 1, 'permission': 1, 'operation': 2, 'value': 3}
            q = min(eligible, key=lambda q: (rank[q['dimension']],
                    requirement_position(session, q['item'])))
        pending = session['pending']
        menu = pending and pending['turn'] == turn and pending['decision_key'] == q['key']
        strict_menu = session.get('strict_menus') and q['dimension'] in ('scope', 'field')
        return {'action': 'menu' if menu or strict_menu or q['dimension'] == 'permission' else 'ask',
                'turn': turn, 'question_id': q['key']}
    if session['permission_issues'] or session.get('menu_blockers'):
        return {'action': 'blocked', 'reason': 'unresolved_permission', 'turn': turn}
    return {'action': 'deliver', 'turn': turn, 'reason': 'user_requested_submission' if submitted
            else 'remaining_uncertainty' if session['view']['questions'] or any(
                r['status'] == 'unresolved' for r in session['view']['items'].values()) else 'no_pending_questions'}


def decision(session, plan):
    if plan != next_step(session) or plan['action'] not in ('ask', 'menu'):
        raise ValueError('Stale or invalid interaction')
    return next(q for q in session['view']['questions'] if q['key'] == plan['question_id'])


def public_question(session, plan):
    q = decision(session, plan)
    if session.get('strict_menus') and q['dimension'] in ('scope', 'field', 'permission'):
        raise ValueError('Scope and field decisions must use a prepared authorized menu')
    question = {'question': 'Regarding "' + q['item']['span'] + '": ' + q['focus'],
                'focus': q['focus'], 'options': [], 'multi_select': False}
    if q.get('open_bundle'):
        question['requested_dimensions'] = q['requested_dimensions']
    return question


def menu_dependency_columns(source, decision, dimension):
    """Keep established dependencies, excluding the purpose being replaced.

    A compiler's table search scope and proposed columns are not commitments.
    Permission menus replace their local denied purpose; other established
    requirements in the same owner still constrain every offered alternative.
    """
    required = set(source['required_columns'])
    if dimension == 'permission':
        required -= set(decision['required_columns'])
    return sorted(required)


def connected_options(session, context, target, cols):
    graph, error = _authorized_graph(context, session['allowed'])
    if error:
        raise ValueError(error)
    columns = indexes(context)[1]
    dependencies = set(cols)
    explicit_menu = 'menu_required_columns' in target
    if explicit_menu:
        dependencies.update(target['menu_required_columns'])
    if not dependencies <= set(session['allowed']):
        return False
    needed = {columns[c] for c in dependencies}
    if not explicit_menu and session.get('analysis_mode') == 'flat_compact':
        # Cross-table storage is valid only with an authorized path back to
        # the requested entity, even when no other item is clear yet.
        needed.update(target['scope_tables'])
    for other in session['view']['items'].values():
        if other['span'] != target['span'] and not other['missing'] and not other['denied_columns']:
            needed.update(columns[c] for c in other['required_columns'])
    return _connected(graph, needed)


def install_menu(session, q, options, mode):
    new = deepcopy(session)
    selection = ('Select one or more options for this part.' if mode == 'return_fields'
                 else 'Select one option for this decision.')
    public = {'question': 'Regarding "' + q['item']['span'] + '": ' + q['focus'] + ' ' + selection,
        'focus': q['focus'], 'options': [{'id': o['id'], 'label': o['text']} for o in options],
        'multi_select': mode == 'return_fields'}
    if new.get('menu_notes'):
        public['allow_note'] = True
        public['question'] += (' You may add details about your selected options for this part; '
                               'leave the note empty if you have nothing to add.')
    new['pending'] = {'turn': len(new['turns']), 'decision_key': q['key'],
                      'public': public, 'options': options, 'mode': mode}
    if new.get('strict_menus') and mode == 'complete':
        if len(options) > 25:
            raise ValueError('Too many business options for letter IDs and none-of-these')
        none_id = next(chr(65 + i) for i in range(26) if chr(65 + i) not in {o['id'] for o in options})
        public['options'].append({'id': none_id, 'label': 'None of these meets my requirement'})
        new['pending']['none_id'] = none_id
    return new, public


def prepare_field_menu(session, plan, context, max_options=5):
    q = decision(session, plan)
    if session.get('strict_menus'):
        return session, None  # Dynamic hierarchy is prepared by prepare_menu.
    if q.get('open_bundle'):
        return session, None
    item = q['item']
    if session['view'].get('balance'):
        options = session['view']['balance']['options'].get(q['key'], [])
        rule = protocol(item, q['dimension'])
        if q['dimension'] != 'field' or rule['mode'] == 'open' or not options:
            return session, None
        mode = rule['mode']
        return install_menu(session, q, options, mode)
    if q['dimension'] != 'field' or item['denied_columns'] or set(item['missing']) & {'scope', 'operation'}:
        return session, None
    domain = field_domain_explicit(item, session['allowed'], context, max_options=max_options)
    if not domain['shown'] or not connected_options(session, context, item, domain['columns']):
        return session, None
    options = [{'id': o['id'], 'text': o['business_meaning'], 'required_columns': [c]}
               for o, c in zip(domain['options'], domain['columns'])]
    mode = protocol(item, q['dimension'])['mode']
    if mode == 'open':
        return session, None
    return install_menu(session, q, options, mode)


def prepare_menu(call, session, plan, context, max_options=5):
    q = decision(session, plan)
    if session.get('strict_menus') and q['dimension'] in ('scope', 'field', 'permission'):
        from .hierarchical_questions import prepare
        return prepare(call, session, plan, context, max_options)
    if session['pending'] and session['pending']['decision_key'] == q['key']:
        return session, deepcopy(session['pending']['public'])
    target = q['item']
    columns = indexes(context)[1]
    # Scope is a business anchor, not a storage-table whitelist. Apply the
    # same authorized connectivity rule used by the ordinary field menus.
    scoped = [c for c in session['allowed'] if target['scope_tables']
              and connected_options(session, context, target, [c])]
    def check(raw):
        if (not isinstance(raw, dict) or set(raw) != {'options'} or not isinstance(raw['options'], list)
                or len(raw['options']) > max_options):
            raise ValueError('Expected bounded options array')
        accepted, rejected = [], []
        for o in raw['options']:
            if (not isinstance(o, dict) or set(o) != {'text', 'required_columns'}
                    or not isinstance(o['text'], str) or not o['text'].strip()
                    or not isinstance(o['required_columns'], list)
                    or any(not isinstance(c, str) for c in o['required_columns'])):
                raise ValueError('Invalid candidate')
            cols = set(o['required_columns'])
            if (not cols or not cols <= set(scoped)
                    or not connected_options(session, context, target, cols)):
                rejected.append(o)
            elif o['text'] not in [x['text'] for x in accepted]:
                accepted.append({'id': chr(65 + len(accepted)), **o})
        return {'options': accepted, 'rejected': rejected}
    payload = {'current_request': session['current_request'], 'public_conversation': public_history(session),
               'target': target, 'scope_tables': target['scope_tables'], 'allowed_columns': scoped,
               'primary_scope_columns': scoped, 'max_options': max_options,
               'schema': {k: context[k] for k in ('tables', 'foreign_keys')},
               'column_meanings': {c: context.get('column_language', {}).get(c, '') for c in scoped}}
    result = validated_call(call, 'round_candidates', SESSION_CANDIDATES, payload, check) if scoped else {
        'options': [], 'rejected': []}
    new = deepcopy(session)
    new['view']['candidate_audit'].append({'decision_key': q['key'], **result})
    if not result['options']:
        new['view']['deferred'].append(q['key'])
        return new, None
    return install_menu(new, q, result['options'], 'complete')


def commit_answer(call, session, plan, question, answer, context):
    q = decision(session, plan)
    if session.get('strict_menus') and session['pending'] and session['pending']['mode'] == 'hierarchical':
        from .hierarchical_questions import commit
        return commit(call, session, plan, question, answer, context)
    if session.get('strict_menus') and q['dimension'] in ('scope', 'field', 'permission'):
        if session['pending'] and session['pending']['mode'] == 'complete':
            pass  # Read old prepared menus; new strict menus are hierarchical.
        else:
            raise ValueError('Scope, fields and permission require menu selection')
    if (not isinstance(answer, dict) or not set(answer) <= {'action', 'text', 'choices', 'module_ids'}
            or not {'action', 'text', 'choices'} <= set(answer)
            or not isinstance(answer['text'], str) or not isinstance(answer['choices'], list)
            or any(not isinstance(c, str) for c in answer['choices'])):
        raise ValueError('Invalid answer')
    menu = session['pending'] if plan['action'] == 'menu' else None
    expected = menu['public'] if menu else public_question(session, plan)
    if question != expected:
        raise ValueError('Question was not shown by this runner')
    action = answer['action']
    selected = []
    menu_rejected = False
    if action in ('unanswerable', 'refuse', 'submit'):
        if answer['text'] or answer['choices']:
            raise ValueError('Control answer must have empty payload')
    elif action == 'answer' and not menu and answer['text'].strip() and not answer['choices']:
        pass
    elif action == 'choose' and menu and (not answer['text'] or question.get('allow_note') is True):
        choices = answer['choices']
        options = {o['id']: o for o in menu['options']}
        none_id = menu.get('none_id')
        valid_ids = set(options) | ({none_id} if none_id else set())
        if (not choices or len(set(choices)) != len(choices) or not set(choices) <= valid_ids
                or (not question['multi_select'] and len(choices) != 1)):
            raise ValueError('Invalid menu choice')
        menu_rejected = none_id is not None and none_id in choices
        if menu_rejected and (len(choices) != 1 or answer['text']):
            raise ValueError('None-of-these is exclusive and cannot include an acceptance note')
        selected = [] if menu_rejected else [options[c] for c in choices]
        if any(not set(o['required_columns']) <= set(session['allowed']) for o in selected):
            raise ValueError('Unauthorized choice')
        if (selected and session.get('analysis_mode') == 'flat_compact'
                and not connected_options(session, context, q['item'],
                    {c for o in selected for c in o['required_columns']})):
            raise ValueError('Selected columns are not jointly connected by authorized keys')
    else:
        raise ValueError('Answer does not match interaction')
    menu_rejected = menu_rejected or bool(session.get('strict_menus') and menu and action == 'unanswerable')
    new = deepcopy(session)
    turn = {'question': deepcopy(question), 'answer': deepcopy(answer), 'decision_key': q['key']}
    if q.get('decision_ids'):
        turn['decision_ids'] = list(q['decision_ids'])
    if menu_rejected:
        turn.update(answer={'action': 'unanswerable', 'text': '', 'choices': []},
                    menu_rejected=True, rejection_basis='no_authorized_choice_accepted')
        new.setdefault('menu_blockers', {})[q['key']] = q['item']['item_id']
    if session.get('analysis_mode') == 'flat_compact':
        from .relation_context import direct_neighbors, capture_neighbors
        turn.update(target_fragment=q['item']['span'], target_role=q['item']['role'],
                    asked_dimension=q['dimension'], related_fragments=direct_neighbors(session, q['item']))
        if session.get('update_mode') == 'propagation':
            turn.update(target_id=q['item']['item_id'], requested_slots=q.get('requested_slots', []))
            if q.get('requested_dimensions'):
                turn['requested_dimensions'] = q['requested_dimensions']
        if session.get('relation_context') and action in ('answer', 'choose'):
            turn['neighbor_snapshots'] = capture_neighbors(session, q['item'])
    if selected:
        turn.update(selected_options=deepcopy(selected), selection_mode=menu['mode'])
        if menu['mode'] == 'complete':
            new['permission_issues'].pop(q['key'], None)
    new['turns'].append(turn)
    if new.get('strict_menus'):
        new['public_interactions'].append({'question': deepcopy(question), 'answer': deepcopy(answer)})
        if action in ('answer', 'choose') and not menu_rejected:
            new['menu_frontiers'] = {}
    new['pending'] = None
    if action in ('answer', 'choose') and not menu_rejected:
        if session.get('update_mode') == 'propagation':
            new['update_base'] = deepcopy(session['view'])
        new['view'] = None
    return new


def render_session(call, session):
    plan = next_step(session)
    if plan['action'] != 'deliver':
        permission_blocked = plan['action'] == 'blocked'
        return {'action': 'refuse' if plan['action'] in ('refuse', 'blocked') else 'system_error',
                'reason': 'permission_blocked' if permission_blocked else plan.get('reason', 'unfinished_session'),
                'text': '', 'available_for_text2sql': False,
            'access_control': 'blocked' if permission_blocked else 'not_evaluated'}
    return {'action': 'deliver', 'reason': plan['reason'], 'text': session['current_request'],
            'available_for_text2sql': True, 'execution_authorized': False, 'requires_sql_permission_check': True,
            'access_control': 'no_detected_block',
            'remaining_questions': session['view']['questions'],
            'method': 'history_with_round_structure' if session['turns'] else 'unchanged_original'}


def authorize_session_sql(session, context, sql):
    if (next_step(session)['action'] != 'deliver'
            or session['view'] is None or session['permission_issues'] or session.get('menu_blockers')
            or any(t.get('menu_rejected') for t in session['turns'])):
        return {'sql_authorized': False, 'reasons': [{'code': 'pending_permission_or_analysis'}]}
    return validate_sql_dependencies(sql, context, session['allowed'])
