from copy import deepcopy
import pytest

from .state import initial, frontier, validate, add_issue
from .planner import assess, next_step
from .transaction import commit, cancel, candidates
from .runner import run

C = {'tables': [{'name': 't', 'columns': [{'name': n} for n in ('a', 'b', 'time')]}], 'foreign_keys': []}
A = ['t.a', 't.b', 't.time']


def desc(alias, meaning, role='output', columns=None, granularity='atomic'):
    return dict(alias=alias, meaning=meaning, role=role, columns=columns or [], granularity=granularity)


def change(status='answered', owner='p', question='Which?', reason='Actual answer'):
    return dict(status=status, owner=owner, question=question, reason=reason)


def tx(children, changes, **kw):
    return dict(action='expand' if len(children) > 1 else 'revise', children=children,
                issue_updates=changes, new_issues=kw.get('issues', []), relations=kw.get('relations', []),
                retired_columns=[], retirement_reason='', coverage='Preserve all contributions and remainder')


def ready():
    s = initial('A and B')
    add_issue(s, 'f1', 'field', 'Which data?')
    return s, dict(revision=0, target='f1', issue_id='d1', answer={'action': 'answer', 'text': 'A and B'})


def expanded():
    s, e = ready()
    return commit(s, e, tx([desc('a', 'A', columns=['t.a']), desc('b', 'B', columns=['t.b'])],
                           {'d1': change(owner='a')}), C)


def test_root_starts_as_unclear_leaf():
    s = initial('whole sentence')
    assert list(frontier(s)) == ['f1']
    assert next_step(s, C, A)['kind'] == 'assess'


def test_answer_expands_tree_not_initial_analysis():
    s, e = ready()
    new = expanded()
    assert list(frontier(new)) == ['f2', 'f3']
    assert new['nodes']['f1']['meaning'] == 'A and B'
    assert new['nodes']['f1']['children'] == ['f2', 'f3']
    assert new['nodes']['f2']['parent_id'] == 'f1'
    assert not s['nodes']['f1']['children']


def test_parameter_answer_keeps_identity():
    s, e = ready()
    new = commit(s, e, tx([desc('p', 'A at time 1', columns=['t.a'])], {'d1': change()}), C)
    assert list(frontier(new)) == ['f1']
    assert new['next_node'] == 2
    assert new['issues']['d1']['status'] == 'answered'


def test_other_issue_cannot_close_and_failure_atomic():
    s, e = ready()
    add_issue(s, 'f1', 'value', 'Which subset?')
    before = deepcopy(s)
    with pytest.raises(ValueError, match='another issue'):
        commit(s, e, tx([desc('p', 'A')], {'d1': change(), 'd2': change()}), C)
    assert s == before


def test_every_old_issue_must_have_unique_owner():
    s, e = ready()
    add_issue(s, 'f1', 'value', 'Which subset?')
    with pytest.raises(ValueError, match='Every old'):
        commit(s, e, tx([desc('p', 'A'), desc('b', 'B')], {'d1': change()}), C)
    new = commit(s, e, tx([desc('a', 'A'), desc('b', 'B')],
        {'d1': change(owner='a'), 'd2': change('pending', 'b', 'Which subset?')}), C)
    assert new['issues']['d2']['owner'] == 'f3'
    assert new['issues']['d2']['status'] == 'pending'


def test_compound_remainder_blocks_closure():
    s, e = ready()
    new = commit(s, e, tx([desc('a', 'A', columns=['t.a']),
        desc('b', 'other requested information', 'compound', granularity='compound')],
        {'d1': change(owner='a')}), C)
    assert new['nodes']['f3']['granularity'] == 'compound'
    assert next_step(new, C, A)['kind'] != 'deliver'
    assert new['nodes']['f3']['needs_local_focus']
    assert not any(q['question'].startswith('Clarify the remaining business contribution:')
                   for q in new['issues'].values())


def test_retained_and_selected_dependencies_cannot_disappear():
    s, e = ready()
    e['selected_columns'] = ['t.a']
    with pytest.raises(ValueError, match='Lost retained'):
        commit(s, e, tx([desc('p', 'A')], {'d1': change()}), C)


def test_nested_split_reopens_confirmed_scope():
    s, e = ready()
    s = commit(s, e, tx([desc('a', 'A', columns=['t.a']),
        desc('m', 'before event', 'modifier', ['t.time'])], {'d1': change(owner='a')}), C)
    s['relations'][0]['status'] = 'confirmed'
    d = add_issue(s, 'f2', 'field', 'Which details?')
    event = dict(revision=s['revision'], target='f2', issue_id=d, answer={'action': 'answer', 'text': 'A and B'})
    new = commit(s, event, tx([desc('x', 'A', columns=['t.a']), desc('y', 'B', columns=['t.b'])],
                            {d: change(owner='x')}), C)
    assert set(frontier(new)) == {'f3', 'f4', 'f5'}
    assert new['nodes']['f2']['children'] == ['f4', 'f5']
    assert all(r['status'] != 'confirmed' for r in new['relations'])
    assert len([r for r in new['relations'] if r['status'] == 'unresolved']) == 2


def test_shared_anchor_cancel_retains_outputs_and_opens_issue():
    s, e = ready()
    s = commit(s, e, tx([desc('a', 'A', columns=['t.a']), desc('m', 'before event', 'modifier', ['t.time'])],
                        {'d1': change(owner='a')}), C)
    s['relations'][0]['status'] = 'confirmed'
    new = cancel(s, 'f3')
    assert 'f2' in frontier(new)
    assert any(q['owner'] == 'f2' and q['status'] == 'pending' for q in new['issues'].values())
    assert next_step(new, C, A)['kind'] != 'deliver'


def test_permission_failure_precedes_unrelated_analysis():
    s = expanded()
    s['nodes']['f3']['assessed'] = False
    assert next_step(s, C, ['t.b']) == {'kind': 'permission', 'target': 'f2'}
    assert set(frontier(s)) == {'f2', 'f3'}


def test_cancel_does_not_cascade_to_sibling():
    new = cancel(expanded(), 'f2')
    assert list(frontier(new)) == ['f3']
    assert next_step(new, C, A)['kind'] == 'deliver'


def test_no_direct_confirmation_of_model_edges():
    s, e = ready()
    raw = tx([desc('a', 'A'), desc('m', 'time', 'modifier')], {'d1': change(owner='a')},
             relations=[dict(source='m', target='a', kind='applies_to', status='confirmed')])
    with pytest.raises(ValueError, match='user confirmation'):
        commit(s, e, raw, C)


def test_stale_answer_rejected():
    s, e = ready()
    e['revision'] = -1
    with pytest.raises(ValueError, match='Stale'):
        commit(s, e, tx([desc('p', 'A')], {'d1': change()}), C)


def test_cycles_and_detached_nodes_rejected():
    s = expanded()
    s['nodes']['f2']['children'] = ['f1']
    with pytest.raises(ValueError):
        validate(s)


def test_live_loop_field_answer_expands_and_renderer_sees_only_leaves():
    stages = []
    episode = {}
    def call(stage, prompt, payload):
        stages.append(stage)
        if stage == 'tree_plan':
            assert not payload['target']['children']
            return dict(role='compound', granularity='compound', columns=[],
                issues=[dict(dimension='field', question='Which information?')], focus=0,
                options=[dict(label='A and B', columns=['t.a', 't.b'])])
        if stage == 'tree_update':
            assert 'module_ids' not in payload['event']['answer']
            return tx([desc('a', 'A', columns=['t.a']), desc('b', 'B', columns=['t.b'])],
                      {'d1': change(owner='a')})
        if stage == 'tree_render':
            assert set(payload) == {'nodes', 'relations'}
            assert {n['id'] for n in payload['nodes']} == {'f2', 'f3'}
            return {'text': 'Return A and B.'}
        raise AssertionError(stage)
    def ask(q, history):
        assert q['granularity'] == 'field'
        return dict(action='choose', choices=['1'], text='', module_ids=['private_o1'])
    result = run(call, 'A and B', C, A, ask, episode=episode)
    assert result['action'] == 'deliver'
    assert stages == ['tree_plan', 'tree_update', 'tree_render']
    assert episode['session']['nodes']['f1']['children'] == ['f2', 'f3']


def test_budget_keeps_root_and_never_renders():
    def call(stage, prompt, payload):
        assert stage == 'tree_plan'
        return dict(role='compound', granularity='compound', columns=[], issues=[
            dict(dimension='boundary', question='Which result?')], focus=0, options=[])
    episode = {}
    result = run(call, 'A and B', C, A, lambda *a: pytest.fail('No interaction budget'), max_turns=0, episode=episode)
    assert result['reason'] == 'interaction_budget'
    assert list(frontier(episode['session'])) == ['f1']


def test_selected_field_does_not_close_value_issue():
    s, e = ready()
    add_issue(s, 'f1', 'value', 'Which subset?')
    e['selected_columns'] = ['t.a']
    new = commit(s, e, tx([desc('p', 'A for an unspecified subset', columns=['t.a'])],
        {'d1': change(), 'd2': change('pending', question='Which subset?')}), C)
    assert new['issues']['d2']['status'] == 'pending'
    assert next_step(new, C, A)['kind'] == 'question'


def test_planning_cannot_hide_old_pending_question():
    s = expanded()
    add_issue(s, 'f2', 'value', 'Which records?')
    raw = dict(role='output', granularity='atomic', columns=['t.a'], issues=[], focus=None, options=[])
    new = assess(s, 'f2', raw, C)
    assert next_step(new, C, A)['kind'] == 'question'


def test_candidate_columns_do_not_become_required_columns():
    s = initial('Information')
    raw = dict(role='output', granularity='atomic', columns=[], issues=[
        dict(dimension='field', question='Which field?')], focus=0,
        options=[dict(label='A', columns=['t.a']), dict(label='B', columns=['t.b'])])
    new = assess(s, 'f1', raw, C)
    assert new['nodes']['f1']['columns'] == []
    assert next_step(new, C, ['t.a'])['kind'] == 'question'


def test_renderer_child_answer_does_not_contain_parent_dialogue():
    new = expanded()
    assert new['nodes']['f2']['answers'] == [{'issue_id': 'd1', 'answer': 'A'}]
    assert new['audit'][0]['event']['answer']['text'] == 'A and B'


def test_disconnected_combined_selection_never_commits():
    context = {'tables': [{'name': t, 'columns': [{'name': 'x'}]} for t in ('a', 'b')], 'foreign_keys': []}
    def call(stage, prompt, payload):
        assert stage == 'tree_plan'
        return dict(role='output', granularity='atomic', columns=[], issues=[
            dict(dimension='field', question='Which?')], focus=0,
            options=[dict(label=t, columns=[t + '.x']) for t in ('a', 'b')])
    result = run(call, 'Information', context, ['a.x', 'b.x'],
                 lambda *a: dict(action='choose', choices=['1', '2'], text=''))
    assert result['reason'] == 'combined_selection_disconnected'


def test_failed_answer_contract_leaves_tree_unchanged():
    episode = {}
    def call(stage, prompt, payload):
        if stage == 'tree_plan':
            return dict(role='compound', granularity='compound', columns=[], issues=[
                dict(dimension='boundary', question='Which?')], focus=0, options=[])
        return {'invalid': True}
    with pytest.raises(ValueError):
        run(call, 'A and B', C, A, lambda *a: dict(action='answer', text='A', choices=[]), episode=episode)
    assert list(frontier(episode['session'])) == ['f1']
    assert not episode['session']['nodes']['f1']['children']


def test_full_cancellation_is_no_remaining_objective():
    s = expanded()
    s = cancel(cancel(s, 'f2'), 'f3')
    assert s['nodes']['f1']['children'] == ['f2', 'f3']
    assert next_step(s, C, A)['kind'] == 'refuse'


def test_root_source_immutable_while_current_meaning_changes():
    s, e = ready()
    new = commit(s, e, tx([desc('p', 'A for the confirmed owner', columns=['t.a'])], {'d1': change()}), C)
    assert new['nodes']['f1']['source_text'] == new['original_request'] == 'A and B'
    assert new['nodes']['f1']['meaning'] == 'A for the confirmed owner'
    broken = deepcopy(new)
    broken['nodes']['f1']['source_text'] = 'rewritten original'
    with pytest.raises(ValueError, match='provenance'):
        validate(broken)


def test_main_assist_is_read_only_and_exposes_answered_identity():
    from . import main_assist
    from ...engine import simple_session
    before_function = simple_session.commit_answer
    s = expanded()
    before = deepcopy(s)
    view = main_assist.context(s, 'f2')
    assert view['local_decisions'][0]['status'] == 'answered'
    assert 'source_text' not in view['target']
    assert all('source_text' not in n for n in view['current_graph']['nodes'])
    assert 'f1' not in {n['id'] for n in view['current_graph']['nodes']}
    assert 'Split a fragment only' in main_assist.refinement_rules()
    assert 'Copy each piece character-for-character' not in main_assist.refinement_rules()
    view['target']['meaning'] = 'mutated copy'
    assert s == before
    assert simple_session.commit_answer is before_function


def test_pending_paraphrase_reuses_identity_but_answered_id_rejected():
    s, _ = ready()
    raw = dict(role='compound', granularity='compound', columns=[], focus=0, options=[],
               issues=[dict(issue_id='d1', dimension='field', question='Select the desired data')])
    raw['options'] = [dict(label='A', columns=['t.a'])]
    new = assess(s, 'f1', raw, C)
    assert len(new['issues']) == 1
    assert new['issues']['d1']['question'] == 'Select the desired data'
    s['issues']['d1']['status'] = 'answered'
    with pytest.raises(ValueError, match='pending identity'):
        assess(s, 'f1', raw, C)


def test_business_menu_keeps_denied_positive_answer_then_checks_actual_binding():
    seen = []
    def call(stage, prompt, payload):
        if stage == 'tree_plan':
            return dict(role='compound', granularity='compound', columns=[], focus=0,
                issues=[dict(dimension='relation', question='Use the account owner?')],
                options=[dict(label='Yes', columns=['t.a']), dict(label='No', columns=[])])
        if stage == 'tree_update':
            assert payload['event']['selected_columns'] == []
            assert payload['event']['selected_labels'] == ['Yes']
            return tx([desc('p', 'Confirmed owner', columns=['t.a'])], {'d1': change()})
        raise AssertionError(stage)
    def ask(q, history):
        seen.append(q)
        if q['granularity'] == 'local_cancellation':
            return dict(action='choose', choices=['2'], text='')
        assert [o['label'] for o in q['options']] == ['Yes', 'No', 'None of these / needs clarification']
        return dict(action='choose', choices=['1'], text='')
    result = run(call, 'Owner information', C, ['t.b'], ask)
    assert result['reason'] == 'permission_blocked'
    assert len(seen) == 2


def test_unanswerable_menu_can_restate_and_expand_without_extra_planning():
    calls, questions = [], []
    def call(stage, prompt, payload):
        calls.append(stage)
        if stage == 'tree_plan':
            return dict(role='compound', granularity='compound', columns=[], focus=0,
                issues=[dict(dimension='boundary', question='What is needed?')],
                options=[dict(label='One summary', columns=[])])
        if stage == 'tree_update':
            assert payload['event']['answer']['text'] == 'A and B separately'
            return tx([desc('a', 'A', columns=['t.a']), desc('b', 'B', columns=['t.b'])], {'d1': change(owner='a')})
        assert stage == 'tree_render'
        return {'text': 'Return A and B.'}
    def ask(q, history):
        questions.append(q)
        if q['options']:
            return dict(action='unanswerable', text='', choices=[])
        return dict(action='answer', text='A and B separately', choices=[])
    result = run(call, 'A and B', C, A, ask)
    assert result['action'] == 'deliver'
    assert len(questions) == 2
    assert calls == ['tree_plan', 'tree_update', 'tree_render']


def test_saved_live_positive_business_option_not_removed():
    import json
    from ...runtime import ROOT
    path = ROOT / 'artifacts/clarification_main/clarification_tree_live_v1/113_1_reduction.json'
    if not path.exists():
        pytest.skip('Historical live checkpoint unavailable')
    from ...live import load_case
    record = json.loads(path.read_text(encoding='utf8'))
    context = load_case(113, 1)[1]
    raw = json.loads(record['calls'][3]['response']['choices'][0]['message']['content'])
    s = initial(record['request'])
    new = assess(s, 'f1', raw, context)
    focused = new['issues'][new['nodes']['f1']['planned_issue']]
    assert len(focused['options']) == 2
    assert all(not o['columns'] for o in focused['options'])
    assert focused['menu_kind'] == 'business'
    assert new['audit'][-1]['action'] == 'business_option_binding_ignored'


def test_compound_roles_optional_and_attachment_to_compound_valid():
    s, e = ready()
    remainder = dict(alias='rest', meaning='Remaining profile', granularity='compound', columns=[])
    new = commit(s, e, tx([desc('m', 'Loan subject', 'modifier', ['t.a']), remainder],
        {'d1': change(owner='m')}, relations=[dict(source='m', target='rest', kind='applies_to', status='unresolved')]), C)
    assert new['nodes']['f1']['role'] is None
    assert new['nodes']['f3']['role'] is None
    assert new['relations'] == [dict(source='f2', target='f3', kind='applies_to', status='unresolved')]
    validate(new)


def test_compound_plan_needs_no_business_role():
    raw = dict(granularity='compound', columns=[], issues=[dict(dimension='operation', question='Which ratio formula?')],
               focus=0, options=[])
    new = assess(initial('profile and ratio'), 'f1', raw, C)
    assert new['nodes']['f1']['role'] is None
    assert next_step(new, C, A)['kind'] == 'question'


def test_atomic_role_still_required():
    raw = dict(granularity='atomic', columns=['t.a'], issues=[], focus=None, options=[])
    with pytest.raises(ValueError, match='Atomic leaf'):
        assess(initial('A'), 'f1', raw, C)


def test_compound_cannot_guess_new_bindings_in_plan_or_answer():
    s, e = ready()
    with pytest.raises(ValueError, match='speculative bindings'):
        commit(s, e, tx([desc('p', 'A and B', None, ['t.a', 't.b'], 'compound')], {'d1': change()}), C)
    with pytest.raises(ValueError, match='speculative bindings'):
        assess(s, 'f1', dict(granularity='compound', columns=['t.a'], issues=[
            dict(dimension='field', question='Which?')], focus=0,
            options=[dict(label='A', columns=['t.a'])]), C)
    e['selected_columns'] = ['t.a']
    new = commit(s, e, tx([desc('p', 'A and remainder', None, ['t.a'], 'compound')], {'d1': change()}), C)
    assert new['nodes']['f1']['columns'] == ['t.a']


def test_deferred_blocker_does_not_prevent_independent_work_or_allow_delivery():
    s = expanded()
    s['nodes']['f2']['blocked'] = True
    s['nodes']['f3']['assessed'] = False
    assert next_step(s, C, ['t.b']) == dict(kind='assess', target='f3')
    s['nodes']['f3']['assessed'] = True
    assert next_step(s, C, ['t.b']) == dict(kind='unresolved', reason='permission_blocked')


def test_possible_shared_blocker_is_not_treated_as_independent():
    s, e = ready()
    s = commit(s, e, tx([desc('m', 'subject', 'modifier', ['t.a']),
        desc('rest', 'A and B', None, [], 'compound')], {'d1': change(owner='m')}), C)
    s['nodes']['f2']['blocked'] = True
    assert next_step(s, C, ['t.b'])['reason'] == 'permission_blocked'


def test_permission_local_revision_rechecks_changed_dependencies():
    steps = []
    episode = {}
    def call(stage, prompt, payload):
        steps.append(stage)
        if stage == 'tree_plan':
            return dict(role='output', granularity='atomic', columns=['t.a'], issues=[], focus=None, options=[])
        if stage == 'tree_update':
            assert payload['event']['purpose'] == 'permission_local_revision'
            result = tx([desc('p', 'Return B instead', columns=['t.b'])], {'d1': change()})
            result.update(retired_columns=['t.a'], retirement_reason='Actual answer replaces A with B')
            return result
        assert stage == 'tree_render'
        return {'text': 'Return B.'}
    def ask(q, history):
        if q['options']:
            return dict(action='choose', choices=['3'], text='')
        return dict(action='answer', text='Return B instead of A', choices=[])
    result = run(call, 'Return A', C, ['t.b'], ask, episode=episode)
    assert result['action'] == 'deliver'
    assert episode['session']['nodes']['f1']['columns'] == ['t.b']
    assert steps == ['tree_plan', 'tree_update', 'tree_render']


def test_local_guidance_has_no_runtime_main_method_prompt_dependency():
    import inspect
    from . import main_assist
    assert 'from ...method import' not in inspect.getsource(main_assist)
    assert 'MAIN-METHOD' not in main_assist.plan_prompt('')
    assert 'Compound remainders' in main_assist.refinement_rules()
