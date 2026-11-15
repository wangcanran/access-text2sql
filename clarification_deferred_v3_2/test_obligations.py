"""Default answer settlement: actual traces plus identity/lineage regressions."""
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from clarification_deferred_deletion.baseline import module
from clarification_deferred_deletion.method import DeferredDeletion
from .method import (TypedDeferredDeletion, VERSION, reclassified_decisions,
                     remove_unbacked_questions)
from .questions import apply_questions, contract, POLICY, ledger

TRACES = Path('artifacts/clarification_main/deferred_v2_expanded_six_live_20261025')


def trace(qid, level=0):
    d = json.loads((TRACES / f'{qid}_{level}_reduction.json').read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    return d, context


def event_for(s):
    t = s['turns'][-1]
    return dict(kind='answer', target=t['target_id'], dimension=t['asked_dimension'],
                question=t['question'].get('focus', t['question']['question']), turn=t)


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


@pytest.mark.parametrize('qid,level', [(1343, 0), (883, 1), (42, 0)])
def test_real_answer_closes_current_ids_without_minting_followups(qid, level):
    d, context = trace(qid, level)
    s = next(deepcopy(x) for x in d['episode']['snapshots'] if x.get('update_base'))
    raw = json.loads(next(c for c in d['calls'] if c['stage'] == 'semantic_transaction')
                     ['response']['choices'][0]['message']['content'])
    old = ledger.pending(s['update_base'])
    asked = set(s['turns'][-1]['decision_ids'])
    original = deepcopy(s)
    method = TypedDeferredDeletion()
    with method.installed():
        result = method.commit_transaction(s, s['update_base'], event_for(s), raw, context)
    current = ledger.pending(result['view'])
    assert not asked & {d['decision_id'] for d in current}
    assert {d['decision_id'] for d in current} == {d['decision_id'] for d in old} - asked
    assert result['clarification_policy'] == POLICY
    assert {d['decision_id'] for d in result['settled_questions']} == asked
    assert s == original


@pytest.mark.parametrize('dimension', ['scope', 'field', 'operation', 'value'])
def test_paraphrased_or_cross_dimension_child_questions_cannot_reopen_answer(dimension):
    base, event, raw, mapping = minimal()
    mapping[0]['question_focus'] = {dimension: 'Please clarify that answer in more detail?'}
    raw['question_changes'] = {'d1': {'to': ['p1'], 'question': 'Another wording of the old question'}}
    event['turn']['scoped_permission_decision'] = dict(clarification=dict(
        dimension='operation', followups={dimension: 'More detail?'}, continue_decisions=['d1']))
    rows, _, audit = apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})
    assert rows == []
    assert mapping[0]['missing'] == []
    assert audit[0]['reason'] == POLICY


def test_other_question_in_same_dimension_stays_pending_and_keeps_original_wording():
    base, event, raw, mapping = minimal()
    base['progressive']['decision_ledger'].append(dict(decision_id='d2', owner_id='f1',
        dimension='operation', question='Which independent calculation?', source_span='Other result'))
    raw['question_changes'] = {'d2': {'resolved': 'The related node was answered', 'question': 'Changed meaning'}}
    rows, _, _ = apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})
    assert len(rows) == 1 and rows[0]['decision_id'] == 'd2'
    assert rows[0]['question'] == 'Which independent calculation?'


def test_dense_answer_can_explicitly_settle_additional_current_decisions():
    base, event, raw, mapping = minimal()
    base['progressive']['decision_ledger'].extend([
        dict(decision_id='d2', owner_id='f1', dimension='value',
             question='Which country?', source_span='Country'),
        dict(decision_id='d3', owner_id='f1', dimension='field',
             question='Which label?', source_span='Label')])
    raw['settled_decisions'] = ['d2', 'd3']
    rows, _, audit = apply_questions(base, event, raw, mapping,
                                     {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})
    assert rows == []
    assert {a['decision_id'] for a in audit if a.get('effect') == 'resolved'} == {'d1', 'd2', 'd3'}


def test_split_routes_independent_question_but_cannot_route_current_one_back():
    base, event, raw, mapping = minimal()
    base['progressive']['decision_ledger'].append(dict(decision_id='d2', owner_id='f1',
        dimension='value', question='Which category?', source_span='Other requirement'))
    mapping.append(dict(item_id='f3', requirement='Category condition', question_focus={}))
    raw['updates']['f1'].append(dict(id='p2'))
    raw['question_changes'] = {'d1': {'to': ['p1']}, 'd2': {'to': ['p2']}}
    rows, _, _ = apply_questions(base, event, raw, mapping, {'f1': ['f2', 'f3']},
                                 {'p1': 'f2', 'p2': 'f3'}, {'operation'})
    assert [(r['decision_id'], r['owner_id']) for r in rows] == [('d2', 'f3')]


def test_control_answer_does_not_count_as_clarification():
    base, event, raw, mapping = minimal()
    event['turn']['answer'] = dict(action='unanswerable', text='', choices=[])
    rows, _, _ = apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})
    assert len(rows) == 1 and rows[0]['decision_id'] == 'd1'


def test_no_ambiguous_identity_fallback_can_close_independent_questions():
    base, event, raw, mapping = minimal()
    event['turn'].pop('decision_ids')
    base['progressive']['decision_ledger'].append(dict(decision_id='d2', owner_id='f1',
        dimension='operation', question='Which other operation?', source_span='Other result'))
    with pytest.raises(ValueError, match='actual pending question IDs'):
        apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})


def test_later_permission_split_cannot_reopen_settled_lineage():
    session = dict(settled_questions=[dict(owner='f1')], view=dict(
        items={}, progressive=dict(parents={'f2': 'f1'}, decision_ledger=[dict(
            decision_id='d2', owner_id='f2', dimension='value', question='Which category?')])) )
    raw = dict(nodes=[dict(parent='f2', decision_ids=['d2'], questions=dict(
        field='Choose the answered field again', value='Which category?'), candidates={'t.id': 'ID'})])
    result = TypedDeferredDeletion._settled_plan(raw, session)
    assert result['nodes'][0]['questions'] == {'value': 'Which category?'}
    assert not result['nodes'][0]['candidates']
    assert 'field' in raw['nodes'][0]['questions']


def test_compiler_no_longer_has_continuation_authority():
    assert contract({'dimension': 'value'}) == {'dimension': 'value'}
    with pytest.raises(ValueError):
        contract({'dimension': 'field', 'followups': {'field': 'Ask again'}})


def test_reclassification_routes_only_unique_existing_same_owner_decision():
    view = {'progressive': {'decision_ledger': [
        {'decision_id': 'd1', 'owner_id': 'f1', 'dimension': 'scope', 'question': 'Which entity?'},
        {'decision_id': 'd2', 'owner_id': 'f1', 'dimension': 'operation', 'question': 'Which measure?'},
        {'decision_id': 'd3', 'owner_id': 'f2', 'dimension': 'operation', 'question': 'Other measure?'}]}}
    question = {'decision_ids': ['d1'], 'item': {'item_id': 'f1'}}
    decision = {'clarification': {'dimension': 'operation'}}
    assert [d['decision_id'] for d in reclassified_decisions(view, question, decision)] == ['d2']
    question['decision_ids'] = []
    assert [d['decision_id'] for d in reclassified_decisions(view, question, decision)] == ['d2']
    single = {'progressive': {'decision_ledger': [
        {'decision_id': 'd9', 'owner_id': 'f1', 'dimension': 'field', 'question': 'Which measure?'}]}}
    assert [d['decision_id'] for d in reclassified_decisions(single, question, decision)] == ['d9']
    view['progressive']['decision_ledger'].append(
        {'decision_id': 'd4', 'owner_id': 'f1', 'dimension': 'operation', 'question': 'Another measure?'})
    with pytest.raises(ValueError, match='explicit pending decision identity'):
        reclassified_decisions(view, question, decision)


def test_unbacked_derived_question_is_not_user_facing():
    session = {'view': {'progressive': {'decision_ledger': [
        dict(decision_id='d1', owner_id='f1', dimension='field')]},
        'questions': [dict(key='phantom'), dict(key='field', decision_ids=['d1'])]}}
    remove_unbacked_questions(session)
    assert [q['key'] for q in session['view']['questions']] == ['field']


def test_compiler_published_json_example_matches_actual_parser_contract():
    # First v4 live failed twice because the legacy example omitted the
    # mandatory key and the appended prose suggested an alias instead.
    prompt, payload = TypedDeferredDeletion().decision_input('legacy no-formulas contract', {})
    schema = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
    assert 'clarification' in schema['required']
    assert schema['properties']['remaining_requirements']['items']['type'] == 'string'
    assert schema['properties']['clarification']['properties']['dimension']['enum'] == [
        'scope', 'field', 'operation', 'value']
    assert schema['additionalProperties'] is False
    assert 'legacy no-formulas contract' not in prompt
    assert 'additional required clarification' not in prompt
    assert payload['pending_decisions'] == []


@pytest.mark.parametrize('qid,level,stage', [(1343, 0, 'field_decision'), (883, 1, 'field_decision')])
def test_full_compiler_accepts_corrected_live_responses(qid, level, stage):
    folder = Path('artifacts/clarification_main/deferred_v4_compiler_contract_two_live_20261025')
    d = json.loads((folder / f'{qid}_{level}_reduction.json').read_text(encoding='utf8'))
    c = next(c for c in d['calls'] if c['stage'] == stage)
    raw = json.loads(c['response']['choices'][0]['message']['content'])
    _, context = trace(qid, level)
    method = TypedDeferredDeletion()
    source = next(iter(d['episode']['snapshots'][0]['view']['items'].values()))
    result = method.compile_decision(lambda *args: deepcopy(raw),
        dict(source=source, pending_columns=[]), context, field=True)
    assert result['clarification'] == {'dimension': 'field'}
    assert result['field_candidates']


def test_real_calculation_still_asks_operation_before_numeric_attribute():
    d, _ = trace(760)
    method = TypedDeferredDeletion()
    with method.installed():
        s = deepcopy(d['episode']['snapshots'][0])
        plan = method.next_step(s)
        assert plan['action'] == 'ask'
        assert method.engine.decision(s, plan)['dimension'] == 'operation'


def test_removal_prompt_explicitly_requests_json_and_none_path_is_retained():
    prompts = []
    def removal(self, call, *args):
        call('deferred_none_focus', 'Locate rejected leaf.', {})
        return {}
    with patch.object(DeferredDeletion, '_remove', removal):
        TypedDeferredDeletion()._remove(lambda s, p, v: prompts.append(p), {}, {}, {}, 'NONE', {})
    assert prompts and 'JSON' in prompts[0]
    assert VERSION == 'deferred_deletion_v4_1_canonical_ids_answer_settlement'


@pytest.mark.parametrize('stage', ['progressive_coarse', 'progressive_coarse_contract_retry',
                                 'progressive_analysis', 'progressive_analysis_contract_retry'])
def test_initial_question_boundary_applies_to_both_planning_stages_and_retries(stage):
    seen = []
    def initial(self, call, *args):
        call(stage, 'Original stage contract. JSON.', {'current_request': 'public request'})
        return {}
    with patch.object(DeferredDeletion, 'analyze_session', initial):
        TypedDeferredDeletion().analyze_session(lambda s, p, v: seen.append((s, p, v)), {}, {})
    assert seen[0][0] == stage
    assert 'QUESTION DISCOVERY BOUNDARY' in seen[0][1]
    assert 'Original stage contract' in seen[0][1]
    assert seen[0][2] == {'current_request': 'public request'}
