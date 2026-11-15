from copy import deepcopy
from unittest.mock import patch

import pytest

from ..decision_update.tests.test_lightweight import fixture
from ..purpose_withdrawal.method import PurposeWithdrawal
from .method import TextWithdrawal


def question(session):
    return {'item': session['view']['items']['f2'], 'dimension': 'permission',
            'focus': 'Which contact field?', 'scoped_permission_decision': {
                'requirement': 'Return phone numbers', 'role': 'output'}}


def response_call(stages):
    def call(stage, prompt, payload):
        stages.append(stage)
        if stage == 'withdrawal_request':
            assert 'schema' not in payload and 'gold_sql' not in payload
            return {'request': 'Return names'}
        if stage == 'progressive_coarse':
            assert payload == {'current_request': 'Return names'}
            return {'original_text': 'Return names', 'fragments': [
                {'item_id': 'f1', 'span': 'Return names', 'open_choices': []}]}
        if stage == 'progressive_analysis':
            assert payload['schema']['tables'][0]['name'] == 'records'
            assert payload['schema']['foreign_keys'] == []
            assert 'column_meanings' in payload and 'db_name' in payload
            assert payload['event'] is None and not payload['previous_relations']['edges']
            assert not payload['boundary_nodes']
            return {'items': [{'item_id': 'f1', 'role': 'output', 'kind': 'attribute',
                'requirement': 'Return names', 'scope_tables': ['records'],
                'grounded': True, 'required_columns': ['records.name'],
                'missing': [], 'question_focus': {}, 'field_candidates': [], 'reason': 'Explicit field'}],
                'edges': []}
        raise AssertionError((stage, payload))
    return call


def test_new_text_is_the_only_reanalysis_evidence(fixture):
    session, context = fixture
    before = deepcopy(session)
    method, stages = TextWithdrawal(), []
    result = method._remove(response_call(stages), session, session, question(session),
                            'rejected_options', context)
    assert session == before
    assert stages == ['withdrawal_request', 'progressive_coarse', 'progressive_analysis']
    assert result['current_request'] == 'Return names'
    assert not result['permission_issues'] and not result['view']['relations']['edges']
    assert set(result['view']['items']) == {'f1'}
    assert result['view']['items']['f1']['required_columns'] == ['records.name']
    assert result['view']['inventory_id'] != before['view']['inventory_id']
    assert not result['removal_no_query']


def test_empty_request_stops_without_analysis(fixture):
    session, context = fixture
    def call(stage, prompt, payload):
        assert stage == 'withdrawal_request'
        return {'request': ''}
    result = TextWithdrawal()._remove(call, session, session, question(session), 'empty', context)
    assert result['removal_no_query'] and not result['view']['items']


def test_failure_does_not_commit_and_restores_analysis_context(fixture):
    session, context = fixture
    before = deepcopy(session)
    method = TextWithdrawal()
    marker = object()
    method._base = marker
    def fail(stage, prompt, payload):
        if stage == 'withdrawal_request':
            return {'request': 'Return names'}
        raise RuntimeError('analysis unavailable')
    with pytest.raises(RuntimeError, match='analysis unavailable'):
        method._remove(fail, session, session, question(session), 'empty', context)
    assert session == before and method._base is marker


def test_ordinary_answer_path_is_inherited(fixture):
    session, context = fixture
    event = {'kind': 'answer'}
    with patch.object(PurposeWithdrawal, '_transaction', return_value=session) as original:
        assert TextWithdrawal()._transaction(None, session, session['view'], event, context) is session
    original.assert_called_once()
