"""Real parked-root collision plus adversarial structural update proposals."""
import inspect
import json
from copy import deepcopy
from pathlib import Path

import pytest

from clarification_deferred_deletion.baseline import light, module
from clarification_deferred_deletion.method import sync_canonical
from .method import TypedDeferredDeletion
from .questions import apply_questions
from .structure import check_structure
from .transaction import transaction, allocated_ids
from .test_obligations import event_for, minimal
from .questions import apply_questions


def collision_trace():
    p = Path('artifacts/clarification_main/deferred_v4_grounded_questions_five_live_20261025/883_1_reduction.json')
    d = json.loads(p.read_text(encoding='utf8'))
    s = next(deepcopy(s) for s in d['episode']['snapshots'] if s.get('update_base'))
    raw = json.loads(next(c for c in d['calls'] if c['stage'] == 'semantic_transaction')
                     ['response']['choices'][0]['message']['content'])
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[d['db_id']])
    return s, raw, context


def test_owned_transaction_differs_only_in_namespace_and_alias_ownership():
    # Guard the frozen permission/ownership baseline while allowing the explicit
    # transaction compiler extensions covered below.
    original = inspect.getsource(light.transaction)
    expected = original.replace(
        "used = set(base['items']) | set(info['parents']) | set(info['retired'])",
        'used = allocated_ids(session, base)').replace(
        "or temp in base['items'] and temp != parent", 'or temp in used and temp != parent')
    current = inspect.getsource(transaction)
    assert 'used = allocated_ids(session, base)' in current
    assert 'or temp in used and temp != parent' in current
    assert 'edge_endpoint_resolutions' in current
    assert 'if a == b:' in current
    assert 'protected_outputs(base, event)' in current


def test_one_to_one_replacement_resolves_stale_parent_edge_and_elides_self_loop():
    s, raw, context = collision_trace()
    raw['edges'] = [['p1', 'f1', 'context']]
    before = deepcopy(s)
    with TypedDeferredDeletion().installed():
        result = TypedDeferredDeletion().commit_transaction(
            s, s['update_base'], event_for(s), raw, context)
    audit = result['transaction_audit'][-1]['edge_endpoint_resolutions']
    assert any(row.get('effect') == 'elided_self_relation' for row in audit)
    assert all(edge['source_id'] != edge['target_id']
               for edge in result['view']['relations']['edges'])
    assert s == before


@pytest.mark.parametrize('block_id', ['f2', 'f10000'])
def test_answer_never_reuses_a_parked_root_id(block_id):
    s, raw, context = collision_trace()
    if block_id != 'f2':
        # Rename the fixture identity consistently, including blocked ledger,
        # edges and canonical records; do not create an invalid test state.
        s = json.loads(json.dumps(s).replace('"f2"', json.dumps(block_id)))
    before = deepcopy(s)
    parked = deepcopy(s['deferred_state']['blocked_nodes'])
    method = TypedDeferredDeletion()
    with method.installed():
        assert light.transaction is transaction
        result = method.commit_transaction(s, s['update_base'], event_for(s), raw, context)
    result = sync_canonical(result)
    assert not set(result['view']['items']) & set(parked)
    assert result['deferred_state']['blocked_nodes'] == parked
    assert set(parked) <= set(result['deferred_state']['canonical_nodes'])
    assert not result['view']['progressive']['decision_ledger']
    assert max(int(i[1:]) for i in result['view']['items']) > int(block_id[1:])
    assert s == before


@pytest.mark.parametrize('mutation,error', [
    ('alias', 'Local node aliases'),
    ('edge', 'Invalid relation endpoints'),
    ('boundary', 'Updates must reference editable'),
])
def test_model_cannot_address_parked_nodes_or_fake_boundary(mutation, error):
    s, raw, context = collision_trace()
    if mutation == 'alias':
        raw['updates']['f1'][0]['id'] = 'f2'
    elif mutation == 'edge':
        raw['edges'] = [['p1', 'f2', 'context']]
    else:
        raw['updates']['f2'] = [dict(id='p2', text='Invented node', columns=[])]
    before = deepcopy(s)
    with TypedDeferredDeletion().installed(), pytest.raises(ValueError, match=error):
        TypedDeferredDeletion().commit_transaction(s, s['update_base'], event_for(s), raw, context)
    assert s == before


@pytest.mark.parametrize('ids', [['d1', 'invented'], ['d1', 'd1'], ['invented'], []])
def test_invalid_explicit_asked_ids_are_rejected_wholly(ids):
    base, event, raw, mapping = minimal()
    event['turn']['decision_ids'] = ids
    with pytest.raises(ValueError, match='Asked decision IDs'):
        apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})


def test_alias_resolution_cannot_duplicate_question_routes():
    base, event, raw, mapping = minimal()
    base['progressive']['decision_ledger'].append(dict(decision_id='d2', owner_id='f1',
        dimension='value', question='Which category?', source_span='Category'))
    raw['question_changes'] = {'d2': {'to': ['p1', 'f2']}}
    with pytest.raises(ValueError, match='after alias resolution'):
        apply_questions(base, event, raw, mapping, {'f1': ['f2']}, {'p1': 'f2'}, {'operation'})


def test_explicit_scheduler_ids_must_match_original_pending_dimension():
    decision = dict(decision_id='d1', owner_id='f1', dimension='field',
                    question='Which institutional detail?', source_span='Details')
    base = dict(items={}, progressive=dict(decision_ledger=[decision], next_decision_id=2))
    event = dict(kind='answer', target='f1', dimension='scope', turn=dict(
        decision_ids=['d1'], answer=dict(action='choose', text='', choices=['A']),
        selected_options=[dict(text='Schools', required_columns=['schools.School'])]))
    raw = dict(updates={'f1': []})
    mapping = []
    with pytest.raises(ValueError, match='current IDs'):
        apply_questions(base, event, raw, mapping,
                        {'f1': ['f2']}, {'p1': 'f2'}, {'scope'})
    event['turn']['decision_ids'] = []
    event['target'] = 'f1'
    with pytest.raises(ValueError, match='current IDs'):
        apply_questions(base, event, raw, mapping,
                        {'f1': ['f2']}, {'p1': 'f2'}, {'scope'})
    event['turn']['decision_ids'] = ['d1']
    event['target'] = 'f2'
    with pytest.raises(ValueError, match='current IDs'):
        apply_questions(base, event, raw, mapping,
                        {'f1': ['f2']}, {'p1': 'f2'}, {'scope'})


@pytest.mark.parametrize('problem,error', [
    ('duplicate', 'identities must be unique'), ('owner', 'belong to eligible'),
    ('settled', 'cannot reenter'), ('cycle', 'ancestry must be acyclic'),
    ('question_owner', 'another owner decision'), ('stale_menu', 'obsolete decision'),
])
def test_invalid_state_cannot_cross_transition_boundary(problem, error):
    s, _, _ = collision_trace()
    s['view'] = deepcopy(s.pop('update_base'))
    decisions = s['view']['progressive']['decision_ledger']
    if problem == 'duplicate':
        decisions.append(deepcopy(decisions[0]))
    elif problem == 'owner':
        decisions[0]['owner_id'] = 'f2'
    elif problem == 'settled':
        s['settled_questions'] = [dict(decision_id=decisions[0]['decision_id'])]
    elif problem == 'cycle':
        s['view']['progressive']['parents'] = {'f1': 'f3', 'f3': 'f1'}
    elif problem == 'question_owner':
        s['view']['questions'][0]['item'] = dict(item_id='f3')
        s['view']['items']['f3'] = deepcopy(s['view']['items']['f1'])
    else:
        s['pending'] = dict(decision_key='stale_question')
    with pytest.raises(ValueError, match=error):
        check_structure(s)
