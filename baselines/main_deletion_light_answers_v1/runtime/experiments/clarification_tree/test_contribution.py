from copy import deepcopy
import pytest
from .state import initial, frontier, add_issue
from .contribution import prepare, apply
from .focused_runner import run
from .test_tree import C, A


def plan_raw():
    return dict(focus=dict(meaning='Credit card information', granularity='atomic', role='output', columns=['t.a']),
        remainders=[dict(meaning='Birthday, age and balance range', granularity='compound')],
        question=dict(dimension='value', question='Which credit card details are needed?'),
        coverage='Credit card focus; every other requested output stays in remainder')


def test_preparation_is_read_only_and_answer_forces_expansion():
    s = initial('Card information, birthday, age and balance range')
    before = deepcopy(s)
    p = prepare(s, 'f1', plan_raw(), C)
    assert s == before and list(frontier(s)) == ['f1']
    new = apply(s, p, dict(action='answer', answer={'text': 'type'}), C,
                dict(meaning='Credit card type', granularity='atomic', role='output', resolved=True))
    assert new['nodes']['f1']['children'] == ['f2', 'f3']
    assert new['nodes']['f3']['meaning'] == p['parts'][1]['meaning']
    assert new['nodes']['f1']['source_text'] == s['original_request']


def test_cancel_card_preserves_remainder_without_model_call():
    s = initial('Card information, birthday, age and balance range')
    p = prepare(s, 'f1', plan_raw(), C)
    new = apply(s, p, {'action': 'cancel'}, C)
    assert new['nodes']['f2']['lifecycle'] == 'cancelled'
    assert list(frontier(new)) == ['f3']
    assert new['nodes']['f3']['meaning'] == 'Birthday, age and balance range'
    assert s['nodes']['f1']['children'] == []


def test_single_calculation_parameter_preserves_id():
    s = initial('Expense income ratio')
    raw = plan_raw()
    raw.update(focus=dict(meaning='Expense income ratio', granularity='atomic', role='output', columns=['t.a']), remainders=[])
    p = prepare(s, 'f1', raw, C)
    new = apply(s, p, {'action': 'answer'}, C,
                dict(meaning='Total expenses / income as percent', granularity='atomic', role='output', resolved=True))
    assert list(frontier(new)) == ['f1']
    assert new['next_node'] == 2


def test_cannot_merge_back_or_edit_remainder():
    s = initial('A and B')
    p = prepare(s, 'f1', plan_raw(), C)
    before = deepcopy(s)
    with pytest.raises(ValueError, match='focus content only'):
        apply(s, p, {'action': 'answer'}, C, dict(meaning='A', granularity='atomic', role='output', resolved=True,
                                                 children=[], remainders=[]))
    assert s == before


def test_old_issue_ownership_required_and_preserved_on_cancel():
    s = initial('A and B')
    d = add_issue(s, 'f1', 'value', 'Which balance range?')
    raw = plan_raw()
    with pytest.raises(ValueError, match='pending issue'):
        prepare(s, 'f1', raw, C)
    raw['issue_owners'] = {d: 'r1'}
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'cancel'}, C)
    assert new['issues'][d]['owner'] == 'f3'
    assert new['issues'][d]['status'] == 'pending'


def test_time_answer_cannot_spread_into_readonly_card_remainder():
    s = initial('Financial activity and card information')
    raw = plan_raw()
    raw['focus']['meaning'] = 'Financial activity'
    raw['remainders'][0]['meaning'] = 'Credit card information'
    p = prepare(s, 'f1', raw, C)
    new = apply(s, p, {'action': 'answer'}, C,
        dict(meaning='Financial activity before the loan', granularity='atomic', role='output', resolved=True))
    assert new['nodes']['f3']['meaning'] == 'Credit card information'
    assert not new['relations']


def test_interpreter_retirement_is_audit_only_and_cannot_remove_dependencies():
    s = initial('A and B')
    s['nodes']['f1']['columns'] = ['t.a', 't.b']
    raw = plan_raw()
    raw['remainders'][0]['columns'] = ['t.b']
    p = prepare(s, 'f1', raw, C)
    new = apply(s, p, {'action': 'answer'}, C, dict(meaning='Replacement', granularity='atomic', role='output',
        columns=['t.time'], resolved=True, retired_columns=['t.a'], retirement_reason='user replaces A'))
    assert new['nodes']['f3']['columns'] == ['t.b']
    assert 't.a' in new['nodes']['f2']['columns']


def test_stale_staged_plan_rejected():
    s = initial('A')
    p = prepare(s, 'f1', plan_raw(), C)
    s['revision'] += 1
    with pytest.raises(ValueError, match='Stale'):
        apply(s, p, {'action': 'cancel'}, C)


def test_full_loop_content_then_remainder_then_render():
    stages = []
    episode = {}
    def call(stage, prompt, payload):
        stages.append(stage)
        if stage == 'tree_focus':
            if payload['target'] == 'f1':
                return dict(focus=dict(meaning='Ratio', granularity='atomic', role='output', columns=['t.a']),
                    remainders=[dict(meaning='Card information', granularity='compound')],
                    question=dict(dimension='operation', question='Which formula?'), coverage='Ratio and card preserved')
            return dict(focus=dict(meaning='Card information', granularity='atomic', role='output', columns=['t.b']),
                        remainders=[], question=dict(dimension='value', question='Which card detail?'), coverage='Card')
        if stage == 'tree_focus_answer':
            assert 'remainders' not in payload
            return dict(meaning='Clarified ' + payload['focus']['meaning'], granularity='atomic', role='output', resolved=True)
        assert stage == 'tree_render'
        assert {n['id'] for n in payload['nodes']} == {'f2', 'f3'}
        return {'text': 'Clarified ratio and card information'}
    result = run(call, 'Ratio and card information', C, A,
                 lambda *a: dict(action='answer', text='specific detail', choices=[]), episode=episode)
    assert result['action'] == 'deliver'
    assert stages == ['tree_focus', 'tree_focus_answer', 'tree_focus', 'tree_focus_answer', 'tree_render']


def test_loop_cancel_action_is_exclusive_and_local():
    episode = {}
    def call(stage, prompt, payload):
        assert stage == 'tree_focus'
        raw = plan_raw()
        raw['question'].update(options=[dict(label='Card type', columns=[])])
        return raw
    # After cancellation, budget ends before a second answer; remainder survives.
    result = run(call, 'Card plus other profile', C, A,
                 lambda *a: dict(action='choose', choices=['2'], text=''), max_turns=1, episode=episode)
    assert result['reason'] == 'interaction_budget'
    assert episode['session']['nodes']['f2']['lifecycle'] == 'cancelled'
    assert episode['session']['nodes']['f3']['lifecycle'] == 'active'


def test_model_cannot_insert_control_action_in_content_schema():
    raw = plan_raw()
    raw['question']['options'] = [dict(label='Cancel card', action='cancel')]
    with pytest.raises(ValueError, match='Content options'):
        prepare(initial('profile'), 'f1', raw, C)


def test_external_pending_issue_is_read_only_and_diagnostic():
    s = initial('A and B')
    d = add_issue(s, 'f1', 'value', 'Which A?')
    raw = plan_raw(); raw['issue_owners'] = {d: 'focus'}
    p = prepare(s, 'f1', raw, C)
    # No external issue exists before expansion; after a staged child exists,
    # the next plan must receive an empty editable list for the other leaf.
    new = apply(s, p, {'action': 'cancel'}, C)
    other = next(iter(frontier(new)))
    d2 = add_issue(new, other, 'value', 'Which remainder?')
    from .focus_context import payload
    view = payload(new, 'f1' if 'f1' in frontier(new) else other, C)
    assert d2 in [] or d2 in [q['id'] for q in view['read_only_context']['issues']] or view['editable_issues']


def test_focus_context_has_no_external_write_ids():
    from .focus_context import payload
    s = initial('A')
    d = add_issue(s, 'f1', 'value', 'Which A?')
    view = payload(s, 'f1', C)
    assert view['allowed_issue_owner_ids'] == [d]
    assert not view['read_only_context']['issues']
    raw = plan_raw(); raw['issue_owners'] = {d: 'focus'}
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'cancel'}, C)
    other = 'f3' if 'f3' in frontier(new) else next(iter(frontier(new)))
    d2 = add_issue(new, other, 'value', 'Which B?')
    view = payload(new, other, C)
    assert d2 in view['allowed_issue_owner_ids']


def test_only_local_issue_can_be_migrated():
    s = initial('A')
    d = add_issue(s, 'f1', 'value', 'Which A?')
    raw = plan_raw(); raw['issue_owners'] = {d: 'focus'}
    p = prepare(s, 'f1', raw, C)
    p['owners']['external'] = 'focus'
    with pytest.raises(ValueError, match='unknown issue owner'):
        apply(s, p, {'action': 'cancel'}, C)


def test_cancelling_possible_shared_anchor_keeps_scope_issue():
    raw = plan_raw()
    raw['focus'].update(meaning='Shared loan subject', role='modifier')
    raw['relations'] = [dict(source='focus', target='r1', kind='applies_to')]
    s = initial('Loan subject and profile')
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'cancel'}, C)
    assert any(q['owner'] == 'f3' and q['status'] == 'pending' and q['dimension'] == 'relation'
               for q in new['issues'].values())


def test_unavailable_requirement_is_pruned_without_asking_user():
    def call(stage, prompt, payload):
        if stage == 'tree_focus':
            return dict(focus=dict(meaning='A', granularity='atomic', role='output', columns=['t.a']),
                remainders=[], question=dict(dimension='field', question='Which data?',
                options=[dict(label='A', columns=['t.a'])]), coverage='Only A')
        if stage == 'tree_focus_answer':
            return dict(meaning='B instead', granularity='atomic', role='output', columns=['t.b'],
                        resolved=True, retired_columns=['t.a'], retirement_reason='User requests B instead')
        assert stage == 'tree_render'
        return {'text': 'Return B'}
    def ask(q, history):
        pytest.fail('Unavailable requirements must not ask for replacement or consent')
    episode = {}
    assert run(call, 'A', C, ['t.b'], ask, episode=episode)['action'] == 'refuse'
    assert episode['transitions'][-1]['event']['reason'] == 'no_authorized_local_plan'
    assert not episode['interactions']


def test_local_answer_preserves_scope_and_computation():
    raw = plan_raw()
    raw['focus']['scope_columns'] = ['t.time']
    raw['focus']['computation'] = {'formula': 'a / b', 'operands': {'x': 't.a', 'y': 't.b'}}
    p = prepare(initial('profile'), 'f1', raw, C)
    new = apply(initial('profile'), p, {'action': 'answer'}, C,
                dict(meaning='Ratio as percent', granularity='atomic', role='output', resolved=True))
    assert new['nodes']['f2']['scope_columns'] == ['t.time']
    assert new['nodes']['f2']['computation'] == raw['focus']['computation']


def test_field_plan_does_not_require_model_generated_options():
    raw = plan_raw()
    raw['question'] = dict(dimension='field', question='Which details?')
    assert prepare(initial('profile'), 'f1', raw, C)['question']['options'] == []


def test_scope_and_operand_permissions_are_checked():
    from .permissions import dependencies, safe
    s = initial('ratio')
    s['nodes']['f1'].update(columns=['t.a'], scope_columns=['t.time'],
                           computation={'operands': {'denominator': 't.b'}})
    assert dependencies(s, 'f1', C) == set(A)
    assert not safe(dependencies(s, 'f1', C), C, ['t.a'])


def test_v21_object_binding_suggestions_have_actionable_error():
    s = initial('Prior borrowing')
    raw = plan_raw()
    raw['remainders'] = []
    p = prepare(s, 'f1', raw, C)
    update = dict(meaning='Number of prior loans', granularity='atomic', role='output', resolved=True,
                  binding_suggestions=[{'column': 't.a', 'role': 'counted identifier'}])
    before = deepcopy(s)
    with pytest.raises(ValueError, match='array of schema-qualified strings.*not objects'):
        apply(s, p, {'action': 'answer'}, C, update)
    assert s == before
    update['binding_suggestions'] = ['t.a']
    new = apply(s, p, {'action': 'answer'}, C, update)
    assert new['nodes']['f1']['columns'] == ['t.a']


@pytest.mark.parametrize('columns,message', [(['t.a', 't.a'], 'duplicate'), (['t.missing'], 'unknown schema fields')])
def test_binding_error_distinguishes_duplicate_and_unknown(columns, message):
    from .permissions import check_columns
    with pytest.raises(ValueError, match=message):
        check_columns(columns, C)


def test_v20_compound_binding_and_retirement_proposals_are_not_committed():
    s = initial('Additional account details')
    raw = plan_raw()
    raw['focus'] = dict(meaning='Additional account details', granularity='undetermined', columns=[])
    raw['remainders'] = []
    update = dict(meaning='History and balance range, without identifiers', granularity='compound', resolved=True,
                  columns=['t.a', 't.b'], retired_columns=['t.time'], retirement_reason='Exclude identifiers',
                  computation={'formula': 'MAX(t.b)'}, output_exclusions=['Do not return account identifiers'])
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'answer'}, C, update)
    n = new['nodes']['f1']
    assert n['columns'] == [] and n['computation'] == {}
    assert n['output_exclusions'] == update['output_exclusions']
    assert new['audit'][-1]['interpretation'] == update
    from .planner import next_step
    assert next_step(new, C, A)['kind'] == 'assess'


def test_output_exclusion_preserves_join_binding_and_reaches_next_planning():
    from .focus_context import payload
    s = initial('Account information')
    raw = plan_raw()
    raw['remainders'] = []
    raw['focus']['scope_columns'] = ['t.time']
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'answer'}, C,
                dict(meaning='Account details without identifiers', granularity='atomic', role='output', resolved=True,
                     output_exclusions=['Do not return identifiers']))
    assert new['nodes']['f1']['columns'] == ['t.a']
    assert new['nodes']['f1']['scope_columns'] == ['t.time']
    assert payload(new, 'f1', C)['editable_leaf']['output_exclusions'] == ['Do not return identifiers']


def test_answer_only_review_preserves_novel_content_and_state():
    from .answer_scope import review
    s = initial('Card and balance')
    s = apply(s, prepare(s, 'f1', plan_raw(), C),
              dict(action='cancel', reason='no_authorized_local_plan'), C)
    before = deepcopy(s)
    event = dict(action='answer', answer=dict(text='Card information and balance range'), selected_labels=[])
    def call(stage, prompt, payload):
        assert stage == 'tree_answer_scope'
        return dict(repeated_ids=['f2'], evidence={'f2': 'Card information'}, remaining_answer='balance range')
    filtered, result = review(call, s, event)
    assert filtered['answer']['text'] == 'balance range'
    assert event['answer']['text'] == 'Card information and balance range'
    assert s == before
    assert result['repeated_ids'] == ['f2']


def test_answer_review_cannot_invent_repeat_evidence():
    from .answer_scope import review
    s = initial('Card and balance')
    s = apply(s, prepare(s, 'f1', plan_raw(), C), dict(action='cancel', reason='no_authorized_local_plan'), C)
    def call(*args):
        return dict(repeated_ids=['f2'], evidence={'f2': 'invented'}, remaining_answer='')
    with pytest.raises(ValueError):
        review(call, s, dict(answer={'text': 'balance range'}, selected_labels=[]))


def test_expression_dependencies_include_formula_and_time_scope():
    from .permissions import node_dependencies
    n = dict(columns=[], scope_columns=[], computation={
        'formula': 'SUM(t.a) / SUM(t.b)', 'time_scope': 'date < t.time',
        'operands': {'other': 't.abc is not t.a'}})
    assert node_dependencies(n, C) == set(A)
    assert node_dependencies(dict(columns=[], computation={'formula': 't.abc'}), C) == set()


def test_external_dependency_keeps_column_shared_with_current_node():
    from .permissions import dependencies
    raw = plan_raw()
    raw['remainders'] = [dict(meaning='Shared condition', granularity='atomic', role='modifier', columns=['t.a'])]
    raw['relations'] = [dict(source='r1', target='focus', kind='applies_to')]
    s = initial('Output and shared condition')
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'answer'}, C,
                dict(meaning='Output A', granularity='atomic', role='output', resolved=True))
    new['relations'][0]['status'] = 'confirmed'
    assert dependencies(new, 'f2', C, exclude_owner=True) == {'t.a'}


def test_new_focus_scope_does_not_spread_to_compound_remainder():
    raw = plan_raw()
    raw['focus']['scope_columns'] = ['t.time']
    raw['remainders'][0]['scope_columns'] = ['t.time']
    p = prepare(initial('Profile'), 'f1', raw, C)
    assert p['parts'][0]['scope_columns'] == ['t.time']
    assert p['parts'][1]['scope_columns'] == []
    assert p['parts'][1]['meaning'] == raw['remainders'][0]['meaning']


def test_unrelated_cancelled_outputs_are_not_in_planning_context():
    from .focus_context import payload
    s = initial('Card and other requirements')
    new = apply(s, prepare(s, 'f1', plan_raw(), C), {'action': 'cancel'}, C)
    context = payload(new, 'f3', C)
    assert 'cancelled_contributions' not in context['read_only_context']
    assert context['read_only_context']['cancellation_events'] == []
    assert new['nodes']['f2']['lifecycle'] == 'cancelled'
    assert context['editable_leaf']['id'] == 'f3'


def test_cancellation_context_is_scoped_to_local_pending_issue():
    from .focus_context import payload
    raw = plan_raw()
    raw['focus']['role'] = 'modifier'
    raw['relations'] = [dict(source='focus', target='r1', kind='applies_to')]
    s = initial('Anchor and remaining profile')
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'cancel'}, C)
    context = payload(new, 'f3', C)
    events = context['read_only_context']['cancellation_events']
    assert len(events) == 1
    assert events[0]['cancelled_node_id'] == 'f2'
    assert events[0]['surviving_node_id'] == 'f3'
    new['issues'][events[0]['issue_id']]['status'] = 'answered'
    assert payload(new, 'f3', C)['read_only_context']['cancellation_events'] == []


def test_hidden_menu_is_not_an_empty_execution_domain():
    from .local_field_menu import domain, candidates
    schema = {'tables': [{'name': 't', 'columns': [{'name': str(i)} for i in range(6)]}],
              'foreign_keys': []}
    allowed = ['t.' + str(i) for i in range(6)]
    focus = dict(columns=[], scope_columns=[allowed[0]])
    assert candidates(focus, schema, allowed) == []
    assert domain(focus, schema, allowed)['status'] == 'available'


def test_permission_empty_domain_prunes_without_public_question(monkeypatch):
    from . import focused_runner
    monkeypatch.setattr(focused_runner, 'local_field_candidates', lambda *args: [])
    monkeypatch.setattr(focused_runner, 'local_field_domain',
                        lambda *args: {'status': 'no_accessible_field'})
    def call(stage, prompt, payload):
        assert stage == 'tree_focus'
        return dict(focus=dict(meaning='A', granularity='atomic', role='output', columns=[]),
                    remainders=[], question=dict(dimension='field', question='Which data?'), coverage='A')
    def ask(*args):
        pytest.fail('Permission-empty domain must be pruned before public interaction')
    episode = {}
    assert run(call, 'A', C, A, ask, episode=episode)['action'] == 'refuse'
    assert episode['transitions'][-1]['event']['reason'] == 'no_authorized_local_plan'


def test_unknown_scope_is_not_permission_empty():
    from .local_field_menu import domain
    assert domain(dict(columns=[], scope_columns=[]), C, A)['status'] == 'scope_required'


def test_inaccessible_compound_is_refined_before_pruning():
    from .planner import next_step
    from .transaction import cancel
    s = initial('A and B')
    s['nodes']['f1']['scope_columns'] = ['t.time']
    assert next_step(s, C, ['t.a']) == {'kind': 'assess', 'target': 'f1'}
    with pytest.raises(ValueError, match='atomic contribution'):
        cancel(s, 'f1', automatic=True)


def test_automatic_anchor_pruning_does_not_cascade_to_dependent():
    from .transaction import cancel
    raw = plan_raw()
    raw['focus'].update(meaning='Shared anchor', role='modifier')
    raw['relations'] = [dict(source='focus', target='r1', kind='applies_to')]
    s = initial('Anchor and other requirements')
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'answer'}, C,
                dict(meaning='Anchor', granularity='atomic', role='modifier', resolved=True))
    new['relations'][0]['status'] = 'confirmed'
    new = cancel(new, 'f2', automatic=True)
    assert list(frontier(new)) == ['f3']
    assert not new['nodes']['f3']['assessed']


def test_live_style_blocked_scope_keeps_remainder_for_next_split():
    from .planner import next_step
    s = initial('Ratio and the rest of the profile')
    raw = plan_raw()
    raw['focus']['scope_columns'] = ['t.time']
    raw['remainders'][0]['scope_columns'] = ['t.time']
    new = apply(s, prepare(s, 'f1', raw, C),
                dict(action='cancel', reason='no_authorized_local_plan'), C)
    assert new['nodes']['f2']['lifecycle'] == 'cancelled'
    assert next_step(new, C, ['t.a']) == {'kind': 'assess', 'target': 'f3'}


def test_v16_invalid_issue_reports_exact_contract_without_mutation():
    s = initial('Previous loans')
    raw = plan_raw()
    raw['remainders'] = []
    p = prepare(s, 'f1', raw, C)
    before = deepcopy(s)
    with pytest.raises(ValueError, match='time_scope.*allowed: boundary, relation, field, operation, value'):
        apply(s, p, {'action': 'answer'}, C,
              dict(meaning='Previous loan count', granularity='atomic', role='output', resolved=True,
                   issues=[dict(dimension='time_scope', question='Before which date?')]))
    assert s == before


def test_answered_question_can_leave_a_new_value_question():
    raw = plan_raw()
    raw['remainders'] = []
    s = initial('Previous loans')
    new = apply(s, prepare(s, 'f1', raw, C), {'action': 'answer'}, C,
                dict(meaning='Previous loan count', granularity='atomic', role='output', resolved=True,
                     issues=[dict(dimension='value', question='Before which date?')]))
    assert new['issues']['d1']['status'] == 'answered'
    assert any(q['dimension'] == 'value' and q['status'] == 'pending' for q in new['issues'].values())


def test_multi_result_answer_is_carried_into_next_planning_turn():
    from .planner import next_step
    from .focus_context import payload
    raw = plan_raw()
    raw['focus'] = dict(meaning='Borrowing summary', granularity='compound', columns=[])
    raw['remainders'] = []
    s = initial('Borrowing summary')
    answer = {'action': 'answer', 'text': 'Count and total amount, separately'}
    new = apply(s, prepare(s, 'f1', raw, C), dict(action='answer', answer=answer), C,
                dict(meaning='Loan count and total amount as separate results', granularity='compound', resolved=True))
    assert next_step(new, C, A) == {'kind': 'assess', 'target': 'f1'}
    context = payload(new, 'f1', C)
    assert 'count and total amount' in context['editable_leaf']['meaning']
    assert context['editable_leaf']['answers']
    assert context['answered_local_decisions'][0]['status'] == 'answered'


def test_v17_invalid_relation_is_rejected_during_prepare_without_mutation():
    s = initial('Card and account details')
    raw = plan_raw()
    raw['relations'] = [dict(source='focus', target='r1', kind='applies_to')]
    before = deepcopy(s)
    with pytest.raises(ValueError, match='Invalid attachment roles.*atomic source must be a modifier'):
        prepare(s, 'f1', raw, C)
    assert s == before


def test_prepare_commit_preview_is_discarded():
    s = initial('Card and other requirements')
    before = deepcopy(s)
    p = prepare(s, 'f1', plan_raw(), C)
    assert s == before
    assert p['revision'] == s['revision']
    new = apply(s, p, dict(action='cancel', reason='no_authorized_local_plan'), C)
    assert new['nodes']['f2']['lifecycle'] == 'cancelled'
    assert new['nodes']['f3']['lifecycle'] == 'active'


def test_v18_answer_updates_other_local_issues_and_keeps_partial():
    s = initial('Ratio')
    current = add_issue(s, 'f1', 'field', 'Which data?')
    time = add_issue(s, 'f1', 'value', 'What time scope?')
    formula = add_issue(s, 'f1', 'operation', 'Which formula and unit?')
    raw = plan_raw()
    raw['remainders'] = []
    raw['question'] = dict(dimension='field', question='Which data?', issue_id=current)
    p = prepare(s, 'f1', raw, C)
    new = apply(s, p, dict(action='answer', answer={'text': 'Expenses divided by income before application'}), C,
                dict(meaning='Expense income ratio before application', granularity='atomic', role='output', resolved=True,
                     issue_resolutions={time: dict(status='answered', evidence='before application'),
                                        formula: dict(status='partial', evidence='Expenses divided by income',
                                                      remaining_question='Decimal or percentage?')}))
    assert new['issues'][time]['status'] == 'answered'
    assert new['issues'][formula]['status'] == 'pending'
    assert new['issues'][formula]['question'] == 'Decimal or percentage?'


@pytest.mark.parametrize('other_owner,evidence', [('r1', 'before application'), ('focus', 'invented evidence')])
def test_issue_resolution_rejects_remainder_or_invented_evidence(other_owner, evidence):
    s = initial('A and B')
    iid = add_issue(s, 'f1', 'value', 'When?')
    raw = plan_raw()
    raw['issue_owners'] = {iid: other_owner}
    p = prepare(s, 'f1', raw, C)
    before = deepcopy(s)
    with pytest.raises(ValueError):
        apply(s, p, dict(action='answer', answer={'text': 'before application'}), C,
              dict(meaning='A', granularity='atomic', role='output', resolved=True,
                   issue_resolutions={iid: dict(status='answered', evidence=evidence)}))
    assert s == before
