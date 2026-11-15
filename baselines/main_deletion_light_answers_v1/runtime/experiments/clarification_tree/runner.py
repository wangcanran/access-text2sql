"""Independent tree session loop; only actual answers can expand the tree."""
from copy import deepcopy
from ...engine.analysis import validated_call
from .state import VERSION, initial, frontier, graph, require, text, validate, add_issue
from .permissions import safe, dependencies
from .planner import assess, next_step
from .transaction import commit, cancel
from . import prompts
from . import main_assist


def menu(question, labels, granularity, multiple=False):
    return dict(question=question, focus=question, granularity=granularity,
                multi_select=multiple, options=[{'id': str(i + 1), 'label': v} for i, v in enumerate(labels)] +
                [{'id': 'NONE', 'label': 'None of these / needs clarification'}])


def choices(answer, public):
    if answer.get('action') == 'unanswerable':
        return None
    ids = answer.get('choices')
    require(answer.get('action') == 'choose' and isinstance(ids, list) and ids and
            all(isinstance(i, str) for i in ids) and len(ids) == len(set(ids)) and
            set(ids) <= {o['id'] for o in public['options']}, 'Invalid menu answer')
    require(public['multi_select'] or len(ids) == 1, 'Single selection required')
    if 'NONE' in ids:
        require(len(ids) == 1, 'NONE is exclusive')
        return None
    return [int(i) - 1 for i in ids]


class Budget(Exception):
    pass


def run(call, request, context, allowed, ask_user, *, max_turns=40, episode=None, save=lambda: None):
    state = initial(request)
    episode = {} if episode is None else episode
    episode.update(method=VERSION, session=state, interactions=[], snapshots=[], transitions=[])

    def publish(new, event):
        nonlocal state
        episode['transitions'].append({'event': event, 'tree_before': deepcopy(state),
            'tree_after': deepcopy(new), 'frontier_before': list(frontier(state)),
            'frontier_after': list(frontier(new)), 'active_graph': graph(new)})
        state = new
        episode['session'] = state
        episode['snapshots'].append(deepcopy(state))
        save()

    def ask(q):
        if len(episode['interactions']) >= max_turns:
            raise Budget()
        # Private simulator metadata stays in the trace, never model interpretation.
        history = [{'question': t['question'], 'answer': {k: t['answer'].get(k, [] if k == 'choices' else '')
                    for k in ('action', 'text', 'choices')}} for t in episode['interactions']]
        answer = ask_user(deepcopy(q), deepcopy(history))
        require(isinstance(answer, dict), 'Invalid answer')
        episode['interactions'].append({'question': deepcopy(q), 'answer': deepcopy(answer)})
        save()
        return {k: deepcopy(answer.get(k, [] if k == 'choices' else '')) for k in ('action', 'text', 'choices')}

    def finish(action, reason='', rendered=''):
        result = dict(action=action, reason=reason, text=rendered,
                      available_for_text2sql=action == 'deliver', sql_execution_evaluated=False)
        episode['delivery'] = result
        save()
        return result

    publish(state, 'initial')
    try:
        while True:
            step = next_step(state, context, allowed)
            kind = step['kind']
            if kind in ('unresolved', 'refuse'):
                return finish(kind, step['reason'])
            if kind == 'deliver':
                # Strip ancestors, provenance, cancelled nodes and private history.
                payload = {'nodes': [{k: deepcopy(n[k]) for k in ('id', 'meaning', 'role', 'columns', 'answers')}
                                     for n in frontier(state).values()],
                           'relations': [r for r in state['relations'] if r['status'] == 'confirmed']}
                def rendered(raw):
                    require(isinstance(raw, dict) and set(raw) == {'text'} and text(raw['text']), 'Invalid rendering')
                    return raw['text']
                result = validated_call(call, 'tree_render', prompts.RENDER, payload, rendered)
                return finish('deliver', rendered=result)
            if kind == 'relation':
                r = step['relation']
                leaves = frontier(state)
                question = ('Does this condition apply to this result? ' if r['kind'] == 'applies_to'
                            else 'Does the first requirement depend on the second? ')
                q = menu(question + leaves[r['source']]['meaning'] + ' → ' + leaves[r['target']]['meaning'],
                         ['Yes', 'No'], 'relation_confirmation')
                picked = choices(ask(q), q)
                if picked is None:
                    # Keep the edge unresolved and route one local scope explanation.
                    owner = r['source']
                    issue = {'question': 'Please clarify the relationship between these requirements: ' +
                             leaves[owner]['meaning'] + ' / ' + leaves[r['target']]['meaning'],
                             'focus': 'relationship scope', 'granularity': 'relation', 'options': [], 'multi_select': False}
                    answer = ask(issue)
                    if answer.get('action') != 'answer' or not text(answer.get('text')):
                        return finish('unresolved', 'relation_scope_unresolved')
                    new = deepcopy(state)
                    iid = add_issue(new, owner, 'relation', issue['question'])
                    publish(new, 'scope_issue')
                    event = dict(revision=state['revision'], target=owner, issue_id=iid, answer=answer)
                    updated = validated_call(call, 'tree_update', main_assist.update_prompt(prompts.UPDATE),
                        {'event': event, **main_assist.context(state, owner), 'schema': context},
                        lambda raw: commit(state, event, raw, context))
                    publish(updated, event)
                    continue
                new = deepcopy(state)
                match = next(x for x in new['relations'] if x == r)
                match['status'] = 'confirmed' if picked == [0] else 'rejected'
                new['revision'] += 1
                publish(validate(new), 'relation_confirmation')
                continue
            target = step['target']
            if kind == 'permission':
                q = menu('This requirement cannot currently be completed: ' + state['nodes'][target]['meaning'],
                         ['Explicitly cancel this requirement', 'Keep unresolved and continue independent requirements',
                          'Revise only this requirement or describe an acceptable alternative'], 'local_cancellation')
                picked = choices(ask(q), q)
                if picked == [0]:
                    publish(cancel(state, target), 'explicit_cancel')
                elif picked == [2]:
                    public = dict(question='What acceptable change should be made only to this requirement? '
                                  + state['nodes'][target]['meaning'], focus=state['nodes'][target]['meaning'],
                                  granularity='local_revision', options=[], multi_select=False)
                    answer = ask(public)
                    if answer.get('action') == 'answer' and text(answer.get('text')):
                        new = deepcopy(state)
                        iid = add_issue(new, target, 'boundary', public['question'])
                        publish(new, 'permission_revision_question')
                        event = dict(revision=state['revision'], target=target, issue_id=iid,
                                     answer=answer, purpose='permission_local_revision')
                        updated = validated_call(call, 'tree_update', main_assist.update_prompt(prompts.UPDATE),
                            {'event': event, **main_assist.context(state, target), 'schema': context},
                            lambda raw: commit(state, event, raw, context))
                        publish(updated, event)
                        continue
                    new = deepcopy(state)
                    new['nodes'][target]['blocked'] = True
                    new['revision'] += 1
                    publish(new, 'permission_revision_unanswered')
                else:
                    new = deepcopy(state)
                    new['nodes'][target]['blocked'] = True
                    new['revision'] += 1
                    publish(new, 'permission_deferred')
                continue
            if kind == 'assess':
                payload = {**main_assist.context(state, target), 'schema': context}
                new = validated_call(call, 'tree_plan', main_assist.plan_prompt(prompts.PLAN), payload,
                                     lambda raw: assess(state, target, raw, context))
                # Planning cannot mint children. Repeated unproductive field plans
                # stop explicitly instead of burning the API budget in a loop.
                before = {i for i, q in state['issues'].items() if q['owner'] == target and q['status'] == 'pending'}
                after_step = next_step(new, context, allowed)
                publish(new, 'local_plan')
                if after_step == step and before:
                    return finish('unresolved', 'planner_no_progress')
                continue
            decision = state['issues'][step['issue_id']]
            options = decision.get('options', [])
            binding = decision['dimension'] == 'field'
            eligible = [o for o in options if not binding or
                        safe(dependencies(state, target) | set(o['columns']), context, allowed)]
            if options and not eligible:
                q = menu('No available alternative for: ' + decision['question'],
                         ['Explicitly cancel this requirement', 'Keep unresolved'], 'local_cancellation')
                if choices(ask(q), q) != [0]:
                    return finish('unresolved', 'empty_candidate_domain')
                publish(cancel(state, target), 'explicit_cancel')
                continue
            public = (menu(decision['question'], [o['label'] for o in eligible], decision['dimension'], True)
                      if eligible else dict(question=decision['question'], focus=decision['question'],
                          granularity=decision['dimension'], options=[], multi_select=False))
            answer = ask(public)
            selected = []
            if eligible:
                picked = choices(answer, public)
                if picked is None:
                    restatement = dict(question='Please describe what you need for this question in your own words: '
                                       + decision['question'], focus=decision['question'],
                                       granularity=decision['dimension'], options=[], multi_select=False)
                    answer = ask(restatement)
                    if answer.get('action') != 'answer' or not text(answer.get('text')):
                        return finish('unresolved', 'answer_requires_restatement')
                else:
                    selected = [eligible[i] for i in picked]
            elif answer.get('action') != 'answer' or not text(answer.get('text')):
                return finish('unresolved', 'answer_unavailable')
            cols = {c for o in selected for c in o['columns']} if binding else set()
            if binding and not safe(dependencies(state, target) | cols, context, allowed):
                return finish('unresolved', 'combined_selection_disconnected')
            event = dict(revision=state['revision'], target=target, issue_id=decision['id'], answer=answer,
                         selected_columns=sorted(cols), selected_labels=[o['label'] for o in selected])
            new = validated_call(call, 'tree_update', main_assist.update_prompt(prompts.UPDATE),
                {'event': event, **main_assist.context(state, target), 'schema': context},
                lambda raw: commit(state, event, raw, context))
            publish(new, event)
    except Budget:
        return finish('unresolved', 'interaction_budget')
