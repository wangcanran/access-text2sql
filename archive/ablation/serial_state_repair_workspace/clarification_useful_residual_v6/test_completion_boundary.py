"""Cross-entry publication and delivery-completion regressions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from clarification_deferred_deletion.baseline import module
from .completion import COMPLETE, validate_completion, completion_signature, readiness_issues
from .publication import exposed_identifiers, validate_public_wording
from .method import UsefulResidualMethod, has_removal_authorization


def test_deferred_removal_requires_explicit_authorization():
    # A blocked projection by itself is not a user decision and must not
    # authorize deleting its canonical leaves.
    assert not has_removal_authorization({'turns': [], 'permission_issues': {}})
    assert has_removal_authorization({
        'turns': [{'answer': {'action': 'unanswerable'}}],
        'permission_issues': {},
    })
    assert has_removal_authorization({'turns': [], 'permission_issues': {'p': {}}})


def test_current_permission_block_proof_authorizes_deferred_pruning_even_with_legacy_empty_evidence():
    assert has_removal_authorization({
        'turns': [], 'permission_issues': {},
        'deferred_state': {
            'permission_evidence': [{}],
            'batches': [{'turn': 0, 'block_proofs': {'p2': {'rule': 'empty_authorized_field_domain'}}}],
        },
    })


ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / 'artifacts/clarification_main/gpt52_v67_publication_regression7'


def historical(name='1475_2'):
    path = TRACES / (name + '_reduction.json')
    if not path.exists():
        pytest.skip('Historical trace not distributed')
    record = json.loads(path.read_text(encoding='utf8'))
    live = module('live')
    context = live.schema_context(json.loads(live.SCHEMAS.read_text(encoding='utf8'))[record['db_id']])
    return deepcopy(record['episode']['session']), context


def completed(session, extra=()):
    rows = []
    for iid, n in session['view']['items'].items():
        claims = deepcopy(session['binding_claims'][iid])
        for c in claims:
            c['state'] = 'bound'
            if 'date field/source' in c['meaning']:
                c['columns'] = ['yearmonth.Date']
        for c in n['required_columns']:
            if not any(c in claim['columns'] for claim in claims):
                claims.append(dict(meaning='Existing dependency', use='condition', state='bound', columns=[c]))
        claims.extend(deepcopy(extra))
        rows.append(dict(id=iid, requirement=n['requirement'], binding_claims=claims))
    return {'nodes': rows, 'unresolved': []}


@pytest.mark.parametrize('identifier', ['yearmonth.Consumption', '`yearmonth`.`Consumption`',
    '[yearmonth] . [Consumption]', 'YEARMONTH . CONSUMPTION', '`Consumption`', 'CustomerID'])
@pytest.mark.parametrize('location', ['question', 'focus', 'label'])
def test_public_boundary_covers_all_strings_and_identifier_spellings(identifier, location):
    _, context = historical()
    public = {'question': 'Which consumption measure?', 'focus': 'Which consumption measure?',
              'options': [{'id': 'A', 'label': 'Annual consumption'}], 'multi_select': False}
    if location == 'label':
        public['options'][0]['label'] += ' (' + identifier + ')'
    else:
        public[location] += ' (' + identifier + ')'
    assert exposed_identifiers(public, context)
    safe = {'question': 'Which consumption measure?', 'focus': 'Which consumption measure?',
            'options': [{'id': 'A', 'label': 'Annual consumption'}], 'multi_select': False}
    assert validate_public_wording({'public': safe}, public, context) == safe
    with pytest.raises(ValueError, match='identifiers'):
        validate_public_wording({'public': public}, public, context)


@pytest.mark.parametrize('change', ['ids', 'order', 'selection', 'duplicate_labels', 'extra_keys'])
def test_presentation_cannot_modify_the_authorized_choice_protocol(change):
    _, context = historical()
    original = {'question': 'Choose consumption', 'focus': 'Consumption', 'multi_select': True,
                'options': [{'id': 'A', 'label': 'Annual total'}, {'id': 'B', 'label': 'Monthly total'}]}
    raw = {'public': deepcopy(original)}
    p = raw['public']
    if change == 'ids': p['options'][0]['id'] = 'C'
    if change == 'order': p['options'].reverse()
    if change == 'selection': p['multi_select'] = False
    if change == 'duplicate_labels': p['options'][1]['label'] = p['options'][0]['label']
    if change == 'extra_keys': p['allowed_columns'] = ['yearmonth.Consumption']
    with pytest.raises(ValueError):
        validate_public_wording(raw, original, context)


def test_completion_covers_all_claim_uses_and_never_accepts_partial_inventory():
    session, context = historical()
    raw = completed(session)
    assert validate_completion(raw, session, context)['nodes']
    for use in ['returned', 'operand', 'condition', 'subject']:
        invalid = deepcopy(raw)
        invalid['nodes'][0]['binding_claims'].append(dict(
            meaning='Unresolved internal binding', use=use, state='open', columns=[]))
        with pytest.raises(ValueError, match='Every claim use'):
            validate_completion(invalid, session, context)
    for nodes in [[], raw['nodes'] * 2]:
        with pytest.raises(ValueError):
            validate_completion(dict(nodes=nodes, unresolved=[]), session, context)
    missing = deepcopy(raw)
    for c in missing['nodes'][0]['binding_claims']:
        c['columns'] = [x for x in c['columns'] if x != 'customers.Segment']
    with pytest.raises(ValueError, match='retire'):
        validate_completion(missing, session, context)


def test_real_open_date_is_completed_internally_without_new_user_question():
    session, context = historical()
    before = deepcopy(session)
    calls = []
    method = UsefulResidualMethod()
    def call(stage, prompt, payload):
        calls.append(stage)
        assert stage == 'binding_completion'
        assert payload['original_request'] == before['original_request']
        assert 'reference_request' not in payload and 'private_original' not in payload
        return completed(before)
    result = method._complete_delivery(call, session, context)
    assert calls == ['binding_completion']
    assert session == before
    assert not readiness_issues(result)
    assert all('yearmonth.Date' in n['required_columns'] for n in result['view']['items'].values())
    assert result['view']['progressive']['decision_ledger'] == before['view']['progressive']['decision_ledger']
    assert result['turns'] == before['turns']
    assert method._complete_delivery(call, result, context) == result  # no duplicate audit
    assert calls == ['binding_completion']
    assert result['completion_signature'] == completion_signature(result)


def test_new_denied_grounding_reaches_deletion_before_delivery(monkeypatch):
    session, context = historical()
    session['allowed'].remove('yearmonth.Date')
    method = UsefulResidualMethod()
    projected = []
    def decompose(call, current, schema):
        projected.append(deepcopy(current))
        assert any('yearmonth.Date' in n['denied_columns'] for n in current['view']['items'].values())
        current['contract_unresolved_reason'] = 'Stop at permission boundary for this test'
        return current
    monkeypatch.setattr(method, '_decompose', decompose)
    result = method._complete_delivery(lambda *a: completed(session), session, context)
    assert len(projected) == 1
    assert 'yearmonth.Date' not in result['allowed']
    assert method.render_session(None, result)['action'] == 'unresolved'


def test_output_display_audit_gets_original_intent_and_preserves_name_dependencies():
    session, context = historical('893_0')
    method = UsefulResidualMethod()
    def call(stage, prompt, payload):
        assert 'Name' in payload['original_request']
        assert 'human-readable names' in prompt
        raw = completed(session, [dict(meaning='Return driver names', use='returned', state='bound',
                                     columns=['drivers.forename', 'drivers.surname'])])
        for row in raw['nodes']:
            row['requirement'] += ' Return each driver\'s first name and surname.'
        return raw
    result = method._complete_delivery(call, session, context)
    for n in result['view']['items'].values():
        assert {'drivers.forename', 'drivers.surname'} <= set(n['required_columns'])
        assert 'surname' in n['requirement']


def test_unresolved_and_stale_completion_cannot_be_rendered():
    session, context = historical()
    method = UsefulResidualMethod()
    result = method._complete_delivery(lambda *a: {'nodes': [], 'unresolved': ['Ambiguous internal date source']},
                                       session, context)
    assert method.render_session(None, result)['action'] == 'unresolved'
    assert method.render_session(None, session)['reason'] == 'internal_completion_incomplete'
    result = method._complete_delivery(lambda *a: completed(session), session, context)
    next(iter(result['view']['items'].values()))['requirement'] += ' changed'
    assert method.render_session(None, result)['action'] == 'unresolved'


def test_pending_business_choices_do_not_trigger_internal_completion():
    session, context = historical()
    iid = next(iter(session['view']['items']))
    session['view']['progressive']['decision_ledger'] = [dict(decision_id='d100', owner_id=iid, dimension='field')]
    assert UsefulResidualMethod()._complete_delivery(lambda *a: pytest.fail('Business choice is still pending'),
                                                   session, context) == session
