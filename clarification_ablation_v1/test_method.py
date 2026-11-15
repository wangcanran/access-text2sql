"""Offline intervention tests: never construct a model client."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from clarification_ablation_v1.method import AblationMethod, direct_closure
from clarification_deferred_deletion import graph
import clarification_deferred_deletion.method as deletion
from clarification_contract_v5 import bindings
import clarification_contract_v5.method as contracts
from clarification_useful_residual_v6 import obligations, pool_decomposition
from clarification_useful_residual_v6.pool_method import PoolMethod
from scripts.run_clarification_v10 import verify_sources
from scripts.run_main_ablations import summarize


def plan():
    def node(i, role, columns, candidates=None):
        return dict(id=i, role=role, columns=columns, candidates=candidates or {})
    return dict(nodes=[node('o', 'output', ['t.name']),
        node('m', 'modifier', []), node('secret', 'modifier', ['t.secret']),
        node('independent', 'output', ['t.id']),
        node('orphan', 'modifier', ['t.id'])],
        edges=[['o', 'm', 'requires'], ['m', 'secret', 'requires']])


def session(state='open', use='returned'):
    columns = [] if state == 'open' else ['t.name']
    return obligations.initialize(dict(
        view=dict(items={'f1': dict(role='output', required_columns=columns, missing=[])},
            progressive=dict(decision_ledger=[]), relations=dict(edges=[]), questions=[]),
        binding_claims={'f1': [dict(meaning='Names', use=use, state=state, columns=columns)]},
        turns=[], deferred_final=dict(action='deliver', text='Return names', available_for_text2sql=True)))


def test_production_lock_and_multihop_intervention():
    verify_sources()
    p = plan()
    assert set(graph.closure(p, ['t.name', 't.id'])) == {'o', 'm', 'secret', 'orphan'}
    assert set(direct_closure(p, ['t.name', 't.id'])) == {'secret', 'orphan'}
    assert 't.secret' in graph.required_columns(p)['o']
    method = AblationMethod(variant='no_dependency')
    with method.installed():
        assert deletion.required_columns(p)['o'] == {'t.name'}
        assert set(deletion.closure(p, ['t.name', 't.id'])) == {'secret', 'orphan'}
    assert method.audit()['hook_counts'] == {'direct_columns': 1, 'direct_closure': 1}
    assert 't.secret' in deletion.required_columns(p)['o']
    verify_sources()


def test_direct_exclusions_none_and_orphans_survive_ablation():
    p = plan()
    p['nodes'][3]['candidates'] = {'t.denied': 'Denied option'}
    result = direct_closure(p, ['t.name', 't.id'], {'o': dict(rule='user_none')})
    assert result['o']['rule'] == 'user_none'
    assert result['secret']['rule'] == 'denied_dependency'
    assert result['independent']['rule'] == 'empty_authorized_field_domain'
    assert result['m']['rule'] == 'no_surviving_output_consumer'


def test_operand_propagation_all_import_aliases_and_restoration():
    nodes = [dict(id='m', text='Compare', role='modifier', kind='instruction',
                  binding_claims=[], columns=[]),
             dict(id='o', binding_claims=[dict(use='operand', state='bound', columns=['t.secret'])])]
    edges = [['m', 'o', 'requires']]
    originals = [(owner, owner.derive_requires_operand_claims)
                 for owner in (bindings, contracts, pool_decomposition)]
    assert bindings.derive_requires_operand_claims(deepcopy(nodes), edges)[0]['columns'] == ['t.secret']
    with AblationMethod(variant='no_dependency').installed():
        for owner, _ in originals:
            assert owner.derive_requires_operand_claims(deepcopy(nodes), edges)[0]['columns'] == []
    for owner, fn in originals:
        assert owner.derive_requires_operand_claims is fn


@pytest.mark.parametrize('variant,blocked', [('full', True), ('no_dependency', True),
    ('no_obligation_gate', False), ('no_both', False)])
def test_actual_scheduler_and_renderer_terminal_gates(variant, blocked):
    s = session()
    method = AblationMethod(variant=variant)
    with method.installed():
        # deferred_final lets the real inherited scheduler/renderer reach terminal gates.
        decision = method.next_step(s)
        rendered = method.render_session(lambda *a: pytest.fail('Unexpected model call'), s)
    assert (decision['action'] == 'unresolved') == blocked
    assert (rendered['action'] == 'unresolved') == blocked
    if not blocked:
        assert decision['action'] == rendered['action'] == 'deliver'
        assert method.hook_counts['binding_gate_suppressed'] > 0
        assert method.hook_counts['pool_gate_suppressed'] > 0
    assert PoolMethod().next_step(s)['action'] == 'unresolved'


def test_non_gate_issues_remain_and_hooks_restore_after_exception():
    originals = (obligations.issues, contracts.closure_issues, deletion.closure)
    with pytest.raises(RuntimeError, match='sentinel'):
        with AblationMethod(variant='no_both').installed():
            assert obligations.issues({}) == [dict(code='obligation_pool_not_initialized')]
            assert contracts.closure_issues(session(use='condition')) == [
                dict(node='f1', code='output_without_return_binding')]
            s = session(state='bound')
            s['view']['items']['f1']['required_columns'] = []
            assert any(p['code'] == 'uncommitted_binding_dependency' for p in obligations.issues(s))
            s = session()
            s['contract_unresolved_reason'] = 'grounding failed'
            assert AblationMethod(variant='no_both').next_step(s)['action'] == 'unresolved'
            raise RuntimeError('sentinel')
    assert originals == (obligations.issues, contracts.closure_issues, deletion.closure)


def test_full_variant_hooks_are_identity_and_all_population_denominator():
    original = graph.closure
    with AblationMethod(variant='full').installed():
        assert graph.closure is original
    stats = summarize([dict(outcome='deliver', classification='FP'),
                       dict(outcome='unresolved', classification=None)], 4)
    assert stats['observed_VR'] == .25
    assert stats['completion_rate'] == .25
    assert stats['pending_cases'] == 2
