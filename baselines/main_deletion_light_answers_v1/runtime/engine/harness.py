"""Main-method orchestration without historical CLI branches."""
from copy import deepcopy

def run_session(call, request, context, db_name, allowed, ask_user, *, max_turns=12,
                save=lambda: None, episode=None, mode='structured', analysis_mode='joint', relation_context=False, menu_notes=False,
                question_policy='heuristic', update_mode='full', local_bundle=False, method=None):
    """UI agnostic; private simulator reference is never a session input."""
    if method not in (None, 'main', 'baseline'):
        raise ValueError('Unknown method')
    if method == 'main':
        mode, analysis_mode, update_mode, local_bundle = 'simple', 'flat_compact', 'propagation', True
        relation_context, question_policy = False, 'heuristic'
    episode = {} if episode is None else episode
    if method != 'main':
        raise ValueError('This extracted harness runs the main method only')
    from . import simple_session as engine
    episode.update(request=request, snapshots=[], interactions=[])
    if analysis_mode != 'joint' and mode != 'simple':
        raise ValueError('flat_compact analysis requires mode=simple')
    if (update_mode != 'full' or local_bundle) and mode != 'simple':
        raise ValueError('Graph updates require mode=simple')
    if relation_context and (mode != 'simple' or analysis_mode != 'flat_compact'):
        raise ValueError('relation_context requires mode=simple and flat_compact analysis')
    if type(menu_notes) is not bool or (menu_notes and mode != 'simple'):
        raise ValueError('menu_notes requires mode=simple')
    if (question_policy not in ('heuristic', 'expected_gain')
            or (question_policy == 'expected_gain' and (mode != 'simple' or analysis_mode != 'flat_compact'))):
        raise ValueError('expected_gain requires mode=simple and flat_compact analysis')
    session = engine.start_session(call, request, context, db_name, allowed,
        **({'analysis_mode': analysis_mode, 'relation_context': relation_context,
            'menu_notes': menu_notes, 'question_policy': question_policy,
            'update_mode': update_mode, 'local_bundle': local_bundle,
            'strict_menus': method == 'main'} if mode == 'simple' else {}))
    episode['method'] = {'name': 'main' if method == 'main' else 'configured', 'mode': mode,
                        'analysis_mode': analysis_mode, 'update_mode': update_mode,
                        'local_bundle': local_bundle, 'question_policy': question_policy,
                        'strict_menus': method == 'main'}
    episode['session'] = session
    save()
    session = engine.analyze_session(call, session, context)
    episode['snapshots'].append(deepcopy(session))
    # Empty, deferred permission menus are internal work, not user interactions.
    while True:
        episode['session'] = session
        save()
        plan = engine.next_step(session)
        if plan['action'] not in ('ask', 'menu'):
            break
        if plan['action'] == 'menu':
            session, question = engine.prepare_menu(call, session, plan, context)
            if question is None:
                if mode == 'simple' and question_policy == 'expected_gain':
                    session = engine.analyze_session(call, session, context)
                if engine.next_step(session) == plan:
                    raise ValueError('Empty menu did not advance or defer its decision')
                continue
        else:
            session, question = engine.prepare_field_menu(session, plan, context)
            if question is None:
                question = engine.public_question(session, plan)
            else:
                plan = engine.next_step(session)
        # Finish internal routing even at the interaction limit. A refreshed
        # permission menu can be empty and lead to a valid refusal or delivery.
        if len(episode['interactions']) >= max_turns:
            break
        public_turns = (deepcopy(session['public_interactions']) if session.get('strict_menus') else
                        [{'question': deepcopy(t['question']), 'answer': deepcopy(t['answer'])}
                         for t in session['turns']])
        answer = ask_user(deepcopy(question), public_turns)
        episode['interactions'].append({'plan': plan, 'question': question, 'answer': answer})
        save()
        engine_answer = {k: answer[k] for k in ('action', 'text', 'choices')}
        session = engine.commit_answer(call, session, plan, question, engine_answer, context)
        episode['session'] = session
        episode['snapshots'].append(deepcopy(session))
        save()
        session = engine.analyze_session(call, session, context)
        episode['snapshots'].append(deepcopy(session))
    episode['session'] = session
    if engine.next_step(session)['action'] in ('ask', 'menu', 'analyze'):
        episode['delivery'] = {'action': 'system_error', 'reason': 'experiment_turn_budget',
                               'available_for_text2sql': False, 'text': ''}
    else:
        episode['delivery'] = engine.render_session(call, session)
    save()
    return episode['delivery']

