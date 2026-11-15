"""Own state machine: no inherited session, monkey patches or legacy updater."""
from copy import deepcopy

from ...engine.analysis import indexes, validated_call
from ...engine.execution import _authorized_graph, _connected
from . import prompts

VERSION = 'progressive_graph_v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def initial_state(request):
    require(nonempty(request), 'Nonempty request required')
    return {'version': VERSION, 'revision': 0, 'next_id': 2,
            'nodes': {'f1': {'id': 'f1', 'text': request, 'role': 'compound',
                'compound': True, 'status': 'active', 'parent': None,
                'columns': [], 'questions': [], 'answers': [], 'analyzed': False}},
            'relations': [], 'proposals': [], 'audit': [], 'history': []}


def active(state):
    return [n for n in state['nodes'].values() if n['status'] == 'active']


def validate_partition(raw, text):
    require(isinstance(raw, dict) and isinstance(raw.get('children'), list)
            and bool(raw['children']), 'Expected nonempty children')
    covered = set()
    signatures = set()
    for child in raw['children']:
        require(isinstance(child, dict) and set(child) == {'text', 'role', 'compound', 'quotes'},
                'Invalid business child contract')
        require(nonempty(child['text']) and child['role'] in ('output', 'modifier')
                and type(child['compound']) is bool, 'Invalid role/text/compound')
        require(not (child['role'] == 'modifier' and child['compound']),
                'Compound modifiers are unsupported; retain uncertainty instead')
        signature = child['text'].strip().casefold()
        require(signature not in signatures, 'Duplicate business child')
        signatures.add(signature)
        require(isinstance(child['quotes'], list) and bool(child['quotes']), 'Missing provenance')
        for quote in child['quotes']:
            require(nonempty(quote) and quote in text, 'Quote absent from parent')
            start = 0
            while (start := text.find(quote, start)) >= 0:
                covered.update(range(start, start + len(quote)))
                start += 1
    require(all(i in covered for i, c in enumerate(text) if not c.isspace()),
            'Incomplete source coverage; parent must remain active')
    require(not (len(raw['children']) == 1 and raw['children'][0]['compound']),
            'Split must make progress')
    return deepcopy(raw)


def propose(state, target, raw):
    parent = state['nodes'][target]
    require(parent['status'] == 'active' and parent['compound'], 'Not an active compound')
    plan = validate_partition(raw, parent['text'])
    result = deepcopy(state)
    plan.update(target=target, revision=state['revision'], id=len(state['proposals']) + 1)
    result['proposals'].append(plan)
    return result, plan


def commit_partition(state, plan, outputs, modifiers):
    require(plan in state['proposals'] and plan['revision'] == state['revision'], 'Stale proposal')
    require(state['nodes'][plan['target']]['status'] == 'active', 'Retired parent')
    selected = set(outputs) | set(modifiers)
    require(set(outputs).isdisjoint(modifiers), 'Conflicting roles')
    require(all(type(i) is int and 0 <= i < len(plan['children']) for i in selected), 'Unknown child')
    require(all(plan['children'][i]['role'] == 'output' for i in outputs)
            and all(plan['children'][i]['role'] == 'modifier' for i in modifiers), 'Role mismatch')
    new = deepcopy(state)
    target = plan['target']
    new['nodes'][target]['status'] = 'retired'
    children = []
    for i, child in enumerate(plan['children']):
        iid = 'n' + str(new['next_id'])
        new['next_id'] += 1
        new['nodes'][iid] = {'id': iid, 'parent': target, **deepcopy(child),
            'status': 'active' if i in selected else 'unconfirmed',
            'columns': [], 'questions': [], 'answers': [], 'analyzed': False}
        children.append(iid)
    # A prior scope decision for the compound does not confirm child attachments.
    for relation in new['relations']:
        if relation['output'] == target:
            relation['status'] = 'retired'
    new['revision'] += 1
    sync_relations(new)
    new['audit'].append({'event': 'partition', 'parent': target, 'children': children,
                         'proposal': plan['id'], 'selected_indices': sorted(selected)})
    return new


def sync_relations(state):
    """Enumerate scope candidates without treating absent edges as independence."""
    known = {(r['modifier'], r['output']) for r in state['relations'] if r['status'] != 'retired'}
    for m in active(state):
        if m['role'] != 'modifier':
            continue
        for o in active(state):
            if o['role'] == 'output' and (m['id'], o['id']) not in known:
                state['relations'].append({'modifier': m['id'], 'output': o['id'],
                                          'status': 'unresolved'})


def relation_answer(state, modifier, candidates, selected, revision):
    require(revision == state['revision'], 'Stale relation question')
    require(bool(selected) and len(selected) == len(set(selected))
            and len(candidates) == len(set(candidates)) and set(selected) <= set(candidates),
            'Scope remains unresolved')
    pending = {r['output'] for r in state['relations']
               if r['modifier'] == modifier and r['status'] == 'unresolved'}
    require(set(candidates) == pending, 'Stale relation candidates')
    new = deepcopy(state)
    for r in new['relations']:
        if r['modifier'] == modifier and r['output'] in pending:
            r['status'] = 'confirmed' if r['output'] in selected else 'rejected'
    new['revision'] += 1
    new['audit'].append({'event': 'relation_confirmation', 'modifier': modifier,
                         'selected': sorted(selected), 'reviewed': sorted(pending)})
    return new


def cancel(state, target):
    """Explicit user cancellation never cascades to other outputs."""
    require(state['nodes'][target]['status'] in ('active', 'unconfirmed'), 'Inactive target')
    new = deepcopy(state)
    node = new['nodes'][target]
    node['status'] = 'cancelled'
    for r in new['relations']:
        if target in (r['modifier'], r['output']) and r['status'] != 'retired':
            r['status'] = 'retired'
    if node['role'] == 'modifier':
        for r in state['relations']:
            if r['modifier'] == target and r['status'] == 'confirmed':
                # Preserve outputs but refuse to silently generalize their scope.
                new['nodes'][r['output']]['status'] = 'unresolved'
    for m in active(new):
        if m['role'] == 'modifier':
            current = [r for r in new['relations'] if r['modifier'] == m['id']
                       and r['status'] in ('confirmed', 'unresolved')]
            old = [r for r in state['relations'] if r['modifier'] == m['id']
                   and r['status'] == 'confirmed']
            if not current and old:
                m['status'] = 'orphaned'
    new['revision'] += 1
    new['audit'].append({'event': 'user_cancel', 'target': target})
    return new


def columns_check(values, context):
    _, columns, _ = indexes(context)
    require(isinstance(values, list) and all(isinstance(c, str) for c in values)
            and len(values) == len(set(values)) and set(values) <= set(columns), 'Invalid schema columns')


def binding_safe(columns, context, allowed):
    columns_check(list(columns), context)
    if not set(columns) <= set(allowed):
        return False
    graph, error = _authorized_graph(context, allowed)
    return not error and _connected(graph, {c.rsplit('.', 1)[0] for c in columns})


def validate_local(raw, context):
    require(isinstance(raw, dict) and set(raw) == {'columns', 'questions'}, 'Invalid local analysis')
    columns_check(raw['columns'], context)
    require(isinstance(raw['questions'], list), 'Questions must be an array')
    seen = set()
    for q in raw['questions']:
        require(isinstance(q, dict) and set(q) == {'dimension', 'question', 'options'}, 'Invalid question')
        require(q['dimension'] in ('field', 'operation', 'value') and nonempty(q['question'])
                and isinstance(q['options'], list), 'Invalid dimension/question/options')
        require(q['question'] not in seen, 'Duplicate question')
        seen.add(q['question'])
        require(bool(q['options']) == (q['dimension'] == 'field'), 'Only field questions have options')
        labels = set()
        for option in q['options']:
            require(isinstance(option, dict) and set(option) == {'label', 'columns'}
                    and nonempty(option['label']) and option['label'] not in labels,
                    'Invalid/duplicate option')
            labels.add(option['label'])
            columns_check(option['columns'], context)
            require(bool(option['columns']), 'Field option needs dependencies')
    return deepcopy(raw)


def local_context(state, target):
    edges = [r for r in state['relations'] if r['status'] == 'confirmed'
             and target in (r['modifier'], r['output'])]
    ids = {target} | {r[k] for r in edges for k in ('modifier', 'output')}
    return {'nodes': [deepcopy(n) for n in active(state) if n['id'] in ids], 'edges': deepcopy(edges)}


def dependencies(state, target):
    ids = {target}
    ids.update(r['modifier'] for r in state['relations']
               if r['output'] == target and r['status'] == 'confirmed')
    return {c for iid in ids for c in state['nodes'][iid]['columns']}


def commit_field(state, target, question_id, options, context, allowed):
    new = deepcopy(state)
    node = new['nodes'][target]
    require(node['status'] == 'active', 'Inactive question owner')
    q = next(q for q in node['questions'] if q['id'] == question_id)
    require(q['status'] == 'pending' and q['dimension'] == 'field', 'Inactive field decision')
    require(bool(options) and len(set(options)) == len(options)
            and all(type(i) is int and 0 <= i < len(q['options']) for i in options), 'Invalid selection')
    chosen = [q['options'][i] for i in options]
    cols = {c for o in chosen for c in o['columns']}
    require(binding_safe(dependencies(state, target) | cols, context, allowed), 'Unsafe combined choice')
    node['columns'] = sorted(set(node['columns']) | cols)
    q['status'] = 'answered'
    node['answers'].append({'question_id': q['id'], 'dimension': 'field',
                            'question': q['question'], 'answer': [o['label'] for o in chosen]})
    new['revision'] += 1
    return new


def commit_detail(state, target, question_id, raw, context):
    require(isinstance(raw, dict) and set(raw) == {'resolved', 'answer', 'columns', 'remaining_question'},
            'Invalid detail interpretation')
    require(type(raw['resolved']) is bool and nonempty(raw['answer'])
            and isinstance(raw['remaining_question'], str), 'Invalid detail answer')
    require(raw['resolved'] or nonempty(raw['remaining_question']), 'Partial answer needs remaining question')
    columns_check(raw['columns'], context)
    new = deepcopy(state)
    node = new['nodes'][target]
    require(node['status'] == 'active', 'Inactive question owner')
    q = next(q for q in node['questions'] if q['id'] == question_id)
    require(q['status'] == 'pending' and q['dimension'] != 'field', 'Inactive detail decision')
    node['answers'].append({'question_id': q['id'], 'dimension': q['dimension'],
                            'question': q['question'], 'answer': raw['answer']})
    node['columns'] = sorted(set(node['columns']) | set(raw['columns']))
    if raw['resolved']:
        q['status'] = 'answered'
    else:
        q['question'] = raw['remaining_question']
    new['revision'] += 1
    return new


def next_step(state, context, allowed):
    for n in state['nodes'].values():
        if n['status'] == 'unconfirmed':
            return {'kind': 'boundary_remainder', 'target': n['id']}
    for n in active(state):
        if n['compound']:
            return {'kind': 'partition', 'target': n['id']}
    for r in state['relations']:
        if r['status'] == 'unresolved':
            return {'kind': 'relation', 'target': r['modifier']}
    # Analyze shared conditions first so field options include their dependencies.
    nodes = sorted(active(state), key=lambda n: n['role'] != 'modifier')
    for n in nodes:
        if not n['analyzed']:
            return {'kind': 'analyze', 'target': n['id']}
    for n in nodes:
        if not binding_safe(dependencies(state, n['id']), context, allowed):
            return {'kind': 'permission', 'target': n['id']}
    for dim in ('field', 'operation', 'value'):
        for n in nodes:
            for q in n['questions']:
                if q['status'] == 'pending' and q['dimension'] == dim:
                    return {'kind': dim, 'target': n['id'], 'question_id': q['id']}
    if any(n['status'] == 'unresolved' for n in state['nodes'].values()):
        return {'kind': 'unresolved', 'reason': 'structural_revision_required'}
    outputs = [n for n in nodes if n['role'] == 'output']
    if not outputs:
        return {'kind': 'refuse', 'reason': 'no_remaining_objective'}
    if any(not n['columns'] for n in outputs):
        return {'kind': 'unresolved', 'reason': 'output_without_binding'}
    if any(n['role'] == 'modifier' and not any(r['modifier'] == n['id']
            and r['status'] == 'confirmed' for r in state['relations']) for n in nodes):
        return {'kind': 'unresolved', 'reason': 'modifier_without_scope'}
    if not binding_safe({c for n in nodes for c in n['columns']}, context, allowed):
        return {'kind': 'unresolved', 'reason': 'joint_connectivity'}
    return {'kind': 'deliver'}


def menu(question, labels, granularity, *, multiple=True):
    return {'question': question, 'focus': question, 'granularity': granularity,
            'multi_select': multiple, 'options':
            [{'id': str(i + 1), 'label': label} for i, label in enumerate(labels)] +
            [{'id': 'NONE', 'label': 'None of these / needs clarification'}]}


def selected(answer, question):
    require(isinstance(answer, dict), 'Invalid answer')
    if answer.get('action') == 'unanswerable':
        return None
    require(answer.get('action') == 'choose' and isinstance(answer.get('choices'), list), 'Expected choice')
    choices = answer['choices']
    valid = {o['id'] for o in question['options']}
    require(bool(choices) and len(choices) == len(set(choices)) and set(choices) <= valid, 'Invalid menu choice')
    require(question['multi_select'] or len(choices) == 1, 'Single choice required')
    if 'NONE' in choices:
        require(len(choices) == 1, 'NONE is exclusive')
        return None
    return [int(x) - 1 for x in choices]


def run(call, request, context, allowed, ask_user, *, max_turns=40, episode=None, save=lambda: None):
    """Only public inputs enter this method. State commits follow successful validation."""
    state = initial_state(request)
    episode = {} if episode is None else episode
    episode.update(method=VERSION, session=state, snapshots=[], interactions=[])

    def publish(new):
        nonlocal state
        state = new
        episode['session'] = state
        episode['snapshots'].append(deepcopy(state))
        save()

    def ask(q):
        if len(episode['interactions']) >= max_turns:
            raise TurnBudget()
        a = ask_user(deepcopy(q), deepcopy(episode['interactions']))
        episode['interactions'].append({'question': deepcopy(q), 'answer': deepcopy(a)})
        save()
        return a

    def stop(reason, action='unresolved'):
        delivery = {'action': action, 'reason': reason, 'available_for_text2sql': False, 'text': ''}
        episode['delivery'] = delivery
        save()
        return delivery

    publish(state)
    try:
        while True:
            step = next_step(state, context, allowed)
            kind, target = step['kind'], step.get('target')
            node = state['nodes'].get(target)
            if kind in ('unresolved', 'refuse'):
                return stop(step['reason'], kind)
            if kind == 'deliver':
                payload = {'nodes': [{k: deepcopy(n[k]) for k in ('id', 'text', 'role', 'columns', 'answers')}
                                     for n in active(state)],
                           'edges': [r for r in state['relations'] if r['status'] == 'confirmed']}
                def rendered(raw):
                    require(isinstance(raw, dict) and set(raw) == {'text'} and nonempty(raw['text']), 'Invalid rendering')
                    return raw['text']
                text = validated_call(call, 'pg_render', prompts.RENDER, payload, rendered)
                episode['delivery'] = {'action': 'deliver', 'text': text,
                                       'available_for_text2sql': True, 'sql_execution_evaluated': False}
                save()
                return episode['delivery']
            if kind == 'partition':
                raw = validated_call(call, 'pg_split', prompts.SPLIT, {'text': node['text']},
                                     lambda r: validate_partition(r, node['text']))
                new, plan = propose(state, target, raw)
                publish(new)  # Candidate audit survives an incomplete confirmation.
                confirmed = {}
                for role, title in [('output', 'Which parts should be separately returned?'),
                                    ('modifier', 'Which parts describe conditions or calculation context, rather than returned results?')]:
                    indices = [i for i, c in enumerate(plan['children']) if c['role'] == role]
                    if not indices:
                        confirmed[role] = []
                        continue
                    q = menu(title + ' Unselected parts remain unconfirmed.',
                             [plan['children'][i]['text'] for i in indices], 'boundary_confirmation')
                    picks = selected(ask(q), q)
                    confirmed[role] = [] if picks is None else [indices[i] for i in picks]
                publish(commit_partition(state, plan, confirmed['output'], confirmed['modifier']))
            elif kind == 'boundary_remainder':
                q = menu('This branch is still unconfirmed: ' + node['text'],
                         ['Confirm as a returned result', 'Confirm as a condition or calculation context',
                          'Explicitly cancel this branch'],
                         'boundary_confirmation', multiple=False)
                picks = selected(ask(q), q)
                if picks is None:
                    return stop('unconfirmed_business_boundary')
                if picks == [2]:
                    publish(cancel(state, target))
                else:
                    new = deepcopy(state)
                    new['nodes'][target]['status'] = 'active'
                    new['nodes'][target]['role'] = 'output' if picks == [0] else 'modifier'
                    if picks == [1] and node['compound']:
                        return stop('compound_modifier_requires_repartition')
                    new['revision'] += 1
                    sync_relations(new)
                    publish(new)
            elif kind == 'relation':
                candidates = [r['output'] for r in state['relations']
                              if r['modifier'] == target and r['status'] == 'unresolved']
                q = menu('Select ALL results to which this condition applies: ' + node['text']
                         + '. Unselected results will be confirmed as outside its scope.',
                         [state['nodes'][i]['text'] for i in candidates], 'relation_confirmation')
                picks = selected(ask(q), q)
                if picks is None:
                    followup = {'question': 'Please explain which results this condition applies to: ' + node['text'],
                                'focus': node['text'], 'granularity': 'relation_scope_detail',
                                'options': [], 'multi_select': False}
                    answer = ask(followup)
                    if answer.get('action') != 'answer' or not nonempty(answer.get('text')):
                        return stop('relation_scope_unresolved')
                    def scope(raw):
                        require(isinstance(raw, dict) and set(raw) == {'selected_outputs', 'resolved'}
                                and type(raw['resolved']) is bool, 'Invalid scope proposal')
                        ids = raw['selected_outputs']
                        require(isinstance(ids, list) and all(isinstance(i, str) for i in ids)
                                and len(ids) == len(set(ids)) and set(ids) <= set(candidates), 'Unknown scope targets')
                        require(not raw['resolved'] or bool(ids), 'Empty scope remains unresolved')
                        return raw
                    proposal = validated_call(call, 'pg_scope', prompts.SCOPE,
                        {'condition': node['text'], 'answer': answer['text'],
                         'outputs': [{'id': i, 'text': state['nodes'][i]['text']} for i in candidates]}, scope)
                    if not proposal['resolved']:
                        return stop('relation_scope_unresolved')
                    chosen_ids = proposal['selected_outputs']
                    confirm = menu('Confirm this condition applies exactly to: ' + ', '.join(
                        state['nodes'][i]['text'] for i in chosen_ids), ['Confirm this scope'],
                        'relation_confirmation', multiple=False)
                    if selected(ask(confirm), confirm) != [0]:
                        return stop('relation_scope_unresolved')
                    picks = [candidates.index(i) for i in chosen_ids]
                publish(relation_answer(state, target, candidates, [candidates[i] for i in picks], state['revision']))
            elif kind == 'analyze':
                raw = validated_call(call, 'pg_local', prompts.LOCAL,
                    {'target': deepcopy(node), 'confirmed_context': local_context(state, target), 'schema': context},
                    lambda r: validate_local(r, context))
                new = deepcopy(state)
                current = new['nodes'][target]
                current.update(columns=raw['columns'], analyzed=True)
                current['questions'] = [{**q, 'id': target + ':d' + str(i + 1), 'status': 'pending'}
                                        for i, q in enumerate(raw['questions'])]
                new['revision'] += 1
                publish(new)
            elif kind == 'permission':
                q = menu('This requirement cannot currently be completed. How should we proceed? ' + node['text'],
                         ['Explicitly cancel this requirement', 'Keep it unresolved'], 'local_cancellation', multiple=False)
                if selected(ask(q), q) != [0]:
                    return stop('local_binding_unresolved')
                publish(cancel(state, target))
            else:
                decision = next(q for q in node['questions'] if q['id'] == step['question_id'])
                if kind == 'field':
                    eligible = [i for i, o in enumerate(decision['options'])
                                if binding_safe(dependencies(state, target) | set(o['columns']), context, allowed)]
                    if not eligible:
                        q = menu('No available alternative for: ' + decision['question'],
                                 ['Explicitly cancel this requirement', 'Keep it unresolved'], 'local_cancellation', multiple=False)
                        if selected(ask(q), q) != [0]:
                            return stop('empty_field_domain')
                        publish(cancel(state, target))
                        continue
                    q = menu(decision['question'], [decision['options'][i]['label'] for i in eligible], 'field')
                    picks = selected(ask(q), q)
                    if picks is None:
                        followup = menu('How should we proceed with: ' + node['text'],
                            ['Explicitly cancel this requirement', 'Keep it unresolved'],
                            'local_cancellation', multiple=False)
                        if selected(ask(followup), followup) != [0]:
                            return stop('field_selection_unresolved')
                        publish(cancel(state, target))
                        continue
                    chosen = [eligible[i] for i in picks]
                    if not binding_safe(dependencies(state, target) | {c for i in chosen for c in decision['options'][i]['columns']}, context, allowed):
                        return stop('combined_selection_unavailable')
                    publish(commit_field(state, target, decision['id'], chosen, context, allowed))
                else:
                    q = {'question': decision['question'], 'focus': decision['question'],
                         'granularity': kind, 'options': [], 'multi_select': False}
                    answer = ask(q)
                    if answer.get('action') != 'answer' or not nonempty(answer.get('text')):
                        return stop('detail_unresolved')
                    new = validated_call(call, 'pg_detail', prompts.DETAIL,
                        {'target': deepcopy(node), 'question': deepcopy(decision),
                         'answer': answer['text'], 'schema': context},
                        lambda raw: commit_detail(state, target, decision['id'], raw, context))
                    publish(new)
    except TurnBudget:
        return stop('interaction_budget')


class TurnBudget(Exception):
    pass
