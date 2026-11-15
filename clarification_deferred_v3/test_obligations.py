"""Regression tests reconstructed from actual expanded-v2 failures."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from clarification_deferred_deletion.baseline import module, light
from .method import TypedDeferredDeletion
from .questions import apply_questions

TRACES = Path('artifacts/clarification_main/deferred_v2_expanded_six_live_20261025')


def trace(qid, level=0):
    d = json.loads((TRACES / f'{qid}_{level}_reduction.json').read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    return d, context


def first_answer(d):
    return next(deepcopy(s) for s in d['episode']['snapshots'] if s.get('update_base'))


def raw_response(d, stage):
    c = next(c for c in d['calls'] if c['stage'] == stage)
    return json.loads(c['response']['choices'][0]['message']['content'])


def event_for(s):
    t = s['turns'][-1]
    return dict(kind='answer', target=t['target_id'], dimension=t['asked_dimension'],
                question=t['question'].get('focus', t['question']['question']), turn=t)


@pytest.mark.parametrize('qid,level,dimension,question', [
    (1343, 0, 'value', 'Which expenditure category value should be used?'),
    (883, 1, 'field', 'Which race information should be returned?'),
])
def test_real_scope_answer_cannot_erase_required_continuation(qid, level, dimension, question):
    d, context = trace(qid, level)
    s = first_answer(d)
    e = event_for(s)
    e['turn']['scoped_permission_decision']['clarification'] = dict(
        dimension=e['dimension'], followups={dimension: question})
    raw = raw_response(d, 'semantic_transaction')
    if qid == 1343:
        # The real response splits category/rank/output. v3 requires the model
        # to route the new value obligation to the category child explicitly.
        raw['followup_routes'] = {dimension: 'p1'}
    method = TypedDeferredDeletion()
    before = deepcopy(s)
    with method.installed():
        result = method.commit_transaction(s, s['update_base'], e, raw, context)
        assert any(q['dimension'] == dimension and q['focus'] == question for q in result['view']['questions'])
        assert method.next_step(result)['action'] in ('ask', 'menu')
    assert s == before


def test_real_percentage_menu_is_reclassified_before_none_can_prune():
    d, context = trace(760)
    s = deepcopy(d['episode']['snapshots'][0])
    # Exercise the correction path even when the upstream kind was not tagged
    # calculation. Correctly tagged calculations use operation-first directly.
    for n in s['view']['items'].values():
        n['kind'] = 'attribute'
    for q in s['view']['questions']:
        q['item']['kind'] = 'attribute'
    raw = raw_response(d, 'field_decision')
    raw['question'] = 'What summary calculation should be computed?'
    raw['clarification'] = dict(dimension='operation', followups={})
    method = TypedDeferredDeletion()
    stages = []
    def call(stage, prompt, payload):
        stages.append(stage)
        assert stage == 'field_decision'
        assert 'clarification=' in prompt
        return raw
    with method.installed():
        plan = method.next_step(s)
        result, public = method.prepare_menu(call, s, plan, context)
        assert public is None
        assert stages == ['field_decision']
        assert not result.get('deferred_state')
        assert not any(q['dimension'] == 'field' for q in result['view']['questions'])
        assert any(raw['question'] in q['focus'] and q['dimension'] == 'operation'
                   for q in result['view']['questions'])
        assert method.next_step(result)['action'] == 'ask'


def test_real_calculation_asks_operation_before_binding_numeric_attribute():
    d, _ = trace(760)
    s = deepcopy(d['episode']['snapshots'][0])
    method = TypedDeferredDeletion()
    with method.installed():
        plan = method.next_step(s)
        q = method.engine.decision(s, plan)
    assert plan['action'] == 'ask'
    assert q['dimension'] == 'operation'


def test_parent_question_snapshot_does_not_duplicate_existing_identity():
    base, event, raw, mapping = minimal()
    base['items'] = {'f1': {'question_focus': {'operation': 'Which ranking method?'}}}
    mapping[0]['question_focus'] = {'operation': 'Which ranking method?'}
    rows, _, _ = apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})
    assert rows == []


def test_continued_decision_retains_identity_after_menu_selection():
    base, event, raw, mapping = minimal()
    event['turn'].update(selected_options=[dict(text='Attribute')], scoped_permission_decision=dict(
        clarification=dict(dimension='field', followups={}, continue_decisions=['d1'])))
    mapping[0]['question_focus'] = {}
    rows, _, _ = apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})
    assert len(rows) == 1
    assert rows[0]['decision_id'] == 'd1'
    assert rows[0]['owner_id'] == 'f2'


def minimal():
    d = dict(decision_id='d1', owner_id='f1', dimension='operation',
             question='Which ranking method?', source_span='Rank records')
    base = dict(items={}, progressive=dict(decision_ledger=[d], next_decision_id=2))
    event = dict(kind='answer', target='f1', dimension='operation', turn=dict(
        decision_ids=['d1'], answer=dict(action='answer', text='Dense rank', choices=[])))
    mapping = [dict(item_id='f2', requirement='Rank records using dense rank',
                    question_focus={'operation': 'How should equal scores be handled?'})]
    raw = dict(updates={'f1': [dict(id='p1')]})
    return base, event, raw, mapping


def test_new_same_dimension_question_does_not_require_magic_words():
    base, event, raw, mapping = minimal()
    rows, _, _ = apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})
    assert len(rows) == 1
    assert rows[0]['decision_id'] != 'd1'
    assert rows[0]['question'] == 'How should equal scores be handled?'


def test_menu_choice_cannot_resolve_other_value_dimension():
    base, event, raw, mapping = minimal()
    base['progressive']['decision_ledger'].append(dict(decision_id='d2', owner_id='f1',
        dimension='value', question='Which category?', source_span='Rank records'))
    event['dimension'] = 'field'
    event['turn'].update(answer=dict(action='choose', text='', choices=['A']),
                         selected_options=[dict(text='Category field')])
    raw['question_changes'] = {'d2': {'resolved': 'Category field selected'}}
    with pytest.raises(ValueError, match='different clarification dimension'):
        apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'field'})


def test_followup_cannot_be_routed_to_sibling_or_silently_duplicated():
    base, event, raw, mapping = minimal()
    event['turn'].update(selected_options=[dict(text='Category')], scoped_permission_decision=dict(
        clarification=dict(dimension='field', followups={'value': 'Which category label?'})))
    mapping.append(dict(item_id='f3', requirement='Independent output', question_focus={}))
    raw['updates']['f1'].append(dict(id='p2'))
    with pytest.raises(ValueError, match='Route mandatory'):
        apply_questions(base, event, raw, mapping, {'f1': ['f2', 'f3']},
                        {'p1': 'f2', 'p2': 'f3'}, {'operation'})
    raw['followup_routes'] = {'value': 'p1'}
    rows, _, _ = apply_questions(base, event, raw, mapping, {'f1': ['f2', 'f3']},
                                 {'p1': 'f2', 'p2': 'f3'}, {'operation'})
    assert sum(d['dimension'] == 'value' for d in rows) == 1
    assert next(d['owner_id'] for d in rows if d['dimension'] == 'value') == 'f2'
