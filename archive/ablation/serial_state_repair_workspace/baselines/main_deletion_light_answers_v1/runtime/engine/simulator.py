"""Private source-constrained simulator adapter; evidence is never agent context."""


def scope_payload(frame, relations, item_id):
    """Experimental projection: source spans and typed edges, never grounding.

    Edges describe interpretation, not independence or permission. Preserve their
    direction and allow overlapping spans instead of declaring them disjoint.
    """
    rows = {}
    for role, fragments in (('output', frame.outputs), ('modifier', frame.modifiers)):
        for fragment in fragments:
            iid, span = fragment['item_id'], fragment['span']
            if iid in rows or span not in frame.original_text:
                raise ValueError('Invalid linguistic fragment')
            rows[iid] = {'item_id': iid, 'span': span, 'role': role}
    if item_id not in rows:
        raise ValueError('Unknown target item')
    edges = []
    for edge in relations.get('edges', []):
        source, target, kind = edge['source_id'], edge['target_id'], edge['kind']
        if source not in rows or target not in rows or kind not in ('context', 'applies_to'):
            raise ValueError('Invalid linguistic relation')
        if item_id in (source, target):
            edges.append({'source_id': source, 'target_id': target, 'kind': kind})
    return {'target_fragment': rows[item_id],
            'other_fragments': [row for iid, row in rows.items() if iid != item_id],
            'incident_relations': edges}


def option_column_bindings(options):
    """Collect physical field bindings, including multi-column candidate bundles."""
    result = {}
    for row in options:
        columns = []
        if isinstance(row.get('column'), str):
            columns.append(row['column'])
        item = row.get('item', {})
        for bundle in item.get('bindings', row.get('bindings', [])):
            if isinstance(bundle, list):
                columns.extend(column for column in bundle if isinstance(column, str))
        if columns:
            result[row['id']] = sorted(set(columns))
    return result


def extract_answer(raw, original, public, multi_select=False, *, allow_legacy_refusal=True,
                   acceptable_columns=None, private_option_columns=None):
    if not isinstance(raw, dict) or set(raw) != {'action', 'quotes', 'choices'}:
        raise ValueError('Expected action/quotes/choices')
    action, quotes, choices = raw['action'], raw['quotes'], raw['choices']
    if not isinstance(quotes, list) or not isinstance(choices, list):
        raise ValueError('quotes and choices must be arrays')
    if any(not isinstance(x, str) for x in quotes + choices):
        raise ValueError('quotes and choices must contain strings')
    if action == 'answer':
        if public['options']:
            raise ValueError('A menu requires action=choose with offered choice IDs or action=unanswerable; do not return an open answer.')
        if choices or len(quotes) != 1 or not quotes[0].strip() or quotes[0] not in original:
            raise ValueError('Open answer requires one exact source phrase')
        return {'action': action, 'text': quotes[0], 'choices': []}
    if action == 'choose':
        ids = {o['id'] for o in public['options']}
        if quotes or not choices or len(set(choices)) != len(choices) or not set(choices) <= ids:
            raise ValueError('Invalid menu choices')
        if len(choices) > 1 and not multi_select:
            raise ValueError('Single-choice menu')
        if acceptable_columns is not None:
            bindings = private_option_columns or {}
            if any(not (set(bindings.get(choice, [])) & set(acceptable_columns)) for choice in choices):
                raise ValueError('Selected option is outside the annotated user intention')
        return {'action': action, 'text': '', 'choices': choices}
    if action == 'unanswerable' and not quotes and not choices:
        return {'action': action, 'text': '', 'choices': []}
    if action == 'refuse' and not quotes and not choices:
        if allow_legacy_refusal:
            return {'action': action, 'text': '', 'choices': []}
        raise RuntimeError('Legacy refusal is disabled in the current requester protocol')
    raise ValueError('Invalid simulator action/payload')


def free_answer(raw, public, multi_select=False, *, acceptable_columns=None,
                private_option_columns=None):
    """Validate the free-language simulator without treating absence as refusal."""
    if not isinstance(raw, dict) or set(raw) != {'action', 'text', 'choices'}:
        raise ValueError('Expected action/text/choices')
    action, text, choices = raw['action'], raw['text'], raw['choices']
    if not isinstance(text, str) or not isinstance(choices, list) or any(
            not isinstance(choice, str) for choice in choices):
        raise ValueError('Invalid simulator answer types')
    if action in ('unanswerable', 'refuse') and not text and not choices:
        return raw
    if action == 'answer' and not public['options'] and text.strip() and not choices:
        return raw
    if (action == 'choose' and public['options'] and choices
            and (not text or public.get('allow_note') is True)):
        ids = {option['id'] for option in public['options']}
        if len(set(choices)) == len(choices) and set(choices) <= ids and (
                multi_select or len(choices) == 1):
            if acceptable_columns is not None:
                bindings = private_option_columns or {}
                if any(not (set(bindings.get(choice, [])) & set(acceptable_columns))
                       for choice in choices):
                    raise ValueError('Selected option is outside the annotated user intention')
            return raw
    raise ValueError('Simulator answer does not match the current question')


def requester_answer(raw, public, *, flat_units=(), original=None, history=(), allow_refusal=False):
    """Validate the strict requester protocol and return the engine payload.

    Module IDs state which flat linguistic units the answer addresses. They are
    evidence boundaries, not a second semantic judge.
    """
    if not isinstance(raw, dict) or set(raw) != {'action', 'answer', 'selected_ids', 'module_ids'}:
        raise ValueError('Expected action/answer/selected_ids/module_ids')
    module_ids = raw['module_ids']
    valid_modules = {u['item_id'] for u in flat_units} or set(module_ids)
    if (not isinstance(module_ids, list) or not module_ids or
            any(not isinstance(x, str) or x not in valid_modules for x in module_ids) or
            len(set(module_ids)) != len(module_ids)):
        raise ValueError('module_ids must name one or more supplied flat units')
    if raw['action'] == 'refuse' and not allow_refusal:
        raise ValueError('Strict requester has no explicit intention to refuse; use unanswerable')
    if raw['action'] == 'answer' and (not isinstance(raw['answer'], str) or not raw['answer'].strip()):
        raise ValueError('Open answer must be nonempty')
    return free_answer({'action': raw['action'], 'text': raw['answer'],
                        'choices': raw['selected_ids']}, public,
                       public.get('multi_select', False))


def requester_selection(raw, public, flat_units):
    """Validate the first, non-linguistic selection call."""
    if not isinstance(raw, dict) or set(raw) != {'action', 'module_ids', 'selected_ids'}:
        raise ValueError('Expected action/module_ids/selected_ids')
    ids = raw['module_ids']
    valid = {u['item_id'] for u in flat_units}
    if (not isinstance(ids, list) or any(not isinstance(x, str) for x in ids)
            or len(set(ids)) != len(ids) or not set(ids) <= valid
            or (raw['action'] != 'unanswerable' and not ids)):
        raise ValueError('module_ids must name supplied flat units')
    if raw['action'] == 'unanswerable':
        if raw['selected_ids'] != []:
            raise ValueError('Unanswerable cannot select menu options')
        return raw
    if raw['action'] == 'answer':
        if raw['selected_ids'] != []:
            raise ValueError('Answer cannot select menu options')
        if public.get('options'):
            raise ValueError('Menu requires choose or unanswerable')
        return raw
    if raw['action'] == 'choose':
        if not public.get('options') or not isinstance(raw['selected_ids'], list):
            raise ValueError('Choose requires offered options')
        offered = {o['id'] for o in public['options']}
        chosen = raw['selected_ids']
        if not chosen or any(not isinstance(x, str) for x in chosen) or len(set(chosen)) != len(chosen) or not set(chosen) <= offered:
            raise ValueError('Invalid selected_ids')
        if not public.get('multi_select') and len(chosen) != 1:
            raise ValueError('Single-choice menu')
        return raw
    raise ValueError('Invalid requester action')


def requester_render(raw, *, selection, flat_units):
    if not isinstance(raw, dict) or set(raw) != {'answer'} or not isinstance(raw['answer'], str):
        raise ValueError('Expected answer only')
    if selection['action'] == 'answer' and not raw['answer'].strip():
        return {'action': 'unanswerable', 'text': '', 'choices': [],
                'module_ids': list(selection['module_ids'])}
    if selection['action'] != 'answer' and raw['answer'].strip():
        raise ValueError('Only open answers may contain rendered text')
    return {'action': selection['action'], 'text': raw['answer'],
            'choices': list(selection['selected_ids']), 'module_ids': list(selection['module_ids'])}


def public_requester_question(question):
    """Only user-visible wording and options, never bindings/focus/policy."""
    result = {'question': question['question'],
            'options': [{'id': o['id'], 'text': o.get('label', o.get('text', o.get('business_meaning', '')))}
                        for o in question.get('options', [])],
            'multi_select': bool(question.get('multi_select', False))}
    if 'request' in question:  # The actual text shown during a delivery review.
        result['request'] = question['request']
    if question.get('allow_note') is True:
        result['allow_note'] = True
    return result


def requester_payload(original, question, history):
    return {'private_original': original,
            'public_history': [
                {'question': public_requester_question(t['question']),
                 'answer': {'action': t['answer']['action'],
                            'answer': t['answer'].get('text', t['answer'].get('request', '')),
                            'selected_ids': list(t['answer'].get('choices', []))}}
                for t in history],
            'question': public_requester_question(question),
            'flat_units': []}


def review_requester_answer(call, answer, original, question, *, flat_units=()):
    """Conservative semantic gate, separate from the clarification agent.

    The reviewer is a model, not a proof of entailment. Raw decisions stay in logs.
    """
    from .analysis import validated_call
    from .prompts import STRICT_REQUESTER_REVIEW
    if answer['action'] not in ('answer', 'choose'):
        return answer

    def check(raw):
        if (not isinstance(raw, dict) or set(raw) != {'supported', 'reference_quote', 'reason'}
                or type(raw['supported']) is not bool
                or not isinstance(raw['reason'], str) or not raw['reason'].strip()
                or not isinstance(raw['reference_quote'], str)):
            raise ValueError('Invalid strict reviewer contract')
        quote = raw['reference_quote']
        if (raw['supported'] and (not quote.strip() or quote not in original)
                or not raw['supported'] and quote):
            raise ValueError('Supported replies require exact reference evidence')
        return raw['supported']

    visible = public_requester_question(question)
    # Review every selected option independently; a supported first option must
    # not authorize an unsupported extra choice.
    replies = ([{'action': 'choose', 'answer': '', 'selected_ids': [choice]}
                for choice in answer['choices']] if answer['action'] == 'choose' else
               [{'action': 'answer', 'answer': answer['text'], 'selected_ids': []}])
    decisions = [validated_call(call, 'session_user_strict_review', STRICT_REQUESTER_REVIEW,
             {'private_original': original, 'question': visible,
              'flat_units': list(flat_units), 'proposed_reply': reply}, check)
             for reply in replies]
    if not all(decisions):
        return {'action': 'unanswerable', 'text': '', 'choices': []}
    return answer


def simulate_requester(call, original, question, history, flat_units):
    """Select once, then render only selected source modules; no review call."""
    from .analysis import validated_call
    from .prompts import SESSION_SELECT, SESSION_RENDER
    base = {**requester_payload(original, question, history), 'flat_units': flat_units}
    selection = validated_call(call, 'session_user_select', SESSION_SELECT, base,
                               lambda raw: requester_selection(raw, question, flat_units))
    if selection['action'] != 'answer':
        return requester_render({'answer': ''}, selection=selection, flat_units=flat_units)
    selected = [u for u in flat_units if u['item_id'] in selection['module_ids']]
    return validated_call(call, 'session_user_render', SESSION_RENDER,
        {'question': public_requester_question(question), 'selection': selection,
         'selected_modules': selected},
        lambda raw: requester_render(raw, selection=selection, flat_units=flat_units))


def simulate_requester_with_sql(call, original, question, history, flat_units, gold_sql):
    """One generation call (plus contract retry if needed); no semantic reviewer."""
    from .analysis import validated_call
    from .prompts import SESSION_USER_GOLD

    def check(raw):
        if not isinstance(raw, dict) or set(raw) != {
                'action', 'module_ids', 'answer', 'selected_ids', 'basis'}:
            raise ValueError('Expected action/module_ids/answer/selected_ids/basis')
        selection = requester_selection({k: raw[k] for k in
            ('action', 'module_ids', 'selected_ids')}, question, flat_units)
        basis = raw['basis']
        if not isinstance(basis, str) or basis not in ('natural_language', 'gold_sql', 'both', 'none'):
            raise ValueError('Invalid answer basis')
        if (selection['action'] == 'unanswerable') != (basis == 'none'):
            raise ValueError('Use basis=none exactly for unanswerable')
        answer = free_answer({'action': selection['action'], 'text': raw['answer'],
                              'choices': selection['selected_ids']}, question,
                             question.get('multi_select', False))
        return {**answer, 'module_ids': list(selection['module_ids']), 'basis': basis}

    prompt = SESSION_USER_GOLD
    if question.get('allow_note') is True:
        from .prompts import SESSION_MENU_NOTE_RULE
        prompt = prompt.replace('answer is nonempty only\nfor answer.',
            'answer is nonempty for answer; for choose it may contain an optional note.')
        prompt += SESSION_MENU_NOTE_RULE
    return validated_call(call, 'session_user_gold', prompt,
        {**requester_payload(original, question, history), 'flat_units': flat_units,
         'private_gold_sql': gold_sql}, check)
def simulate_menu_requester(call, original, question, history, flat_units, gold_sql=None):
    """Private intent stays in the simulator; only menu IDs enter the runner."""
    from .analysis import validated_call
    from .prompts import MENU_REQUESTER

    def check(raw):
        if not isinstance(raw, dict) or set(raw) != {'selected_ids'}:
            raise ValueError('Expected selected_ids only')
        answer = free_answer({'action': 'choose', 'text': '', 'choices': raw['selected_ids']},
                             question, question.get('multi_select', False))
        none = question['options'][-1]['id']
        if none in answer['choices'] and len(answer['choices']) != 1:
            raise ValueError('None-of-these must be selected alone')
        return answer

    payload = {**requester_payload(original, question, history), 'flat_units': flat_units}
    if gold_sql:
        payload['private_gold_sql'] = gold_sql
    return validated_call(call, 'session_user_menu', MENU_REQUESTER, payload, check)


