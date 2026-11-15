from copy import deepcopy
import pytest
from .state import initial, frontier, add_issue
from .contribution import prepare
from .answer_package import apply_package
from .test_tree import C


def fixture():
    state = initial('Activity details and unrelated birthday')
    plan = prepare(state, 'f1', dict(
        focus=dict(meaning='Activity details', granularity='compound'),
        remainders=[dict(meaning='Unrelated birthday', granularity='compound')],
        question=dict(dimension='boundary', question='Which activity details?'), coverage='Both survive'), C)
    event = dict(action='answer', answer=dict(text='amount and type'), selected_columns=[], selected_labels=[])
    raw = dict(update=dict(meaning='Activity amount and activity type', granularity='compound', resolved=True),
        refinement=dict(children=[dict(meaning='Activity amount', granularity='atomic', role='output', columns=['t.a']),
                                  dict(meaning='Activity type', granularity='atomic', role='output', columns=['t.b'])],
                        issue_owners={}, new_issues=[], relations=[], coverage='Amount and type survive'))
    return state, plan, event, raw


def test_package_keeps_sibling_and_commits_one_revision():
    state, plan, event, raw = fixture()
    before = deepcopy(state)
    result = apply_package(state, plan, event, C, raw)
    assert state == before
    assert result['revision'] == state['revision'] + 1
    assert {n['meaning'] for n in frontier(result).values()} == {'Activity amount', 'Activity type', 'Unrelated birthday'}
    assert all(n['assessed'] for n in frontier(result).values() if n['granularity'] == 'atomic')


def test_package_failure_does_not_commit_answer_or_partition():
    state, plan, event, raw = fixture()
    state['nodes']['f1']['columns'] = ['t.time']
    # Reprepare to put the existing dependency in the focus.
    plan['parts'][0]['columns'] = ['t.time']
    before = deepcopy(state)
    with pytest.raises(ValueError, match='Lost retained'):
        apply_package(state, plan, event, C, raw)
    assert state == before


def test_package_proposed_relation_stays_unconfirmed():
    state, plan, event, raw = fixture()
    raw['refinement']['children'][0].update(role='modifier')
    raw['refinement']['relations'] = [dict(source=0, target=1, kind='applies_to')]
    result = apply_package(state, plan, event, C, raw)
    assert all(r['status'] != 'confirmed' for r in result['relations'])


def test_pending_issue_moves_to_one_child_without_fabricated_closure():
    state, plan, event, raw = fixture()
    iid = add_issue(state, 'f1', 'value', 'Which date?')
    plan['owners'][iid] = 'focus'
    raw['refinement']['issue_owners'] = {iid:1}
    result = apply_package(state, plan, event, C, raw)
    q = result['issues'][iid]
    assert q['status'] == 'pending'
    assert result['nodes'][q['owner']]['meaning'] == 'Activity type'


def test_scope_cannot_silently_disappear():
    state, plan, event, raw = fixture()
    plan['parts'][0]['scope_columns'] = ['t.time']
    with pytest.raises(ValueError, match='subject/scope'):
        apply_package(state, plan, event, C, raw)
