from copy import deepcopy

from clarification_batch_deletion.test_graph import fixture
from .baseline import light
from .method import DeferredDeletion, park, pending, reframe, sync_canonical


def test_blocking_keeps_original_leaf_and_unanswered_question_until_commit():
    session, context, plan = fixture()
    plan['nodes'][0]['questions'] = {'field': 'Which name information?'}
    plan['nodes'][0]['candidates'] = {'t.name': 'Name'}
    original = deepcopy(session)
    method = DeferredDeletion()
    with method.installed():
        result = park(session, plan, context)
        assert len(result['deferred_state']['canonical_nodes']) == 2
        assert len(result['deferred_state']['blocked_nodes']) == 1
        assert len(result['view']['items']) == 1
        assert not result.get('reduction_audit')
        assert any(d['decision_id'] == 'd1' for d in result['deferred_state']['canonical_decisions'])
        assert not any(q['dimension'] == 'permission' for q in result['view']['questions'])
        assert method.next_step(result)['action'] in ('menu', 'ask')
        stages = []
        checked = method.analyze_session(lambda *args: stages.append(args[0]), result, context)
        assert not stages
        assert not checked['deferred_state']['committed']
    assert session == original


def test_commit_after_answer_is_one_atomic_removal_and_retains_final_text():
    session, context, plan = fixture()
    plan['nodes'][0]['questions'] = {'field': 'Which name information?'}
    plan['nodes'][0]['candidates'] = {'t.name': 'Name'}
    method = DeferredDeletion()
    with method.installed():
        parked = park(session, plan, context)
        target = next(iter(parked['view']['items']))
        event = {'kind': 'answer', 'target': target, 'dimension': 'field', 'question': 'Which name information?',
            'turn': {'target_id': target, 'target_role': 'output', 'asked_dimension': 'field',
                'selection_mode': 'return_fields', 'selected_options': [{'text': 'Name', 'required_columns': ['t.name']}],
                'answer': {'action': 'choose', 'text': '', 'choices': ['A']}}}
        answered = light.transaction(parked, parked['view'], event, {'updates': {target: [
            {'id': 'answer', 'text': 'Return name', 'columns': ['t.name']}] }}, context)
        answered['turns'] = [event['turn']]
        stages = []
        def call(stage, prompt, payload):
            stages.append(stage)
            assert len(payload['canonical_nodes']) == 2
            assert payload['candidate_text'] == 'Return name'
            return {'accepted': True, 'issues': []}
        result = method.analyze_session(call, answered, context)
        assert stages == ['deferred_commit_review']
        assert len(result['reduction_audit']) == 1
        assert result['reduction_audit'][0]['after_turn'] == 1
        assert result['reduction_audit'][0]['blocked_at_turns'] == [0]
        assert result['deferred_state']['committed']
        assert method.render_session(None, result)['text'] == 'Return name'
        assert len(result['deferred_state']['canonical_nodes']) == 2
        ids = set(result['deferred_state']['canonical_nodes'])
        assert all(e['source_id'] in ids and e['target_id'] in ids for e in result['deferred_state']['canonical_edges'])


def test_all_blocked_commits_without_asking_for_unavailable_details():
    session, context, plan = fixture()
    plan['edges'] = [['out', 'filter', 'requires']]
    method = DeferredDeletion()
    with method.installed():
        state = park(session, plan, context)
        assert not state['view']['items']
        assert state['deferred_state']['canonical_typed_edges'] == [
            {'source_id': 'f2', 'target_id': 'f3', 'kind': 'requires'}]
        assert any(e['kind'] == 'requires'
                   for e in state['deferred_state']['canonical_edges'])
        assert not state.get('reduction_audit')
        result = method.analyze_session(lambda *args: {'accepted': True, 'issues': []}, state, context)
        assert result['deferred_final']['action'] == 'refuse'
        assert len(result['reduction_audit']) == 1
        assert result['deferred_state']['commit_turn'] == 0


def test_negative_review_does_not_commit_or_deliver():
    session, context, plan = fixture()
    method = DeferredDeletion()
    with method.installed():
        state = park(session, plan, context)
        result = method.analyze_session(lambda *args: {'accepted': False, 'issues': ['Wrong scope']}, state, context)
        assert not result['deferred_state']['committed']
        assert not result.get('reduction_audit')
        assert method.render_session(None, result)['available_for_text2sql'] is False
        assert method.next_step(result)['action'] == 'unresolved'


def test_unresolved_survivor_cannot_be_delivered_by_deferring_its_question():
    session, context, plan = fixture()
    plan['nodes'][0]['questions'] = {'value': 'Which names?'}
    method = DeferredDeletion()
    with method.installed():
        state = park(session, plan, context)
        state['view']['deferred'] = [q['key'] for q in state['view']['questions']]
        result = method.analyze_session(lambda *args: None, state, context)
        assert result['deferred_incomplete']
        assert not result['deferred_state']['committed']
        assert not method.render_session(None, result)['available_for_text2sql']


def test_compound_none_splits_before_queuing_only_rejected_leaf():
    session, context, plan = fixture()
    session['allowed'].append('t.secret')
    session['permission_issues'] = {}
    plan['nodes'][1]['role'] = 'output'
    plan['edges'] = []
    target = session['view']['items']['f1']
    q = {'item': target, 'dimension': 'field', 'focus': 'Which name information?',
         'scoped_permission_decision': {'requirement': 'Return name', 'role': 'output',
             'remaining_requirements': ['Return secret condition details']}}
    stages = []
    def call(stage, prompt, payload):
        stages.append(stage)
        if stage == 'deferred_none_decomposition':
            return plan
        if stage == 'deferred_none_focus':
            return {'blocked_aliases': ['out'], 'reason': 'Only name was rejected'}
        raise AssertionError(stage)
    method = DeferredDeletion()
    with method.installed():
        result = method._remove(call, session, session, q, 'no_accepted_local_answer', context)
    assert stages == ['deferred_none_decomposition', 'deferred_none_focus']
    assert len(result['deferred_state']['canonical_nodes']) == 2
    assert len(result['deferred_state']['blocked_nodes']) == 1
    assert not result.get('reduction_audit')
    assert next(iter(result['view']['items'].values()))['requirement'] == 'Require secret condition'
