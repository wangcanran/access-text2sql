from copy import deepcopy
from unittest.mock import patch
import pytest

from clarification_ablation_v1.four_arm import FourArmMethod
from clarification_ablation_v1.test_modules import state
from clarification_useful_residual_v6.pool_method import PoolMethod
from clarification_deferred_deletion.baseline import module
from scripts.run_clarification_v10 import verify_sources


def test_serial_answer_carries_policy_probe_without_business_gap():
    from clarification_deferred_v3_2.test_obligations import minimal
    import clarification_deferred_v3_2.method as typed
    base, event, raw, mapping = minimal()
    base['progressive']['decision_ledger'].append(dict(
        decision_id='d2', owner_id='f1', dimension='permission',
        question='Which authorized information?', source_span='Rank records'))
    original = deepcopy(base)
    method = FourArmMethod(variant='clarify_then_prune')
    with method.installed():
        carried, _, _ = typed.apply_questions(base, event, raw, mapping,
            {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})
    assert base == original
    assert carried[0]['decision_id'] == 'd2'
    assert carried[0]['dimension'] == 'permission'
    assert mapping[0]['missing'] == []
    assert mapping[0]['question_focus'] == {}
    session = dict(view=dict(items={'f2': mapping[0]},
        progressive=dict(decision_ledger=carried)))
    method._refresh_focus(session)
    assert session['view']['progressive']['decision_ledger'] == carried
    assert session['view']['items']['f2']['missing'] == []


def test_business_gap_filter_preserves_unanswered_business_choices():
    method = FourArmMethod(variant='clarify_then_prune')
    rows = [dict(missing=['permission', 'value'],
                 question_focus={'permission': 'Policy probe', 'value': 'Which year?'})]
    method._business_question_focus(rows)
    assert rows == [dict(missing=['value'], question_focus={'value': 'Which year?'})]
    method.phase = 'prune'
    unchanged = [dict(missing=['permission'], question_focus={'permission': 'Policy probe'})]
    before = deepcopy(unchanged)
    method._business_question_focus(unchanged)
    assert unchanged == before


def test_no_clarification_executes_empty_menu_pruning_before_stopping():
    method = FourArmMethod(variant='no_clarification')
    before = state()
    after = deepcopy(before)
    after['current_request'] = 'internally pruned'
    with patch.object(method, 'next_step', side_effect=[dict(action='menu'), dict(action='ask')]), \
         patch.object(method, 'prepare_menu', return_value=(after, None)) as prepare:
        result = method.no_user_internal_work(None, before, {})
    assert result is after
    prepare.assert_called_once()
    assert not result['turns']


def test_no_clarification_does_not_answer_a_real_menu():
    method = FourArmMethod(variant='no_clarification')
    s = state()
    with patch.object(method, 'next_step', return_value=dict(action='menu')), \
         patch.object(method, 'prepare_menu', return_value=(s, {'question': 'Which field?'})):
        assert method.no_user_internal_work(None, s, {}) is s
    assert s['view']['items']['f1']['missing'] == ['value']


def test_no_pruning_refuses_before_decomposition_or_partial_output():
    method = FourArmMethod(variant='no_pruning')
    s = state()
    with patch.object(PoolMethod, '_decompose', side_effect=AssertionError('must not split')):
        result = method._decompose(None, s, {})
    assert result['view'] == s['view']
    assert method.next_step(result)['action'] == 'refuse'
    assert method.render_session(None, result)['text'] == ''
    assert result['allowed'] == s['allowed']


def test_permission_compiler_uses_current_typed_boundary():
    method = FourArmMethod(variant='clarify_then_prune')
    with patch('clarification_ablation_v1.four_arm.light.LightweightUpdate.compile_decision',
               return_value='typed') as compiler:
        assert method.compile_decision(None, {}, {}, field=False) == 'typed'
    assert compiler.call_args.kwargs == dict(field=False, scope=False)


def test_serial_retains_nodes_and_permission_facts_during_clarification():
    method = FourArmMethod(variant='clarify_then_prune')
    s = state()
    with patch.object(PoolMethod, '_decompose', side_effect=AssertionError('premature projection')):
        assert method._decompose(None, s, {}) is s
    assert method._complete_delivery(None, s, {}) is s
    method.phase = 'prune'
    sentinel = object()
    with patch.object(PoolMethod, '_decompose', return_value=sentinel):
        assert method._decompose(None, s, {}) is sentinel


def test_serial_routes_business_questions_without_erasing_permission_evidence():
    method = FourArmMethod(variant='clarify_then_prune')
    s = state()
    s['permission_issues'] = {'p1': {'denied_columns': ['t.secret']}}
    s['view']['questions'] = [dict(key='p1', dimension='permission'),
                              dict(key='q1', dimension='field')]
    before = deepcopy(s)
    def scheduler(routing):
        assert routing['permission_issues'] == s['permission_issues']
        assert [q['key'] for q in routing['view']['questions']] == ['q1']
        return dict(action='menu', question_id='q1')
    method.original['next_step'] = scheduler
    assert method.next_step(s)['question_id'] == 'q1'
    assert s == before


def test_serial_rejection_records_actual_event_without_deleting_parent():
    method = FourArmMethod(variant='clarify_then_prune')
    s = state()
    s['view']['deferred'] = []
    q = dict(key='q1', item={'item_id': 'f1'}, decision_ids=['d1'], focus='Which value?')
    updated = deepcopy(s)
    updated['turns'] = [{'answer': {'action': 'unanswerable'}}]
    result = method._remove(None, updated, s, q, 'no_accepted_local_answer', {})
    assert result['view']['items'] == s['view']['items']
    assert result['view']['deferred'] == ['q1']
    assert result['serial_pending_removals'][0]['asked_ids'] == ['d1']
    assert result['turns'] == updated['turns']
    assert result['allowed'] == s['allowed']


def test_serial_uncertified_lost_event_is_unresolved_not_invented_deletion():
    method = FourArmMethod(variant='clarify_then_prune')
    s = state()
    s['serial_pending_removals'] = [dict(asked_ids=['absent'], target='f1')]
    with patch('clarification_ablation_v1.four_arm.light.pending', return_value=[]):
        result = method.finish_serial(None, s, {})
    assert result['contract_unresolved_reason'] == 'Serial removal lost its decision identity'
    assert result['view']['items'] == s['view']['items']


def test_no_user_actual_harness_preserves_locked_production_source():
    verify_sources()
    harness = module('engine.harness')
    engine = module('engine.simple_session')
    original = harness.run_session
    method = FourArmMethod(variant='no_clarification')
    episode = {}
    s = state()
    with method.installed(), \
         patch.object(engine, 'start_session', return_value={}), \
         patch.object(engine, 'analyze_session', return_value=s), \
         patch.object(method, 'no_user_internal_work', return_value=s) as work:
        result = harness.run_session(None, 'request', {}, 'db', ['t.name'],
            lambda *a: pytest.fail('no user calls'), episode=episode, method='main')
    assert result['quality'] == 'unclarified'
    assert episode['interactions'] == []
    work.assert_called_once()
    assert harness.run_session is original
    verify_sources()
