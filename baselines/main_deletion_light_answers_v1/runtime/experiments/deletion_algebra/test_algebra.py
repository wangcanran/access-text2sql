import pytest

from .algebra import Requirements
from .cases import cases
from .__main__ import offline, compare


# Independent, hand-written last-step oracles, not produced by graph traversal.
FINAL_ACTIVE = {
    'shared_local': {'accounts', 'opened', 'id'},
    'independent_existence': {'accounts', 'opened', 'exists', 'binding', 'id'},
    'filter_role_school': {'schools', 'info'},
    'client_binding_finance': {'client', 'loan_binding', 'age', 'activity', 'ratio', 'range', 'cards', 'borrowing'},
    'hero_shared_scope': {'hero', 'all', 'attributes', 'binding'},
    'local_vs_population': {'schools', 'region', 'name'},
    'independent_group_order': {'districts', 'name', 'group', 'order', 'count_calc', 'binding'},
    'boolean_scope': {'schools', 'name', 'not_closed'},
}


@pytest.mark.parametrize('c', cases(), ids=lambda c: c['name'])
def test_independent_oracles_idempotence_and_order(c):
    assert offline(c)['independent_order_invariance']
    g = Requirements.parse(c['expected'], c['atoms'])
    for step in c['steps']:
        g = g.withdraw(step['withdraw'])
    assert g.live() == FINAL_ACTIVE[c['name']]


def test_shared_qualifier_survives_first_withdrawal():
    c = cases()[0]
    g = Requirements.parse(c['expected'], c['atoms']).withdraw(['minimum'])
    assert 'preloan' in g.live() and 'maximum' in g.live()
    assert 'minimum' not in g.live()


def test_live_auxiliary_cannot_be_silently_erased():
    c = cases()[0]
    with pytest.raises(ValueError, match='auxiliary'):
        Requirements.parse(c['expected'], c['atoms']).withdraw(['preloan'])


def test_root_needed_by_another_root_is_not_silently_kept_or_cascaded():
    g = Requirements.parse({'roots': ['a', 'b'], 'requires': [['b', 'a']]}, ['a', 'b'])
    with pytest.raises(ValueError, match='surviving requirement'):
        g.withdraw(['a'])
    assert not g.withdraw(['a', 'b']).live()


def test_scope_metric_rejects_qualifier_attached_to_wrong_consumer():
    a = Requirements.parse({'roots': ['a', 'b'], 'requires': [['a', 'c']]}, ['a', 'b', 'c'])
    b = Requirements.parse({'roots': ['a', 'b'], 'requires': [['b', 'c']]}, ['a', 'b', 'c'])
    assert compare(a, b)['exact_active']
    assert not compare(a, b)['exact_scope']


def test_equivalent_transitive_edges_are_accepted():
    a = Requirements.parse({'roots': ['a'], 'requires': [['a', 'b'], ['b', 'c']]}, ['a', 'b', 'c'])
    b = Requirements.parse({'roots': ['a'], 'requires': [['a', 'b'], ['b', 'c'], ['a', 'c']]}, ['a', 'b', 'c'])
    assert compare(a, b)['exact_scope']


@pytest.mark.parametrize('raw', [
    {'roots': ['unknown'], 'requires': []},
    {'roots': ['a'], 'requires': [['a', 'a']]},
    {'roots': ['a'], 'requires': [['a', 'b'], ['b', 'a']]},
])
def test_malformed_graph_is_rejected(raw):
    with pytest.raises(ValueError):
        Requirements.parse(raw, ['a', 'b'])
