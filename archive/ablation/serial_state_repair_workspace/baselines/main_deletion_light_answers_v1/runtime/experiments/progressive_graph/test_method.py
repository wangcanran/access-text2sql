from copy import deepcopy

import pytest

from .method import (initial_state, propose, commit_partition, relation_answer,
                     next_step, cancel, commit_field, commit_detail, run, validate_partition)


CONTEXT = {'tables': [{'name': 't', 'columns': [{'name': c} for c in ('a', 'b', 'time')]}],
           'foreign_keys': []}
ALLOWED = ['t.a', 't.b', 't.time']
TEXT = 'Return A and B before the event.'
PLAN = {'children': [
    {'text': 'A', 'role': 'output', 'compound': False, 'quotes': ['Return A and ']},
    {'text': 'B', 'role': 'output', 'compound': False, 'quotes': ['B ']},
    {'text': 'before the event', 'role': 'modifier', 'compound': False, 'quotes': ['before the event.']} ]}


def partition(outputs=(0, 1)):
    s, p = propose(initial_state(TEXT), 'f1', PLAN)
    return commit_partition(s, p, outputs, [2])


def bound():
    s = partition()
    s = relation_answer(s, 'n4', ['n2', 'n3'], ['n2', 'n3'], s['revision'])
    for iid, col in [('n2', 't.a'), ('n3', 't.b'), ('n4', 't.time')]:
        s['nodes'][iid].update(columns=[col], analyzed=True)
    return s


def test_initial_compound_precedes_permission_and_fields():
    assert next_step(initial_state(TEXT), CONTEXT, []) == {'kind': 'partition', 'target': 'f1'}


def test_coverage_failure_keeps_parent():
    s = initial_state(TEXT)
    with pytest.raises(ValueError, match='coverage'):
        propose(s, 'f1', {'children': PLAN['children'][:1]})
    assert s['nodes']['f1']['status'] == 'active'
    assert not s['proposals']


def test_unselected_branch_not_deleted():
    s = partition((0,))
    assert s['nodes']['n3']['status'] == 'unconfirmed'
    assert next_step(s, CONTEXT, ALLOWED)['kind'] == 'boundary_remainder'
    assert s['nodes']['f1']['status'] == 'retired'


def test_modifier_not_promoted_to_output():
    s, p = propose(initial_state(TEXT), 'f1', PLAN)
    with pytest.raises(ValueError, match='Role mismatch'):
        commit_partition(s, p, [0, 1, 2], [])


def test_relation_before_local_analysis():
    s = partition()
    assert next_step(s, CONTEXT, ALLOWED)['kind'] == 'relation'
    new = relation_answer(s, 'n4', ['n2', 'n3'], ['n2'], s['revision'])
    assert [r['status'] for r in new['relations']] == ['confirmed', 'rejected']
    assert new['nodes']['n3']['status'] == 'active'
    assert next_step(new, CONTEXT, ALLOWED)['kind'] == 'analyze'


def test_stale_scope_and_none_are_not_committed():
    s = partition()
    before = deepcopy(s)
    for selected, revision in [([], s['revision']), (['n2'], -1)]:
        with pytest.raises(ValueError):
            relation_answer(s, 'n4', ['n2', 'n3'], selected, revision)
    assert s == before


def test_cancel_preserves_other_output_and_shared_condition():
    s = cancel(bound(), 'n2')
    assert s['nodes']['n3']['status'] == s['nodes']['n4']['status'] == 'active'
    assert next_step(s, CONTEXT, ALLOWED)['kind'] == 'deliver'
    s = cancel(s, 'n3')
    assert s['nodes']['n4']['status'] == 'orphaned'
    assert next_step(s, CONTEXT, ALLOWED)['kind'] == 'refuse'


def test_shared_anchor_cancel_preserves_outputs_as_unresolved():
    s = cancel(bound(), 'n4')
    assert s['nodes']['n2']['status'] == s['nodes']['n3']['status'] == 'unresolved'
    assert next_step(s, CONTEXT, ALLOWED)['kind'] == 'unresolved'


def test_field_answer_closes_only_its_owned_question():
    s = bound()
    s['nodes']['n2']['questions'] = [
        {'id': 'n2:d1', 'dimension': 'field', 'question': 'Which data?', 'status': 'pending',
         'options': [{'label': 'A', 'columns': ['t.a']}]},
        {'id': 'n2:d2', 'dimension': 'value', 'question': 'Which subset?', 'status': 'pending', 'options': []}]
    before = deepcopy(s['nodes']['n3'])
    new = commit_field(s, 'n2', 'n2:d1', [0], CONTEXT, ALLOWED)
    assert new['nodes']['n2']['questions'][1]['status'] == 'pending'
    assert new['nodes']['n3'] == before
    assert next_step(new, CONTEXT, ALLOWED)['kind'] == 'value'


def test_permission_cannot_silently_retire_requirements():
    s = bound()
    before = deepcopy(s)
    assert next_step(s, CONTEXT, ['t.a', 't.b'])['kind'] == 'permission'
    assert s == before


def test_detail_new_denied_dependency_is_rechecked():
    s = bound()
    s['nodes']['n2']['questions'] = [{'id': 'd', 'dimension': 'value', 'question': 'When?',
                                     'status': 'pending', 'options': []}]
    new = commit_detail(s, 'n2', 'd', {'resolved': True, 'answer': 'At event time',
                        'columns': ['t.time'], 'remaining_question': ''}, CONTEXT)
    assert next_step(new, CONTEXT, ['t.a', 't.b'])['kind'] == 'permission'


def test_duplicate_business_child_rejected():
    raw = deepcopy(PLAN)
    raw['children'][1]['text'] = 'A'
    with pytest.raises(ValueError, match='Duplicate'):
        validate_partition(raw, TEXT)


def test_end_to_end_structural_order_and_render_is_graph_only():
    stages, questions = [], []
    episode = {}
    def call(stage, prompt, payload):
        stages.append(stage)
        if stage == 'pg_split':
            assert set(payload) == {'text'}  # No schema or private reference.
            return PLAN
        if stage == 'pg_local':
            iid = payload['target']['id']
            col = {'n2': 't.a', 'n3': 't.b', 'n4': 't.time'}[iid]
            return {'columns': [col], 'questions': []}
        if stage == 'pg_render':
            assert set(payload) == {'nodes', 'edges'}
            assert {n['id'] for n in payload['nodes']} == {'n2', 'n3', 'n4'}
            assert all(r['status'] == 'confirmed' for r in payload['edges'])
            return {'text': 'Return A and B before the event.'}
        raise AssertionError(stage)
    def ask(q, history):
        questions.append(q['granularity'])
        return {'action': 'choose', 'choices': [o['id'] for o in q['options'] if o['id'] != 'NONE'], 'text': ''}
    result = run(call, TEXT, CONTEXT, ALLOWED, ask, episode=episode)
    assert result['action'] == 'deliver'
    assert questions == ['boundary_confirmation', 'boundary_confirmation', 'relation_confirmation']
    assert stages == ['pg_split', 'pg_local', 'pg_local', 'pg_local', 'pg_render']


def test_zero_budget_preserves_compound_and_does_not_render():
    episode = {}
    result = run(lambda *args: PLAN, TEXT, CONTEXT, ALLOWED,
                 lambda *args: pytest.fail('Must not ask'), max_turns=0, episode=episode)
    assert result['action'] == 'unresolved'
    assert episode['session']['nodes']['f1']['status'] == 'active'


def test_none_relation_blocks_delivery_without_deleting_outputs():
    def ask(q, history):
        if not q['options']:
            return {'action': 'unanswerable', 'text': '', 'choices': []}
        choices = ['NONE'] if q['granularity'] == 'relation_confirmation' else [
            o['id'] for o in q['options'] if o['id'] != 'NONE']
        return {'action': 'choose', 'choices': choices}
    episode = {}
    result = run(lambda *a: PLAN, TEXT, CONTEXT, ALLOWED, ask, episode=episode)
    assert result['reason'] == 'relation_scope_unresolved'
    assert episode['session']['nodes']['n2']['status'] == 'active'


def test_relation_none_can_be_rephrased_then_confirmed():
    def call(stage, prompt, payload):
        if stage == 'pg_split':
            return PLAN
        if stage == 'pg_scope':
            return {'resolved': True, 'selected_outputs': ['n2']}
        if stage == 'pg_local':
            return {'columns': ['t.a'], 'questions': []}
        if stage == 'pg_render':
            assert len(payload['edges']) == 1
            return {'text': 'A before event; B.'}
        raise AssertionError(stage)
    def ask(q, history):
        if q['granularity'] == 'relation_scope_detail':
            return {'action': 'answer', 'text': 'Only A', 'choices': []}
        if q['granularity'] == 'relation_confirmation':
            return {'action': 'choose', 'choices': ['1'] if q['question'].startswith('Confirm') else ['NONE']}
        return {'action': 'choose', 'choices': [o['id'] for o in q['options'] if o['id'] != 'NONE']}
    assert run(call, TEXT, CONTEXT, ALLOWED, ask)['action'] == 'deliver'


def test_joint_disconnected_selection_is_atomic():
    context = {'tables': [{'name': t, 'columns': [{'name': 'x'}]} for t in ('a', 'b')], 'foreign_keys': []}
    s = bound()
    s['nodes']['n2']['columns'] = []
    s['nodes']['n4']['columns'] = []
    s['nodes']['n2']['questions'] = [{'id': 'd', 'dimension': 'field', 'question': 'Which?',
        'status': 'pending', 'options': [{'label': t, 'columns': [t + '.x']} for t in ('a', 'b')]}]
    before = deepcopy(s)
    with pytest.raises(ValueError, match='Unsafe combined'):
        commit_field(s, 'n2', 'd', [0, 1], context, ['a.x', 'b.x'])
    assert s == before


def test_nested_partition_invalidates_old_attachment():
    s = partition()
    s['nodes']['n2']['compound'] = True
    s['nodes']['n2']['text'] = 'A1 A2'
    s = relation_answer(s, 'n4', ['n2', 'n3'], ['n2'], s['revision'])
    raw = {'children': [{'text': t, 'role': 'output', 'compound': False, 'quotes': [t]}
                        for t in ('A1', 'A2')]}
    s, p = propose(s, 'n2', raw)
    new = commit_partition(s, p, [0, 1], [])
    assert any(r['output'] == 'n2' and r['status'] == 'retired' for r in new['relations'])
    assert next_step(new, CONTEXT, ALLOWED)['kind'] == 'relation'
