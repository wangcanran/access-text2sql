from copy import deepcopy
import pytest

from .hybrid import active, fixtures, update, validate, withdraw_code


def graph(roots, edges):
    ids = sorted(set(roots) | {i for e in edges for i in e})
    return {'nodes': [{'id': i, 'text': i} for i in ids],
            'roots': roots, 'requires': [list(e) for e in edges]}


def test_shared_local_condition_survives_until_last_computation_withdrawn():
    g = graph(['id', 'min', 'max', 'opened'],
              [('id', 'accounts'), ('opened', 'accounts'), ('min', 'preloan'),
               ('max', 'preloan'), ('preloan', 'accounts')])
    first = withdraw_code(g, ['min'])
    assert 'preloan' in validate(first).live()
    second = withdraw_code(first, ['max'])
    assert validate(second).live() == {'id', 'opened', 'accounts'}
    assert len(second['nodes']) == len(g['nodes'])  # delayed physical cleanup
    assert {n['id'] for n in active(second)['nodes']} == {'id', 'opened', 'accounts'}


def test_independent_existence_filter_survives_output_withdrawal():
    g = graph(['id', 'max', 'exists'],
              [('id', 'accounts'), ('max', 'preloan'), ('exists', 'preloan'), ('preloan', 'accounts')])
    after = withdraw_code(g, ['max'])
    assert validate(after).roots == {'id', 'exists'}
    assert 'preloan' in validate(after).live()


def test_remove_display_count_preserves_sort_operand_without_restoring_output():
    g = graph(['name', 'count_output', 'order'],
              [('name', 'district'), ('count_output', 'count_calc'),
               ('order', 'count_calc'), ('count_calc', 'district')])
    after = withdraw_code(g, ['count_output'])
    assert validate(after).roots == {'name', 'order'}
    assert 'count_calc' in validate(after).live()
    assert 'count_output' not in validate(after).live()


def test_removing_filter_does_not_remove_subject_and_other_demands():
    g = graph(['specified', 'age', 'ratio'],
              [('specified', 'loan'), ('age', 'loan'), ('ratio', 'loan'), ('loan', 'client')])
    after = withdraw_code(g, ['specified'])
    assert validate(after).live() == {'age', 'ratio', 'loan', 'client'}


def test_unseparated_display_dependency_routes_to_model_instead_of_cascading():
    g = graph(['count', 'order'], [('order', 'count')])
    before = deepcopy(g)
    with pytest.raises(ValueError, match='still needs'):
        withdraw_code(g, ['count'])
    assert g == before


def test_empty_effective_request_is_representable():
    g = graph(['output'], [('output', 'subject')])
    assert active(withdraw_code(g, ['output'])) == {'nodes': [], 'roots': [], 'requires': []}


def test_model_region_commit_preserves_disconnected_boundary():
    g = graph(['mixed', 'other'], [('mixed', 'subject'), ('other', 'unrelated')])
    before = deepcopy(g)
    def call(stage, prompt, payload):
        assert {n['id'] for n in payload['boundary_nodes']} == {'other', 'unrelated'}
        return {'nodes': [{'id': 'survivor', 'text': 'remaining output'},
                          {'id': 'subject', 'text': 'subject'}],
                'roots': ['survivor'], 'requires': [['survivor', 'subject']]}
    after, scope = update(call, g, 'original', ['partial withdrawal'], {'node_ids': ['mixed']})
    assert validate(after).roots == {'survivor', 'other'}
    assert ['other', 'unrelated'] in after['requires']
    assert scope == {'editable_nodes': 2, 'boundary_nodes': 2}
    assert g == before


def test_initial_graphs_are_coarse_not_fine_answer_inventories():
    for c in fixtures():
        assert len(c['graph']['nodes']) == 3
        validate(c['graph'])
    assert 'birthday, age' in fixtures()[0]['graph']['nodes'][1]['text']
