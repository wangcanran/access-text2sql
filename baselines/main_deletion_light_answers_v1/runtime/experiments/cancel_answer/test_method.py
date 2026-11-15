from copy import deepcopy
import pytest

from ..decision_update.tests.test_lightweight import fixture, edit, question_state, field_event
from .method import CancelAnswer


def cancel(session, context, target, raw, role='output'):
    calls = []
    def call(stage, prompt, payload):
        calls.append((stage, payload))
        active = payload.get('input', payload)
        assert active['event']['kind'] == 'answer'
        assert active['event']['answer']['action'] == 'cancel'
        return deepcopy(raw)
    q = {'item': session['view']['items'][target], 'dimension': 'permission',
         'focus': 'Which information?', 'scoped_permission_decision': {
             'requirement': session['view']['items'][target]['requirement'], 'role': role}}
    result = CancelAnswer()._remove(call, session, session, q, 'empty_candidate_domain', context)
    assert len(calls) == 1
    return result


def test_cancel_exclusive_modifier_and_keep_other_output(fixture):
    session, context = fixture
    original = deepcopy(session)
    result = cancel(session, context, 'f1', {'updates': {'f1': [], 'f3': []}})
    assert set(result['view']['items']) == {'f2'}
    assert session == original
    assert not result['view']['relations']['edges']


def test_cancel_shared_modifier_keeps_surviving_attachment(fixture):
    session, context = fixture
    session['view']['relations']['edges'].append(
        {'source_id': 'f3', 'target_id': 'f2', 'kind': 'applies_to'})
    result = cancel(session, context, 'f1', {
        'updates': {'f1': []}, 'edges': [['f3', 'f2', 'applies_to']]})
    assert set(result['view']['items']) == {'f2', 'f3'}
    assert result['view']['items']['f3']['role'] == 'modifier'


def test_cancel_filter_in_compound_output_preserves_remainder(fixture):
    session, context = fixture
    session['view']['items']['f1']['required_columns'].append('records.score')
    result = cancel(session, context, 'f1', {'updates': {'f1': [
        edit(session['view']['items']['f1'], text='Return names', columns=['records.name'])]}}, role='modifier')
    child = result['view']['progressive']['replacements']['f1'][0]
    assert result['view']['items'][child]['required_columns'] == ['records.name']
    assert result['view']['items'][child]['role'] == 'output'


def test_consecutive_cancel_can_naturally_end_no_objective(fixture):
    session, context = fixture
    first = cancel(session, context, 'f2', {'updates': {'f2': []}})
    second = cancel(first, context, 'f1', {'updates': {'f1': [], 'f3': []}})
    assert second['removal_no_query']
    assert not second['view']['items']
    assert len(second['transaction_audit']) == 2


def test_regular_field_answer_still_preserves_unanswered_coverage(fixture):
    session, context, ledger = question_state(fixture, {'field': 'Which field?', 'value': 'Which subset?'})
    def call(stage, prompt, payload):
        return {'updates': {'f1': [{'id': 'p1', 'text': 'Names for some records',
                                  'columns': ['records.name']}]}}
    result = CancelAnswer()._transaction(call, session, session['view'],
        field_event(ledger[0]['decision_id']), context)
    assert [d['question'] for d in result['view']['progressive']['decision_ledger']] == ['Which subset?']


def test_cancel_filter_cannot_drop_its_output_container(fixture):
    session, context = fixture
    with pytest.raises(ValueError, match='Preserve independent output'):
        cancel(session, context, 'f1', {'updates': {'f1': [], 'f3': []}}, role='modifier')
