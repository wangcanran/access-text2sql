from copy import deepcopy

import pytest

from .method import prepare_relation_question, commit_relation_answer


def graph():
    return {'items': [
        {'item_id': 'time', 'role': 'modifier',
         'requirement': 'Use the period before the associated loan'},
        {'item_id': 'history', 'role': 'output',
         'requirement': 'Summarize transaction history'},
        {'item_id': 'ratio', 'role': 'output',
         'requirement': 'Calculate the expense-to-income ratio'},
        {'item_id': 'balance', 'role': 'output',
         'requirement': 'Give the balance range'},
    ], 'edges': []}


def question(g):
    return prepare_relation_question(g['items'], [{
        'relation_id': 'r_time', 'modifier_id': 'time',
        'candidate_output_ids': ['history', 'ratio', 'balance'],
        'evidence': 'Each output refers to activity before the same loan.'}])[0]


def test_relation_uncertainty_renders_business_menu_with_multiselect():
    q = question(graph())
    assert q['multi_select'] is True
    assert [o['label'] for o in q['options']] == [
        'Summarize transaction history',
        'Calculate the expense-to-income ratio',
        'Give the balance range']
    assert 'loan' in q['question']


def test_relation_answer_adds_only_selected_edges_without_text_edits():
    source = graph()
    before = deepcopy(source)
    q = question(source)
    updated, added = commit_relation_answer(source, q, ['A', 'B'])
    assert source == before
    assert set((e['source_id'], e['target_id']) for e in updated['edges']) == {
        ('time', 'history'), ('time', 'ratio')}
    assert ('time', 'balance', 'applies_to') not in {
        (e['source_id'], e['target_id'], e['kind']) for e in updated['edges']}
    assert len(added) == 2
    assert [n['requirement'] for n in updated['items']] == [n['requirement'] for n in source['items']]
    assert updated['resolved_relations'][0]['reviewed_outputs'] == ['history', 'ratio', 'balance']
    assert updated['resolved_relations'][0]['unselected_outputs_confirmed_unattached'] is True


def test_none_of_these_is_not_a_valid_empty_relation_answer():
    g, q = graph(), question(graph())
    with pytest.raises(ValueError, match='Choose one or more'):
        commit_relation_answer(g, q, [])


def test_relation_answer_rejects_invalid_options():
    g, q = graph(), question(graph())
    with pytest.raises(ValueError, match='Choose one or more'):
        commit_relation_answer(g, q, ['Z'])
