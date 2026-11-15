"""Real failure checkpoints and conservative normalization boundaries."""
from copy import deepcopy
import json
import pytest

from .state import initial, add_issue
from .planner import assess
from .transaction import commit
from .test_tree import C, A, ready, desc, tx, change


def compact_plan():
    return {'granularity': 'compound', 'issues': [{'dimension': 'operation', 'question': 'Which formula?',
             'options': [{'label': 'Outgoings / income'}]}]}


def test_nested_options_single_focus_and_business_columns_are_code_owned():
    raw = compact_plan()
    before = deepcopy(raw)
    new = assess(initial('profile'), 'f1', raw, C)
    assert new['issues']['d1']['options'] == [{'label': 'Outgoings / income', 'columns': []}]
    assert new['nodes']['f1']['planned_issue'] == 'd1'
    assert raw == before
    assert new['audit'][0]['action'] == 'normalize_plan'


def test_conflicting_locations_rejected_atomically():
    raw = compact_plan()
    raw['options'] = [{'label': 'A different formula', 'columns': []}]
    s = initial('profile')
    before = deepcopy(s)
    with pytest.raises(ValueError, match='Conflicting'):
        assess(s, 'f1', raw, C)
    assert s == before


def test_identical_dual_menu_is_not_a_conflict():
    raw = compact_plan()
    raw['options'] = deepcopy(raw['issues'][0]['options'])
    assert assess(initial('profile'), 'f1', raw, C)['issues']['d1']['options']


def test_nonfocused_options_not_lost():
    raw = compact_plan()
    raw['issues'].append({'dimension': 'field', 'question': 'Which data?',
                         'options': [{'label': 'A', 'columns': ['t.a']}]})
    raw['focus'] = 0
    new = assess(initial('profile'), 'f1', raw, C)
    assert new['issues']['d2']['options'] == [{'label': 'A', 'columns': ['t.a']}]


def test_multiple_questions_need_focus_and_field_choices_need_dependencies():
    raw = compact_plan()
    raw['issues'].append({'dimension': 'value', 'question': 'Which date?'})
    with pytest.raises(ValueError, match='explicit focus'):
        assess(initial('profile'), 'f1', raw, C)
    raw = compact_plan()
    raw['issues'][0]['dimension'] = 'field'
    with pytest.raises(ValueError):
        assess(initial('profile'), 'f1', raw, C)
    raw['issues'][0]['options'][0]['columns'] = []
    with pytest.raises(ValueError, match='concrete dependencies'):
        assess(initial('profile'), 'f1', raw, C)


def test_compact_update_keeps_unasked_issue_pending_and_old_columns():
    s, e = ready()
    s['nodes']['f1']['columns'] = ['t.a']
    e['selected_columns'] = ['t.b']
    add_issue(s, 'f1', 'value', 'Which subset?')
    raw = {'children': [{'meaning': 'A with subset still unclear', 'granularity': 'atomic', 'role': 'output'}],
           'issue_updates': {'d1': {'status': 'answered', 'reason': 'The user selected A'}},
           'coverage': 'A confirmed, subset remains open'}
    new = commit(s, e, raw, C)
    assert new['nodes']['f1']['columns'] == ['t.a', 't.b']
    assert new['issues']['d2']['status'] == 'pending'
    assert new['issues']['d2']['owner'] == 'f1'
    assert new['issues']['d1']['status'] == 'answered'
    assert new['audit'][-1]['transaction']['action'] == 'revise'


def test_omitted_asked_issue_does_not_mean_answered():
    s, e = ready()
    new = commit(s, e, {'children': [{'meaning': 'A', 'granularity': 'atomic', 'role': 'output'}],
                        'coverage': 'Still need confirmation'}, C)
    assert new['issues']['d1']['status'] == 'pending'


def test_compact_expansion_uses_positional_aliases_and_unresolved_edges():
    s, e = ready()
    raw = {'children': [{'meaning': 'A', 'granularity': 'atomic', 'role': 'output', 'columns': ['t.a']},
                        {'meaning': 'time condition', 'granularity': 'atomic', 'role': 'modifier', 'columns': ['t.time']}],
           'issue_updates': {'d1': {'status': 'answered', 'owner': 'p1', 'reason': 'A selected'}},
           'relations': [{'source': 'p2', 'target': 'p1', 'kind': 'applies_to'}],
           'coverage': 'Return A with shared time condition'}
    new = commit(s, e, raw, C)
    assert new['nodes']['f1']['children'] == ['f2', 'f3']
    assert new['relations'][0]['status'] == 'unresolved'
    assert new['audit'][-1]['transaction']['action'] == 'expand'


def test_cannot_guess_expansion_ownership_or_retire_dependencies():
    s, e = ready()
    raw = {'children': [desc('a', 'A'), desc('b', 'B')], 'coverage': 'A and B'}
    with pytest.raises(ValueError, match='Every old'):
        commit(s, e, raw, C)
    s['nodes']['f1']['columns'] = ['t.a']
    raw = {'children': [desc('p', 'A', columns=[])], 'coverage': 'A'}
    with pytest.raises(ValueError, match='Lost retained'):
        commit(s, e, raw, C)


def test_explicit_conflicting_action_and_confirmed_edge_still_rejected():
    s, e = ready()
    raw = tx([desc('p', 'A')], {'d1': change()})
    raw['action'] = 'expand'
    with pytest.raises(ValueError, match='Expansion'):
        commit(s, e, raw, C)
    raw = tx([desc('a', 'A'), desc('b', 'B')], {'d1': change(owner='a')},
             relations=[{'source': 'a', 'target': 'b', 'kind': 'depends_on', 'status': 'confirmed'}])
    with pytest.raises(ValueError, match='user confirmation'):
        commit(s, e, raw, C)


def test_v3_original_nested_responses_accept_without_model_repair():
    from ...runtime import ROOT
    from ...live import load_case
    path = ROOT / 'artifacts/clarification_main/clarification_tree_live_v3/113_1_reduction.json'
    if not path.exists():
        pytest.skip('Historical v3 checkpoint absent')
    record = json.loads(path.read_text(encoding='utf8'))
    context = load_case(113, 1)[1]
    for index, state, target in [(0, initial(record['request']), 'f1'),
                                 (6, record['episode']['session'], 'f3')]:
        raw = json.loads(record['calls'][index]['response']['choices'][0]['message']['content'])
        before = deepcopy(state)
        new = assess(state, target, raw, context)
        chosen = new['issues'][new['nodes'][target]['planned_issue']]
        assert len(chosen['options']) == 3
        assert state == before
    # The failed repair erased binding content. Normalization must not pretend
    # that a field option with no columns is a valid executable alternative.
    raw = json.loads(record['calls'][7]['response']['choices'][0]['message']['content'])
    with pytest.raises(ValueError, match='concrete dependencies'):
        assess(record['episode']['session'], 'f3', raw, context)
