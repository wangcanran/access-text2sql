"""Opt-in persistent obligations experiment; no terminal global LLM audit.

The pending question ledger remains authoritative. Contribution IDs record
lineage, not semantic equivalence: the model still proposes the refinements.
"""
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from unittest.mock import patch

from .method import UsefulResidualMethod, has_removal_authorization
from . import obligations as pool
from .pool_transitions import (answer_review_context, compile_inherited_claims,
    compile_inherited_dependencies, compile_model_references)
from clarification_contract_v5.method import ContractMethod
import clarification_contract_v5.method as contracts
import clarification_deferred_v3_2.method as typed_method
from clarification_contract_v5.bindings import validate_claims
from clarification_deferred_deletion.baseline import analysis, light, main, updates
from clarification_deferred_deletion.method import reframe, sync_canonical
from .state_sync import reconcile_state
from .state_contract import audit_transition
from .output_contracts import compile_output, OutputContractError

VERSION = 'useful_residual_v6_pool_v10_2_prompt_chain'
_PATCH_LOCK = RLock()


def disambiguate_wire_prompt(stage, prompt):
    """Make fragment ownership and requester wire output unambiguous."""
    def replace_required(old, new):
        nonlocal prompt
        if prompt.count(old) != 1:
            raise ValueError(f'{stage} inherited prompt changed: expected one occurrence of {old[:80]!r}')
        prompt = prompt.replace(old, new, 1)

    if stage == 'progressive_coarse':
        old_example = '''Return only JSON: {"original_text":"exact current_request",
"fragments":[{"item_id":"f1","span":"exact contiguous quote"}]}.
Use unique f1,f2,... IDs.'''
        new_example = '''Return only JSON: {"original_text":"Show depot information",
"fragments":[{"item_id":"f1","span":"depot information",
"open_choices":[{"dimension":"field","span":"depot information",
"question":"Which depot information should be returned?",
"answer_kind":"attribute"}]}]}.
Copy original_text and spans exactly from the actual current_request. Use unique
f1,f2,... IDs. Each fragment MUST have open_choices, [] when none; each choice
MUST include dimension, local quote span, business question and answer_kind.'''
        replace_required(old_example, new_example)
        prompt += '''
COMPARISON DIRECTION: Preserve a generic requested difference at the user's
specificity. If a filter, ranking or sign-sensitive result cannot be understood
without subtraction order or absolute-versus-signed meaning, and the public
request does not specify it, retain ONE operation open_choice. Do not infer
either interpretation from private SQL or ask about SQL-only implementation.
'''
    elif stage == 'progressive_analysis':
        replace_required(
            "Each row owns ONLY its fragment's contribution and unanswered decisions.",
            "Each row owns ALL contributions inside its fragment and their unanswered decisions.")
        replace_required(
            'Unmapped contributions use false and []; alternative choices are not dependencies.',
            'Use grounded=false and required_columns=[] only if no part of this row '
            'maps to the schema. Known operands remain required even when a '
            'different returned field is still missing. Alternative field '
            'choices are not dependencies.')
        replace_required(
            '  its operands and necessary join keys. Other fragments own their filter columns.',
            '  its operands, attached conditions and necessary join keys. Each other '
            'fragment owns its own dependencies.')
        replace_required(
            '  attributes are valid when their meaning fits this question. For calculations,\n'
            '  offer the relevant participating measures as field choices, even when several\n'
            '  are needed together. Keep the formula, operand roles and condition-combination\n'
            '  logic missing under operation unless the request or answers already specify them.',
            '  attributes are valid when their meaning fits this question. For a known\n'
            '  calculation with independently unspecified input attributes, offer only\n'
            '  relevant operand fields as field choices. If the requested statistic,\n'
            '  formula or comparison itself is unspecified, ask ONE operation question\n'
            '  about that complete business measure and its inputs; do not add a field\n'
            '  question for an arbitrary numeric attribute. Keep genuinely missing\n'
            '  formula roles and condition-combination logic under operation.')
        typed_source = 'Each open_choices entry additionally has answer_kind, exactly one of:'
        typed_target = ('For each question_focus entry, put its answer_kind in '
                        'answer_kinds; use exactly one of:')
        if typed_source in prompt:
            # Inherited adapters may append the same typed contract twice.
            # Rewrite every copy instead of leaving one contradictory instruction.
            prompt = prompt.replace(typed_source, typed_target)
        elif typed_target not in prompt:
            # Some inherited call paths append ANSWER_TYPES after this adapter.
            # The final wire must still describe the analysis output field.
            prompt += '\n' + typed_target + '\nentity_value, literal_value, attribute, operation, entity_kind, relationship.\n'
        old_example = '''Return JSON only: {"items":[{"item_id":"o1","kind":"attribute",
"requirement":"Return selected depot information","grounded":true,
"scope_tables":["depots"],"required_columns":[],"missing":["field"],
"question_focus":{"field":"Which depot information should be returned?"},
"field_candidates":[{"column":"depots.code","label":"Depot code"},
{"column":"depots.name","label":"Depot name"}],"reason":"Return attributes are unselected."}]}.
The example row illustrates the contract; actual responses must cover ALL supplied
IDs, including clear rows.'''
        new_example = '''Return JSON only: {"items":[{"item_id":"f1","role":"output",
"kind":"attribute","requirement":"Return depot information",
"grounded":false,"scope_tables":["depots"],"required_columns":[],
"missing":["field"],"question_focus":{"field":"Which depot information should be returned?"},
"answer_kinds":{"field":"attribute"},
"field_candidates":[{"column":"depots.code","label":"Depot code"},
{"column":"depots.name","label":"Depot name"}],
"binding_claims":[{"meaning":"Requested depot information","use":"returned",
"state":"open","columns":[]}],"reason":"Return attributes are unselected."}],
"edges":[]}.
This example represents ONE editable source fragment with an unselected return
field. For a different input, use its supplied IDs and actual schema columns.'''
        replace_required(old_example, new_example)
        prompt += '''
COMPOSITE FRAGMENT WIRE CONTRACT (overrides generic one-contribution wording):
Return exactly ONE items row for EACH supplied editable fragment item_id.
Never repeat an item_id and never create a new item_id at analysis time.
A row represents its whole source fragment, not a single business claim.
When a fragment requests returned information under a condition, keep both
in ONE role=output row. Express its distinct returned, operand, subject, and
condition contributions as separate binding_claims inside that row.
Its requirement describes the whole fragment. required_columns contains all
known schema dependencies from those claims, including known condition columns.
Keep an unselected returned field open in missing/question_focus; candidate
alternatives are not required_columns. grounded=true if at least one known
schema dependency maps, even while another choice remains missing.
An unrelated fragment owns only its own wording, never this row's condition.
For example, given f1="Return product details where price exceeds 30"
and f2="Also return the category", output exactly one f1 row and one f2 row.
In f1, keep the returned product detail claim open, bind the threshold and its
price operand, and include the actual price column in required_columns. In f2,
represent only the category output. Use actual schema columns from the input;
the example column names are not binding instructions.
Use later refinement to create separate graph nodes when independent treatment
becomes necessary. Analysis itself cannot split or invent IDs.
If an unspecified subtraction order or absolute-versus-signed interpretation
changes a stated filter, ranking or sign-sensitive result, retain one operation
question. A generic returned difference can remain at the user's specificity;
do not choose a formula that the request or answer did not establish.
'''
    elif stage == 'hierarchical_field_filter':
        old = 'Permission filtering is performed by the runner after this selection.'
        new = ('For a reopened permission field choice, candidate_domain may already '
               'be permission-filtered. In every case code checks authorization before '
               'display. Treat candidate_domain as the only possible field IDs; do not '
               'infer access from schema descriptions.')
        if old in prompt:
            replace_required(old, new)
        elif new not in prompt:
            raise ValueError('hierarchical_field_filter inherited prompt changed')
    elif stage == 'hierarchical_menu_groups':
        # The inherited prompt described field granularity as one option per
        # field, while the compiler requires a strict 2..max_options partition
        # whenever the domain exceeds max_options.  State the wire/partition
        # contract at the final callback so those instructions cannot diverge.
        prompt += '''
GROUPING WIRE CONTRACT (overrides generic option wording):
Return exactly {"options":[{"label":"business category",
"choice_ids":["supplied_choice_id"]}]} and no other keys. The payload supplies
max_options. When the supplied domain is larger than max_options, return between
2 and max_options groups, partitioning every supplied choice_id exactly once;
do not return one option per field and do not omit, duplicate or invent IDs.
Every group must contain fewer choice_ids than the full domain. When the domain
already fits max_options, this grouping stage is not called. Labels describe
shared business meaning and must remain distinct; they do not select fields or
change permissions. The code, not the model, assigns public option IDs.
'''
    elif stage == 'session_user_gold':
        replace_required(
            'If none matches the reference intention use unanswerable.',
            'When the menu offers an explicit None-of-these option and no '
            'substantive option matches the reference intention, choose that '
            'option if the intended answer is supported by a supplied flat unit. '
            'Use unanswerable when the menu has no such option or the intended '
            'answer cannot be established from the supplied private evidence.')
        prompt += '''
FINAL RESPONSE CHECK: Always emit exactly action, module_ids, answer,
selected_ids, and basis. A menu selection of "None of these meets my requirement"
still needs module_ids citing the supplied flat unit(s) that support the
original intention. Example when E is that offered option and o2 is the
supporting unit: {"action":"choose","module_ids":["o2"],"answer":"",
"selected_ids":["E"],"basis":"natural_language"}. Replace both IDs and basis
with the actual evidence; E is not automatically the correct choice.
If no supplied unit supports the current question, use action=unanswerable,
module_ids=[], answer="", selected_ids=[], basis="none". Never omit a key.
'''
        if 'This menu allows an optional note in answer alongside selected_ids.' in prompt:
            replace_required(
                'For choose, answer="" and selected_ids must be nonempty (unless\n'
                'the optional menu-note rule below explicitly permits a note). No additional keys.',
                'For choose, selected_ids must be nonempty; answer may be a short\n'
                'note only when allow_note=true in the supplied question. If\n'
                'allow_note is absent or false, answer="". No additional keys.')
        else:
            replace_required(
                'For choose, answer="" and selected_ids must be nonempty (unless\n'
                'the optional menu-note rule below explicitly permits a note). No additional keys.',
                'For choose, answer="" and selected_ids must be nonempty.\n'
                'No additional keys.')
    elif stage == 'semantic_transaction':
        replace_required(
            'Edges use child aliases or existing boundary IDs; applies_to means source\n'
            'restricts target, context means source relies on target. Preserve attachments\n'
            'to untouched nodes. Boundary nodes are read-only.',
            'Edges are a local delta: declare only NEW relationships involving a\n'
            'replacement child alias. Code preserves and remaps existing attachments\n'
            'to untouched boundary nodes. Boundary nodes are read-only.')
        replace_required(
            '"columns":["table.column"],"pending":["d2"]}]}',
            '"columns":["table.column"],"pending":["d2"],'
            '"binding_claims":[{"meaning":"current requirement",'
            '"use":"returned","state":"bound",'
            '"columns":["table.column"],"obligation_ids":["o1"]}]}]}')
        replace_required(
            '"question_changes":{"d2":{"to":["p1"]}}}.',
            '"retire_columns":{},"question_changes":{"d2":{"to":["p1"]}},'
            '"settled_decisions":[]}.')
        replace_required(
            '[{"meaning":"actual returned information", "use":"returned",\n'
            '"state":"bound", "columns":["table.column"]}].',
            '[{"meaning":"actual returned information", "use":"returned",\n'
            '"state":"bound", "columns":["table.column"], '
            '"obligation_ids":["o1"]}].')
        replace_required(
            'Only updates is required. Keys must be editable parent IDs; omit unchanged parents.',
            'Return the complete transaction object with updates, edges, '
            'retire_columns, question_changes and settled_decisions. Use {}, [], '
            '{}, {}, [] respectively when no change of that kind is needed. '
            'Keys in updates must be editable parent IDs; omit unchanged parents.')
        replace_required(
            'Each child requires id, text, columns. Optional role, kind, tables inherit the',
            'Each child requires id, text, columns and binding_claims, including '
            'obligation_ids for every claim. Optional role, kind, tables inherit the')
        replace_required(
            'For still-pending field/operation decisions use open with no columns. When those\n'
            'decisions are answered, bind their claims. Do not invent further questions or',
            'For still-pending field/operation decisions use open with no columns. '
            'When answered, settle the asked decision and bind only facts whose '
            'schema operands are actually known; preserve an ungrounded business '
            'meaning as an open claim for internal grounding. Do not invent further questions or')
        prompt += '''
Preserve comparison direction as actually stated in the public request or
accepted answer. A generic difference does not establish an absolute value or
the order of subtraction; do not invent either while replacing a node.
'''
    return prompt


def reclassify_asked_decisions(view, question, decision):
    """Changing question type must target the asked IDs, not the new type."""
    ids = question.get('decision_ids', [])
    owner = question['item']['item_id']
    rows = view['progressive']['decision_ledger']
    if (not isinstance(ids, list) or not ids or len(set(ids)) != len(ids)
            or any(not isinstance(i, str) for i in ids)):
        raise ValueError('Reclassification requires current asked identities')
    selected = [d for d in rows if d['decision_id'] in ids]
    if len(selected) != len(ids) or any(d['owner_id'] != owner for d in selected):
        raise ValueError('Reclassification cannot target stale or cross-owner identities')
    return selected


class PoolMethod(UsefulResidualMethod):
    VERSION = VERSION
    # Experiments may accept a semantically incomplete answer for downstream
    # Text2SQL evaluation. Structural, ownership, permission and deletion
    # checks remain mandatory; this flag only disables the extra model review.
    skip_answer_contribution_review = False
    publish_unresolved_to_text2sql = False
    def _public_text(self, call, public, context):
        from .publication import (PUBLIC_WORDING, identifier_patterns,
                                  public_strings, validate_public_wording)
        if public is None:
            return None
        patterns = identifier_patterns(context)
        def hits(value):
            return sorted({m.group().strip() for s in public_strings(value)
                           for pattern in patterns for m in pattern.finditer(s)})
        forbidden = hits(public)
        if not forbidden:
            return public
        def check(raw):
            if isinstance(raw, dict) and isinstance(raw.get('public'), dict):
                remaining = hits(raw['public'])
                if remaining:
                    raise ValueError('Rewrite these exact physical schema spellings in ALL wording, '
                                     'including quoted requirement context: ' + repr(remaining))
            return validate_public_wording(raw, public, context)
        return analysis.validated_call(call, 'public_wording', PUBLIC_WORDING + '''
forbidden_schema_spellings is computed by code from the supplied wording.
Replace EVERY listed physical spelling, including those inside quoted context,
with its business meaning. Preserve names/values and option distinctions; do not
simply copy the quoted requirement. For example atom_id becomes atom identifier,
and GasStationID becomes gas station identifier. Keep all structural metadata.
''', dict(public=deepcopy(public), forbidden_schema_spellings=forbidden), check)

    @contextmanager
    def installed(self):
        # The frozen runtime exposes module functions rather than instance
        # hooks. Serialize this adapter scope so concurrent cases cannot observe
        # another method instance's temporary replacements.
        with _PATCH_LOCK:
            with super().installed():
                original = analysis.validated_call
                original_finish = updates.finish_view
                original_promote = contracts.promote_claims

                def promote(row, role, *args, **kwargs):
                    source = getattr(self, '_claim_source', None)
                    parent = row.get('parent', getattr(self, '_claim_parents', {}).get(row.get('id')))
                    if source is not None and parent is not None:
                        compile_inherited_claims(row, parent, source)
                    pool.validate_role_claims(row, role)
                    return original_promote(row, role, *args, **kwargs)

                def finish(session, context):
                    result = original_finish(session, context)
                    # split_graph has provisional duplicate question IDs; the
                    # parking compiler makes them unique before publication.
                    if getattr(self, '_decomposing_pool', False):
                        return result
                    if (getattr(self, '_initializing_contracts', False)
                            and not result.get('turns') and result.get('binding_claims') is not None):
                        result = pool.initialize(result)
                        result = reconcile_state(result, event='initial_publish')
                        self._initial_claims = deepcopy(result['binding_claims'])
                    return result

                def validated(call, stage, prompt, payload, validator):
                    # Append at the actual call boundary: inherited adapters replace
                    # the plan prompt and otherwise could erase this contract.
                    def invoke(actual_stage, actual_prompt, actual_payload):
                        wire_stage = stage.removesuffix('_contract_retry')
                        if wire_stage == 'progressive_analysis':
                            actual_prompt += pool.INITIAL_CONTRACT
                        elif wire_stage in ('contract_permission_decomposition', 'contract_none_decomposition'):
                            from .pool_prompts import DECOMPOSE
                            actual_prompt = DECOMPOSE
                        if actual_prompt and wire_stage != 'semantic_transaction':
                            actual_prompt = disambiguate_wire_prompt(wire_stage, actual_prompt)
                        return call(actual_stage, actual_prompt, actual_payload)
                    if stage == 'progressive_analysis':
                        # A model may repeat a known operand column on an open
                        # calculation claim. Normalize that redundant shape
                        # before the legacy claim validator; genuinely
                        # ungrounded open columns remain a hard contract error.
                        def initial_check(raw):
                            raw = deepcopy(raw)
                            for row in raw.get('items', []) if isinstance(raw, dict) else []:
                                pool.normalize_redundant_initial_columns(row)
                                if getattr(self, 'semantic_compiler_mode', False):
                                    pool.normalize_unbound_initial_claims(row)
                            return validator(raw)
                        return original(invoke, stage, prompt, payload, initial_check)
                    return original(invoke, stage, prompt, payload, validator)

                with patch.object(analysis, 'validated_call', validated), \
                     patch.object(updates, 'finish_view', finish), \
                     patch.object(contracts, 'promote_claims', promote), \
                     patch.object(typed_method, 'reclassified_decisions', reclassify_asked_decisions):
                    yield self

    def prepare_menu(self, call, session, plan, context, max_options=5):
        # The frozen menu module imports validated_call directly. That alias
        # bypasses the analysis-module adapter, so rewrite at its model callback.
        def menu_call(stage, prompt, payload):
            wire_stage = stage.removesuffix('_contract_retry')
            if wire_stage in ('hierarchical_field_filter', 'hierarchical_menu_groups'):
                prompt = disambiguate_wire_prompt(wire_stage, prompt)
            return call(stage, prompt, payload)
        updated, public = super().prepare_menu(menu_call, session, plan, context, max_options)
        if updated.get('obligation_pool'):
            updated = pool.refresh(updated, 'menu_transition')
        return updated, public

    def decision_dimension(self, decision, original):
        # All type changes use the same atomic ledger/queue transition. The
        # legacy scope->field path rewrote a question in-place and invalidated
        # the scheduler's reservation before menu preparation finished.
        dimension = decision['clarification']['dimension']
        if dimension != original or dimension in ('operation', 'value'):
            raise typed_method.ReclassifyQuestion(decision)
        return dimension

    def commit_transaction(self, session, base, event, raw, context):
        # Validation failures propagate inside the existing bounded retry. Never
        # mutate the published session while checking a proposed transaction.
        # Direct callers and model-driven calls use the identical boundary.
        # Envelope normalization must precede ownership traversal of updates.
        raw = compile_output('semantic_transaction', raw).to_legacy()
        # One deterministic ownership boundary handles every model reference
        # before the legacy transaction can allocate IDs or mutate graph state.
        raw = compile_model_references(raw, session, base)
        before = pool.initialize(session)
        # The frozen transaction adapter historically mutates some nested
        # structures while validating a proposal. Preserve an immutable
        # pre-state for transition auditing; otherwise the audit compares the
        # answer against its already-consumed ledger and reports a false
        # cross-state reference.
        before_snapshot = deepcopy(before)
        proposed = compile_inherited_dependencies(light.normalize_transaction(deepcopy(raw)), before)
        previous = getattr(self, '_claim_source', None), getattr(self, '_claim_parents', {})
        self._claim_source = before
        self._claim_parents = {c.get('id'): parent for parent, children in proposed.get('updates', {}).items()
                               for c in children}
        try:
            result = super().commit_transaction(before, base, event, proposed, context)
        finally:
            self._claim_source, self._claim_parents = previous
        audit = result['transaction_audit'][-1]
        result = pool.transfer(before, result, audit['replacements'],
                               retired=audit.get('retire_columns', {}), event='answer')
        answer = event.get('turn', {}).get('answer', {})
        if (event.get('kind') == 'answer' and answer.get('action') == 'answer'
                and answer.get('text', '').strip()
                and not self.skip_answer_contribution_review):
            from .pool_answer_review import PROMPT, validate
            reviewer = getattr(self, '_answer_review_call', None)
            if reviewer is None:
                raise ValueError('Free-text answer publication requires its local coverage review')
            text = answer['text']
            owners = [i for children in audit['replacements'].values() for i in children]
            try:
                review = analysis.validated_call(reviewer, 'answer_contribution_review', PROMPT,
                    dict(answer_text=text, asked_question=event.get('question', ''),
                         interaction_context=answer_review_context(session, event),
                         schema=light.schema_payload(context),
                         replacements=[dict(id=i, requirement=result['view']['items'][i]['requirement'],
                                            binding_claims=result['binding_claims'][i]) for i in owners]),
                    lambda raw: validate(raw, text))
            except (ValueError, TypeError, KeyError) as exc:
                # Coverage failure is a business-level unresolved answer, not
                # a runtime failure. Roll back graph and pool atomically while
                # retaining the already recorded interaction for the next turn.
                rollback = deepcopy(before)
                rollback['contract_unresolved_reason'] = (
                    'Answer coverage could not be committed: ' + str(exc))
                rollback.setdefault('answer_coverage_audit', []).append(dict(
                    turn=len(rollback.get('turns', [])), owners=owners,
                    review={'accepted': False, 'issues': [str(exc)]},
                    action='rollback_unresolved'))
                # Do not re-project the pool here: historical snapshots may
                # intentionally contain blocked holders that are not present
                # in the eligible view. The rollback must preserve that exact
                # authoritative state.
                return rollback
            if not review['accepted']:
                rollback = deepcopy(before)
                rollback['contract_unresolved_reason'] = (
                    'Answer coverage could not be committed: ' + str(review['issues']))
                rollback.setdefault('answer_coverage_audit', []).append(dict(
                    turn=len(rollback.get('turns', [])), owners=owners,
                    review=deepcopy(review), action='rollback_unresolved'))
                return rollback
            result.setdefault('answer_coverage_audit', []).append(dict(
                turn=len(result.get('turns', [])), owners=owners, review=review))
        result = reconcile_state(result, event='answer_transaction')
        transition_errors = audit_transition(before_snapshot, result, event)
        if transition_errors:
            result = deepcopy(result)
            result['contract_unresolved_reason'] = (
                'State transition contract: ' + '; '.join(transition_errors))
            result.setdefault('state_transition_audit', []).append({
                'event': 'answer_transaction', 'errors': transition_errors})
        return result

    def _transaction(self, call, session, base, event, context):
        # ContractMethod reconstructs ANSWER_UPDATE downstream of validated_call.
        # This outer callback sees the final prompt after every legacy adapter.
        def invoke(stage, prompt, payload):
            if stage in ('semantic_transaction', 'semantic_transaction_contract_retry'):
                # ContractMethod replaces the inherited transaction prompt after
                # validated_call. This callback sees that actual final prompt.
                prompt = disambiguate_wire_prompt('semantic_transaction', prompt)
                prompt += pool.TRANSFER_CONTRACT + '''
EDGE DECLARATION CONTRACT: edges is a local delta. Existing graph attachments
are preserved and remapped by code. Do not copy an existing edge or rewrite it
using a retired parent ID. Return only genuinely NEW relationships that involve
at least one child alias created in this update. An unchanged boundary node may
be the other endpoint, but cannot itself be edited.
GROUNDING CONTRACT: business meaning alone does not bind a predicate. Include
all necessary category/status/date and specific relationship association fields
in bound claims, even when inaccessible. Counting keys cannot also implement a
category restriction. Entity identifiers alone do not establish their requested
association. If intent is known but binding remains unknown, register a precise
open claim; do not label a partial implementation as fully bound.
'''
            return call(stage, prompt, payload)
        previous = getattr(self, '_answer_review_call', None)
        self._answer_review_call = call
        try:
            return super()._transaction(invoke, session, base, event, context)
        finally:
            self._answer_review_call = previous

    def _decompose(self, call, session, context, none_event=None):
        if none_event is None and not has_removal_authorization(session):
            return session
        from .pool_decomposition import decompose
        previous = getattr(self, '_decomposing_pool', False)
        self._decomposing_pool = True
        try:
            return decompose(self, call, session, context, none_event)
        finally:
            self._decomposing_pool = previous

    def _ground(self, call, session, context):
        """Run one local binding task per eligible owner; never answer a question."""
        current = pool.refresh(session)
        for owner, tasks in pool.internal_tasks(current):
            def check(raw):
                raw = compile_output('obligation_grounding', raw).to_legacy()
                if raw['unresolved']:
                    return raw
                ids = [r.get('claim_id') for r in raw['resolutions'] if isinstance(r, dict)]
                if len(ids) != len(tasks) or len(set(ids)) != len(ids) or set(ids) != {t['claim_id'] for t in tasks}:
                    raise ValueError('Resolve every local claim handle exactly once')
                claims = deepcopy(current['binding_claims'][owner])
                by_id = {t['claim_id']: t for t in tasks}
                for row in raw['resolutions']:
                    if set(row) != {'claim_id', 'columns'} or not isinstance(row['columns'], list):
                        raise ValueError('Only claim_id and real columns may be proposed')
                    index = by_id[row['claim_id']]['claim_index']
                    if not set(claims[index]['columns']) <= set(row['columns']):
                        raise ValueError('Internal binding cannot retire confirmed dependencies')
                    claims[index].update(state='bound', columns=row['columns'])
                node = current['view']['items'][owner]
                return dict(claims=validate_claims(claims, node['role'], context, node['kind']))

            try:
                raw = analysis.validated_call(call, 'obligation_grounding', pool.GROUND,
                    dict(owner=owner, requirement=current['view']['items'][owner]['requirement'],
                         tasks=tasks, schema=light.schema_payload(context)), check)
            except OutputContractError as exc:
                # A rejected proposal never writes bindings or deletes leaves.
                # Keep the pending internal work; do not turn malformed content
                # into a successful grounding or an uncaught runtime failure.
                current = deepcopy(current)
                current['contract_unresolved_reason'] = 'Internal grounding contract: ' + str(exc)
                current.setdefault('output_contract_failures', []).append(
                    dict(stage=exc.stage, path=exc.path, reason=exc.reason, owner=owner))
                return current
            if raw.get('unresolved'):
                current['contract_unresolved_reason'] = '; '.join(raw['unresolved'])
                return current
            current = deepcopy(current)
            current['binding_claims'][owner] = raw['claims']
            node = current['view']['items'][owner]
            columns = list(dict.fromkeys(node['required_columns'] +
                [c for claim in raw['claims'] for c in claim['columns']]))
            node.update(required_columns=columns,
                        denied_columns=sorted(set(columns) - set(current['allowed'])),
                        bindings=[columns] if columns else [],
                        scope_tables=sorted({c.rsplit('.', 1)[0] for c in columns}) or node['scope_tables'])
            current.setdefault('obligation_grounding_audit', []).append(
                dict(owner=owner, claim_ids=[t['claim_id'] for t in tasks], columns=columns,
                     turn=len(current.get('turns', []))))
            current = pool.refresh(sync_canonical(reframe(current, context)), 'ground')
            current = reconcile_state(current, event='internal_grounding')
        return reconcile_state(current, event='grounding_complete')

    def _complete_delivery(self, call, session, context):
        # Called after every analysis/answer by the inherited lifecycle. There
        # is no global completion model call or completion signature here.
        if not session.get('view') or session.get('contract_unresolved_reason'):
            return session
        current = pool.initialize(session)
        current = self._ground(call, current, context)
        if main.evidence_sources(current) and not current.get('contract_unresolved_reason'):
            current = self._decompose(call, current, context)
        return reconcile_state(pool.refresh(current), event='completion_cycle')

    def analyze_session(self, call, session, context, reconstruct=None):
        """Turn a failed model wire contract into a controlled terminal state.

        A bounded retry is already performed by the shared output boundary. If
        both attempts violate the declared wire, allowing the exception to
        escape produces a transport-looking ``system_error`` and skips the
        normal renderer. The state is left unchanged and marked unresolved;
        no semantic or permission decision is synthesized here.
        """
        try:
            return super().analyze_session(call, session, context, reconstruct)
        except OutputContractError as exc:
            failed = deepcopy(session)
            failed['contract_unresolved_reason'] = (
                f'{exc.stage} output contract failed: {exc.reason}')
            failed.setdefault('output_contract_failures', []).append({
                'stage': exc.stage, 'path': exc.path, 'reason': exc.reason})
            return failed

    def commit_deferred(self, call, session, context):
        current = self._complete_delivery(call, session, context)
        if not has_removal_authorization(current):
            blocked = deepcopy(current)
            blocked['deferred_incomplete'] = True
            blocked['contract_unresolved_reason'] = (
                'Deferred deletion has no user or permission authorization')
            return blocked
        if (current.get('contract_unresolved_reason') or light.pending(current['view'])
                or pool.issues(current)):
            return current
        from .pool_commit import commit
        return commit(self, call, current, context)

    def render_session(self, call, session):
        problems = pool.issues(session) if session.get('view') else []
        # User-facing business decisions have priority over internal binding
        # work. Pending obligations are not terminal while routable questions
        # remain; otherwise a scope answer can stop the conversation before
        # its field/value questions are asked.
        if problems and session.get('view') and not light.pending(session['view']) \
                and not any(n.get('missing') for n in session['view'].get('items', {}).values()):
            if self.publish_unresolved_to_text2sql:
                return self._unresolved_text2sql(session, 'obligation_pool_incomplete', problems)
            return dict(action='unresolved', reason='obligation_pool_incomplete', issues=problems,
                        text='', available_for_text2sql=False)
        result = ContractMethod.render_session(self, call, session)
        if (self.publish_unresolved_to_text2sql and result.get('action') == 'unresolved'
                and session.get('view', {}).get('items')):
            return self._unresolved_text2sql(session, result.get('reason', 'unresolved'),
                                             result.get('issues') or result.get('detail'))
        return result

    @staticmethod
    def _unresolved_text2sql(session, reason, detail=None):
        view = session.get('view') or {}
        text = session.get('current_request')
        if not text and view.get('items'):
            text = light.projection(view['items'], view.get('relations', {}).get('edges', []))
        if not text:
            return dict(action='unresolved', reason=reason, detail=detail,
                        text='', available_for_text2sql=False)
        return dict(action='unresolved', reason=reason, detail=detail,
                    text=text, available_for_text2sql=True,
                    quality='unresolved', requires_sql_permission_check=True)

    def next_step(self, session):
        # Parent still checks question ownership and closure. Final pool guard
        # prevents silent delivery if the scheduler cannot route internal work.
        result = super().next_step(session)
        if result['action'] in ('deliver', 'refuse') and session.get('obligation_pool'):
            problems = pool.issues(session)
            if problems:
                return dict(action='unresolved', reason='obligation_pool_incomplete', issues=problems)
        return result
