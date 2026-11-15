from copy import deepcopy
import pytest

from ..decision_update.tests.test_lightweight import fixture, node, question_state
from ..cancel_answer.test_method import cancel
from .method import MenuPartition, _PartitionReady


def decision(nodes, edges=None, role='output', columns=None):
    return {'requirement': 'Return names', 'role': role, 'kind': 'attribute',
            'scope_status': 'fixed', 'scope_tables': ['records'],
            'required_columns': columns or ['records.name'], 'question': 'Which information?',
            'field_candidates': [{'column': 'records.name', 'label': 'Name'}],
            'remaining_requirements': ['Return phone numbers'],
            'partition': {'nodes': nodes, 'edges': edges or []}}


def setup_compound(fixture):
    session, context = fixture
    session['view']['items'] = {'f1': node('f1', 'Return names and phone numbers',
                                          columns=['records.name', 'records.phone'])}
    session['view']['relations']['edges'] = []
    session['permission_issues'] = {}
    return session, context


def compile_split(session, context, raw):
    method = MenuPartition()
    method._partition_session = session
    calls = []
    def call(stage, prompt, payload):
        calls.append(stage)
        return deepcopy(raw)
    payload = {'source': session['view']['items']['f1'], 'pending_columns': []}
    with pytest.raises(_PartitionReady) as exc:
        method.compile_decision(call, payload, context, field=True)
    assert calls == ['field_decision']
    return method, exc.value.session


def simple_nodes():
    return [{'id': 'ask', 'text': 'Names', 'columns': ['records.name']},
            {'id': 'rest', 'text': 'Return phone numbers', 'columns': ['records.phone']}]


def test_partition_then_cancel_cannot_erase_independent_remainder(fixture):
    session, context = setup_compound(fixture)
    original = deepcopy(session)
    method, result = compile_split(session, context, decision(simple_nodes()))
    assert session == original
    audit = result['partition_audit'][-1]
    asked, rest = audit['children']
    assert audit['asked'] == asked
    method._partition_session = result
    def no_call(*a):
        pytest.fail('A partitioned decision must reuse the same compiler response')
    cached = method.compile_decision(no_call,
        {'source': result['view']['items'][asked]}, context, field=True)
    assert cached['remaining_requirements'] == []
    q = {'item': result['view']['items'][asked], 'dimension': 'permission',
         'focus': 'Which information?', 'scoped_permission_decision': cached}
    with pytest.raises(ValueError, match='editable parent'):
        method._remove(lambda *a: {'updates': {asked: [], rest: []}},
                       result, result, q, 'empty_candidate_domain', context)
    kept = method._remove(lambda *a: {'updates': {asked: []}},
                          result, result, q, 'empty_candidate_domain', context)
    assert set(kept['view']['items']) == {rest}
    assert kept['view']['items'][rest]['requirement'] == 'Return phone numbers'


def test_split_routes_existing_questions_without_answering_them(fixture):
    session, context, ledger = question_state(fixture, {'field': 'Which fields?', 'value': 'Which subset?'})
    session['view']['items']['f1']['required_columns'] = ['records.name', 'records.phone']
    nodes = simple_nodes()
    nodes[0]['pending'] = [ledger[0]['decision_id']]
    nodes[1]['pending'] = [ledger[1]['decision_id']]
    _, result = compile_split(session, context, decision(nodes))
    kept = result['view']['progressive']['decision_ledger']
    assert {d['decision_id'] for d in ledger} <= {d['decision_id'] for d in kept}
    children = result['partition_audit'][-1]['children']
    assert next(d for d in kept if d['decision_id'] == ledger[1]['decision_id'])['owner_id'] == children[1]


def test_partition_keeps_shared_modifier_and_attachments(fixture):
    session, context = setup_compound(fixture)
    session['view']['items']['f1']['required_columns'].append('records.score')
    nodes = simple_nodes() + [{'id': 'filter', 'role': 'modifier', 'text': 'Score above ten',
                               'columns': ['records.score']}]
    _, result = compile_split(session, context, decision(nodes,
        [['filter', 'ask', 'applies_to'], ['filter', 'rest', 'applies_to']]))
    asked, rest, qualifier = result['partition_audit'][-1]['children']
    method = MenuPartition()
    q = {'item': result['view']['items'][asked], 'dimension': 'permission', 'focus': 'Which?',
         'scoped_permission_decision': {'requirement': 'Return names', 'role': 'output'}}
    def call(stage, prompt, payload):
        active = payload.get('input', payload)
        assert active['editable_ids'] == [asked]
        return {'updates': {asked: []}}
    kept = method._remove(call, result, result, q, 'empty_candidate_domain', context)
    assert set(kept['view']['items']) == {rest, qualifier}
    assert [{k: e[k] for k in ('source_id', 'target_id', 'kind')}
            for e in kept['view']['relations']['edges']] == [
        {'source_id': qualifier, 'target_id': rest, 'kind': 'applies_to'}]


def test_exclusive_modifier_is_in_cancel_write_boundary(fixture):
    session, context = fixture
    event = {'kind': 'answer', 'target': 'f1', 'turn': {'answer': {'action': 'cancel'}}}
    assert MenuPartition().edit_boundary(session['view'], event) == {'f1', 'f3'}


def test_partition_cannot_silently_retire_old_dependencies(fixture):
    session, context = setup_compound(fixture)
    raw = decision(simple_nodes())
    raw['partition']['nodes'][1]['columns'] = []
    with pytest.raises(ValueError, match='Unwithdrawn dependencies'):
        compile_split(session, context, raw)


def test_split_transition_returns_no_stale_public_menu(fixture, monkeypatch):
    session, context = fixture
    result = deepcopy(session)
    result['view']['inventory_id'] = 'new-identity'
    def parent(*a, **kw):
        raise _PartitionReady(result)
    from ..cancel_answer.method import CancelAnswer
    monkeypatch.setattr(CancelAnswer, 'prepare_menu', parent)
    method = MenuPartition()
    updated, public = method.prepare_menu(None, session, {}, context)
    assert updated is result and public is None
    assert method._partition_session is None


def test_real_menu_pipeline_replans_and_reuses_partitioned_compiler(fixture):
    session, context, ledger = question_state(fixture, {'field': 'Which fields?'})
    session['allowed'].append('records.phone')
    session['view']['items']['f1']['required_columns'] = ['records.name', 'records.phone']
    nodes = simple_nodes()
    nodes[0]['pending'] = [ledger[0]['decision_id']]
    calls = []
    def call(stage, prompt, payload):
        calls.append(stage)
        assert stage == 'field_decision'
        return decision(nodes)
    method = MenuPartition()
    with method.installed():
        plan = method.next_step(session)
        updated, public = method.prepare_menu(call, session, plan, context)
        assert public is None and updated['partition_audit']
        plan = method.next_step(updated)
        updated, public = method.prepare_menu(call, updated, plan, context)
        assert public is not None
        assert updated['pending']['public'] == public
        # The next normal answer must reference a CURRENT child identity.
        q = method.engine.decision(updated, plan)
        assert q['item']['item_id'] == updated['partition_audit'][-1]['asked']
    assert calls == ['field_decision']
