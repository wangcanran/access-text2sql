import pytest

from .method import UsefulResidualMethod


def session_with_pending(rows, *, asked='field', origin='scope'):
    return {'update_base': {'view': {'progressive': {'decision_ledger': rows}}},
            'turns': [{'target_id': 'f1', 'asked_dimension': asked,
                       'requested_slots': ['f1/' + asked],
                       'scoped_permission_decision': {'origin_dimension': origin}}]}


def test_scope_origin_field_menu_uses_public_question_and_unique_id():
    session = session_with_pending([
        {'decision_id': 'd4', 'owner_id': 'f1', 'dimension': 'field', 'question': 'fields'}
    ])
    UsefulResidualMethod._attach_unique_decision_id(session)
    assert session['turns'][-1]['decision_ids'] == ['d4']
    event = {'dimension': 'field', 'turn': session['turns'][-1]}
    assert UsefulResidualMethod._public_answer_dimensions(event) == {'field'}


def test_ambiguous_same_dimension_is_not_guessed():
    session = session_with_pending([
        {'decision_id': 'd1', 'owner_id': 'f1', 'dimension': 'field', 'question': 'identity'},
        {'decision_id': 'd2', 'owner_id': 'f1', 'dimension': 'field', 'question': 'metrics'},
    ])
    UsefulResidualMethod._attach_unique_decision_id(session)
    assert 'decision_ids' not in session['turns'][-1]


def test_permission_origin_field_menu_uses_unique_permission_decision():
    session = session_with_pending([
        {'decision_id': 'd7', 'owner_id': 'f1', 'dimension': 'permission', 'question': 'authorized fields'}
    ], origin='permission')
    UsefulResidualMethod._attach_unique_decision_id(session)
    assert session['turns'][-1]['decision_ids'] == ['d7']


def test_initial_segmentation_ledger_materializes_empty_serialized_ids():
    session = {
        'update_base': {'segmentation': {'inventory': {'progressive': {
            'decision_ledger': [
                {'decision_id': 'd2', 'owner_id': 'f1',
                 'dimension': 'scope', 'question': 'scope'}]}}}},
        'turns': [{'target_id': 'f1', 'asked_dimension': 'scope',
                   'requested_slots': ['f1/scope'], 'decision_ids': [],
                   'scoped_permission_decision': {'origin_dimension': 'scope'}}],
    }
    UsefulResidualMethod._attach_unique_decision_id(session)
    assert session['turns'][-1]['decision_ids'] == ['d2']


def test_code_owned_asked_ids_rejects_ambiguous_pending_dimensions():
    base = {'items': {}, 'progressive': {}, 'relations': {}}
    # Use the real pending representation with two same-dimension decisions.
    base['progressive']['decision_ledger'] = [
        {'decision_id': 'd1', 'owner_id': 'f1', 'dimension': 'field'},
        {'decision_id': 'd2', 'owner_id': 'f1', 'dimension': 'field'},
    ]
    event = {'kind': 'answer', 'target': 'f1', 'dimension': 'field',
             'turn': {'answer': {'action': 'choose'}, 'decision_ids': []}}
    assert UsefulResidualMethod._code_owned_asked_ids(
        base, event, {'field'}) == set()


@pytest.mark.parametrize('asked,origin', [
    ('field', 'scope'), ('field', 'permission'), ('value', 'scope'),
    ('operation', 'scope'), ('permission', 'field'),
])
def test_public_dimension_never_inherits_compiler_origin(asked, origin):
    turn = {'asked_dimension': asked, 'requested_slots': ['f1/' + asked],
            'scoped_permission_decision': {'origin_dimension': origin}}
    assert UsefulResidualMethod._public_answer_dimensions(
        {'dimension': asked, 'turn': turn}) == {asked}
