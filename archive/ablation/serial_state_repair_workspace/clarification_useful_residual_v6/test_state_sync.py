from copy import deepcopy
import pytest

from .state_sync import reconcile_state
from .test_obligations import session, claim


def test_reconcile_mirrors_ledger_and_rebuilds_current_request():
    s = session([claim('Output', 'returned', 'bound', ['t.name'])])
    s['view']['progressive']['decision_ledger'] = []
    s['view']['segmentation'] = {'inventory': {}}
    s['view']['items']['f1'].update(role='output', requirement='Return names')
    out = reconcile_state(s)
    assert out['view']['segmentation']['inventory']['progressive'] == out['view']['progressive']
    assert out['current_request']
    assert out['state_sync_audit'][-1]['errors'] == []


def test_reconcile_retains_historical_claim_owner():
    s = session([claim('Output', 'returned', 'bound', ['t.name'])])
    s['binding_claims']['stale'] = [claim('Old', 'condition')]
    out = reconcile_state(s)
    assert out['binding_claims']['stale']


def test_reconcile_does_not_modify_semantic_claims():
    s = session([claim('Output', 'returned', 'bound', ['t.name'])])
    before = deepcopy(s['binding_claims'])
    out = reconcile_state(s)
    assert out['binding_claims'] == before


def test_empty_active_graph_clears_only_current_projection():
    s = session([claim('Output', 'returned', 'bound', ['t.name'])])
    s['view']['items'] = {}
    s['view']['relations'] = {'edges': []}
    s['binding_claims'] = {}
    s['obligation_pool'] = None
    s['current_request'] = 'stale active query'
    s['original_request'] = 'Return names'
    out = reconcile_state(s)
    assert out['current_request'] == ''
    assert out['original_request'] == 'Return names'
