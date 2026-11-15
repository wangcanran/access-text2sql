"""Failure traces become contract tests, without claiming semantic parity."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from clarification_deferred_deletion.baseline import module
from .bindings import (closure_issues, promote_claims, complete_open_question_claims,
                       derive_requires_operand_claims, remove_covered_summary_roots)
from .bindings import (route_unique_decision_dimensions, normalize_subject_edges,
                       complete_open_question_claims, promote_claims, compile_subject_lineage,
                       validate_subject_edges, normalize_plan_bindings)
from .contracts import normalize_choices, normalize_questions
from .method import ContractMethod, validate_requester_literal
from clarification_deferred_deletion.method import reframe


RUN = Path('artifacts/clarification_main/deferred_v4_1_heldout20_live_20261025')


def trace(qid, level):
    d = json.loads((RUN/f'{qid}_{level}_reduction.json').read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    return d, context


def test_entity_value_is_code_routed_to_open_question():
    raw = dict(fragments=[dict(open_choices=[dict(answer_kind='entity_value',
        dimension='scope', span='a specific player', question='Which player?')])])
    got = normalize_choices(raw)
    assert got['fragments'][0]['open_choices'][0]['dimension'] == 'value'
    assert raw['fragments'][0]['open_choices'][0]['dimension'] == 'scope'


def test_untyped_question_cannot_create_a_ledger_obligation():
    with pytest.raises(ValueError, match='answer_kind'):
        normalize_choices(dict(fragments=[dict(open_choices=[dict(dimension='scope')])]))
    with pytest.raises(ValueError, match='answer_kinds'):
        normalize_questions(dict(question_focus={'scope': 'Which country?'}, missing=['scope']))


def test_mapping_normalizes_entity_value_and_preserves_specific_question():
    row = dict(question_focus={'scope': 'Which player?', 'field': 'Which output field?'},
               answer_kinds={'scope': 'entity_value', 'field': 'attribute'},
               missing=['scope', 'field'], field_candidates=[dict(column='Player.player_name')])
    normalize_questions(row)
    assert row['question_focus'] == {'value': 'Which player?', 'field': 'Which output field?'}


def test_returned_claim_requires_real_returned_dependency():
    d, context = trace(863, 1)
    row = dict(columns=['races.name'], binding_claims=[dict(
        meaning='Return the season page', use='returned', state='bound',
        columns=['seasons.url'])])
    claims = promote_claims(row, 'output', context, 'columns')
    assert 'seasons.url' in row['columns']
    assert 'seasons.url' in claims[0]['columns']
    assert 'seasons.url' not in d['allowed_columns']
    with pytest.raises(ValueError, match='returned claim'):
        promote_claims(dict(columns=['races.name'], binding_claims=[dict(
            meaning='Select race by name', use='subject', state='bound',
            columns=['races.name'])]), 'output', context, 'columns')


def test_output_without_binding_cannot_be_delivered():
    d, _ = trace(863, 1)
    s = deepcopy(d['episode']['session'])
    assert closure_issues(s)[0]['code'] == 'output_without_return_binding'
    method = ContractMethod()
    with method.installed():
        assert method.next_step(s)['action'] == 'unresolved'
        assert method.render_session(lambda *args: None, s)['available_for_text2sql'] is False


def test_compiler_only_uses_catalog_handles():
    method = ContractMethod()
    payload = dict(schema={'tables': {'cards': {'id': ['INTEGER', ''], 'name': ['TEXT', '']}}})
    prompt, payload = method.decision_input('legacy', payload)
    assert payload['candidate_catalog'] == {'c1': 'cards.id', 'c2': 'cards.name'}
    assert 'candidate_catalog handles' in prompt
    assert method._resolve_candidates({'field_candidates': [dict(column='c2', label='Card name')]})[
        'field_candidates'][0]['column'] == 'cards.name'
    with pytest.raises(ValueError, match='Unknown candidate handle'):
        method._resolve_candidates({'field_candidates': [dict(column='cards.unknown', label='Fake')]})


def test_none_for_compound_owner_always_enters_decomposition():
    d, context = trace(1237, 1)
    s = deepcopy(d['episode']['snapshots'][0])
    decision = s['view']['questions'][0]
    assert len(s['view']['progressive']['decision_ledger']) > 1
    captured = {}
    def decompose(call, session, context, none_event):
        captured.update(none_event)
        return session
    method = ContractMethod()
    method._decompose = decompose
    with method.installed():
        method._remove(lambda *args: None, s, s, decision, 'no_accepted_local_answer', context)
    assert captured['target'] == decision['item']['item_id']
    assert captured['asked_ids'] == decision['decision_ids']


def test_none_plan_must_split_asked_from_other_decisions():
    d, context = trace(1237, 1)
    s = deepcopy(d['episode']['snapshots'][0])
    q = s['view']['questions'][0]
    from clarification_deferred_deletion.graph import canonical_plan
    raw = dict(nodes=[dict(id='p1', parent='f1', text='Entire parent', role='output',
        kind='attribute', columns=['Laboratory.HGB', 'Laboratory.ID', 'Patient.ID'], questions={}, candidates={},
        decision_ids=[x['decision_id'] for x in s['view']['progressive']['decision_ledger']])],
        edges=[], retired_dependencies={})
    # Canonical planner accepts routing all decisions to one leaf; v5 must not.
    canonical_plan(raw, s['view'], context, s['view']['progressive']['decision_ledger'])
    asked = set(q['decision_ids'])
    owners = [n for n in raw['nodes'] if set(n['decision_ids']) & asked]
    assert any(set(n['decision_ids']) - asked for n in owners)


def test_code_adds_open_claim_for_omitted_pending_operation_without_retyping_it():
    decision = dict(decision_id='d2', dimension='operation', question='What ranking criterion?')
    node = dict(role='modifier', questions={'operation': decision['question']}, binding_claims=[])
    complete_open_question_claims(node, [decision])
    assert node['binding_claims'] == [dict(meaning=decision['question'], use='operand',
                                          state='open', columns=[])]
    malformed = dict(role='modifier', questions={'value': decision['question']}, binding_claims=[])
    with pytest.raises(ValueError, match='original dimension'):
        complete_open_question_claims(malformed, [decision])


def test_pending_question_dimension_is_exact_and_claim_type_is_code_owned():
    operation = dict(decision_id='d9', dimension='operation',
                     question='What ranking criterion?')
    node = dict(role='modifier', questions={'value': operation['question']},
                binding_claims=[])
    with pytest.raises(ValueError, match='original dimension'):
        complete_open_question_claims(node, [operation])

    node = dict(role='modifier', questions={'operation': operation['question']},
                binding_claims=[dict(meaning=operation['question'], use='condition',
                                     state='open', columns=[])])
    with pytest.raises(ValueError, match='claim type'):
        complete_open_question_claims(node, [operation])

    scope = dict(decision_id='d10', dimension='scope',
                 question='Which organizations were intended?')
    renamed = dict(role='output', questions={'scope': 'Which organization class applies?'},
                   binding_claims=[])
    complete_open_question_claims(renamed, [scope])
    assert renamed['binding_claims'] == [dict(meaning=scope['question'], use='condition',
                                              state='open', columns=[])]
    shifted = dict(role='output', questions={'value': 'Which organization class applies?'},
                   binding_claims=[])
    with pytest.raises(ValueError, match='original dimension'):
        complete_open_question_claims(shifted, [scope])


def test_structural_relation_modifier_does_not_need_redundant_claim():
    context = {'tables': [
        {'name': 'Team', 'columns': [{'name': 'team_api_id'}]},
        {'name': 'Team_Attributes', 'columns': [{'name': 'team_api_id'}]},
    ]}
    row = dict(kind='relation', columns=['Team.team_api_id',
                                       'Team_Attributes.team_api_id'],
               binding_claims=[])
    claims = promote_claims(row, 'modifier', context, 'columns', 'relation')
    assert claims == []
    assert set(row['columns']) == {'Team.team_api_id', 'Team_Attributes.team_api_id'}

    omitted = dict(kind='relation', columns=['Team.team_api_id',
                                            'Team_Attributes.team_api_id'])
    assert promote_claims(omitted, 'modifier', context, 'columns', 'relation') == []
    with pytest.raises(ValueError, match='business contribution'):
        promote_claims(dict(kind='instruction', columns=[], binding_claims=[]),
                       'modifier', context, 'columns', 'instruction')


def test_requires_edge_propagates_existing_operand_claim_to_instruction():
    nodes = [
        {'id': 'order', 'role': 'modifier', 'kind': 'instruction',
         'text': 'Order teams by chance-creation passing', 'columns': [],
         'binding_claims': []},
        {'id': 'measure', 'role': 'modifier', 'kind': 'attribute',
         'text': 'Use chance-creation passing as the ordering operand',
         'columns': ['Team_Attributes.chanceCreationPassing'],
         'binding_claims': [dict(meaning='Ordering measure', use='operand',
             state='bound', columns=['Team_Attributes.chanceCreationPassing'])]},
    ]
    derive_requires_operand_claims(nodes, [['order', 'measure', 'requires']])
    assert nodes[0]['binding_claims'] == [dict(
        meaning='Order teams by chance-creation passing', use='operand', state='bound',
        columns=['Team_Attributes.chanceCreationPassing'])]
    assert nodes[0]['columns'] == ['Team_Attributes.chanceCreationPassing']

    unrelated = {'id': 'order', 'role': 'modifier', 'kind': 'instruction',
                 'text': 'Order teams', 'columns': [], 'binding_claims': []}
    derive_requires_operand_claims([unrelated, nodes[1]], [])
    assert unrelated['binding_claims'] == []


def test_redundant_summary_root_requires_full_claim_and_column_coverage():
    summary = {'id': 'f1', 'parent': None, 'columns': ['t.a'],
               'decision_ids': [], 'binding_claims': [
                   dict(use='returned', columns=[], meaning='Return detail')]}
    leaf = {'id': 'p1', 'parent': 'f1', 'columns': ['t.a'],
            'binding_claims': [dict(use='returned', columns=[], meaning='Return detail')]}
    raw = {'nodes': [summary, leaf], 'edges': [], 'retired_dependencies': {}}
    assert remove_covered_summary_roots(raw, {'f1'})['nodes'] == [leaf]
    assert raw['nodes'] == [summary, leaf]
    missing = {**leaf, 'columns': []}
    assert remove_covered_summary_roots({**raw, 'nodes': [summary, missing]}, {'f1'})[
        'nodes'] == [summary, missing]


def test_value_answer_cannot_retire_confirmed_output_after_normalization():
    folder = Path('artifacts/clarification_main/contract_v5_10_targeted_three_live_20261025')
    d = json.loads((folder/'131_0_reduction.json').read_text(encoding='utf8'))
    s = next(x for x in d['episode']['snapshots']
             if x.get('update_base') and len(x.get('turns', [])) == 2)
    response = [c for c in d['calls'] if c['stage'] == 'semantic_transaction'][-1]
    raw = json.loads(response['response']['choices'][0]['message']['content'])
    assert 'retire_columns' in raw['updates']
    from clarification_deferred_deletion.baseline import module
    from clarification_deferred_v3_2.test_obligations import event_for
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    method = ContractMethod()
    with method.installed(), pytest.raises(ValueError, match='cannot retire confirmed dependencies'):
        method.commit_transaction(s, s['update_base'], event_for(s), raw, context)


def test_distinct_pending_decisions_may_share_question_wording():
    question = 'Which specific loan-related characteristic should distinguish the unit?'
    node = dict(role='output', questions={'field': question, 'value': question},
                binding_claims=[dict(meaning=question, use='returned',
                                     state='open', columns=[])])
    decisions = [dict(decision_id='d2', dimension='value', question=question),
                 dict(decision_id='d3', dimension='field', question=question)]
    complete_open_question_claims(node, decisions)
    assert {(c['use'], c['state']) for c in node['binding_claims']} == {
        ('returned', 'open'), ('condition', 'open')}


def test_selected_operand_survives_inside_compound_output():
    folder = Path('artifacts/clarification_main/contract_v5_11_heldout8_live_20261025')
    d = json.loads((folder/'1369_0_reduction.json').read_text(encoding='utf8'))
    s = d['episode']['snapshots'][-1]
    response = [c for c in d['calls'] if c['stage'] == 'semantic_transaction'][-1]
    raw = json.loads(response['response']['choices'][0]['message']['content'])
    from clarification_deferred_v3_2.test_obligations import event_for
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    result = ContractMethod().commit_transaction(s, s['update_base'], event_for(s), raw, context)
    child = result['transaction_audit'][-1]['replacements']['f3'][0]
    assert 'budget.amount' in result['view']['items'][child]['required_columns']


def test_answered_scope_is_not_restored_by_reframe():
    folder = Path('artifacts/clarification_main/contract_v5_12_targeted_live_20261025')
    d = json.loads((folder/'1369_0_reduction.json').read_text(encoding='utf8'))
    s = next(x for x in d['episode']['snapshots'] if x.get('update_base'))
    response = next(c for c in d['calls'] if c['stage'] == 'semantic_transaction')
    raw = json.loads(response['response']['choices'][0]['message']['content'])
    from clarification_deferred_v3_2.test_obligations import event_for
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    result = ContractMethod().commit_transaction(s, s['update_base'], event_for(s), raw, context)
    assert all('scope' not in n['missing'] for n in result['view']['items'].values())


def test_unique_dimension_routes_repair_swapped_ids_without_guessing_ambiguous_routes():
    folder = Path('artifacts/clarification_main/contract_v5_11_heldout8_live_20261025')
    d = json.loads((folder/'131_0_reduction.json').read_text(encoding='utf8'))
    raw = json.loads(next(c for c in d['calls'] if c['stage'] == 'contract_none_decomposition')
                     ['response']['choices'][0]['message']['content'])
    decisions = d['episode']['snapshots'][-1]['view']['progressive']['decision_ledger']
    nodes = route_unique_decision_dimensions(raw['nodes'], decisions)
    assert {n['id']: set(n['decision_ids']) for n in nodes} == {
        'p1': {'d2', 'd3'}, 'p2': {'d1'}}
    ambiguous = [dict(parent='f1', questions={'field': 'A'}, decision_ids=['d1']),
                 dict(parent='f1', questions={'field': 'B'}, decision_ids=[])]
    old = [dict(owner_id='f1', decision_id='d1', dimension='field')]
    assert route_unique_decision_dimensions(ambiguous, old) == ambiguous
    assert ambiguous[0]['decision_ids'] == ['d1']


def test_unique_id_holder_restores_ledger_dimension_before_claim_validation():
    folder = Path('artifacts/clarification_main/contract_v5_14_131_live_20261025')
    d = json.loads((folder/'131_0_reduction.json').read_text(encoding='utf8'))
    raw = json.loads(next(c for c in d['calls'] if c['stage'] == 'contract_none_decomposition')
                     ['response']['choices'][0]['message']['content'])
    decisions = d['episode']['snapshots'][-1]['view']['progressive']['decision_ledger']
    nodes = route_unique_decision_dimensions(raw['nodes'], decisions)
    by_id = {n['id']: n for n in nodes}
    assert by_id['p1']['decision_ids'] == ['d2']
    assert by_id['p2']['decision_ids'] == ['d1']
    assert by_id['p2']['questions']['value'] == decisions[0]['question']


def test_requester_cannot_replace_explicit_named_entity_with_a_category():
    payload = {'private_original': 'What is the type of card "Benalish Knight"?',
               'question': {'question': 'Which particular card should be examined?'}}
    with pytest.raises(ValueError, match='named entity'):
        validate_requester_literal({'action': 'answer', 'answer': 'Creature'}, payload)
    assert validate_requester_literal({'action': 'answer', 'answer': 'Benalish Knight'}, payload)
    assert validate_requester_literal({'action': 'choose', 'selected_ids': ['A']}, payload)


def test_settled_decision_ids_from_expanded_permission_trace_are_ignored():
    folder = Path('artifacts/clarification_main/contract_v5_16_heldout20_live_20261025')
    d = json.loads((folder/'1237_1_reduction.json').read_text(encoding='utf8'))
    raw = json.loads(next(c for c in d['calls'] if c['stage'] == 'contract_permission_decomposition')
                     ['response']['choices'][0]['message']['content'])
    snapshot = next(s['update_base'] for s in reversed(d['episode']['snapshots'])
                    if s.get('update_base'))
    decisions = snapshot['progressive']['decision_ledger']
    stale = {i for n in raw['nodes'] for i in n['decision_ids']} - {
        x['decision_id'] for x in decisions}
    assert 'd3' in stale
    route_unique_decision_dimensions(raw['nodes'], decisions)
    assert all(stale.isdisjoint(n['decision_ids']) for n in raw['nodes'])
    # Every pending identity remains assigned; only IDs outside the ledger vanish.
    assert {i for n in raw['nodes'] for i in n['decision_ids']} == {
        x['decision_id'] for x in decisions}


def test_bound_subject_modifier_cannot_become_optional_filter():
    nodes = [
        {'id': 'out', 'role': 'output'},
        {'id': 'scope', 'role': 'modifier', 'binding_claims': [
            {'use': 'subject', 'state': 'bound'}]},
    ]
    assert normalize_subject_edges(nodes, [['scope', 'out', 'applies_to']]) == [
        ['out', 'scope', 'requires']]
    # Ordinary condition claims retain optional applies_to semantics.
    nodes[1]['binding_claims'] = [{'use': 'condition', 'state': 'bound'}]
    assert normalize_subject_edges(nodes, [['scope', 'out', 'applies_to']]) == [
        ['scope', 'out', 'applies_to']]


def test_claim_compiler_owns_control_fields_and_preserves_content():
    context = {'tables': [{'name': 'Patient', 'columns': [
        {'name': 'ID'}, {'name': 'Admission'}]}]}
    row = {'columns': ['Patient.ID'], 'binding_claims': [
        {'meaning': 'Returned patient identifier', 'use': 'invented prose label',
         'state': 'invented state', 'columns': ['Patient.ID']}]}
    claims = promote_claims(row, 'output', context, 'columns', 'attribute')
    assert claims == [dict(meaning='Returned patient identifier', use='returned',
                           state='bound', columns=['Patient.ID'])]
    open_row = {'columns': [], 'binding_claims': [
        {'meaning': 'Patient subgroup', 'columns': []}]}
    assert promote_claims(open_row, 'modifier', context, 'columns', 'attribute')[0]['state'] == 'open'
    instruction = {'columns': ['Patient.ID'], 'binding_claims': [
        {'meaning': 'Distinctness operation', 'use': 'returned',
         'state': 'bound', 'columns': ['Patient.ID']}]}
    compiled = promote_claims(instruction, 'modifier', context, 'columns', 'instruction')
    assert compiled[0]['use'] == 'operand'


def test_fully_specified_literal_operand_can_be_bound_without_schema_column():
    context = {'tables': [{'name': 'Race', 'columns': [{'name': 'points'}]}]}
    row = {'columns': ['Race.points'], 'binding_claims': [
        {'meaning': 'Return race points', 'use': 'returned',
         'state': 'bound', 'columns': ['Race.points']},
        {'meaning': 'Return the top 3 drivers by points', 'use': 'operand',
         'state': 'bound', 'columns': []}]}
    claims = promote_claims(row, 'output', context, 'columns', 'attribute')
    assert claims[1]['state'] == 'bound'
    assert claims[1]['columns'] == []
    invalid = {'columns': [], 'binding_claims': [
        {'meaning': 'Return the driver name', 'use': 'returned',
         'state': 'bound', 'columns': []}]}
    with pytest.raises(ValueError, match='real schema dependencies'):
        promote_claims(invalid, 'output', context, 'columns', 'attribute')


def test_subject_lineage_cannot_be_replaced_by_a_relation_only():
    parent_claims = {'f1': [{'meaning': 'Requested patient subgroup',
                             'use': 'subject', 'state': 'open', 'columns': []}]}
    relation = {'parent': 'f1', 'kind': 'relation', 'binding_claims': [
        {'meaning': 'Patient laboratory join', 'use': 'subject',
         'state': 'bound', 'columns': ['Patient.ID']}], 'columns': ['Patient.ID']}
    with pytest.raises(ValueError, match='Subject lineage'):
        compile_subject_lineage([relation], parent_claims)
    population = {'parent': 'f1', 'kind': 'attribute', 'binding_claims': [
        {'meaning': 'Outpatient population', 'use': 'subject',
         'state': 'bound', 'columns': ['Patient.Admission']}],
                  'role': 'modifier', 'columns': ['Patient.Admission']}
    assert compile_subject_lineage([relation, population], parent_claims)


def test_subject_lineage_compiles_distinguishing_column_and_required_edges():
    parent = {'f1': [dict(meaning='Outpatients with laboratory results', use='subject',
                          state='bound', columns=['Patient.ID', 'Laboratory.ID',
                                                  'Patient.Admission'])]}
    nodes = [
        dict(id='out', parent='f1', role='output', kind='attribute',
             columns=['Patient.ID'], binding_claims=[]),
        dict(id='join', parent='f1', role='modifier', kind='relation',
             columns=['Patient.ID', 'Laboratory.ID'], binding_claims=[]),
        dict(id='population', parent='f1', role='modifier', kind='attribute',
             columns=['Patient.Admission'], binding_claims=[dict(
                 meaning='Outpatient population', use='condition', state='bound',
                 columns=['Patient.Admission'])]),
    ]
    compile_subject_lineage(nodes, parent)
    assert nodes[2]['binding_claims'][0]['use'] == 'subject'
    assert normalize_subject_edges(nodes, [['population', 'out', 'applies_to']]) == [
        ['out', 'population', 'requires']]
    nodes.append(dict(id='ambiguous', parent='f1', role='modifier', kind='attribute',
                      columns=['Patient.Admission'], binding_claims=[dict(
                          meaning='Other use', use='condition', state='bound',
                          columns=['Patient.Admission'])]))
    with pytest.raises(ValueError, match='no unique leaf'):
        compile_subject_lineage(nodes, parent)


def test_subject_lineage_rejects_output_without_required_subject_path():
    parent = {'f1': [dict(meaning='Outpatient population', use='subject',
                          state='bound', columns=['Patient.Admission'])]}
    nodes = [
        dict(id='out', parent='f1', role='output', kind='attribute',
             columns=['Patient.ID'], binding_claims=[]),
        dict(id='scope', parent='f1', role='modifier', kind='attribute',
             columns=['Patient.Admission'], binding_claims=[dict(
                 meaning='Outpatient population', use='subject', state='bound',
                 columns=['Patient.Admission'])]),
    ]
    with pytest.raises(ValueError, match='not connected to output out'):
        validate_subject_edges(nodes, [], parent)


def test_subject_lineage_accepts_transitive_required_subject_path():
    parent = {'f1': [dict(meaning='Outpatient population', use='subject',
                          state='bound', columns=['Patient.Admission'])]}
    nodes = [
        dict(id='out', parent='f1', role='output', kind='attribute',
             columns=['Patient.ID'], binding_claims=[]),
        dict(id='join', parent='f1', role='modifier', kind='relation',
             columns=['Patient.ID'], binding_claims=[]),
        dict(id='scope', parent='f1', role='modifier', kind='attribute',
             columns=['Patient.Admission'], binding_claims=[dict(
                 meaning='Outpatient population', use='subject', state='bound',
                 columns=['Patient.Admission'])]),
    ]
    validate_subject_edges(nodes, [['out', 'join', 'requires'],
                                   ['join', 'scope', 'requires']], parent)


def test_plan_binding_normalization_precedes_subject_lineage_checks():
    context = {'tables': [{'name': 'Patient', 'columns': [
        {'name': 'ID'}, {'name': 'Admission'}]}]}
    parent = {'f1': [dict(meaning='Outpatient population', use='subject',
                          state='bound', columns=['Patient.Admission'])]}
    nodes = [
        dict(id='out', parent='f1', role='output', kind='attribute',
             columns=['patient.id'], binding_claims=[]),
        dict(id='scope', parent='f1', role='modifier', kind='attribute',
             columns=['patient.admission'], binding_claims=[dict(
                 meaning='Outpatient population', use='condition', state='bound',
                 columns=['patient.admission'])]),
    ]
    normalize_plan_bindings(nodes, context)
    compile_subject_lineage(nodes, parent)
    assert nodes[1]['binding_claims'][0]['use'] == 'subject'


def test_malformed_decomposition_response_uses_contract_retry_boundary():
    d, context = trace(1237, 1)
    session = deepcopy(d['episode']['snapshots'][0])
    stages = []

    def malformed(stage, prompt, payload):
        stages.append(stage)
        return []

    result = ContractMethod()._decompose(malformed, session, context)
    assert result['contract_unresolved_reason']
    assert stages == ['contract_permission_decomposition',
                      'contract_permission_decomposition_contract_retry']


def test_reframe_invalidates_shared_decision_groups_after_inventory_change():
    d, context = trace(1237, 1)
    session = deepcopy(d['episode']['snapshots'][0])
    session['view']['shared_decisions'] = [{'slots': ['f1/value', 'f1/field']}]
    result = reframe(session, context)
    assert result['view']['shared_decisions'] == []


def test_1237_planner_response_preserves_outpatient_subject_before_claim_extraction():
    folder = Path('artifacts/clarification_main/contract_v5_24_1237_live_20261026')
    d = json.loads((folder/'1237_1_reduction.json').read_text(encoding='utf8'))
    call = next(c for c in d['calls'] if c['stage'] == 'contract_permission_decomposition')
    raw = json.loads(call['response']['choices'][0]['message']['content'])
    payload = json.loads(call['messages'][-1]['content'])
    parent = payload['binding_claims']
    compile_subject_lineage(raw['nodes'], parent)
    edges = normalize_subject_edges(raw['nodes'], raw['edges'])
    population = next(n for n in raw['nodes'] if 'Patient.Admission' in n['columns'])
    assert population['binding_claims'][0]['use'] == 'subject'
    targets = {e[0] for e in edges if e[1] == population['id'] and e[2] == 'requires'}
    assert targets == {n['id'] for n in raw['nodes'] if n['role'] == 'output'}


def test_delivery_is_blocked_when_node_gap_lacks_a_ledger_entry():
    method = ContractMethod()
    session = {
        'turns': [], 'settled_questions': [],
            'view': {'items': {'f1': {'item_id': 'f1', 'role': 'output',
                                  'missing': ['value'], 'required_columns': [], 'status': 'gap'}},
                 'questions': [], 'relations': {'edges': []},
                 'progressive': {'decision_ledger': []}},
        'permission_issues': {}, 'menu_blockers': {}, 'pending': None,
        'allowed': [],
    }
    result = method.next_step(session)
    assert result['action'] == 'unresolved'
    assert result['reason'] == 'unresolved_node_gaps'


def test_denied_subject_claim_prunes_only_its_dependent_outputs_from_live_trace():
    from clarification_deferred_deletion.graph import canonical_plan, closure
    context = {'tables': [{'name': 'Patient', 'columns': [
        {'name': 'ID'}, {'name': 'Admission'}]}]}
    base = {'items': {'f1': {'role': 'output', 'required_columns': [
        'Patient.ID', 'Patient.Admission']}}}
    raw = {'nodes': [
        {'id': 'out', 'parent': 'f1', 'text': 'Return patient ID.', 'role': 'output',
         'kind': 'attribute', 'columns': ['Patient.ID'], 'questions': {}, 'candidates': {},
         'decision_ids': [], 'binding_claims': [dict(meaning='Patient ID', use='returned',
             state='bound', columns=['Patient.ID'])]},
        {'id': 'scope', 'parent': 'f1', 'text': 'Patients who are outpatients.',
         'role': 'modifier', 'kind': 'attribute', 'columns': ['Patient.Admission'],
         'questions': {}, 'candidates': {}, 'decision_ids': [], 'binding_claims': [
             dict(meaning='Outpatient subject population', use='subject', state='bound',
                  columns=['Patient.Admission'])]},
    ], 'edges': [['scope', 'out', 'applies_to']], 'retired_dependencies': {}}
    raw['edges'] = normalize_subject_edges(raw['nodes'], raw['edges'])
    for node in raw['nodes']:
        promote_claims(node, node['role'], context, 'columns', node.get('kind'))
    plan = canonical_plan(raw, base, context, [])
    dropped = closure(plan, {'Patient.ID'})
    assert dropped['scope']['rule'] == 'denied_dependency'
    assert dropped['out']['rule'] == 'lost_required_provider'


