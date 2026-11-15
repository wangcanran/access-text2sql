from copy import deepcopy

import pytest

from clarification_deferred_deletion.graph import closure, required_columns
from clarification_deferred_deletion.baseline import analysis
from clarification_main.experiments.useful_residual.model import Node, Edge, Graph
from clarification_main.experiments.useful_residual.prune import prune
from .compiler import compile_plan, compile_subject_lineage, validate_subject_edges, review_permission_facts
from .method import UsefulResidualMethod
from .prompts import PLAN, BINDINGS


def fixture():
    def n(i, kind, cols, affects):
        return dict(id=i, parent='f1', text=i, semantic_class=kind,
                    kind='attribute', columns=cols, questions={}, candidates={},
                    decision_ids=[], binding_claims=[], affects=affects)
    return dict(nodes=[n('id', 'output', ['e.id'], []),
                       n('phone', 'output', ['e.phone'], []),
                       n('year', 'scope', ['e.year'], ['id', 'phone']),
                       n('legend', 'annotation', ['e.legend'], ['phone'])],
                retired_dependencies={})


@pytest.mark.parametrize('denied,expected', [
    ([], set()), (['e.id'], {'id'}), (['e.phone'], {'phone', 'legend'}),
    (['e.year'], {'id', 'phone', 'year', 'legend'}),
    (['e.legend'], {'legend'}),
])
def test_compiled_deferred_closure_matches_useful_residual_rules(denied, expected):
    raw = fixture()
    original = deepcopy(raw)
    plan = compile_plan(raw)
    allowed = {'e.id', 'e.phone', 'e.year', 'e.legend'} - set(denied)
    assert set(closure(plan, allowed)) == expected
    graph = Graph(tuple(Node(n['id'], 'output' if n['semantic_class'] == 'output' else 'modifier',
                             n['text'], frozenset(n['columns']), n['semantic_class'])
                        for n in raw['nodes']), tuple(Edge(*e) for e in plan['edges']))
    assert prune(graph, allowed).dropped_ids == expected
    assert raw == original
    # Runtime permission checking consumes transitive dependencies before menus.
    assert required_columns(plan)['phone'] == {'e.phone', 'e.year'}


def test_none_on_scope_propagates_but_annotation_does_not():
    plan = compile_plan(fixture())
    allowed = {'e.id', 'e.phone', 'e.year', 'e.legend'}
    assert set(closure(plan, allowed, {'year': {'rule': 'NONE'}})) == {
        'year', 'id', 'phone', 'legend'}
    assert set(closure(plan, allowed, {'legend': {'rule': 'NONE'}})) == {'legend'}


@pytest.mark.parametrize('mutation', ['role', 'edges', 'missing_class', 'unknown_consumer', 'orphan'])
def test_model_cannot_take_over_structural_protocol(mutation):
    raw = fixture()
    if mutation == 'role':
        raw['nodes'][0]['role'] = 'output'
    elif mutation == 'edges':
        raw['edges'] = []
    elif mutation == 'missing_class':
        raw['nodes'][0].pop('semantic_class')
    elif mutation == 'unknown_consumer':
        raw['nodes'][2]['affects'] = ['absent']
    else:
        raw['nodes'][2]['affects'] = []
    with pytest.raises(ValueError):
        compile_plan(raw)


def test_real_installed_stage_compiles_before_existing_validator_and_restores_runtime():
    original = analysis.validated_call
    prompts = []
    def call(stage, prompt, payload):
        prompts.append(prompt)
        return fixture()
    with UsefulResidualMethod().installed():
        plan = analysis.validated_call(call, 'contract_permission_decomposition',
                                       'obsolete prompt', {}, lambda raw: raw)
        assert ['phone', 'year', 'requires'] in plan['edges']
        assert prompts == [PLAN + BINDINGS]
    assert analysis.validated_call is original


def subject_nodes():
    return [dict(id='out', parent='f1', role='output', kind='attribute',
                 columns=['Patient.ID'], binding_claims=[dict(columns=['Patient.ID'])]),
            dict(id='scope', parent='f1', role='modifier', kind='attribute',
                 columns=['Patient.ID', 'Laboratory.ID'],
                 binding_claims=[dict(meaning='Patient locator', columns=['Patient.ID', 'Laboratory.ID'])])]


def test_returned_identifier_is_not_a_second_subject_provider():
    nodes = subject_nodes()
    claims = {'f1': [dict(meaning='Patient locator', use='subject', columns=['Patient.ID', 'Laboratory.ID'])]}
    compile_subject_lineage(nodes, claims)
    assert nodes[0]['columns'] == ['Patient.ID']
    assert 'use' not in nodes[0]['binding_claims'][0]
    assert nodes[1]['binding_claims'][0]['use'] == 'subject'
    with pytest.raises(ValueError, match='not connected'):
        validate_subject_edges(nodes, [], claims)
    validate_subject_edges(nodes, [['out', 'scope', 'requires']], claims)


@pytest.mark.parametrize('mode', ['absent', 'ambiguous'])
def test_subject_provider_must_still_exist_and_be_unique(mode):
    nodes = subject_nodes()
    if mode == 'absent':
        nodes.pop()
    else:
        duplicate = deepcopy(nodes[1])
        duplicate['id'] = 'other'
        nodes.append(duplicate)
    with pytest.raises(ValueError, match='no unique claim'):
        compile_subject_lineage(nodes, {'f1': [dict(meaning='Patient locator', use='subject', columns=['Patient.ID'])]})


def test_review_permissions_use_exact_table_and_column_not_schema_presence():
    payload = dict(allowed_columns=['gasstations.GasStationID'],
                   canonical_nodes=[dict(id='scope', columns=[
                       'gasstations.GasStationID', 'transactions_1k.GasStationID'])])
    original = deepcopy(payload)
    result = review_permission_facts(payload)
    assert result['permission_facts'] == [dict(id='scope',
        allowed_dependencies=['gasstations.GasStationID'],
        denied_dependencies=['transactions_1k.GasStationID'])]
    assert payload == original


def test_review_installed_hook_supplies_facts_and_removes_old_optional_filter_rule():
    observed = []
    def call(stage, prompt, payload):
        observed.append((prompt, payload))
        return {'accepted': True, 'issues': []}
    payload = dict(allowed_columns=[], canonical_nodes=[dict(id='p', columns=['t.c'])])
    with UsefulResidualMethod().installed():
        analysis.validated_call(call, 'deferred_commit_review',
                                'loss\nof an optional condition', payload, lambda raw: raw)
    assert 'optional condition' not in observed[0][0]
    assert observed[0][1]['permission_facts'][0]['denied_dependencies'] == ['t.c']


def shared_column_subjects():
    nodes = [dict(id='out', parent='f1', role='output', kind='attribute',
                  columns=['d.dob'], binding_claims=[dict(meaning='Returned DOB', columns=['d.dob'])])]
    for alias, meaning in [('range', 'Birth range'), ('sort', 'Descending DOB')]:
        nodes.append(dict(id=alias, parent='f1', role='modifier', kind='instruction',
                          columns=['d.dob'], binding_claims=[dict(meaning=meaning, columns=['d.dob'])]))
    claims = {'f1': [dict(meaning=m, use='subject', columns=['d.dob'])
                     for m in ('Birth range', 'Descending DOB')]}
    return nodes, claims


def test_distinct_business_obligations_can_share_a_physical_column():
    nodes, claims = shared_column_subjects()
    compile_subject_lineage(nodes, claims)
    assert 'use' not in nodes[0]['binding_claims'][0]
    assert all(n['binding_claims'][0]['use'] == 'subject' for n in nodes[1:])
    validate_subject_edges(nodes, [['out', 'range', 'requires'], ['out', 'sort', 'requires']], claims)
    # Indirect prerequisite paths are equally valid.
    validate_subject_edges(nodes, [['out', 'range', 'requires'], ['range', 'sort', 'requires']], claims)


@pytest.mark.parametrize('missing', ['range', 'sort'])
def test_each_obligation_requires_its_own_reachable_provider(missing):
    nodes, claims = shared_column_subjects()
    compile_subject_lineage(nodes, claims)
    edges = [['out', n, 'requires'] for n in ('range', 'sort') if n != missing]
    with pytest.raises(ValueError, match='not connected'):
        validate_subject_edges(nodes, edges, claims)


@pytest.mark.parametrize('damage', ['meaning', 'claim_columns', 'node_columns', 'duplicate'])
def test_subject_identity_and_dependencies_cannot_be_replaced_by_column_overlap(damage):
    nodes, claims = shared_column_subjects()
    if damage == 'meaning':
        nodes[2]['binding_claims'][0]['meaning'] = 'Different purpose'
    elif damage == 'claim_columns':
        nodes[2]['binding_claims'][0]['columns'] = []
    elif damage == 'node_columns':
        nodes[2]['columns'] = []
    else:
        duplicate = deepcopy(nodes[2])
        duplicate['id'] = 'duplicate'
        nodes.append(duplicate)
    with pytest.raises(ValueError):
        compile_subject_lineage(nodes, claims)
    # Validation fails atomically: no partial subject promotion.
    assert 'use' not in nodes[1]['binding_claims'][0]
