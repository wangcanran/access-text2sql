from copy import deepcopy
import pytest

from .deferred import (active_graph, append_event, fixtures, new_state, planning_view,
                       project, region, validate_graph)


def test_coarse_compounds_are_connected_and_not_preseparated():
    c = fixtures()[0]
    assert len(c['graph']['nodes']) == 3
    assert region(c['graph'], ['p']) == {'s', 'p', 'f'}
    assert 'specified' in c['graph']['nodes'][0]['text']
    assert 'birthday' in c['graph']['nodes'][1]['text']
    assert 'age' in c['graph']['nodes'][1]['text']


def test_marking_invalidates_plans_and_cannot_be_used_as_active_graph():
    c = fixtures()[0]
    state = new_state(c)
    state['next_questions'] = [{'owner_ids': ['s'], 'question': 'Which loan?'}]
    after = append_event(state, c['events'][0])
    assert after['graph'] == state['graph']
    assert after['next_questions'] == []
    assert state['events'] == []
    with pytest.raises(ValueError, match='Pending semantic projection'):
        planning_view(after)


def test_tombstone_and_its_selection_edge_excluded_from_active_graph():
    graph = {'nodes': [
        {'id': 's', 'text': 'client', 'kind': 'context', 'state': 'active'},
        {'id': 'w', 'text': 'specified loan', 'kind': 'filter', 'state': 'withdrawn'}],
        'edges': [['w', 's', 'restricts']]}
    active = active_graph(graph)
    assert [n['id'] for n in active['nodes']] == ['s']
    assert active['edges'] == []
    assert len(graph['nodes']) == 2


def test_live_dependency_on_tombstone_is_rejected():
    graph = {'nodes': [
        {'id': 's', 'text': 'client', 'kind': 'context', 'state': 'active'},
        {'id': 'w', 'text': 'loan binding', 'kind': 'context', 'state': 'withdrawn'}],
        'edges': [['s', 'w', 'needs']]}
    with pytest.raises(ValueError, match='withdrawn dependency'):
        validate_graph(graph)


def test_failed_projection_keeps_original_state():
    c = fixtures()[0]
    state = append_event(new_state(c), c['events'][0])
    before = deepcopy(state)
    def invalid(*args):
        return {'nodes': [], 'edges': [['unknown', 's', 'needs']], 'next_questions': []}
    with pytest.raises(ValueError):
        project(invalid, c, state, 'deferred')
    assert state == before


def test_final_planning_uses_only_active_nodes_after_projection():
    c = fixtures()[2]
    state = append_event(new_state(c), c['events'][0])
    def response(*args):
        return {'nodes': [
            {'id': 's', 'text': '3-D Man', 'kind': 'context', 'state': 'active'},
            {'id': 'o', 'text': 'All attributes', 'kind': 'output', 'state': 'active'},
            {'id': 'w', 'text': 'Values', 'kind': 'output', 'state': 'withdrawn'}],
            'edges': [['o', 's', 'needs'], ['w', 's', 'needs']],
            'next_questions': [{'owner_ids': ['o'], 'question': 'Attribute names?'}]}
    result = project(response, c, state, 'deferred')
    view = planning_view(result)
    assert len(result['graph']['nodes']) == 3
    assert len(view['graph']['nodes']) == 2
    assert all(n['state'] == 'active' for n in view['graph']['nodes'])
    assert result['revisions'][0]['boundary_count'] == 0

