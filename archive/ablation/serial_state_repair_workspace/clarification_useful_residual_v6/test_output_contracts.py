"""Wire-boundary and actual state-consumer regressions; no live/network calls."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from clarification_deferred_deletion.baseline import analysis, module
from .output_contracts import (compile_output, OutputContractError,
                               STAGE_FAMILIES, validated_output_call)
from .pool_method import PoolMethod


TRACES = Path(__file__).resolve().parents[1] / 'artifacts/clarification_main/gpt52_pool_v30_stratified240_20261029'


def historical(case):
    path = TRACES / (case + '_reduction.json')
    if not path.exists():
        pytest.skip('Historical trace not distributed')
    return json.loads(path.read_text(encoding='utf8'))


def response(call):
    return json.loads(call['response']['choices'][0]['message']['content'])


def transaction():
    return {'updates': {'f1': [{'id': 'p1', 'text': 'Return name', 'columns': ['t.name']}]}}


def test_single_item_analysis_claims_have_unique_wire_owner():
    claim = dict(meaning='Return name', use='returned', state='bound', columns=['t.name'])
    item = dict(item_id='f1', role='output', kind='attribute', requirement='Return name',
                scope_tables=['t'], required_columns=['t.name'], missing=[], question_focus={})
    raw = dict(items=[deepcopy(item)], binding_claims=[claim])
    result = compile_output('progressive_analysis', raw).to_legacy()
    assert result == dict(items=[dict(item, binding_claims=[claim])])
    assert 'binding_claims' in raw and 'binding_claims' not in raw['items'][0]

    with pytest.raises(OutputContractError, match='no unique item owner'):
        compile_output('progressive_analysis', dict(items=[item, dict(item, item_id='f2')],
            binding_claims=[claim]))
    with pytest.raises(OutputContractError, match='no unique item owner'):
        compile_output('progressive_analysis', dict(items=[dict(item, binding_claims=[claim])],
            binding_claims=[claim]))


def test_misnested_routing_reaches_real_legacy_question_consumer():
    d = historical('1494_2')
    raw = response(d['calls'][-1])
    saved = deepcopy(raw)
    assert 'question_changes' in raw['updates'] and 'question_changes' not in raw
    session = d['episode']['session']
    base = session['update_base']
    turn = session['turns'][-1]
    event = dict(kind='answer', target=turn['target_id'], dimension=turn['asked_dimension'],
                 question=turn['question']['focus'], turn=deepcopy(turn))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    before = deepcopy(session)
    method = PoolMethod()
    method._context = context
    with method.installed():
        # Wire repair must not hide the response's independent omission of
        # gasstations.Segment, the field actually selected by the user.
        with pytest.raises(ValueError, match='Selected fields must survive'):
            method.commit_transaction(session, base, event, raw, context)
        corrected = deepcopy(raw)
        child = corrected['updates']['f1'][0]
        child['columns'].append('gasstations.Segment')
        child['binding_claims'].append(dict(
            meaning='Use gas station segment as the premium-classification operand',
            use='operand', state='bound', columns=['gasstations.Segment'], obligation_ids=[]))
        # This explicit semantic correction is a test fixture, not automatic
        # repair performed by the new parser.
        result = method.commit_transaction(session, base, event, corrected, context)
    assert raw == saved and session == before
    effects = result['transaction_audit'][-1]['question_effects']
    assert any(e['decision_id'] == 'd2' and e['effect'] == 'retained' for e in effects)
    assert any(e['decision_id'] == 'd1' and e['effect'] == 'resolved' for e in effects)


@pytest.mark.parametrize('key,value', [
    ('edges', [['p1', 'f2', 'context']]),
    ('retire_columns', {'f1': ['t.old']}),
    ('question_changes', {'d2': {'to': ['p1']}}),
    ('settled_decisions', ['d1']),
])
def test_envelope_alias_is_lossless_idempotent_and_conflicts_are_rejected(key, value):
    raw = transaction()
    raw['updates'][key] = value
    saved = deepcopy(raw)
    parsed = compile_output('semantic_transaction', raw).to_legacy()
    assert parsed[key] == value and key not in parsed['updates']
    assert compile_output('semantic_transaction', parsed).to_legacy() == parsed
    assert raw == saved
    raw[key] = {'different': []}
    with pytest.raises(OutputContractError, match='conflicting envelope'):
        compile_output('semantic_transaction', raw)


@pytest.mark.parametrize('raw,path', [
    ({'updates': []}, '$.updates'),
    ({'updates': {'f1': [None]}}, '$.updates.f1[0]'),
    ({'updates': {'f1': [{'id': 'p1', 'columns': []}]}}, '$.updates.f1[0]'),
    ({'updates': {'f1': [{'id': 'p1', 'text': 'x', 'columns': [[]]}]}}, '$.updates.f1[0].columns[0]'),
    (dict(transaction(), question_changes={'d1': {'to': [None]}}), '$.question_changes.d1.to[0]'),
    (dict(transaction(), question_changes={'d1': None}), '$.question_changes.d1'),
    (dict(transaction(), settled_decisions=[{}]), '$.settled_decisions[0]'),
])
def test_bad_shapes_never_reach_state_consumer(raw, path):
    reached = []
    attempts = []
    def call(stage, prompt, payload):
        attempts.append((stage, payload))
        return raw
    with pytest.raises(OutputContractError) as error:
        validated_output_call(call, 'semantic_transaction', '', {}, lambda x: reached.append(x))
    assert error.value.path == path
    assert not reached and len(attempts) == 2
    assert attempts[-1][1]['previous_response'] == raw


def test_retry_keeps_original_response_and_compiles_before_inherited_adapters():
    # Initial claim normalization in ContractMethod indexes rows; a list here
    # formerly could raise AttributeError before a usable contract diagnostic.
    seen = []
    def call(stage, prompt, payload):
        seen.append((stage, payload))
        return {'items': [None]}
    original = analysis.validated_call
    with PoolMethod().installed():
        with pytest.raises(OutputContractError, match=r'\$\.items\[0\]'):
            from baselines.main_deletion_light_answers_v1.runtime.engine.prompts import FLAT_COMPACT_ANALYZE
            from baselines.main_deletion_light_answers_v1.runtime.method import MAPPING_RULES, ROLE_CONTRACT, MAP
            analysis.validated_call(call, 'progressive_analysis',
                FLAT_COMPACT_ANALYZE + MAPPING_RULES + ROLE_CONTRACT + MAP, {}, lambda x: x)
    assert analysis.validated_call is original
    assert len(seen) == 2
    assert seen[-1][1]['previous_response'] == {'items': [None]}


def test_no_default_value_can_swallow_a_question_change():
    raw = transaction()
    raw['updates']['question_changes'] = {'d1': {'to': ['p1']}}
    def consume(value):
        return value['question_changes']['d1']['to']
    with PoolMethod().installed():
        assert analysis.validated_call(lambda *args: raw, 'semantic_transaction', '', {}, consume) == ['p1']


@pytest.mark.parametrize('module_name,stage,raw', [
    ('engine.hierarchical_questions', 'hierarchical_menu_groups', {'options': [None]}),
    ('engine.category_propagation', 'category_propagation', {'items': [None]}),
])
def test_frozen_imported_aliases_use_same_boundary_and_restore(module_name, stage, raw):
    target = module(module_name)
    old = target.validated_call
    reached = []
    with PoolMethod().installed():
        with pytest.raises(OutputContractError):
            target.validated_call(lambda *args: raw, stage, '', {}, lambda x: reached.append(x))
    assert not reached
    assert target.validated_call is old


@pytest.mark.parametrize('case', ['1221_1', '1494_1', '70_1', '960_0'])
def test_structured_grounding_unresolved_has_a_controlled_exit_without_binding_writes(case):
    d = historical(case)
    failed = [c for c in d['calls'] if c['stage'].startswith('obligation_grounding')][-1]
    raw = response(failed)
    raw_before = deepcopy(raw)
    # The failed run did not publish the tentative graph; reconstruct only the
    # internal owner's input claims from the recorded grounding task payload.
    payload = json.loads(failed['messages'][-1]['content'])['input']
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    from . import obligations as pool
    from .test_obligations import session as make_session, claim
    claims = [claim('Already grounded literal', 'operand', 'bound', [])
              for _ in range(max(t['claim_index'] for t in payload['tasks']) + 1)]
    for task in payload['tasks']:
        claims[task['claim_index']] = {k: task[k] for k in ('meaning', 'use', 'state', 'columns')}
    session = make_session(claims)
    session['view']['items']['f1'] = dict(requirement=payload['requirement'], missing=[],
                                        role='output', kind='calculation', required_columns=[])
    session['allowed'] = d['allowed_columns']
    session = pool.initialize(session)
    before = deepcopy(session)
    calls = []
    def call(stage, prompt, payload):
        calls.append(stage)
        return raw
    with PoolMethod().installed():
        result = PoolMethod()._ground(call, session, context)
    assert result.get('contract_unresolved_reason', '')
    assert not result.get('output_contract_failures')
    assert result['binding_claims'] == before['binding_claims']
    assert session == before and raw == raw_before
    assert len(calls) == 1


@pytest.mark.parametrize('case', ['1041_2', '1221_1', '1407_0', '963_2'])
def test_empty_non_decision_focus_is_removed_without_changing_pending_questions(case):
    d = historical(case)
    call = next(c for c in d['calls'] if c['stage'].startswith('progressive_analysis'))
    raw = response(call)
    saved = deepcopy(raw)
    parsed = compile_output('progressive_analysis', raw).to_legacy()
    assert raw == saved
    for original, normalized in zip(raw['items'], parsed['items']):
        assert original['missing'] == normalized['missing']
        assert all(value.strip() for value in normalized['question_focus'].values())
        for dimension in original['missing']:
            if dimension in original['question_focus']:
                assert normalized['question_focus'][dimension] == original['question_focus'][dimension]


def test_empty_pending_focus_and_ambiguous_unresolved_object_still_fail():
    analysis = {'items': [dict(item_id='f1', role='output', kind='attribute',
                               requirement='Return a value', scope_tables=[], required_columns=[],
                               missing=['field'], question_focus={'field': ''})]}
    with pytest.raises(OutputContractError, match='question_focus.field'):
        compile_output('progressive_analysis', analysis)
    with pytest.raises(OutputContractError, match='unresolved'):
        compile_output('obligation_grounding',
                       {'resolutions': [], 'unresolved': [{'reason': 'No binding'}]})


def test_single_question_wrapper_is_losslessly_compiled_but_extra_metadata_fails():
    d = historical('1475_2')
    call = next(c for c in d['calls'] if c['stage'] == 'contract_none_decomposition')
    raw = response(call)
    saved = deepcopy(raw)
    compiled = compile_output('contract_none_decomposition', raw).to_legacy()
    assert compiled['nodes'][6]['questions']['value'] == raw['nodes'][6]['questions']['value']['question']
    assert raw == saved
    assert compile_output('contract_none_decomposition', compiled).to_legacy() == compiled
    invalid = deepcopy(raw)
    invalid['nodes'][6]['questions']['value']['confidence'] = 0.7
    with pytest.raises(OutputContractError, match='questions.value'):
        compile_output('contract_none_decomposition', invalid)


def test_json_parser_does_not_guess_or_discard_duplicates():
    for raw in ['{"request":"a","request":"b"}', '{"request": NaN}', '```json\n{}\n```']:
        with pytest.raises(OutputContractError):
            compile_output('final_active_request', raw)


def test_field_filter_has_an_explicit_shape_contract():
    assert compile_output('hierarchical_field_filter', {'columns': ['t.name']}).to_legacy() == {
        'columns': ['t.name']}
    with pytest.raises(OutputContractError):
        compile_output('hierarchical_field_filter', {'fields': ['t.name']})


def test_transport_failures_are_not_retried_as_model_contract_failures():
    def call(*args):
        raise TimeoutError('transport')
    with pytest.raises(TimeoutError, match='transport'):
        validated_output_call(call, 'semantic_transaction', '', {}, lambda x: x)


def test_all_observed_batch_stages_have_a_registered_shape_contract():
    if not TRACES.exists():
        pytest.skip('Historical batch not distributed')
    seen = set()
    for path in TRACES.glob('*_reduction.json'):
        d = json.loads(path.read_text(encoding='utf8'))
        seen.update(c['stage'].removesuffix('_contract_retry') for c in d['calls'])
    assert seen <= STAGE_FAMILIES.keys()
