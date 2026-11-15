from copy import deepcopy
import json
from pathlib import Path

import pytest

from clarification_deferred_deletion.baseline import updates, main
from .method import UsefulResidualMethod
from .question_registry import register_questions, bind_question


def fixture():
    item = {'item_id': 'f1', 'span': 'properties for an unspecified entity',
            'missing': ['scope', 'field', 'value'],
            'question_focus': {'scope': 'Which entity?', 'field': 'Which properties?', 'value': 'Which ID?'}}
    rows = [{'decision_id': 'd1', 'owner_id': 'f1', 'dimension': 'value', 'question': 'Which ID?'},
            {'decision_id': 'd2', 'owner_id': 'f1', 'dimension': 'field', 'question': 'Which properties?'}]
    questions = [{'item': item, 'dimension': dim, 'focus': text,
                  'requested_slots': ['f1/' + dim], 'key': dim}
                 for dim, text in item['question_focus'].items()]
    return {'turns': [], 'permission_issues': {}, 'view': {'items': {'f1': item}, 'questions': questions,
            'progressive': {'decision_ledger': rows, 'next_decision_id': 3},
            'segmentation': {'inventory': {}}}}


def test_new_scope_preserves_existing_obligations_and_snapshot():
    session = fixture()
    original = deepcopy(session['view']['progressive']['decision_ledger'])
    register_questions(session)
    view = session['view']
    assert view['progressive']['decision_ledger'][:2] == original
    assert view['questions'][0]['decision_ids'] == ['d3']
    assert view['segmentation']['inventory']['progressive'] == view['progressive']
    before = deepcopy(session)
    register_questions(session)
    assert session == before


@pytest.mark.parametrize('dim', ['scope', 'field', 'permission', 'operation', 'value'])
def test_all_question_types_bind_before_answer_including_none(dim):
    q = {'item': {'item_id': 'f1'}, 'dimension': dim}
    rows = [{'decision_id': 'd7', 'owner_id': 'f1', 'dimension': dim}]
    bind_question(q, rows)
    assert q['decision_ids'] == ['d7']
    for action in ['answer', 'choose', 'unanswerable']:
        assert bind_question(q, rows) == ['d7']  # publication precedes answer action


@pytest.mark.parametrize('ids', [['d1', 'stale'], ['d1', 'd1'], ['foreign'], 'd1', 7])
def test_invalid_ids_never_partially_accepted(ids):
    q = {'item': {'item_id': 'f1'}, 'dimension': 'value', 'decision_ids': ids}
    rows = [{'decision_id': 'd1', 'owner_id': 'f1', 'dimension': 'value'},
            {'decision_id': 'foreign', 'owner_id': 'f2', 'dimension': 'value'}]
    with pytest.raises(ValueError):
        bind_question(q, rows)


def test_bundle_requires_every_purpose_and_ambiguity_is_not_guessed():
    q = {'item': {'item_id': 'f1'}, 'dimension': 'value',
         'requested_dimensions': ['value', 'operation'], 'decision_ids': ['d1']}
    rows = [{'decision_id': 'd1', 'owner_id': 'f1', 'dimension': 'value'},
            {'decision_id': 'd2', 'owner_id': 'f1', 'dimension': 'operation'}]
    with pytest.raises(ValueError):
        bind_question(q, rows)
    q.pop('decision_ids')
    assert set(bind_question(q, rows)) == {'d1', 'd2'}
    q.pop('decision_ids')
    rows.append({'decision_id': 'd3', 'owner_id': 'f1', 'dimension': 'value'})
    with pytest.raises(ValueError):
        bind_question(q, rows)


def test_post_answer_hints_cannot_mint_or_publish_questions():
    session = fixture()
    session['turns'] = [{'answer': {'action': 'answer'}}]
    with pytest.raises(ValueError, match='cannot mint'):
        register_questions(session)
    plan = UsefulResidualMethod().next_step(session)
    assert plan['action'] == 'unresolved'
    assert plan['reason'].startswith('question_identity_contract:')


def test_transaction_rejects_unregistered_answer_before_model_call():
    session = fixture()
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'scope',
             'turn': {'answer': {'action': 'answer', 'text': 'a bond'}}}
    def no_call(*args):
        pytest.fail('An unregistered answer must not reach the model')
    with pytest.raises(ValueError, match='unique pending decision'):
        UsefulResidualMethod()._transaction(no_call, session, session['view'], event, {})


def test_finish_hook_runs_before_initial_question_publication(monkeypatch):
    monkeypatch.setattr(updates, 'finish_view', lambda session, context: session)
    monkeypatch.setattr(main, 'check_active_state', lambda session: None)
    with UsefulResidualMethod().installed():
        result = updates.finish_view(fixture(), {})
        assert result['view']['questions'][0]['decision_ids'] == ['d3']


def test_real_236_initial_state_registration():
    path = Path(__file__).resolve().parents[1] / 'artifacts/clarification_main/gpt52_v64_stratified80_broad/236_0_reduction.json'
    if not path.exists():
        pytest.skip('Historical trace is not distributed with source')
    saved = json.loads(path.read_text(encoding='utf8'))['episode']['session']
    session = {'turns': [], 'view': deepcopy(saved['update_base'])}
    register_questions(session)
    view = session['view']
    scope = next(q for q in view['questions'] if q['dimension'] == 'scope')
    assert scope['decision_ids'] == ['d3']
    assert {r['dimension'] for r in view['progressive']['decision_ledger']} == {'scope', 'value', 'field'}
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'scope',
             'turn': {'decision_ids': scope['decision_ids'], 'answer': {'action': 'answer', 'text': 'the specified bond'}}}
    assert UsefulResidualMethod._code_owned_asked_ids(view, event, {'scope'}) == {'d3'}
