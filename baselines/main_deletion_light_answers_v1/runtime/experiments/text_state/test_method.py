from copy import deepcopy
from unittest.mock import patch

import pytest

from ...engine import harness, hierarchical_questions as hierarchy, session_update, simple_session as engine
from ..decision_update.tests.test_lightweight import fixture, node
from .method import TextState, VERSION


def calls_for(request, stages, *, detail=None):
    def call(stage, prompt, payload):
        stages.append((stage, deepcopy(payload)))
        if stage == 'text_state_revise':
            assert set(payload) == {'current_request', 'interaction'}
            assert 'original_request' not in payload and 'gold_sql' not in payload
            return {'request': request}
        if stage == 'progressive_coarse':
            text = payload['current_request']
            assert payload == {'current_request': text}
            return {'original_text': text, 'fragments': [{'item_id': 'f1', 'span': text,
                'open_choices': ([{'dimension': 'detail', 'span': text,
                    'question': detail}] if detail else [])}]}
        if stage == 'progressive_analysis':
            text = payload['current_request']
            assert payload['schema']['tables'][0]['name'] == 'records'
            assert payload['event'] is None and not payload['previous_relations']['edges']
            assert not payload['boundary_nodes']
            return {'items': [{'item_id': 'f1', 'role': 'output', 'kind': 'attribute',
                'requirement': text, 'scope_tables': ['records'], 'grounded': True,
                'required_columns': ['records.name'], 'missing': ['value'] if detail else [],
                'question_focus': {'value': detail} if detail else {},
                'field_candidates': [], 'reason': 'Explicit names'}], 'edges': []}
        raise AssertionError((stage, payload))
    return call


def open_session(fixture):
    session, context = fixture
    session.update(update_mode='full', local_bundle=False, permission_issues={})
    session['view']['items'] = {'f1': node('f1', 'Names for some records',
        columns=['records.name'], questions={'value': 'Which subset?'})}
    session['view']['relations'] = {'edges': []}
    session['view']['progressive'] = {}
    return session_update.finish_view(session, context), context


@pytest.mark.parametrize('action,kind,text', [
    ('answer', 'answer', 'Return names for all records'),
    ('unanswerable', 'cancel', 'Return names')])
def test_open_answers_and_rejections_share_one_writer(fixture, action, kind, text):
    session, context = open_session(fixture)
    method, stages = TextState(), []
    before = deepcopy(session)
    with method.installed():
        plan = engine.next_step(session)
        question = engine.public_question(session, plan)
        result = engine.commit_answer(calls_for(text, stages), session, plan, question,
            {'action': action, 'text': 'All records' if action == 'answer' else '', 'choices': []}, context)
        assert engine.analyze_session(None, result, context) is result
        delivered = engine.render_session(None, result)
        assert delivered['text'] == text and not delivered['execution_authorized']
    assert session == before
    assert [s for s, p in stages] == ['text_state_revise', 'progressive_coarse', 'progressive_analysis']
    assert stages[0][1]['interaction']['kind'] == kind
    assert result['view']['inventory_id'] != session['view']['inventory_id']


def test_category_navigation_never_edits_semantics(fixture):
    session, context = open_session(fixture)
    q = session['view']['questions'][0]
    q.update(dimension='field', focus='Which fields?')
    key = q['key']
    public = {'question': 'Which category?', 'options': [{'id': 'A', 'label': 'Identity'}],
              'multi_select': True}
    session['pending'] = {'turn': 0, 'decision_key': key, 'public': public,
        'options': [{'id': 'A', 'text': 'Identity', 'choice_ids': ['c1']}],
        'mode': 'hierarchical', 'none_id': 'B', 'category': True, 'multi': True}
    session['menu_frontiers'] = {key: {'leaves': [], 'active': ['c1', 'c2'],
        'queue': [], 'selected': [], 'path': [], 'history': []}}
    before = deepcopy(session)
    with TextState().installed():
        result = engine.commit_answer(lambda *a: pytest.fail('Navigation called a model'),
            session, engine.next_step(session), public,
            {'action': 'choose', 'text': '', 'choices': ['A']}, context)
    assert result['view'] == before['view']
    assert result['current_request'] == before['current_request']
    assert not result['turns'] and result['menu_frontiers'][key]['active'] == ['c1']


@pytest.mark.parametrize('choices,cancel', [(['A'], False), (['B'], True)])
def test_leaf_selection_and_none_are_validated_then_revised(fixture, choices, cancel):
    session, context = open_session(fixture)
    q = session['view']['questions'][0]
    q.update(dimension='field', focus='Which fields?')
    key = q['key']
    option = {'id': 'A', 'choice_id': 'c1', 'text': 'Names', 'required_columns': ['records.name']}
    public = {'question': 'Which fields?', 'options': [{'id': 'A', 'label': 'Names'},
        {'id': 'B', 'label': 'None'}], 'multi_select': True}
    session['pending'] = {'turn': 0, 'decision_key': key, 'public': public,
        'options': [option], 'mode': 'hierarchical', 'none_id': 'B', 'category': False, 'multi': True}
    session['menu_frontiers'] = {key: {'leaves': [option], 'active': ['c1'],
        'queue': [], 'selected': [], 'path': [], 'history': []}}
    stages = []
    with TextState().installed():
        result = engine.commit_answer(calls_for('Return names', stages), session,
            engine.next_step(session), public, {'action': 'choose', 'text': '', 'choices': choices}, context)
        assert engine.next_step(result)['action'] == 'deliver'
        assert not result['turns'][-1].get('menu_rejected')
        if not cancel:
            assert stages[0][1]['interaction']['selected_options'][0]['required_columns'] == ['records.name']
    assert stages[0][1]['interaction']['kind'] == ('cancel' if cancel else 'answer')


def test_fresh_analysis_failure_is_atomic(fixture):
    session, context = open_session(fixture)
    before = deepcopy(session)
    with TextState().installed():
        plan = engine.next_step(session)
        def call(stage, prompt, payload):
            if stage == 'text_state_revise':
                return {'request': 'Return names'}
            raise RuntimeError('Analysis unavailable')
        with pytest.raises(RuntimeError, match='Analysis unavailable'):
            engine.commit_answer(call, session, plan, engine.public_question(session, plan),
                {'action': 'unanswerable', 'text': '', 'choices': []}, context)
    assert session == before


def test_consecutive_cancellations_use_only_latest_text(fixture):
    session, context = open_session(fixture)
    q = session['view']['questions'][0]
    method, first, second = TextState(), [], []
    with method.installed():
        result = method._remove(calls_for('Return names', first), session, session, q, 'empty', context)
        final = method._remove(calls_for('', second), result, result, q, 'empty', context)
        assert engine.next_step(final)['action'] == 'refuse'
    assert second[0][1]['current_request'] == 'Return names'
    assert [s for s, _ in second] == ['text_state_revise']
    assert len(final['reduction_audit']) == 2


def test_harness_runs_actual_open_answer_path(fixture):
    _, context = fixture
    stages, episode = [], {}
    initial = calls_for('', stages, detail='Which subset?')
    after = calls_for('Return names for all records', stages)
    answered = False
    def call(stage, prompt, payload):
        nonlocal answered
        if stage == 'text_state_revise':
            answered = True
        return (after if answered else initial)(stage, prompt, payload)
    with TextState().installed():
        delivery = harness.run_session(call, 'Names for some records', context, 'example',
            ['records.name'], lambda question, history: {
                'action': 'answer', 'text': 'All records', 'choices': []},
            method='main', episode=episode)
    assert delivery['text'] == 'Return names for all records'
    assert len(episode['interactions']) == 1
    assert episode['session']['update_mode'] == 'full'
    assert not episode['session']['local_bundle']
    assert [s for s, _ in stages].count('text_state_revise') == 1
    assert not any('propagat' in s or 'render' in s or 'lineage' in s for s, _ in stages)


def test_sql_guard_still_checks_actual_sql_after_local_cancellation(fixture):
    session, context = fixture
    method = TextState()
    q = session['view']['questions'][-1]
    with method.installed():
        result = method._remove(calls_for('Return names', []), session, session, q, 'empty', context)
        assert engine.authorize_session_sql(result, context, 'SELECT name FROM records')['sql_authorized']
        assert not engine.authorize_session_sql(result, context, 'SELECT phone FROM records')['sql_authorized']


def test_installation_restores_baseline_after_error():
    original = engine.start_session, engine.commit_answer, hierarchy._choices
    with pytest.raises(RuntimeError):
        with TextState().installed():
            raise RuntimeError('stop')
    assert original == (engine.start_session, engine.commit_answer, hierarchy._choices)


def test_noop_update_does_not_loop_or_deliver(fixture):
    session, context = open_session(fixture)
    with TextState().installed() as method:
        result = method._remove(calls_for(session['current_request'], []), session, session,
            session['view']['questions'][0], 'empty', context)
        assert engine.next_step(result)['action'] == 'unresolved'
        assert not engine.render_session(None, result)['available_for_text2sql']


@pytest.mark.parametrize('column,empty', [('records.name', False), ('records.phone', True)])
def test_real_menu_compilation_authorization_and_empty_domain(fixture, column, empty):
    session, context = open_session(fixture)
    item = session['view']['items']['f1']
    item.update(missing=['field'], question_focus={'field': 'Which fields?'},
                required_columns=[], bindings=[])
    session = session_update.finish_view(session, context)
    stages = []
    base = calls_for('Return names', stages)
    def call(stage, prompt, payload):
        if stage == 'field_decision':
            stages.append((stage, deepcopy(payload)))
            return {'scope_status': 'fixed', 'requirement': 'Return requested information',
                'role': 'output', 'kind': 'attribute', 'question': 'Which fields?',
                'scope_tables': ['records'], 'required_columns': [],
                'field_candidates': [{'column': column, 'label': 'Names' if not empty else 'Phone'}],
                'remaining_requirements': [], 'replaceable_columns': []}
        return base(stage, prompt, payload)
    with TextState().installed():
        updated, public = engine.prepare_menu(call, session, engine.next_step(session), context)
        if empty:
            assert public is None
            assert updated['current_request'] == 'Return names'
            assert updated['reduction_audit'][-1]['trigger'] == 'empty_candidate_domain'
        else:
            assert public is not None
            assert 'Phone' not in str(public)
            assert engine.public_history(updated) == {
                'original_request': updated['current_request'], 'turns': []}
            assert not any(s == 'text_state_revise' for s, _ in stages)
            answer = {'action': 'choose', 'choices': [public['options'][0]['id']], 'text': ''}
            result = engine.commit_answer(call, updated, engine.next_step(updated), public, answer, context)
            assert engine.next_step(result)['action'] == 'deliver'


def test_unauthorized_leaf_is_rejected_before_text_editor(fixture):
    session, context = open_session(fixture)
    q = session['view']['questions'][0]
    q.update(dimension='field', focus='Which fields?')
    key = q['key']
    bad = {'id': 'A', 'choice_id': 'c1', 'text': 'Phone', 'required_columns': ['records.phone']}
    public = {'question': 'Which fields?', 'options': [{'id': 'A', 'label': 'Phone'}],
              'multi_select': True}
    session['pending'] = {'turn': 0, 'decision_key': key, 'public': public,
        'options': [bad], 'mode': 'hierarchical', 'none_id': 'B', 'category': False, 'multi': True}
    session['menu_frontiers'] = {key: {'leaves': [bad], 'active': ['c1'],
        'queue': [], 'selected': [], 'path': [], 'history': []}}
    before = deepcopy(session)
    with TextState().installed(), pytest.raises(ValueError, match='jointly authorized'):
        engine.commit_answer(lambda *a: pytest.fail('Invalid choice reached editor'),
            session, engine.next_step(session), public,
            {'action': 'choose', 'text': '', 'choices': ['A']}, context)
    assert session == before
