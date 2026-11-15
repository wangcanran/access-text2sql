"""Offline module boundary tests, including actual harness interception."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from clarification_ablation_v1.modules import ModuleAblationMethod, unclarified_delivery
from clarification_deferred_deletion.baseline import module
from clarification_useful_residual_v6.pool_method import PoolMethod
from scripts.run_clarification_v10 import verify_sources


def state():
    return dict(allowed=['t.name'], turns=[], original_request='Show names and private details',
        current_request='Show names', view=dict(items={
            'f1':dict(role='output', requirement='Return names', missing=['value']),
            'm1':dict(role='modifier', requirement='For the requested region', missing=['value'])},
            relations=dict(edges=[dict(source_id='m1',target_id='f1',kind='applies_to')])),
        deferred_state=dict(blocked_nodes={'f2':dict(role='output',requirement='Private details')},committed=False))


def test_no_clarification_preserves_ambiguity_relations_and_input():
    s=state(); before=deepcopy(s)
    result=unclarified_delivery(s)
    assert result['action']=='deliver' and result['quality']=='unclarified'
    assert 'Return names' in result['text'] and 'For the requested region' in result['text']
    assert 'applies_to' in result['text'] and 'Private details' not in result['text']
    assert s==before and s['view']['items']['f1']['missing']==['value']


def test_failed_analysis_and_zero_residual_are_not_silent_success():
    s=state();s['contract_unresolved_reason']='broken wire'
    assert unclarified_delivery(s)['action']=='unresolved'
    s=state();s['view']['items']={}
    assert unclarified_delivery(s)['action']=='refuse'


def test_without_reduction_unclarified_input_stays_verbatim():
    s=state();s.pop('deferred_state')
    assert unclarified_delivery(s)['text']==s['original_request']


def test_prefix_cannot_use_later_answers_and_rejects_missing_initial_state():
    from scripts.prepare_module_ablation_prefixes import derive
    initial=state();initial.pop('deferred_state')
    future=state();future['turns']=[{'answer':{'text':'a private later answer'}}]
    trace=dict(episode=dict(snapshots=[initial,future],session=future),
               delivery=dict(action='deliver',text='final clarified answer'))
    nc,turns,_=derive(trace)['no_clarification']
    assert turns==0 and nc['text']==initial['original_request']
    assert derive(trace)['no_pruning'][0]['action']=='refuse'
    trace['episode']['snapshots']=[future]
    with pytest.raises(ValueError,match='before the first user answer'):derive(trace)


def test_no_pruning_rejects_whole_request_without_rendering_partial_text():
    s=state();method=ModuleAblationMethod(variant='no_pruning')
    with patch.object(PoolMethod,'next_step',side_effect=AssertionError('partial scheduler')):
        assert method.next_step(s)['action']=='refuse'
    assert method.render_session(lambda *a: pytest.fail('model call'),s)['text']==''
    assert s['allowed']==['t.name']


def test_no_pruning_no_conflict_delegates_unchanged():
    s=state();s.pop('deferred_state')
    sentinel=dict(action='menu',reason='original')
    with patch.object(PoolMethod,'next_step',return_value=sentinel) as parent:
        assert ModuleAblationMethod(variant='no_pruning').next_step(s) is sentinel
        parent.assert_called_once_with(s)


def test_real_harness_boundary_makes_zero_user_calls_and_restores():
    verify_sources()
    harness=module('engine.harness');engine=module('engine.simple_session')
    original=harness.run_session;episode={};s=state()
    method=ModuleAblationMethod(variant='no_clarification')
    with method.installed():
        with patch.object(engine,'start_session',return_value={'allowed':['t.name']}) as start, \
             patch.object(engine,'analyze_session',return_value=s):
            result=harness.run_session(lambda *a:pytest.fail('model call'), 'ambiguous',{},'db',
                ['t.name'],lambda *a:pytest.fail('user must not be called'),episode=episode,method='main')
            assert start.call_args.args[-1]==['t.name']
    assert result['quality']=='unclarified' and episode['interactions']==[]
    assert method.audit()['delivered_with_reduction']
    assert harness.run_session is original
    verify_sources()
