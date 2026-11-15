"""Regression at real v1 failure boundaries; no gold intent enters the method."""
from copy import deepcopy
import json
from pathlib import Path
from threading import Event, Lock, Thread
from time import sleep

import pytest

from . import obligations as pool
from .pool_method import PoolMethod
from .pool_transitions import (answer_review_context, compile_inherited_claims,
    resolve_permission_probes, deletion_input, sanitize_cross_parent_claims,
    compile_model_references)
from .test_obligations import session, claim
from clarification_deferred_deletion.baseline import module

TRACES = Path(__file__).resolve().parents[1] / 'artifacts/clarification_main/gpt52_pool_v1_heldout20_20261028'


def historical(name):
    path = TRACES/(name + '_reduction.json')
    if not path.exists():
        pytest.skip('Historical live trace not distributed')
    d = json.loads(path.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    return d, context


def test_cross_parent_duplicate_obligation_is_removed_only_when_neighbor_still_holds_it():
    s = pool.initialize(session([claim('Output', 'returned', 'open', []),
                                 claim('Subject', 'condition', 'bound', ['t.id'])]))
    subject = deepcopy(s['binding_claims']['f1'][1])
    s['binding_claims']['f1'] = [s['binding_claims']['f1'][0]]
    s['binding_claims']['f2'] = [subject]
    raw = {'updates': {'f1': [{'id': 'p1', 'binding_claims': [
        dict(meaning='Output', use='returned', state='open', columns=[], obligation_ids=['o1']),
        dict(meaning='Copied subject', use='condition', state='bound', columns=['t.id'],
             obligation_ids=['o2'])]}]}}
    sanitize_cross_parent_claims(raw, s)
    assert [c['obligation_ids'] for c in raw['updates']['f1'][0]['binding_claims']] == [['o1']]


def test_reference_compiler_rejects_foreign_pending_decision_before_commit():
    s = pool.initialize(session([claim('Output', 'returned', 'open', [])]))
    s['view']['progressive']['decision_ledger'] = [
        {'decision_id': 'd1', 'owner_id': 'f1', 'dimension': 'field'},
        {'decision_id': 'd2', 'owner_id': 'f2', 'dimension': 'field'},
    ]
    raw = {'updates': {'f1': [{'id': 'p1', 'text': 'Output', 'columns': [],
                               'pending': ['d2'], 'binding_claims': []}]}}
    with pytest.raises(ValueError, match='current parent boundary'):
        compile_model_references(raw, s)


def test_reference_compiler_rejects_unknown_edge_endpoint_before_commit():
    s = pool.initialize(session([claim('Output', 'returned', 'open', [])]))
    raw = {'updates': {'f1': [{'id': 'p1', 'text': 'Output', 'columns': [],
                               'binding_claims': []}]},
           'edges': [['p1', 'phantom', 'requires']]}
    with pytest.raises(ValueError, match='outside the current graph boundary'):
        compile_model_references(raw, s)


def test_reference_compiler_derives_existing_attachment_from_canonical_graph():
    s = pool.initialize(session([claim('Output', 'returned', 'open', [])]))
    base = deepcopy(s['view'])
    base['items']['f2'] = deepcopy(base['items']['f1'])
    base['items']['f2']['item_id'] = 'f2'
    base['relations']['edges'] = [dict(source_id='f1', target_id='f2', kind='context')]
    raw = {'updates': {'f1': [{'id': 'p1', 'text': 'Output', 'columns': [],
                               'binding_claims': []}]},
           'edges': [['f1', 'f2', 'context'], ['p1', 'f2', 'context']]}
    result = compile_model_references(raw, s, base)
    assert result['edges'] == []
    assert len(raw['edges']) == 2


def test_reference_compiler_rejects_new_read_only_boundary_edge():
    s = pool.initialize(session([claim('Output', 'returned', 'open', [])]))
    base = deepcopy(s['view'])
    for iid in ('f2', 'f3'):
        base['items'][iid] = deepcopy(base['items']['f1'])
        base['items'][iid]['item_id'] = iid
    raw = {'updates': {'f1': [{'id': 'p1', 'text': 'Output', 'columns': [],
                               'binding_claims': []}]},
           'edges': [['f2', 'f3', 'context']]}
    with pytest.raises(ValueError, match='local replacement alias'):
        compile_model_references(raw, s, base)


def test_real_841_duplicate_edges_are_compiled_from_transaction_base():
    path = TRACES.parent / 'gpt52_pool_v19_controls4_architecture_20261029' / '841_1_reduction.json'
    if not path.exists():
        pytest.skip('Real 841 failed transaction trace not distributed')
    d = json.loads(path.read_text(encoding='utf8'))
    s = d['episode']['snapshots'][-1]
    base = s['update_base']
    response = next(c for c in d['calls'] if c['stage'] == 'semantic_transaction')
    raw = json.loads(response['response']['choices'][0]['message']['content'])
    compiled = compile_model_references(raw, s, base)
    assert compiled['edges'] == []
    assert raw['edges'] == [['p1', 'f2', 'context']]


def test_inherited_use_is_compiled_before_kind_default_no_semantic_change_allowed():
    s = pool.initialize(session([claim('Cohort', 'condition', 'open', [])]))
    row = dict(role='modifier', kind='instruction', binding_claims=[dict(
        meaning='Clarified cohort', columns=[], obligation_ids=['o1'])])
    compile_inherited_claims(row, 'f1', s)
    assert row['binding_claims'][0]['use'] == 'condition'
    row['binding_claims'][0]['use'] = 'operand'
    compile_inherited_claims(row, 'f1', s)
    assert row['binding_claims'][0]['use'] == 'condition'


def test_internal_permission_resolution_never_settles_business_or_drops_contribution():
    s = session()
    s['view']['items']['f1'] = dict(missing=['permission', 'value'], question_focus=dict(permission='Access?', value='When?'))
    rows = [dict(decision_id='d1', owner_id='f1', dimension='permission'),
            dict(decision_id='d2', owner_id='f1', dimension='value')]
    s['view']['progressive']['decision_ledger'] = rows
    s['view']['segmentation'] = dict(inventory=dict(progressive=deepcopy(s['view']['progressive'])))
    s = pool.initialize(s)
    new = resolve_permission_probes(s)
    assert new['view']['progressive']['decision_ledger'] == [rows[1]]
    assert new['binding_claims'] == s['binding_claims']
    assert new['obligation_pool']['questions']['d1']['status'] == 'internal_routed'
    assert new['obligation_pool']['questions']['d2']['status'] == 'pending'
    assert not new.get('settled_questions')
    assert s['view']['progressive']['decision_ledger'] == rows


@pytest.mark.parametrize('name', ['1510_1', '863_1', '1237_1', '1277_1', '223_1'])
def test_real_decomposition_replay(name):
    d, context = historical(name)
    s = deepcopy(d['episode']['session'])
    s.pop('contract_unresolved_reason', None)
    s.pop('deferred_incomplete', None)
    calls = [c for c in d['calls'] if 'decomposition' in c['stage']]
    raw = json.loads(calls[-1]['response']['choices'][0]['message']['content'])
    original_payload = json.loads(calls[-1]['messages'][1]['content'])
    payload = original_payload.get('input', original_payload)
    none_event = payload.get('none_event')
    method = PoolMethod()
    method._context = context
    def call(stage, prompt, data):
        if stage == 'contract_none_focus':
            return dict(blocked_aliases=data['eligible_seed_aliases'], reason='Current NONE purpose only')
        assert 'decomposition' in stage
        return deepcopy(raw)
    with method.installed():
        result = method._decompose(call, s, context, none_event)
    assert not result.get('contract_unresolved_reason'), result.get('contract_unresolved_reason')
    assert result.get('deferred_state')
    assert result['deferred_state']['batches'][-1]['pruning_policy'] == method.VERSION
    assert all(d['dimension'] != 'permission' for d in result['view']['progressive']['decision_ledger'])
    assert result['obligation_pool'] == pool.refresh(result)['obligation_pool']


def test_real_84_declaration_cannot_move_returned_information_into_a_condition():
    d, context = historical('84_1')
    s = deepcopy(d['episode']['session'])
    s.pop('contract_unresolved_reason', None)
    s.pop('deferred_incomplete', None)
    raw = json.loads(d['calls'][-1]['response']['choices'][0]['message']['content'])
    method = PoolMethod()
    method._context = context
    with method.installed():
        result = method._decompose(lambda *a: deepcopy(raw), s, context)
    assert 'Preserve returned obligation' in result['contract_unresolved_reason']
    assert result['binding_claims'] == s['binding_claims']
    assert not result.get('deferred_state')


def test_decomposition_rejects_missing_affects_instead_of_losing_result_dependency():
    d, context = historical('84_1')
    s = deepcopy(d['episode']['session'])
    s.pop('contract_unresolved_reason', None)
    s.pop('deferred_incomplete', None)
    raw = json.loads(d['calls'][-1]['response']['choices'][0]['message']['content'])
    raw['nodes'][0].pop('affects', None)
    method = PoolMethod()
    method._context = context
    with method.installed():
        result = method._decompose(lambda *a: deepcopy(raw), s, context)
    assert 'affects' in result['contract_unresolved_reason']
    assert result['binding_claims'] == s['binding_claims']
    assert not result.get('deferred_state')


def test_real_scope_to_field_transition_advances_without_stale_menu():
    d, context = historical('1464_0')
    s = d['episode']['session']
    raw = json.loads(d['calls'][-1]['response']['choices'][0]['message']['content'])
    method = PoolMethod()
    method._context = context
    with method.installed():
        plan = method.next_step(s)
        asked = method.engine.decision(s, plan)['decision_ids']
        result, public = method.prepare_menu(lambda *a: deepcopy(raw), s, plan, context)
        assert public is None
        assert method.next_step(result) != plan
    assert all(d['dimension'] == 'field' for d in result['view']['progressive']['decision_ledger']
               if d['decision_id'] in asked)
    assert result['obligation_pool'] == pool.refresh(result)['obligation_pool']


def test_empty_candidate_deletion_review_gets_explicit_commit_receipt():
    d, _ = historical('1484_1')
    s = d['episode']['session']
    payload = json.loads(d['calls'][-1]['messages'][1]['content'])
    result = deletion_input(payload, s)
    receipt = result['deletion_transaction']
    assert receipt['candidate_action'] == 'refuse'
    assert receipt['drop_ids'] == sorted(s['deferred_state']['blocked_nodes'])
    assert receipt['remaining_objectives'] == 0
    assert receipt['block_proofs']
    assert payload.get('deletion_transaction') is None


def test_raw_operand_state_is_normalized_before_instruction_propagation():
    folder = TRACES.parent/'gpt52_pool_v3_commit_answer6_20261028'
    path = folder/'1038_1_reduction.json'
    if not path.exists():
        pytest.skip('Historical normalization-order trace not distributed')
    d = json.loads(path.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    s = deepcopy(d['episode']['session'])
    s.pop('contract_unresolved_reason', None)
    s.pop('deferred_incomplete', None)
    raw = json.loads(d['calls'][-1]['response']['choices'][0]['message']['content'])
    method = PoolMethod()
    method._context = context
    with method.installed():
        result = method._decompose(lambda *a: deepcopy(raw), s, context)
    assert not result.get('contract_unresolved_reason'), result.get('contract_unresolved_reason')
    batch = result['deferred_state']['batches'][-1]
    owner = batch['aliases']['p3']
    assert result['binding_claims'][owner][0]['state'] == 'bound'
    assert result['binding_claims'][owner][0]['columns']


def test_none_facets_share_one_contribution_but_independent_ids_must_split():
    from .pool_decomposition import none_eligible
    plan = dict(nodes=[dict(id='p1', parent='f1', decision_ids=['d1', 'd2'])])
    event = dict(target='f1', asked_ids=['d1'])
    claims = dict(p1=[dict(claim('Cohort', 'condition'), obligation_ids=['o1'])])
    assert none_eligible(plan, claims, event) == ['p1']
    claims['p1'].append(dict(claim('Birthday'), obligation_ids=['o2']))
    with pytest.raises(ValueError, match='independent contribution'):
        none_eligible(plan, claims, event)


def test_inherited_bound_literal_does_not_reopen_when_model_omits_state():
    s = pool.initialize(session([claim('Top five', 'condition', 'bound', [])]))
    row = dict(id='p1', role='modifier', binding_claims=[dict(
        meaning='Top five', columns=[], obligation_ids=['o1'])])
    compile_inherited_claims(row, 'f1', s)
    assert row['binding_claims'][0]['state'] == 'bound'
    assert row['binding_claims'][0]['use'] == 'condition'


def test_inherited_operand_cannot_be_promoted_to_an_unrequested_output():
    s = pool.initialize(session([claim('Ranking score', 'operand', 'bound', ['t.x'])]))
    row = dict(id='p2', role='output', binding_claims=[dict(
        meaning='Ranking score', columns=['t.x'], obligation_ids=['o1'])])
    with pytest.raises(ValueError, match='Node p2.*non-returned'):
        compile_inherited_claims(row, 'f1', s)


def test_business_subject_identity_is_not_a_physical_join_even_if_kind_echo_is_relation():
    s = pool.initialize(session([claim('Specified unit', 'subject', 'open', [])]))
    row = dict(id='p1', role='modifier', kind='relation', binding_claims=[dict(
        meaning='Specified unit', columns=[], obligation_ids=['o1'])])
    compile_inherited_claims(row, 'f1', s)
    assert row['kind'] == 'attribute'
    assert row['binding_claims'][0]['use'] == 'subject'


def test_real_none_subject_split_compiles_relation_label_before_lineage():
    path = TRACES.parent/'gpt52_pool_v5_contract4_20261028'/'223_1_reduction.json'
    if not path.exists():
        pytest.skip('Historical subject-kind trace not distributed')
    d = json.loads(path.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    s = deepcopy(d['episode']['session'])
    s.pop('contract_unresolved_reason', None)
    s.pop('deferred_incomplete', None)
    call_data = d['calls'][-1]
    raw = json.loads(call_data['response']['choices'][0]['message']['content'])
    payload = json.loads(call_data['messages'][1]['content'])
    event = payload.get('input', payload)['none_event']
    # Correct the unrelated duplicated decision annotation from this same raw
    # proposal: the regression under test is subject-kind compilation.
    raw['nodes'][0]['decision_ids'] = []
    method = PoolMethod()
    method._context = context
    with method.installed():
        result = method._decompose(lambda *a: deepcopy(raw), s, context, event)
    assert not result.get('contract_unresolved_reason'), result.get('contract_unresolved_reason')
    assert result['deferred_state']['blocked_nodes']
    assert not result['view']['items']


def test_real_84_split_publishes_only_after_descendant_decision_ids_are_allocated(monkeypatch):
    path = TRACES.parent/'gpt52_pool_v4_flow10_20261028'/'84_1_reduction.json'
    if not path.exists():
        pytest.skip('Historical successful split trace not distributed')
    d = json.loads(path.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    call_data = [c for c in d['calls'] if c['stage'] == 'contract_none_decomposition'][-1]
    raw = json.loads(call_data['response']['choices'][0]['message']['content'])
    session_before = deepcopy(d['episode']['snapshots'][0])
    payload = json.loads(call_data['messages'][1]['content'])
    none_event = payload.get('input', payload).get('none_event')
    from . import method as useful_method
    from clarification_deferred_deletion.baseline import updates
    register = useful_method.register_questions
    core_finish = updates.finish_view
    injected = {'done': False}
    def duplicate_transient_ledger(session, context):
        result = core_finish(session, context)
        if (getattr(method, '_decomposing_pool', False) and not injected['done']
                and len(result.get('view', {}).get('items', {})) > 1):
            rows = result['view']['progressive']['decision_ledger']
            if rows:
                duplicate = deepcopy(rows[0])
                duplicate['owner_id'] = next(i for i in result['view']['items']
                                             if i != duplicate['owner_id'])
                rows.append(duplicate)
                injected['done'] = True
        return result
    def reject_transient_publication(session):
        ledger = session['view']['progressive']['decision_ledger']
        ids = [d['decision_id'] for d in ledger]
        if len(ids) != len(set(ids)):
            raise ValueError('Transient split queue must not be published')
        return register(session)
    monkeypatch.setattr(updates, 'finish_view', duplicate_transient_ledger)
    monkeypatch.setattr(useful_method, 'register_questions', reject_transient_publication)
    method = PoolMethod()
    method._context = context
    saved = lambda stage, prompt, payload: deepcopy(raw)
    with method.installed():
        result = method._decompose(saved, session_before, context, none_event)
    assert not result.get('contract_unresolved_reason'), result.get('contract_unresolved_reason')
    assert injected['done']
    active_ids = [d['decision_id'] for d in result['view']['progressive']['decision_ledger']]
    assert len(active_ids) == len(set(active_ids))
    assert all(d['owner_id'] in result['view']['items'] for d in result['view']['progressive']['decision_ledger'])
    for question in result['view']['questions']:
        assert question['decision_ids']
        assert set(question['decision_ids']) <= set(active_ids)
    assert result['obligation_pool'] == pool.refresh(result)['obligation_pool']


def test_real_1072_binds_open_menu_choice_before_transaction_permission_projection():
    path = TRACES.parent/'gpt52_pool_v7_heldout20_20261028'/'1072_1_reduction.json'
    if not path.exists():
        pytest.skip('Historical answer transaction trace not distributed')
    record = json.loads(path.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[record['db_id']])
    prior = deepcopy(record['episode']['snapshots'][0])
    post_question = record['episode']['snapshots'][1]
    turn = deepcopy(post_question['turns'][-1])
    base = post_question['update_base']
    raw = json.loads([c for c in record['calls'] if c['stage'] == 'semantic_transaction_contract_retry'][-1]
                     ['response']['choices'][0]['message']['content'])
    event = {'kind': 'answer', 'target': turn['target_id'], 'dimension': turn['asked_dimension'],
             'question': turn['question']['focus'], 'turn': turn}
    method = PoolMethod()
    method._context = context
    method._answer_review_call = lambda *args: dict(accepted=True, issues=[])
    with method.installed():
        result = method.commit_transaction(prior, base, event, raw, context)
    replacements = result['transaction_audit'][-1]['replacements']['f1']
    assert len(replacements) == 1
    node = result['view']['items'][replacements[0]]
    claims = result['binding_claims'][replacements[0]]
    selected_condition = next(c for c in claims if c['obligation_ids'] == ['o3'])
    assert selected_condition['state'] == 'bound'
    assert selected_condition['columns'] == ['Player.player_name']
    assert set(selected_condition['columns']) <= set(node['required_columns'])
    assert 'Player.player_api_id' not in node['required_columns']
    assert node['denied_columns'] == sorted(set(node['required_columns']) - set(result['allowed']))


def test_answer_review_context_contains_prior_menu_and_current_selection():
    from .pool_transitions import answer_review_context
    session = {'view': {'progressive': {'parents': {'f2': 'f1'}}}, 'turns': [
        {'target_id': 'f1', 'question': {'question': 'Which category?', 'focus': 'Which category?'},
         'decision_ids': ['d1'], 'selected_options': [{'text': 'Discount gas station'}],
         'answer': {'action': 'choose'}}]}
    event = {'kind': 'answer', 'target': 'f2', 'dimension': 'field', 'question': 'Which field?',
             'turn': {'decision_ids': ['d2'], 'selected_options': [{'text': 'Name'}],
                      'answer': {'action': 'choose', 'text': 'Return name'}}}
    context = answer_review_context(session, event)
    assert context['prior_interactions'][0]['selected_options'][0]['text'] == 'Discount gas station'
    assert context['asked_interaction']['selected_options'][0]['text'] == 'Name'
    assert context['asked_interaction']['answer']['text'] == 'Return name'


def test_global_legacy_adapter_scope_is_serialized_between_cases():
    state_lock, entered = Lock(), Event()
    state = {'active': 0, 'overlap': False, 'errors': []}
    def run():
        try:
            with PoolMethod().installed():
                with state_lock:
                    state['overlap'] |= state['active'] > 0
                    state['active'] += 1
                entered.set()
                sleep(0.03)
                with state_lock:
                    state['active'] -= 1
        except BaseException as exc:
            state['errors'].append(exc)
    first = Thread(target=run)
    second = Thread(target=run)
    first.start()
    assert entered.wait(2)
    second.start()
    first.join(2)
    second.join(2)
    assert not first.is_alive() and not second.is_alive()
    assert not state['overlap']
    assert not state['errors']


@pytest.mark.parametrize('case,prior_option,current_quote', [
    ('322_1', 'Element', None),
    ('963_0', None, 'French driver')])
def test_answer_review_context_replays_real_menu_and_answer_evidence(case, prior_option, current_quote):
    if case == '322_1':
        path = TRACES.parent/'useful_residual_v6_heldout20_live_20261026'/(case+'_reduction.json')
    else:
        path = TRACES.parent/'gpt52_pool_v7_heldout20_20261028'/(case+'_reduction.json')
    if not path.exists():
        pytest.skip('Historical interaction trace not distributed')
    record = json.loads(path.read_text(encoding='utf8'))
    turns = record['episode']['session'].get('turns', [])
    assert turns
    previous = {'view': record['episode']['session'].get('view'), 'turns': turns[:-1]}
    last = turns[-1]
    event = {'kind': 'answer', 'target': last['target_id'],
             'dimension': last.get('asked_dimension'),
             'question': last.get('question', {}).get('focus'), 'turn': last}
    context = answer_review_context(previous, event)
    if prior_option:
        assert any(option.get('text') == prior_option
                   for interaction in context['prior_interactions']
                   for option in interaction.get('selected_options', []))
    if current_quote:
        assert current_quote in context['asked_interaction']['answer'].get('text', '')


@pytest.mark.parametrize('stage', ['contract_none_decomposition', 'contract_permission_decomposition'])
def test_final_decomposition_prompt_is_one_consistent_pool_contract(stage):
    # Exercise the inherited adapter which otherwise overwrites new prompts.
    from .pool_prompts import DECOMPOSE
    from clarification_deferred_deletion.baseline import analysis
    seen = []
    method = PoolMethod()
    def call(actual_stage, prompt, payload):
        seen.append((actual_stage, prompt))
        return {}
    with method.installed():
        with pytest.raises((ValueError, KeyError, TypeError)):
            analysis.validated_call(call, stage, 'obsolete contract', {}, lambda x: x)
    assert seen and all(prompt == DECOMPOSE for _, prompt in seen)
    assert 'The seed leaf may own only the asked IDs' not in DECOMPOSE
    assert 'Code compiles use and state from the node role/kind' not in DECOMPOSE
    assert 'association-table endpoints' in DECOMPOSE


@pytest.mark.parametrize('original', ['scope', 'field', 'permission'])
@pytest.mark.parametrize('target', ['scope', 'field', 'operation', 'value'])
def test_all_question_type_changes_share_atomic_route(original, target):
    import clarification_deferred_v3_2.method as typed
    method = PoolMethod()
    d = dict(clarification=dict(dimension=target))
    if target != original or target in ('operation', 'value'):
        with pytest.raises(typed.ReclassifyQuestion):
            method.decision_dimension(d, original)
    else:
        assert method.decision_dimension(d, original) == target


def test_public_retry_identifies_exact_leak_in_quoted_context_without_changing_menu():
    context = dict(tables=[dict(name='atom', columns=[dict(name='atom_id')])])
    original = dict(question='Return atom_id for which unit?', focus='Which unit?',
                    options=[dict(id='A', label='Molecule')], multi_select=False)
    seen = []
    def call(stage, prompt, payload):
        seen.append(payload)
        assert stage.startswith('public_wording')
        if len(seen) == 1:
            assert payload['forbidden_schema_spellings'] == ['atom_id']
            return dict(public=deepcopy(original))
        assert 'atom_id' in payload['contract_error']
        fixed = deepcopy(original)
        fixed['question'] = 'Return atom identifiers for which unit?'
        return dict(public=fixed)
    result = PoolMethod()._public_text(call, original, context)
    assert len(seen) == 2
    assert result['options'] == original['options']
    assert original['question'] == 'Return atom_id for which unit?'
