from copy import deepcopy
import json

import pytest

from clarification_deferred_deletion.baseline import module
from .acceptance import (acceptance_matrix, delivery_failures, commit_failures,
                         inspect_case)


def call(stage, payload, response):
    return dict(stage=stage, messages=[{}, {'content': json.dumps(payload)}],
                response={'choices': [{'message': {'content': json.dumps(response)}}]})


def record():
    nodes = [dict(item_id='o', role='output', kind='attribute', requirement='Return name.',
                  required_columns=['t.name'], missing=[], question_focus={}),
             dict(item_id='m', role='modifier', kind='instruction', requirement='Only active people.',
                  required_columns=['t.active'], missing=[], question_focus={})]
    edge = dict(source_id='o', target_id='m', kind='context')
    delivery = dict(action='deliver', text='Return names of active people.', final_render='active_graph_model')
    render = call('final_active_request', dict(returned_values=nodes[:1], nonreturned_requirements=nodes[1:],
                  edges=[['o','m','context']]), {'request': delivery['text']})
    review = call('deferred_commit_review', dict(candidate_text=delivery['text'],
                  surviving_nodes=nodes, surviving_edges=[edge]), {'accepted': True, 'issues': []})
    return dict(outcome='deliver', delivery=delivery, calls=[render,review], overlay_unchanged=True,
                episode=dict(delivery=deepcopy(delivery), session=dict(current_request='internal graph text',
                    view=dict(items={n['item_id']:n for n in nodes},relations={'edges':[edge]}),
                    deferred_state={'committed':True}, deferred_final=deepcopy(delivery))))


def test_graph_render_can_differ_from_internal_text_with_exact_provenance():
    d=record()
    assert delivery_failures(d)==[]
    assert commit_failures(d)==[]


@pytest.mark.parametrize('damage,expected', [
    ('text','render_output_disagrees'), ('nodes','render_graph_nodes_disagree'),
    ('edges','render_graph_edges_disagree'), ('no_render','render_evidence_missing'),
    ('mode','unknown_render_mode'), ('missing','unresolved_node_delivered'),
    ('ledger','pending_decision_delivered'), ('contract','unresolved_contract_delivered')])
def test_render_provenance_rejects_stale_or_unfinished_delivery(damage,expected):
    d=record();s=d['episode']['session']
    if damage=='text': d['delivery']['text']='Return everything.'
    elif damage=='nodes': s['view']['items']['m']['requirement']='Only recent people.'
    elif damage=='edges': s['view']['relations']['edges']=[]
    elif damage=='no_render': d['calls'].pop(0)
    elif damage=='mode': d['delivery']['final_render']='invented'
    elif damage=='missing': s['view']['items']['o']['missing']=['field']
    elif damage=='ledger': s['view']['progressive']={'decision_ledger':[{'decision_id':'d1'}]}
    else: s['contract_unresolved_reason']='unresolved'
    assert expected in delivery_failures(d)


@pytest.mark.parametrize('damage,expected', [
    ('no_review','commit_review_missing'), ('uncommitted','deferred_not_committed'),
    ('review_text','review_candidate_disagrees'), ('review_nodes','review_graph_nodes_disagree'),
    ('rejected','commit_review_not_accepted'), ('cached','committed_delivery_disagrees')])
def test_commit_requires_review_of_exact_delivery_and_current_graph(damage,expected):
    d=record();s=d['episode']['session']
    if damage=='no_review': d['calls'].pop()
    elif damage=='uncommitted': s['deferred_state']['committed']=False
    elif damage=='review_text': d['calls'][-1]['messages'][1]['content']=json.dumps({'candidate_text':'other'})
    elif damage=='review_nodes': s['view']['items']['o']['required_columns']=[]
    elif damage=='rejected': d['calls'][-1]['response']['choices'][0]['message']['content']=json.dumps({'accepted':False,'issues':['lost scope']})
    else: s['deferred_final']['text']='stale'
    assert expected in commit_failures(d)


def test_single_active_requirement_and_plain_text_paths():
    d=record();s=d['episode']['session'];s['view']['items'].pop('m')
    d['delivery'].update(final_render='single_active_requirement',text='Return name.')
    d['episode']['delivery']=deepcopy(d['delivery'])
    assert delivery_failures(d)==[]
    s['needs_final_render']=True
    assert 'required_render_skipped' in delivery_failures(d)
    d['delivery'].pop('final_render');d['delivery']['text']=s['current_request']
    d['episode']['delivery']=deepcopy(d['delivery'])
    assert delivery_failures(d)==[]


def test_legacy_guards_are_preserved_and_record_is_not_mutated(monkeypatch):
    d=record();before=deepcopy(d)
    monkeypatch.setattr(module('acceptance'),'inspect_case',lambda *args:
        {'failures':['delivery_text_disagrees','declared_forbidden_dependency_delivered']})
    result=inspect_case(d,{})
    assert result['failures']==['declared_forbidden_dependency_delivered']
    assert result['legacy_failures']==['delivery_text_disagrees','declared_forbidden_dependency_delivered']
    assert d==before


def test_current_pool_version_cannot_skip_obligation_closure(monkeypatch):
    from . import obligations
    d = record()
    d['config'] = {'method': 'useful_residual_v6_pool_v10_consolidated'}
    monkeypatch.setattr(module('acceptance'), 'inspect_case', lambda *args: {'failures': []})
    monkeypatch.setattr(obligations, 'issues', lambda session: [{'code': 'pending_internal_claim'}])
    result = inspect_case(d, {})
    assert 'pending_internal_claim' in result['failures']


def test_missing_evidence_is_failure_not_crash(monkeypatch):
    d=record();d['calls'][0]['response']={}
    monkeypatch.setattr(module('acceptance'),'inspect_case',lambda *args: {'failures':[]})
    assert 'invalid_delivery_evidence' in inspect_case(d,{})['failures']


def test_retry_uses_corrected_input_and_latest_response():
    d=record();c=d['calls'][0];c['stage'] += '_contract_retry'
    payload=json.loads(c['messages'][1]['content'])
    c['messages'][1]['content']=json.dumps({'input':payload,'previous_response':{'request':'wrong'}})
    d['calls'].insert(0,call('final_active_request',{}, {'request':'invalid old attempt'}))
    assert delivery_failures(d)==[]


def test_matrix_reports_missing_cases():
    passed, missing = acceptance_matrix([(1, 1)], [], True, [])
    assert missing == [(1, 1)]
    assert passed is False
