from copy import deepcopy

from ..decision_update.tests.test_lightweight import fixture, question_state, field_event
from .method import PreparedUpdate


def test_question_intent_reaches_updater_without_consuming_other_questions(fixture):
    session, context, ledger = question_state(fixture, {
        'field': 'Which field?', 'value': 'Which subset?'})
    event = field_event(ledger[0]['decision_id'])
    intent = {'purpose': 'Choose the attribute only',
              'on_accept': 'Bind selected attribute; keep coverage pending',
              'on_cancel': 'Cancel this output only', 'preserve': ['Which subset?']}
    event['turn']['scoped_permission_decision'] = {'update_intent': intent}
    calls = []
    def call(stage, prompt, payload):
        calls.append(stage)
        assert payload['question_update_intent'] == intent
        return {'updates': {'f1': [{'id': 'bound', 'text': 'Names for some records',
                                    'columns': ['records.name']}]}}
    original = deepcopy(session)
    result = PreparedUpdate()._transaction(call, session, session['view'], event, context)
    assert calls == ['semantic_transaction']
    assert session == original
    assert [d['decision_id'] for d in result['view']['progressive']['decision_ledger']] == [ledger[1]['decision_id']]


def test_deterministic_field_binding_is_separate_opt_in(fixture):
    session, context, ledger = question_state(fixture, {
        'field': 'Which field?', 'value': 'Which subset?'})
    event = field_event(ledger[0]['decision_id'])
    event['turn']['scoped_permission_decision'] = {
        'scope_status': 'fixed', 'binding': {'template': 'Return {fields} for some records',
                                           'common_columns': []}}
    def no_call(*args):
        raise AssertionError('Prepared binding must not call a model')
    result = PreparedUpdate(execute_prepared=True)._transaction(
        no_call, session, session['view'], event, context)
    assert result['prepared_update_audit'][-1]['operation'] == 'bind_fields'
    assert [d['decision_id'] for d in result['view']['progressive']['decision_ledger']] == [ledger[1]['decision_id']]


def test_answered_dimension_cannot_reopen_under_paraphrase(fixture):
    session, context, ledger = question_state(fixture, {'field': 'Which fields?'})
    event = field_event(ledger[0]['decision_id'])
    result = PreparedUpdate()._transaction(
        lambda *args: {'updates': {'f1': [{'id': 'p1', 'text': 'Names',
                                           'columns': ['records.name'],
                                           'questions': {'field': 'Which attributes?'}}]}},
        session, session['view'], event, context)
    assert result['view']['progressive']['decision_ledger'] == []


def test_partition_requires_complete_dependency_coverage(fixture):
    session, context, ledger = question_state(fixture, {'field': 'Which field?'})
    method = PreparedUpdate()
    method._partition_session = session
    source = session['view']['items']['f1']
    source['required_columns'] = ['records.name']
    decision = {
        'requirement': 'Return names', 'role': 'output', 'kind': 'attribute',
        'scope_status': 'fixed', 'scope_tables': ['records'],
        'required_columns': ['records.name'], 'question': 'Which field?',
        'field_candidates': [{'column': 'records.name', 'label': 'Name'}],
        'remaining_requirements': [], 'update_intent': {
            'purpose': 'choose field', 'on_accept': 'bind field',
            'on_cancel': 'remove field', 'preserve': [], 'update_nodes': ['ask']},
        'partition': {'nodes': [{'id': 'ask', 'columns': ['records.name']}], 'edges': []},
        'dependency_coverage': {}}
    import pytest
    with pytest.raises(ValueError, match='Dependency coverage mismatch'):
        method.compiled_decision(decision, source, context)


def test_dependency_coverage_repairs_child_projection(fixture):
    session, context, ledger = question_state(fixture, {'field': 'Which field?'})
    method = PreparedUpdate()
    method._partition_session = session
    source = session['view']['items']['f1']
    source['required_columns'] = ['records.name']
    decision = {
        'requirement': 'Return names', 'role': 'output', 'kind': 'attribute',
        'scope_status': 'fixed', 'scope_tables': ['records'],
        'required_columns': ['records.name'], 'question': 'Which field?',
        'field_candidates': [{'column': 'records.name', 'label': 'Name'}],
        'remaining_requirements': [], 'update_intent': {
            'purpose': 'choose field', 'on_accept': 'bind field',
            'on_cancel': 'remove field', 'preserve': [], 'update_nodes': ['ask']},
        'partition': {'nodes': [
            {'id': 'ask', 'text': 'Names', 'role': 'output', 'kind': 'attribute',
             'tables': ['records'], 'columns': [], 'questions': {}, 'pending': ['d1']},
            {'id': 'rest', 'text': 'Other details', 'role': 'output', 'kind': 'attribute',
             'tables': ['records'], 'columns': ['records.phone'], 'questions': {}, 'pending': []}], 'edges': []},
        'dependency_coverage': {'records.name': ['ask']}}
    result = method.compiled_decision(decision, source, context)
    assert result.get('_partition_commit') is not None
    children = result['_partition_commit']['partition_audit'][-1]['children']
    assert 'records.name' in result['_partition_commit']['view']['items'][children[0]]['required_columns']


def test_partition_maps_update_nodes_to_materialized_children(fixture):
    session, context, ledger = question_state(fixture, {'field': 'Which field?'})
    method = PreparedUpdate()
    method._partition_session = session
    source = session['view']['items']['f1']
    source['required_columns'] = ['records.name', 'records.phone']
    decision = {
        'requirement': 'Return names', 'role': 'output', 'kind': 'attribute',
        'scope_status': 'fixed', 'scope_tables': ['records'],
        'required_columns': ['records.name'], 'question': 'Which field?',
        'field_candidates': [{'column': 'records.name', 'label': 'Name'}],
        'remaining_requirements': [], 'update_intent': {
            'purpose': 'choose field', 'on_accept': 'bind field',
            'on_cancel': 'remove field', 'preserve': [], 'update_nodes': ['ask']},
        'partition': {'nodes': [
            {'id': 'ask', 'text': 'Names', 'role': 'output', 'kind': 'attribute',
             'tables': ['records'], 'columns': ['records.name'], 'questions': {}, 'pending': ['d1']},
            {'id': 'rest', 'text': 'Phone', 'role': 'output', 'kind': 'attribute',
             'tables': ['records'], 'columns': ['records.phone'], 'questions': {}, 'pending': []}],
            'edges': []},
        'dependency_coverage': {'records.name': ['ask'], 'records.phone': ['rest']}}
    result = method.compiled_decision(decision, source, context)
    ask = result['_partition_commit']['partition_audit'][-1]['asked']
    assert result['update_node_ids'] == [ask]
    assert result['_partition_commit']['partition_decisions'][ask]['update_node_ids'] == [ask]


def test_prepared_update_nodes_are_enforced_as_write_boundary(fixture):
    import pytest
    session, context = fixture
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'field', 'question': 'Which field?',
             'turn': {'decision_ids': [], 'answer': {'action': 'answer', 'text': 'Names'},
                      'scoped_permission_decision': {'prepared_atomic': True,
                                                     'update_node_ids': ['f1']}}}
    def call(stage, prompt, payload):
        active = payload.get('input', payload)
        assert active['question_scope']['prepared_update_nodes'] == ['f1']
        return {'updates': {'f1': [{'id': 'p1', 'text': 'Names', 'columns': ['records.name']}],
                            'f2': [{'id': 'p2', 'text': 'Phone numbers', 'columns': ['records.phone']}]}}
    with pytest.raises(ValueError, match='Updates must reference editable parent IDs'):
        PreparedUpdate()._transaction(call, session, session['view'], event, context)
