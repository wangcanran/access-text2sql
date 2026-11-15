"""Regress real initial scheduler order and whole-node answer replacement."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from clarification_deferred_deletion.baseline import module, updates, main
from .method import UsefulResidualMethod
from .answer_contract import prepare_answer_input
from .test_question_registry import fixture


TRACES = Path(__file__).resolve().parents[1] / 'artifacts/clarification_main/gpt52_v66_identity_regression7'


@pytest.mark.parametrize('name', ['236_2', '893_2'])
def test_initial_contracts_exist_before_inner_scheduler(name):
    path = TRACES / (name + '_reduction.json')
    if not path.exists():
        pytest.skip('Historical live trace not distributed')
    trace = json.loads(path.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[trace['db_id']])
    calls = iter(c for c in trace['calls'] if c.get('response_status') == 'valid_json_object')
    seen = []

    class Observed(UsefulResidualMethod):
        def next_step(self, session):
            if session.get('view'):
                assert set(session['binding_claims']) == set(session['view']['items'])
                assert 'decision_ledger' in session['view']['progressive']
            return super().next_step(session)

    def call(stage, prompt, payload):
        if stage == 'binding_completion':
            # This test replays the old initialization lifecycle. Model
            # completion is a new, separately tested boundary, absent in v6.6.
            rows = []
            for n in payload['nodes']:
                claims = deepcopy(n['binding_claims'])
                extra = set(n['required_columns']) - {c for claim in claims for c in claim['columns']}
                if extra:
                    claims.append(dict(meaning='Existing dependencies', use='condition',
                                       state='bound', columns=sorted(extra)))
                rows.append({'id': n['item_id'], 'requirement': n['requirement'], 'binding_claims': claims})
            return {'nodes': rows,
                    'unresolved': []}
        c = next(calls)
        assert c['stage'] == stage
        seen.append(stage)
        return json.loads(c['response']['choices'][0]['message']['content'])

    with Observed().installed():
        result = live.engine_module('harness').run_session(
            call, trace['request'], context, trace['db_id'], trace['allowed_columns'],
            lambda *a: None, max_turns=30, episode={}, method='main')
    assert result['action'] == 'deliver'
    assert seen.count('progressive_analysis') == 1  # no false full resynchronization


def test_initial_full_resynchronization_builds_ledger_and_never_replaces_claims(monkeypatch):
    session = fixture()
    session['view']['progressive'].pop('decision_ledger')
    method = UsefulResidualMethod()
    method._initializing_contracts = True
    method._initial_claims = {'f1': [{'meaning': 'existing initial meaning'}]}
    monkeypatch.setattr(updates, 'finish_view', lambda s, c: s)
    monkeypatch.setattr(main, 'check_active_state', lambda s: None)
    from clarification_deferred_deletion.baseline import light
    monkeypatch.setattr(light, 'discover', lambda *args: (fixture()['view']['progressive']['decision_ledger'], 3))
    with method.installed():
        result = updates.finish_view(session, {})
        assert result['binding_claims'] == method._initial_claims
        assert result['view']['questions'][0]['decision_ids'] == ['d3']
        result['binding_claims'] = {'f1': [{'meaning': 'subsequent decomposition meaning'}]}
        assert updates.finish_view(result, {})['binding_claims'] != method._initial_claims


@pytest.mark.parametrize('retry', [False, True])
def test_answer_contract_separates_question_purpose_from_output_container(retry):
    active = {'event': {'kind': 'answer', 'asked_decisions': ['d2'],
                        'purpose': {'role': 'modifier', 'remaining_requirements': ['another question']}},
              'editable_ids': ['f3'], 'preserve_outputs': ['f3'],
              'nodes': [{'id': 'f3', 'role': 'output', 'decisions': [{'id': 'd2'}, {'id': 'd4'}]},
                        {'id': 'f2', 'role': 'modifier', 'decisions': []}]}
    payload = {'input': active, 'contract_error': 'keep output'} if retry else active
    before = deepcopy(payload)
    result = prepare_answer_input(payload)
    assert payload == before
    prepared = result['input'] if retry else result
    assert 'remaining_requirements' not in prepared['event']['purpose']
    assert prepared['replacement_contract'] == {'f3': {'must_keep_output': True,
        'source_role': 'output', 'pending_decisions': ['d4']}}


def test_removal_input_not_changed():
    payload = {'event': {'kind': 'removal', 'purpose': {'remaining_requirements': ['keep']}}}
    assert prepare_answer_input(payload) == payload
