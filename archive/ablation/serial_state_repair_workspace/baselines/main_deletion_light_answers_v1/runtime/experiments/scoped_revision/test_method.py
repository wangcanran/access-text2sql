from copy import deepcopy

import pytest

from ..decision_update.tests.test_lightweight import fixture, question_state, field_event, edit
from ..menu_partition.test_method import setup_compound, decision, simple_nodes
from ..menu_partition.method import _PartitionReady
from .method import ScopedRevision, permission_menu_dependencies


def test_field_revision_preserves_independent_coverage(fixture):
    session, context, ledger = question_state(fixture, {
        'field': 'Which information?', 'value': 'Which subset?'})
    event = field_event(ledger[0]['decision_id'])
    before = deepcopy(session)

    def call(stage, prompt, payload):
        assert payload['revision_locator']['decision_ids'] == [ledger[0]['decision_id']]
        assert payload['revision_locator']['argument'] == 'field'
        assert payload['editable_ids'] == ['f1']
        return {'updates': {'f1': [{'id': 'bound', 'text': 'Names for some records',
                                   'columns': ['records.name']}]}}

    result = ScopedRevision()._transaction(call, session, session['view'], event, context)
    assert session == before  # Non-destructive revision, not in-place mutation.
    assert [d['decision_id'] for d in result['view']['progressive']['decision_ledger']] == [ledger[1]['decision_id']]
    assert result['revision_audit'][-1]['locator']['root'] == 'f1'


def test_hint_paraphrases_do_not_duplicate_pending_or_reopen_answer(fixture):
    session, context, ledger = question_state(fixture, {
        'field': 'Which fields?', 'value': 'Which subset?'})
    event = field_event(ledger[0]['decision_id'])
    raw = {'updates': {'f1': [{'id': 'bound', 'text': 'Names for some records',
                              'columns': ['records.name'], 'questions': {
                                  'field': 'Which attributes?', 'value': 'Which coverage?'}}]}}
    result = ScopedRevision()._transaction(lambda *a: deepcopy(raw), session,
                                           session['view'], event, context)
    remaining = result['view']['progressive']['decision_ledger']
    assert [(d['decision_id'], d['dimension']) for d in remaining] == [(ledger[1]['decision_id'], 'value')]


def test_partition_rejects_copied_shared_argument(fixture):
    session, context, ledger = question_state(fixture, {'value': 'Which subset?'})
    method = ScopedRevision()
    method._partition_session = session
    nodes = simple_nodes()
    for row in nodes:
        row['pending'] = [ledger[0]['decision_id']]
    with pytest.raises(ValueError, match='single modifier'):
        method.compiled_decision(decision(nodes), session['view']['items']['f1'], context)


def test_shared_argument_has_one_owner_and_multiple_attachments(fixture):
    session, context, ledger = question_state(fixture, {'value': 'Which subset?'})
    method = ScopedRevision()
    method._partition_session = session
    nodes = simple_nodes() + [{'id': 'coverage', 'role': 'modifier',
        'text': 'Restrict both returned fields to the selected subset of records',
        'columns': [], 'questions': {'value': 'Which coverage?'},
        'pending': [ledger[0]['decision_id']]}]
    raw = decision(nodes, [['coverage', 'ask', 'applies_to'],
                           ['coverage', 'rest', 'applies_to']])
    result = method.compiled_decision(raw, session['view']['items']['f1'], context)['_partition_commit']
    remaining = result['view']['progressive']['decision_ledger']
    assert [d['decision_id'] for d in remaining] == [ledger[0]['decision_id']]
    owner = remaining[0]['owner_id']
    assert result['view']['items'][owner]['role'] == 'modifier'
    assert sum(e['source_id'] == owner for e in result['view']['relations']['edges']) == 2


def test_partition_keeps_parent_dependencies_on_asked_child(fixture):
    session, context, ledger = question_state(fixture, {'scope': 'Which scope?'})
    method = ScopedRevision()
    session['view']['items'] = {'f1': {
        'item_id': 'f1', 'span': 'Names, phone numbers and scores',
        'requirement': 'Names, phone numbers and scores', 'role': 'output',
        'kind': 'attribute', 'scope_tables': ['records'],
        'required_columns': ['records.name', 'records.phone', 'records.score'],
        'missing': ['scope'], 'question_focus': {'scope': 'Which scope?'},
        'field_candidates': [], 'denied_columns': []}}
    session['view']['items']['f1']['required_columns'] = [
        'records.name', 'records.phone', 'records.score']
    method._partition_session = session
    nodes = [
        {'id': 'ask', 'role': 'output', 'kind': 'attribute', 'text': 'Names',
         'tables': ['records'], 'columns': ['records.name'], 'questions': {},
         'pending': [ledger[0]['decision_id']]},
        {'id': 'rest', 'role': 'output', 'kind': 'attribute', 'text': 'Phone numbers',
         'tables': ['records'], 'columns': ['records.phone'], 'questions': {},
         'pending': []},
    ]
    raw = decision(nodes, [['ask', 'rest', 'context']])
    raw['remaining_requirements'] = ['Phone numbers']
    source = session['view']['items']['f1']
    result = method.compiled_decision(raw, source, context)['_partition_commit']
    rows = result['view']['items']
    asked_id, remainder_id = result['partition_audit'][-1]['children']
    asked = rows[asked_id]
    assert set(asked['required_columns']) == {'records.name', 'records.score'}
    remainder = rows[remainder_id]
    assert remainder['required_columns'] == ['records.phone']


def test_connected_node_is_context_not_write_authority(fixture):
    session, context = fixture
    event = field_event('d1')
    before = deepcopy(session)

    def bad_call(stage, prompt, payload):
        payload = payload.get('input', payload)
        assert any(n['id'] == 'f2' for n in payload['read_only_query']['nodes'])
        return {'updates': {'f1': [{'id': 'p1', 'text': 'Names', 'columns': ['records.name']}],
                            'f2': [{'id': 'p2', 'text': 'Other', 'columns': []}]}}

    with pytest.raises(ValueError, match='editable parent'):
        ScopedRevision()._transaction(bad_call, session, session['view'], event, context)
    assert session == before


def test_split_then_cancel_keeps_sibling(fixture):
    session, context = setup_compound(fixture)
    method = ScopedRevision()
    method._partition_session = session
    with pytest.raises(_PartitionReady) as exc:
        method.compile_decision(lambda *a: decision(simple_nodes()),
            {'source': session['view']['items']['f1'], 'pending_columns': []}, context, field=True)
    split = exc.value.session
    asked, rest = split['partition_audit'][-1]['children']
    event = {'kind': 'answer', 'target': asked, 'dimension': 'permission',
             'question': 'Which information?', 'turn': {
                 'answer': {'action': 'cancel', 'text': ''},
                 'scoped_permission_decision': split['partition_decisions'][asked]}}
    result = method._transaction(lambda *a: {'updates': {asked: []}},
                                 split, split['view'], event, context)
    assert set(result['view']['items']) == {rest}
    assert result['view']['items'][rest] == split['view']['items'][rest]


def test_cancel_keeps_shared_modifier_but_removes_exclusive_one(fixture):
    session, context = fixture
    session['view']['relations']['edges'].append(
        {'source_id': 'f3', 'target_id': 'f2', 'kind': 'applies_to'})
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'permission', 'question': 'Which?',
             'turn': {'answer': {'action': 'cancel', 'text': ''},
                      'scoped_permission_decision': {'requirement': 'Return names', 'role': 'output'}}}
    result = ScopedRevision()._transaction(lambda *a: {'updates': {'f1': []}},
                                           session, session['view'], event, context)
    assert set(result['view']['items']) == {'f2', 'f3'}
    assert result['view']['items']['f3'] == session['view']['items']['f3']
    session['view']['relations']['edges'] = [e for e in session['view']['relations']['edges']
                                            if e['target_id'] != 'f2']
    result = ScopedRevision()._transaction(lambda *a: {'updates': {'f1': [], 'f3': []}},
                                           session, session['view'], event, context)
    assert set(result['view']['items']) == {'f2'}


def test_reversed_attachment_is_oriented_by_declared_roles(fixture):
    session, context = setup_compound(fixture)
    method = ScopedRevision()
    method._partition_session = session
    nodes = simple_nodes() + [{'id': 'coverage', 'role': 'modifier',
        'text': 'Score above ten', 'columns': ['records.score']}]
    raw = decision(nodes, [['ask', 'coverage', 'applies_to'],
                           ['rest', 'coverage', 'applies_to']])
    result = method.compiled_decision(raw, session['view']['items']['f1'], context)['_partition_commit']
    for edge in result['view']['relations']['edges']:
        assert result['view']['items'][edge['source_id']]['role'] == 'modifier'
        assert result['view']['items'][edge['target_id']]['role'] == 'output'


def test_cancel_redundant_resolution_needs_no_retry(fixture):
    session, context, ledger = question_state(fixture, {'field': 'Which fields?'})
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'field', 'question': 'Which fields?',
             'turn': {'answer': {'action': 'cancel', 'text': ''},
                      'scoped_permission_decision': {'requirement': 'Return names', 'role': 'output'}}}
    calls = []
    def call(stage, prompt, payload):
        calls.append(stage)
        return {'updates': {'f1': []}, 'question_changes': {
            ledger[0]['decision_id']: {'resolved': 'This requirement was cancelled.'}}}
    result = ScopedRevision()._transaction(call, session, session['view'], event, context)
    assert not result['view']['items']
    assert calls == ['semantic_transaction']


def test_echoed_unchanged_boundary_edges_are_ignored_and_preserved(fixture):
    session, context = fixture
    base = session['view']
    base['relations']['edges'] = [
        {'source_id': 'f3', 'target_id': 'f2', 'kind': 'context'}]
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'scope',
             'question': 'Which scope?', 'turn': {
                 'answer': {'action': 'cancel', 'text': ''},
                 'scoped_permission_decision': {'requirement': 'Select account scope',
                                                'role': 'output'}}}
    raw = {'updates': {'f1': []}, 'edges': [['f3', 'f2', 'context']]}
    result = ScopedRevision()._transaction(lambda *a: deepcopy(raw), session, base, event, context)
    assert [(e['source_id'], e['target_id'], e['kind'])
            for e in result['view']['relations']['edges']] == [('f3', 'f2', 'context')]


def test_selected_field_replaces_stale_candidate_dependencies(fixture):
    from ..decision_update.tests.test_lightweight import node
    session, context, _ = question_state(fixture, {'field': 'Which transaction details?'})
    session['view']['items']['f1']['required_columns'] = ['records.name', 'records.phone']
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'permission',
             'question': 'Which transaction details?', 'turn': {
                 'selection_mode': 'return_fields',
                 'selected_options': [{'text': 'Score', 'required_columns': ['records.score']}],
                 'scoped_permission_decision': {'origin_dimension': 'permission',
                     'replaceable_columns': ['records.name', 'records.phone'],
                     'retained_columns': []}}}
    raw = {'updates': {'f1': [{'id': 'selected', 'text': 'Score',
                               'columns': ['records.score']}]}}
    result = ScopedRevision()._transaction(lambda *a: deepcopy(raw), session,
                                           session['view'], event, context)
    child = next(iter(result['view']['items'].values()))
    assert child['required_columns'] == ['records.score']
    assert result['transaction_audit'][-1]['retire_columns']['f1'] == [
        'records.name', 'records.phone']


def test_permission_menu_requires_explicitly_replaceable_dependencies():
    source = {'required_columns': ['loan.account_id', 'disp.account_id', 'disp.client_id']}
    decision = {'required_columns': ['loan.account_id', 'disp.account_id', 'disp.client_id'],
                'replaceable_columns': []}
    assert permission_menu_dependencies(source, decision, 'permission') == [
        'disp.account_id', 'disp.client_id', 'loan.account_id']
    # Old/incomplete decisions fail closed; they cannot silently turn all
    # purpose dependencies into replacement candidates.
    assert permission_menu_dependencies(source, {'required_columns': ['disp.client_id']},
                                        'permission') == [
        'disp.account_id', 'disp.client_id', 'loan.account_id']


def test_financial_loan_anchor_cannot_be_replaced_by_client_disposition_fields():
    source = {'required_columns': ['account.account_id', 'loan.account_id', 'loan.loan_id',
                                  'disp.account_id', 'disp.client_id', 'client.client_id']}
    decision = {'required_columns': ['loan.account_id', 'disp.account_id',
                                    'disp.client_id', 'client.client_id'],
                'replaceable_columns': []}
    menu_dependencies = permission_menu_dependencies(source, decision, 'permission')
    assert 'loan.account_id' in menu_dependencies
    assert set(menu_dependencies) == set(source['required_columns'])
    # The former rule used source minus purpose dependencies and therefore
    # mistakenly omitted loan.account_id from the common menu obligations.
    buggy = set(source['required_columns']) - set(decision['required_columns'])
    assert 'loan.account_id' not in buggy
    assert 'loan.account_id' in menu_dependencies


def test_financial_permission_menu_rejects_disposition_only_without_loan_anchor():
    from ...live import load_case
    from ...engine.simple_session import connected_options
    _, context, _, allowed, _ = load_case(113, 1)
    source = {'required_columns': ['account.account_id', 'client.client_id',
        'disp.account_id', 'disp.client_id', 'loan.account_id', 'loan.loan_id']}
    decision = {'required_columns': ['client.client_id', 'disp.account_id',
        'disp.client_id', 'loan.account_id', 'loan.loan_id'], 'replaceable_columns': []}
    target = {'span': 'Use the client associated with the selected loan application',
        'menu_required_columns': permission_menu_dependencies(source, decision, 'permission')}
    session = {'allowed': allowed, 'view': {'items': {}}}
    disposition_fields = ['disp.account_id', 'disp.client_id']
    assert not connected_options(session, context, target, disposition_fields)
    # With the buggy complement rule, the loan anchor was omitted. Disposition
    # columns might then pass an authorized graph check as a standalone branch.
    buggy_target = {'span': target['span'], 'menu_required_columns':
        sorted(set(source['required_columns']) - set(decision['required_columns']))}
    assert permission_menu_dependencies(source, decision, 'permission') != buggy_target['menu_required_columns']
    assert connected_options(session, context, buggy_target, disposition_fields)


def test_permission_choice_overwrites_only_declared_dependency_and_drops_stale_copy(fixture):
    from ..decision_update.tests.test_lightweight import question_state
    session, context, _ = question_state(fixture, {'field': 'Which identifier should be used?'})
    old_columns = ['records.name', 'records.phone']
    session['view']['items']['f1']['required_columns'] = old_columns
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'permission',
             'question': 'Which identifier should be used?', 'turn': {
                 'selection_mode': 'operand',
                 'selected_options': [{'text': 'Name', 'required_columns': ['records.name']}],
                 'scoped_permission_decision': {'origin_dimension': 'permission',
                     'replaceable_columns': ['records.phone'], 'retained_columns': []}}}
    raw = {'updates': {'f1': [{'id': 'selected', 'text': 'Use the selected name',
                               'columns': ['records.name', 'records.phone']}]}}
    result = ScopedRevision()._transaction(lambda *a: deepcopy(raw), session,
                                           session['view'], event, context)
    child = next(iter(result['view']['items'].values()))
    assert child['required_columns'] == ['records.name']
    assert result['transaction_audit'][-1]['retire_columns']['f1'] == ['records.phone']


def test_unrelated_field_menu_does_not_retire_same_dimension_permission_columns(fixture):
    session, context, _ = question_state(fixture, {'field': 'Which fields?'})
    session['view']['items']['f1']['required_columns'] = ['records.name', 'records.phone']
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'field',
             'question': 'Which fields?', 'turn': {
                 'selection_mode': 'return_fields',
                 'selected_options': [{'text': 'Name', 'required_columns': ['records.name']}],
                 'scoped_permission_decision': {'origin_dimension': 'field',
                     'replaceable_columns': [], 'retained_columns': []}}}
    raw = {'updates': {'f1': [{'id': 'selected', 'text': 'Return names',
                               'columns': ['records.name', 'records.phone']}]}}
    result = ScopedRevision()._transaction(lambda *a: deepcopy(raw), session,
                                           session['view'], event, context)
    child = next(iter(result['view']['items'].values()))
    assert set(child['required_columns']) == {'records.name', 'records.phone'}
    assert not result['transaction_audit'][-1]['retire_columns'].get('f1')


def test_cancelling_shared_modifier_requires_output_revision_in_same_transaction(fixture):
    session, context = fixture
    session['view']['relations']['edges'].append(
        {'source_id': 'f3', 'target_id': 'f2', 'kind': 'applies_to'})
    event = {'kind': 'answer', 'target': 'f3', 'dimension': 'permission',
             'question': 'Which score restriction?',
             'turn': {'answer': {'action': 'cancel', 'text': ''},
                      'scoped_permission_decision': {'requirement': 'Filter by score', 'role': 'modifier'}}}
    method = ScopedRevision()
    assert method.edit_boundary(session['view'], event) == {'f1', 'f2', 'f3'}
    with pytest.raises(ValueError, match='requires revising its outputs'):
        method._transaction(lambda *a: {'updates': {'f3': []}}, session,
                            session['view'], event, context)
    result = method._transaction(lambda *a: {'updates': {
        'f1': [{'id': 'p1', 'text': 'Return names', 'columns': ['records.name']}],
        'f2': [{'id': 'p2', 'text': 'Return phone numbers',
                'columns': ['records.phone']}],
        'f3': [] }}, session,
        session['view'], event, context)
    assert {n['requirement'] for n in result['view']['items'].values()} == {
        'Return names', 'Return phone numbers'}
    assert not result['view']['relations']['edges']


def test_real_financial_cancel_response_no_longer_retries():
    import json
    from ...live import load_case
    from ...runtime import ROOT
    trace = ROOT / 'artifacts/clarification_main/20261023_scoped_revision_v2/revision/113_1_reduction.json'
    record = json.loads(trace.read_text(encoding='utf8'))
    call_record = next(c for c in record['calls'] if c['stage'] == 'semantic_transaction')
    payload = json.loads(next(m['content'] for m in call_record['messages'] if m['role'] == 'user'))
    raw = json.loads(call_record['response']['choices'][0]['message']['content'])
    target = payload['event']['target']
    context = load_case(113, 1)[1]
    session = deepcopy(record['episode']['snapshots'][0])
    compiler = ScopedRevision()
    compiler._partition_session = session
    compile_record = next(c for c in record['calls']
                          if c['stage'] == 'scope_decision' and c.get('response'))
    compiled = json.loads(compile_record['response']['choices'][0]['message']['content'])
    source = next(iter(session['view']['items'].values()))
    session = compiler.compiled_decision(compiled, source, context)['_partition_commit']
    assert target in session['view']['items']
    event = {'kind': 'answer', 'target': target,
             'dimension': payload['event']['dimension'], 'question': payload['event']['question'],
             'turn': {'answer': {'action': 'cancel', 'text': ''},
                      'scoped_permission_decision': payload['event']['purpose']}}
    calls = []
    def replay(stage, prompt, data):
        calls.append(stage)
        return deepcopy(raw)
    result = ScopedRevision()._transaction(replay, session, session['view'], event, context)
    assert target not in result['view']['items']
    assert calls == ['semantic_transaction']
