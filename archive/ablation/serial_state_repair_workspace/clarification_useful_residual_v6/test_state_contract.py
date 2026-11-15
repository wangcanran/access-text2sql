from copy import deepcopy

from .state_contract import validate_projection, audit_transition


def base():
    return {'view': {'items': {'f1': {'missing': ['field']}},
                         'progressive': {'decision_ledger': [
                             {'decision_id': 'd1', 'owner_id': 'f1', 'dimension': 'field'}]},
                         'relations': {'edges': []}}}


def test_projection_accepts_authoritative_ledger_and_missing_summary():
    assert validate_projection(base()) == []


def test_projection_rejects_stale_missing_summary():
    state = base()
    state['view']['items']['f1']['missing'] = ['value']
    errors = validate_projection(state)
    assert any('no decision ledger row' in error for error in errors)


def test_projection_allows_ledger_rebuild_before_cached_missing_summary():
    state = base()
    state['view']['progressive']['decision_ledger'].append(
        {'decision_id': 'd2', 'owner_id': 'f1', 'dimension': 'value'})
    assert validate_projection(state) == []


def test_projection_rejects_active_blocked_overlap():
    state = base()
    state['deferred_state'] = {'blocked_nodes': {'f1': {}}}
    assert any('overlap' in error for error in validate_projection(state))


def test_transition_audit_uses_projection_checks_without_repeating_router_identity():
    before = base()
    after = deepcopy(before)
    after['view']['items']['f1']['missing'] = []
    after['view']['progressive']['decision_ledger'] = []
    assert audit_transition(before, after, {'kind': 'answer', 'decision_ids': ['d1']}) == []

