"""Deterministic lifecycle tests for the independent pool experiment."""
from copy import deepcopy
import pytest
from . import obligations as p
from .pool_method import PoolMethod
from .test_completion_boundary import historical


def claim(meaning='Names', use='returned', state='bound', columns=None):
    return dict(meaning=meaning, use=use, state=state,
                columns=['t.name'] if columns is None else columns)


def session(claims=None):
    claims = [claim()] if claims is None else claims
    required = list(dict.fromkeys(column for c in claims if c['state'] == 'bound'
                                  for column in c['columns']))
    return dict(view=dict(items={'f1': {'role': 'output', 'required_columns': required}}, progressive=dict(decision_ledger=[]),
                          relations=dict(edges=[])),
                binding_claims={'f1': claims})


def test_pool_projection_idempotent_and_initial_ids_code_owned():
    s = p.initialize(session())
    assert p.refresh(s) == s
    assert p.issues(s) == []
    assert s['binding_claims']['f1'][0]['obligation_ids'] == ['o1']
    raw = session([dict(claim(), obligation_ids=['invented'])])
    with pytest.raises(ValueError, match='code-owned'):
        p.initialize(raw)


def test_active_modifier_binding_must_be_in_permission_dependencies():
    s = session([claim('Return names', 'returned', columns=['t.name'])])
    s['view']['items']['m1'] = {'role': 'modifier', 'required_columns': ['t.secret']}
    s['binding_claims']['m1'] = [claim('Restrict population', 'subject', columns=['t.secret'])]
    s = p.initialize(s)
    assert p.issues(s) == []
    drift = deepcopy(s)
    drift['view']['items']['m1']['required_columns'] = []
    assert p.issues(drift) == [dict(code='uncommitted_binding_dependency', node='m1',
                                   columns=['t.secret'])]
    assert PoolMethod().render_session(lambda *args: None, drift)['action'] == 'unresolved'


def test_blocked_modifier_does_not_invalidate_surviving_output():
    s = session([claim('Return names', 'returned', columns=['t.name'])])
    s['view']['items']['m1'] = {'role': 'modifier', 'required_columns': []}
    s['binding_claims']['m1'] = [claim('Denied filter', 'condition', columns=['t.secret'])]
    s['deferred_state'] = {'blocked_nodes': {'m1': {'role': 'modifier'}}, 'committed': False}
    del s['view']['items']['m1']
    s = p.initialize(s)
    assert p.issues(s) == []


def test_open_claim_repeated_column_is_cleared_only_when_bound_claim_covers_it():
    row = {'binding_claims': [
        claim('Name', 'returned', 'bound', ['t.name']),
        claim('Choose aggregation', 'operand', 'open', ['t.name']),
    ]}
    audit = p.normalize_redundant_initial_columns(row)
    assert row['binding_claims'][1]['columns'] == []
    assert audit[0]['rule'] == 'already_covered_by_bound_claim_same_node'

    uncovered = {'binding_claims': [claim('Choose field', 'operand', 'open', ['t.amount'])]}
    assert p.normalize_redundant_initial_columns(uncovered) == []
    assert uncovered['binding_claims'][0]['columns'] == ['t.amount']


def test_split_conserves_commitments_and_is_atomic():
    s = p.initialize(session([claim(), claim('Date', 'condition', 'open', [])]))
    new = deepcopy(s)
    new['view']['items'] = {'a': {}, 'b': {}}
    new['binding_claims'].update(a=[deepcopy(s['binding_claims']['f1'][0])],
                               b=[deepcopy(s['binding_claims']['f1'][1])])
    split = p.transfer(s, new, {'f1': ['a', 'b']})
    assert split['obligation_pool']['contributions']['o2']['holders'][0]['owner'] == 'b'
    assert p.internal_tasks(split)[0][0] == 'b'
    new['binding_claims']['b'] = []
    before = deepcopy(s)
    with pytest.raises(ValueError, match='no inheriting claim'):
        p.transfer(s, new, {'f1': ['a', 'b']})
    assert s == before


def test_no_cross_parent_or_changed_use_and_code_carries_confirmed_dependencies():
    s = p.initialize(session())
    n = deepcopy(s)
    n['view']['items'] = {'a': {}}
    n['binding_claims']['a'] = [dict(claim(), obligation_ids=['o404'])]
    with pytest.raises(ValueError, match='own parent'):
        p.transfer(s, n, {'f1': ['a']})
    n['binding_claims']['a'] = [dict(claim(use='subject'), obligation_ids=['o1'])]
    with pytest.raises(ValueError, match='business use'):
        p.transfer(s, n, {'f1': ['a']})
    n['binding_claims']['a'] = [dict(claim(columns=['t.id']), obligation_ids=['o1'])]
    with pytest.raises(ValueError, match='not compiled before transaction'):
        p.transfer(s, n, {'f1': ['a']})
    from .pool_transitions import compile_inherited_dependencies
    raw = {'updates': {'f1': [{'id': 'a', 'role': 'output', 'kind': 'attribute',
        'columns': ['t.id'], 'binding_claims': [dict(claim(columns=['t.id']), obligation_ids=['o1'])]}]}}
    compiled = compile_inherited_dependencies(raw, s)
    assert compiled['updates']['f1'][0]['binding_claims'][0]['columns'] == ['t.id', 't.name']
    assert compiled['updates']['f1'][0]['columns'] == ['t.id', 't.name']
    n['binding_claims']['a'] = compiled['updates']['f1'][0]['binding_claims']
    n['view']['items']['a']['required_columns'] = compiled['updates']['f1'][0]['columns']
    carried = p.transfer(s, n, {'f1': ['a']})
    assert carried['binding_claims']['a'][0]['columns'] == ['t.id', 't.name']
    retired_raw = deepcopy(raw)
    retired_raw['retire_columns'] = {'f1': ['t.name']}
    retired_compiled = compile_inherited_dependencies(retired_raw, s)
    assert retired_compiled['updates']['f1'][0]['columns'] == ['t.id']
    retired_after = deepcopy(n)
    retired_child = retired_compiled['updates']['f1'][0]
    retired_after['binding_claims']['a'] = retired_child['binding_claims']
    retired_after['view']['items']['a']['required_columns'] = retired_child['columns']
    retired = p.transfer(s, retired_after, {'f1': ['a']}, retired={'f1': ['t.name']})
    assert retired['binding_claims']['a'][0]['columns'] == ['t.id']


def test_split_questions_are_published_only_after_ids_and_blocked_ancestors_compile():
    s = session()
    s['view']['items'] = {'f2': {}}
    s['view']['progressive']['decision_ledger'] = [
        dict(decision_id='d1', owner_id='f2', dimension='field', question='Which field?')]
    s = p.initialize(s)
    new = deepcopy(s)
    new['view']['items'] = {'f3': {}}
    new['view']['progressive']['decision_ledger'] = []
    new['deferred_state'] = dict(blocked_nodes={'f4': {}}, blocked_decisions=[
        dict(decision_id='d1/f4', ancestor_decision_id='d1', owner_id='f4', dimension='field')])
    refreshed = p.refresh(new)
    assert refreshed['obligation_pool']['questions']['d1']['status'] == 'blocked'


def test_exact_unchanged_claims_are_routed_by_code_new_claims_get_fresh_ids():
    s = p.initialize(session())
    n = deepcopy(s)
    n['view']['items'] = {'a': {}}
    n['binding_claims']['a'] = [claim(), claim('Age', columns=['t.age'])]
    result = p.transfer(s, n, {'f1': ['a']})
    assert [c['obligation_ids'] for c in result['binding_claims']['a']] == [['o1'], ['o2']]


def test_open_candidate_columns_may_narrow_when_answer_binds_the_claim():
    s = p.initialize(session([claim('Choose person', 'condition', 'open', ['t.name', 't.id'])]))
    n = deepcopy(s)
    n['view']['items'] = {'a': {}}
    n['binding_claims']['a'] = [dict(claim('Person named Ada', 'condition', 'bound', ['t.name']),
                                     obligation_ids=['o1'])]
    result = p.transfer(s, n, {'f1': ['a']})
    assert result['binding_claims']['a'][0]['columns'] == ['t.name']
    assert result['obligation_pool']['contributions']['o1']['status'] == 'ready'


def test_answer_settlement_does_not_complete_binding_or_repeat_question():
    s = session([claim('Date', 'condition', 'open', [])])
    s['view']['progressive']['decision_ledger'] = [dict(decision_id='d1', owner_id='f1', dimension='value')]
    s = p.initialize(s)
    assert not p.internal_tasks(s)
    n = deepcopy(s)
    n['view']['progressive']['decision_ledger'] = []
    with pytest.raises(ValueError, match='Question disappeared'):
        p.refresh(n)
    n['settled_questions'] = [dict(decision_id='d1')]
    n = p.refresh(n)
    assert n['obligation_pool']['questions']['d1']['status'] == 'answered'
    assert p.issues(n)[0]['code'] == 'pending_internal_obligation'
    assert p.internal_tasks(n)[0][1][0]['claim_id'] == 'b1'
    n['settled_questions'] = []
    assert p.refresh(n)['obligation_pool']['questions']['d1']['status'] == 'answered'


def test_denied_leaf_suspended_then_pruned_unrelated_leaf_retained():
    s = p.initialize(session([claim(), claim('Age', columns=['t.age'])]))
    n = deepcopy(s)
    n['view']['items'] = {'a': {'role': 'output', 'required_columns': ['t.name']}}
    n['deferred_state'] = dict(blocked_nodes={'b': {}}, committed=False)
    n['binding_claims'].update(a=[deepcopy(s['binding_claims']['f1'][0])],
                               b=[deepcopy(s['binding_claims']['f1'][1])])
    n = p.transfer(s, n, {'f1': ['a', 'b']})
    assert [c['status'] for c in n['obligation_pool']['contributions'].values()] == ['ready', 'blocked']
    n['deferred_state']['committed'] = True
    n = p.refresh(n)
    assert [c['status'] for c in n['obligation_pool']['contributions'].values()] == ['ready', 'pruned']
    assert not p.issues(n)


def test_branching_contribution_uses_distinct_local_claim_handles():
    s = p.initialize(session([claim(state='open', columns=[])]))
    n = deepcopy(s)
    n['view']['items'] = {'a': {}}
    n['binding_claims']['a'] = [dict(claim(m, state='open', columns=[]), obligation_ids=['o1'])
                                for m in ('First name', 'Surname')]
    n = p.transfer(s, n, {'f1': ['a']})
    assert [t['claim_id'] for t in p.internal_tasks(n)[0][1]] == ['b1', 'b2']


def test_real_trace_date_binding_is_local_and_no_terminal_global_audit():
    s, context = historical()
    s = p.initialize(s)
    tasks = p.internal_tasks(s)
    assert tasks
    calls = []
    def call(stage, prompt, payload):
        calls.append(stage)
        assert stage == 'obligation_grounding'
        assert 'original_request' not in payload
        return dict(resolutions=[dict(claim_id=t['claim_id'], columns=['yearmonth.Date'])
                                 for t in payload['tasks']], unresolved=[])
    method = PoolMethod()
    method._context = context
    with method.installed():
        result = method._ground(call, s, context)
    assert not p.issues(result)
    assert calls == ['obligation_grounding']
    assert any('yearmonth.Date' in n['required_columns'] for n in result['view']['items'].values())
    assert s['binding_claims'] != result['binding_claims']


def test_commit_compiles_inherited_fields_before_legacy_authorization(monkeypatch):
    from clarification_contract_v5.method import ContractMethod

    before = p.initialize(session([claim(columns=['t.name'])]))
    seen = {}
    def legacy_commit(self, session, base, event, raw, context):
        child = raw['updates']['f1'][0]
        seen['columns'] = list(child['columns'])
        new = deepcopy(session)
        new['view']['items'] = {'c1': {'role': 'output', 'required_columns': list(child['columns'])}}
        new['binding_claims'] = {'c1': deepcopy(child['binding_claims'])}
        new['transaction_audit'] = [{'replacements': {'f1': ['c1']}, 'retire_columns': {}}]
        return new
    monkeypatch.setattr(ContractMethod, 'commit_transaction', legacy_commit)
    raw = {'updates': {'f1': [{'id': 'c1', 'text': 'Return the requested output', 'role': 'output', 'kind': 'attribute',
        'columns': [], 'binding_claims': [dict(claim(), obligation_ids=['o1'])]}]}}
    event = {'kind': 'answer', 'target': 'f1', 'turn': {'answer': {'action': 'choose'}}}
    result = PoolMethod().commit_transaction(before, before['view'], event, raw, {})
    assert seen['columns'] == ['t.name']
    assert result['view']['items']['c1']['required_columns'] == ['t.name']
    assert result['binding_claims']['c1'][0]['columns'] == ['t.name']


def test_transfer_contract_survives_downstream_answer_prompt_replacement(monkeypatch):
    from .method import UsefulResidualMethod
    def downstream(self, call, *args):
        # Reproduce the legacy adapter replacing the complete prompt after the
        # generic validated_call wrapper has already appended its instructions.
        from clarification_deferred_v3_2.prompts import ANSWER_UPDATE
        from clarification_contract_v5.prompts import UPDATE_BINDINGS
        return call('semantic_transaction', ANSWER_UPDATE + UPDATE_BINDINGS, {})
    monkeypatch.setattr(UsefulResidualMethod, '_transaction', downstream)
    seen = []
    PoolMethod()._transaction(lambda stage, prompt, payload: seen.append(prompt), {}, {}, {}, {})
    assert len(seen) == 1
    assert seen[0].startswith('Apply the accepted answer')
    assert p.TRANSFER_CONTRACT in seen[0]
    assert 'Edges are a local delta' in seen[0]
    assert 'GROUNDING CONTRACT' in seen[0]


def test_returned_count_cannot_be_silently_normalized_to_condition():
    from . import pool_method
    row = dict(binding_claims=[claim('Count customers', state='open', columns=[])])
    before = deepcopy(row)
    with PoolMethod().installed():
        with pytest.raises(ValueError, match='requires role=output'):
            pool_method.contracts.promote_claims(row, 'modifier', {}, 'columns', 'calculation')
    assert row == before


def test_reclassification_targets_asked_id_when_new_dimension_already_exists():
    from .pool_method import reclassify_asked_decisions
    rows = [dict(decision_id='d1', owner_id='f1', dimension='scope'),
            dict(decision_id='d2', owner_id='f1', dimension='value')]
    q = dict(item=dict(item_id='f1'), dimension='scope', decision_ids=['d1'])
    selected = reclassify_asked_decisions(dict(progressive=dict(decision_ledger=rows)), q,
                                        dict(clarification=dict(dimension='value')))
    assert selected == [rows[0]]
    selected[0]['dimension'] = 'value'
    assert rows[1] == dict(decision_id='d2', owner_id='f1', dimension='value')


def test_real_menu_reclassification_advances_and_refreshes_pool():
    import json
    from pathlib import Path
    from clarification_deferred_deletion.baseline import module
    path = Path(__file__).resolve().parents[1] / 'artifacts/clarification_main/gpt52_pool_v1_rolefix_1475/1475_2_reduction.json'
    if not path.exists():
        pytest.skip('Live regression trace not distributed')
    d = json.loads(path.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    s = d['episode']['session']
    raw = json.loads(d['calls'][-1]['response']['choices'][0]['message']['content'])
    method = PoolMethod()
    method._context = context
    with method.installed():
        before = method.next_step(s)
        updated, public = method.prepare_menu(lambda *a: deepcopy(raw), s, before, context)
        assert public is None
        assert method.next_step(updated) != before
    assert updated['obligation_pool'] == p.refresh(updated)['obligation_pool']
    assert next(d for d in updated['view']['progressive']['decision_ledger'] if d['decision_id'] == 'd1')['dimension'] == 'value'
