"""Opt-in model-estimated decision gain. Forecasts schedule; never update intent.

The loss is the count of fixed unresolved decisions in this round. A forecast
gives each question's marginal probability of resolving each eligible decision
through explicit, authorized information. Expected reduction is their sum;
independence is not assumed. Probabilities are uncalibrated model estimates,
not Shannon entropy, observed recovery, or a guarantee of user cooperation.
"""
from copy import deepcopy
import math

from .analysis import validated_call
from .question_types import protocol


GAIN_PROMPT = '''You are estimating the usefulness of clarification questions.
Use only the current request, public history, unresolved decisions, and the actual
question wording/options supplied. Forecast a cooperative user's next reply;
their private intention and willingness to accept alternatives are unknown.

For each question, estimate p_resolved for decisions a reply might settle with
explicit information that can pass the supplied access constraints. Include
partial/unanswerable/refusing replies in these marginal probabilities. A field
choice alone settles no unstated operator/value; optional notes are not promised.
An open reply may settle several coupled decisions when the question invites it.
Settling scope may help interpret a neighbor, but a graph edge alone settles
nothing. Count only direct semantic effects explainable from the actual question.
Fewer accessible options does not make an intention clear or an option acceptable.
Already determined decisions are absent. A rejected requirement remains unresolved.
Do not infer success from a plausible guess, deletion, permission filtering, or
unrelated information volunteered by a user. Do not generate answers or new facts.

Return JSON only: {"forecasts":[{"question_id":"...","effects":[
{"decision_id":"...","p_resolved":0.0,"reason":"brief causal explanation"}]}]}.
Include every question once; effects may be empty and omitted decisions mean zero.
Use only IDs in potential_decisions for that question. Probabilities are finite
numbers in [0,1]. Explain why this exact question can elicit the information.
'''


def eligible_questions(session):
    for q in session['view']['questions']:
        past = [t for t in session['turns'] if t['decision_key'] == q['key']]
        if (q['key'] not in session['view']['deferred'] and (session.get('strict_menus') or len(past) < 2)
                and not any(t['answer']['action'] == 'unanswerable' for t in past)):
            yield q


def state_key(session):
    """Invalidate forecasts when public evidence, policy or prepared menus change."""
    from .simple_session import signature, public_history
    view = session['view']
    return signature(session['current_request'], public_history(session), session['allowed'],
        session.get('menu_notes', False), view['questions'], view['relations'],
        view['balance']['options'], view['deferred'])


def forecast_payload(session):
    from .simple_session import signature, public_history, install_menu
    questions = list(eligible_questions(session))
    # Permissions have dynamically generated complete options. Their actual menu
    # is not available at ranking time; retain existing routing for this round.
    if any(q['dimension'] == 'permission' for q in questions):
        return None, 'permission_menu_not_materialized'
    if len(questions) < 2:
        return None, 'fewer_than_two_eligible_questions'
    decisions, question_decisions = {}, {}
    for q in questions:
        item = q['item']
        # Same span/role/dimension is one decision despite duplicated IDs/focus.
        did = signature(item['role'], ' '.join(item['span'].split()).casefold(), q['dimension'])
        question_decisions[q['key']] = did
        decisions.setdefault(did, {'decision_id': did, 'role': item['role'],
            'span': item['span'], 'dimension': q['dimension']})
    rows = []
    edges = session['view']['relations']['edges']
    for q in questions:
        item = q['item']
        mode = protocol(item, q['dimension'])['mode']
        options = session['view']['balance']['options'].get(q['key'], [])
        menu = q['dimension'] == 'field' and mode != 'open' and bool(options)
        if menu:
            _, public = install_menu(session, q, options, mode)
        else:
            public = {'question': 'Regarding "' + item['span'] + '": ' + q['focus'],
                      'focus': q['focus'], 'options': [], 'multi_select': False}
        adjacent = {item['item_id']}
        for e in edges:
            if e['source_id'] == item['item_id']:
                adjacent.add(e['target_id'])
            if e['target_id'] == item['item_id']:
                adjacent.add(e['source_id'])
        potential = sorted({question_decisions[o['key']] for o in questions
                            if o['item']['item_id'] in adjacent})
        # A bare field menu cannot establish values, operations or scope of its
        # own item. No optional-note uplift is assigned deterministically.
        if menu and not public.get('allow_note'):
            potential = [did for did in potential if did == question_decisions[q['key']]
                or any(question_decisions[o['key']] == did and
                       o['item']['item_id'] != item['item_id'] for o in questions)]
        rows.append({'question_id': q['key'], 'target_decision': question_decisions[q['key']],
            'item_id': item['item_id'], 'kind': item['kind'], 'public_question': public,
            'interaction': 'menu' if menu else 'open', 'potential_decisions': potential,
            'access': {'determined_denied': bool(item['denied_columns']),
                       'displayed_options_authorized': bool(menu),
                       'open_answer_requires_mapping_and_authorization': True}})
    return {'current_request': session['current_request'], 'public_history': public_history(session),
            'decisions': list(decisions.values()), 'questions': rows,
            'relations': deepcopy(session['view']['relations']),
            'items': [{'item_id': i['item_id'], 'span': i['span'], 'role': i['role'],
                       'kind': i['kind'], 'missing': list(i['missing']),
                       'scope_tables': list(i['scope_tables']),
                       'required_columns': list(i['required_columns']),
                       'denied_columns': list(i['denied_columns'])}
                      for i in session['view']['items'].values()],
            'allowed_columns': list(session['allowed'])}, None


def validate_forecasts(raw, payload):
    if not isinstance(raw, dict) or set(raw) != {'forecasts'} or not isinstance(raw['forecasts'], list):
        raise ValueError('Expected forecasts array')
    expected = {q['question_id']: q for q in payload['questions']}
    parsed = {}
    for row in raw['forecasts']:
        if not isinstance(row, dict) or set(row) != {'question_id', 'effects'}:
            raise ValueError('Expected question_id/effects')
        qid = row['question_id']
        if not isinstance(qid, str) or qid not in expected or qid in parsed:
            raise ValueError('Unknown or duplicate question_id')
        if not isinstance(row['effects'], list):
            raise ValueError('effects must be an array')
        seen, effects = set(), []
        for e in row['effects']:
            if not isinstance(e, dict) or set(e) != {'decision_id', 'p_resolved', 'reason'}:
                raise ValueError('Expected decision_id/p_resolved/reason')
            did, probability = e['decision_id'], e['p_resolved']
            if (not isinstance(did, str) or did in seen or did not in expected[qid]['potential_decisions']):
                raise ValueError('Duplicate or ineligible effect decision')
            if (type(probability) not in (int, float) or not math.isfinite(probability)
                    or not 0 <= probability <= 1):
                raise ValueError('p_resolved must be finite and in [0,1]')
            if not isinstance(e['reason'], str) or not e['reason'].strip():
                raise ValueError('Effect requires a brief explanation')
            seen.add(did)
            effects.append(deepcopy(e))
        gain = sum(e['p_resolved'] for e in effects)
        parsed[qid] = {'expected_resolved_decisions': gain, 'effects': effects,
                       'question_cost': 1, 'gain_per_question': gain}
    if set(parsed) != set(expected):
        raise ValueError('Missing question IDs: ' + ', '.join(sorted(set(expected) - set(parsed))))
    return parsed


def estimate_gain(call, session):
    """One batched forecast call, never calls the simulator or modifies knowns."""
    key = state_key(session)
    payload, reason = forecast_payload(session)
    if reason:
        return {'state_key': key, 'status': 'fallback', 'reason': reason, 'scores': {}}
    try:
        scores = validated_call(call, 'question_gain', GAIN_PROMPT, payload,
                                lambda raw: validate_forecasts(raw, payload))
    except (ValueError, TypeError, KeyError) as exc:
        return {'state_key': key, 'status': 'fallback', 'reason': 'invalid_gain_contract',
                'error': str(exc), 'scores': {}}
    return {'state_key': key, 'status': 'estimated', 'scores': scores,
            'decision_inventory': payload['decisions'],
            'assumptions': {'cost': 'one user question', 'decision_weight': 1,
                            'probabilities': 'uncalibrated model forecasts',
                            'scope': 'target and direct linguistic neighbors'},
            'meaning': 'sum of marginal authorized-resolution probabilities; not entropy or measured recovery'}


def score_for(session, q):
    forecast = session['view'].get('gain', {})
    if forecast.get('status') != 'estimated' or forecast.get('state_key') != state_key(session):
        return None
    return forecast['scores'].get(q['key'], {}).get('gain_per_question')


def refresh_gain(call, session):
    """Refresh once per public state, outside pure planning/answer validation."""
    if (session.get('question_policy') != 'expected_gain' or session['view'] is None
            or (session['turns'] and session['turns'][-1]['answer']['action'] in ('submit', 'refuse'))):
        return session
    if session['view'].get('gain', {}).get('state_key') == state_key(session):
        return session
    new = deepcopy(session)
    new['view']['gain'] = estimate_gain(call, new)
    return new
