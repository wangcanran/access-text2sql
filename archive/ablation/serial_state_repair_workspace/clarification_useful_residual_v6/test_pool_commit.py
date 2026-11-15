"""Exercise actual render/scheduler dispatch during permission-only refusal."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from .pool_method import PoolMethod
from .pool_commit import commit, validate_review
from .pool_answer_review import validate
from .pool_transitions import deletion_input
from . import obligations as pool
from clarification_deferred_deletion.baseline import module

ROOT = Path(__file__).resolve().parents[1]


def trace(name):
    p = ROOT/'artifacts/clarification_main/gpt52_pool_v2_failures9_control1_20261028'/(name+'_reduction.json')
    if not p.exists():
        pytest.skip('Historical fresh trace not distributed')
    d = json.loads(p.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    return d, context


@pytest.mark.parametrize('name', ['1038_1', '223_1', '1510_1'])
def test_atomic_refusal_refreshes_pool_before_virtual_render_dispatch(name):
    d, context = trace(name)
    s = deepcopy(d['episode']['session'])
    s.pop('deferred_final', None)
    s.pop('deferred_review_rejection', None)
    s['deferred_state']['committed'] = False
    s = pool.refresh(s)
    original = deepcopy(s)
    method = PoolMethod()
    method._context = context
    calls = []
    def call(stage, prompt, payload):
        calls.append(stage)
        assert stage == 'deferred_commit_review'
        assert 'allowed_columns' not in payload
        assert 'schema' not in payload
        assert payload['permission_facts']
        assert payload['deletion_transaction']['candidate_action'] == 'refuse'
        return dict(accepted=True, issues=[])
    with method.installed():
        result = commit(method, call, s, context)
        assert method.next_step(result)['action'] == 'refuse'
        assert method.render_session(call, result)['action'] == 'refuse'
    assert calls == ['deferred_commit_review']
    assert not pool.issues(result)
    assert result['reduction_audit'][-1]['method'] == method.VERSION
    assert all(o['status'] == 'pruned' for o in result['obligation_pool']['contributions'].values())
    assert s == original


def test_semantic_rejection_rolls_back_graph_and_pool_lifecycle_together():
    d, context = trace('1038_1')
    s = deepcopy(d['episode']['session'])
    s.pop('deferred_final', None)
    s['deferred_state']['committed'] = False
    s = pool.refresh(s)
    method = PoolMethod()
    method._context = context
    def call(stage, prompt, payload):
        return dict(accepted=False, issues=[dict(code='unrepresented_intent', node_ids=[],
            evidence_id='e1', quote=payload['evidence_catalog']['e1'],
            reason='Synthetic semantic rejection to exercise rollback')])
    with method.installed():
        result = commit(method, call, s, context)
    assert not result['deferred_state']['committed']
    assert result['deferred_final']['action'] == 'unresolved'
    assert result['obligation_pool'] == pool.refresh(result)['obligation_pool']
    assert all(o['status'] == 'blocked' for o in result['obligation_pool']['contributions'].values())


def test_review_cannot_report_authorization_or_unquoted_findings():
    payload = dict(canonical_nodes=[dict(id='f1')], surviving_nodes=[], evidence_catalog={'e1':'Return names'})
    issue = dict(code='permission_denied', node_ids=['f1'], evidence_id='e1', quote='names', reason='Fake authority')
    with pytest.raises(ValueError):
        validate_review(dict(accepted=False, issues=[issue]), payload)
    issue.update(code='unsupported_dependency', quote='not in the actual intent')
    with pytest.raises(ValueError):
        validate_review(dict(accepted=False, issues=[issue]), payload)
    issue.update(code='answer_conflict', quote='names')
    with pytest.raises(ValueError, match='code-blocked'):
        validate_review(dict(accepted=False, issues=[issue]), payload)


def test_answer_coverage_rejection_requires_exact_user_evidence():
    answer = 'A single discount gas station in the Czech Republic'
    raw = dict(accepted=False, issues=[dict(code='missing_intent', quote='discount gas station', reason='Missing category predicate')])
    assert validate(raw, answer) == raw
    raw['issues'][0]['quote'] = 'gold-only hidden information'
    with pytest.raises(ValueError):
        validate(raw, answer)


def test_permission_blocked_leaf_cannot_veto_partial_delivery_review():
    payload = dict(canonical_nodes=[dict(id='f1'), dict(id='f2')],
                   surviving_nodes=[dict(id='f1')],
                   evidence_catalog={'e1': 'Return names and links'},
                   deletion_transaction=dict(drop_ids=['f2'],
                       block_proofs=[dict(proofs={'f2': {'rule': 'denied_dependency'}})]))
    raw = dict(accepted=False, issues=[dict(code='unrepresented_intent', node_ids=['f2'],
        evidence_id='e1', quote='links', reason='Blocked link is absent')])
    result = validate_review(raw, payload)
    assert result == {'accepted': True, 'issues': []}


def test_review_receives_stable_ids_for_code_blocked_aliases():
    session = dict(view=dict(items={'f1': {'role': 'output'}}), deferred_state=dict(
        blocked_nodes={'f2': {'role': 'output'}}, batches=[dict(
            aliases={'p1': 'f2'}, block_proofs={'p1': {'rule': 'no_accepted_local_answer'}})]))
    payload = deletion_input(dict(canonical_nodes=[dict(id='f1'), dict(id='f2')],
        surviving_nodes=[dict(id='f1')], evidence_catalog={'e1': 'Return names and links'}), session)
    assert payload['deletion_transaction']['block_proofs'] == [dict(
        proofs={'f2': {'rule': 'no_accepted_local_answer'}})]
    issue = dict(code='unrepresented_intent', node_ids=['f2'], evidence_id='e1',
                 quote='links', reason='The blocked link is absent')
    assert validate_review(dict(accepted=False, issues=[issue]), payload) == dict(
        accepted=True, issues=[])
    payload['deletion_transaction']['block_proofs'] = [dict(proofs={})]
    assert validate_review(dict(accepted=False, issues=[issue]), payload)['accepted'] is False


def test_deletion_receipt_rejects_proof_without_blocked_owner():
    session = dict(view=dict(items={}), deferred_state=dict(blocked_nodes={},
        batches=[dict(aliases={'p1': 'f2'}, block_proofs={'p1': {'rule': 'denied_dependency'}})]))
    with pytest.raises(ValueError, match='no blocked stable owner'):
        deletion_input({}, session)


def test_answer_coverage_requires_parenthetical_population_qualifier():
    answer = 'Count distinct drivers, each French driver counted once even if they have multiple laps.'
    raw = dict(accepted=False, issues=[dict(code='missing_intent', quote='French driver',
        reason='The answer qualifies the counted population as French drivers.')])
    assert validate(raw, answer) == raw


def test_answer_coverage_accepts_requested_attribute_with_entity_locator():
    answer = 'The element for atom ID TR000_1 is the atom element (chemical symbol).'
    raw = dict(accepted=True, issues=[])
    assert validate(raw, answer) == raw


@pytest.mark.parametrize('code', ['missing_intent', 'missing_dependency'])
def test_real_answer_omission_cannot_publish_when_local_review_rejects(code):
    d, context = trace('1484_1')
    s = deepcopy(d['episode']['snapshots'][1])
    base = s['update_base']
    turn = s['turns'][-1]
    event = dict(kind='answer', target=turn['target_id'], dimension=turn['asked_dimension'],
                 question=turn['question']['focus'], turn=deepcopy(turn))
    c = next(c for c in d['calls'] if c['stage'] == 'semantic_transaction')
    raw = json.loads(c['response']['choices'][0]['message']['content'])
    saved = deepcopy(s)
    stages = []
    def call(stage, prompt, payload):
        stages.append(stage)
        if stage == 'answer_contribution_review':
            assert payload['schema']
            return dict(accepted=False, issues=[dict(code=code, quote='discount gas station', reason='Missing category condition claim')])
        assert stage in ('semantic_transaction', 'semantic_transaction_contract_retry')
        assert 'Edges are a local delta' in prompt
        assert 'Only updates is required.' not in prompt
        return deepcopy(raw)
    method = PoolMethod()
    method._context = context
    with method.installed():
        result = method._transaction(call, s, base, event, context)
    assert result.get('contract_unresolved_reason', '').startswith('Answer coverage could not be committed:')
    assert result['view'] == saved['view']
    assert result['binding_claims'] == saved['binding_claims']
    assert result['obligation_pool'] == saved['obligation_pool']
    assert stages.count('answer_contribution_review') == 1
    assert s == saved
