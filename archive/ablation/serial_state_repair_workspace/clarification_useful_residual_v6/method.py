"""Declaration compiler plus unchanged contract-v5 answer/deferred lifecycle."""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch
import re

import clarification_contract_v5.method as contract_method
from clarification_contract_v5.method import ContractMethod
from clarification_deferred_deletion.baseline import analysis
from clarification_deferred_deletion.baseline import light
from clarification_deferred_deletion.baseline import main
from clarification_deferred_deletion.baseline import updates
from clarification_deferred_v3_2 import transaction as typed_transaction
from .compiler import compile_plan, compile_subject_lineage, validate_subject_edges, review_permission_facts
from .prompts import PLAN, BINDINGS, REVIEW
from .question_registry import register_questions, bind_question
from .answer_contract import prepare_answer_input, ANSWER_REPLACEMENT_CONTRACT
from .publication import PUBLIC_WORDING, exposed_identifiers, validate_public_wording
from .completion import (COMPLETE, completion_input, completion_signature,
                         validate_completion, readiness_issues)
from .output_contracts import installed_output_boundary
from clarification_deferred_deletion.method import reframe, sync_canonical

VERSION = 'useful_residual_v6_9_output_boundary'
ORIGIN_VERSION = 'useful_residual_contract_v5_25_integration_v3'


def has_removal_authorization(session):
    """Whether deferred deletion has an explicit user or permission basis."""
    state = session.get('deferred_state') or {}
    batches = state.get('batches') or []
    # Authorization must belong to the current batch, not an earlier answer.
    batch = batches[-1] if batches else None
    batch_turn = batch.get('turn') if isinstance(batch, dict) else None
    # A code-produced permission batch is already an authorization proof.  Do
    # not require the legacy permission_evidence list to be non-empty first:
    # older producers intentionally record [{}] while the authoritative proof
    # lives in block_proofs on the same current batch.
    if isinstance(batch, dict) and batch.get('block_proofs'):
        return True
    if any(bool(e) for e in state.get('permission_evidence', [])):
        if batch is None or batch_turn is None:
            return True
        # A permission batch carries a proof/rule and its evidence is recorded
        # at the same transition boundary.
    current_inventory = (session.get('view') or {}).get('inventory_id')
    if any((not issue.get('inventory_id') or issue.get('inventory_id') == current_inventory)
           for issue in session.get('permission_issues', {}).values()
           if isinstance(issue, dict)):
        return True
    turns = session.get('turns', [])
    if batch_turn is not None:
        # ``turn`` is the post-commit count; inspect only the event that
        # created this batch. Earlier unanswerable answers cannot authorize it.
        turns = turns[max(0, int(batch_turn) - 1):int(batch_turn)]
    for turn in turns:
        answer = turn.get('answer', {})
        if answer.get('action') == 'unanswerable' or turn.get('menu_rejected'):
            return True
        if answer.get('action') == 'choose':
            if any(isinstance(o, dict) and str(o.get('label', '')).casefold().startswith('none of')
                   for o in turn.get('selected_options', [])):
                return True
    return False


class UsefulResidualMethod(ContractMethod):
    @staticmethod
    def _public_answer_dimensions(event):
        """Use the question shown to the user, not compiler provenance."""
        turn = event.get('turn', {})
        dimensions = set(turn.get('requested_dimensions', []))
        shown = event.get('dimension') or turn.get('asked_dimension')
        if shown in ('scope', 'field', 'permission', 'operation', 'value'):
            dimensions.add(shown)
        dimensions.update(s.rsplit('/', 1)[-1] for s in turn.get('requested_slots', [])
                          if isinstance(s, str) and '/' in s)
        return dimensions

    @staticmethod
    def _attach_unique_decision_id(session):
        """Materialize an omitted ID only when the ledger has one unambiguous match."""
        turn = (session.get('turns') or [])[-1] if session.get('turns') else None
        base = session.get('update_base')
        if not turn or not base or turn.get('decision_ids'):
            return
        target = turn.get('target_id')
        dimension = turn.get('asked_dimension')
        if not target or not dimension:
            return
        # Initial analysis stores the ledger under segmentation.inventory;
        # later turns store it in the canonical view. Both are code-owned
        # state, while an empty/model-supplied ID list is not authoritative.
        view = base.get('view')
        if view is not None:
            ledger = light.pending(view)
        else:
            ledger = list((base.get('segmentation', {}).get('inventory', {})
                           .get('progressive', {}).get('decision_ledger', [])))
        turn.pop('decision_ids', None)
        owner_decisions = [d for d in ledger if d.get('owner_id') == target]
        candidates = [d for d in owner_decisions if d.get('dimension') == dimension]
        if not candidates:
            # A permission/scope decision can be rendered as a concrete field
            # menu. Use its compiler provenance only as a unique fallback.
            origin = (turn.get('scoped_permission_decision') or {}).get('origin_dimension')
            if origin in ('scope', 'field', 'permission', 'operation', 'value'):
                candidates = [d for d in owner_decisions if d.get('dimension') == origin]
        if len(candidates) == 1:
            turn['decision_ids'] = [candidates[0]['decision_id']]

    @classmethod
    def _code_owned_asked_ids(cls, base, event, dimensions):
        """Resolve answer identities from the current pending ledger only."""
        if event.get('kind') != 'answer':
            return set()
        turn = event.get('turn', {})
        if turn.get('answer', {}).get('action') == 'unanswerable':
            return set()
        rows = [d for d in light.pending(base)
                if d.get('owner_id') == event.get('target')
                and d.get('dimension') in set(dimensions)]
        supplied = turn.get('decision_ids')
        if supplied is not None and not isinstance(supplied, list):
            raise ValueError('Answer identities must be a list')
        if supplied:
            allowed = {d['decision_id'] for d in rows}
            if (not isinstance(supplied, list) or any(not isinstance(i, str) for i in supplied)
                    or len(set(supplied)) != len(supplied) or not set(supplied) <= allowed
                    or {d['dimension'] for d in rows if d['decision_id'] in supplied} != set(dimensions)):
                raise ValueError('Answer IDs must cover exactly the current owner and purposes')
            return set(supplied)
        # A dimension may identify one decision, but never several. This is
        # the same fail-closed rule used by the transition identity repair.
        if not dimensions or any(sum(d['dimension'] == dim for d in rows) != 1 for dim in dimensions):
            return set()
        return {d['decision_id'] for d in rows}

    @contextmanager
    def installed(self):
        # Install beneath ALL inherited adapters, so raw output is checked
        # before any adapter indexes fields, promotes claims or edits state.
        # The frozen baseline remains immutable; restoration is scoped here.
        with installed_output_boundary(analysis), super().installed():
            original = analysis.validated_call
            original_finish = updates.finish_view
            original_public = self.engine.public_question
            original_field_menu = self.engine.prepare_field_menu

            def public_question(session, plan):
                return self._public_text(self._publication_call, original_public(session, plan), self._context)

            def field_menu(session, plan, context, *args, **kwargs):
                updated, public = original_field_menu(session, plan, context, *args, **kwargs)
                return self._publish(self._publication_call, updated, public, context)

            def finish_view(session, context):
                result = original_finish(session, context)
                # The frozen analyzer invokes the virtual scheduler before
                # ContractMethod.analyze_session returns. Publish all initial
                # contracts together, rather than adding claims afterwards.
                if getattr(self, '_initializing_contracts', False) and 'binding_claims' not in result:
                    claims = getattr(self, '_initial_claims', None)
                    if claims is None or set(claims) != set(result['view']['items']):
                        raise ValueError('Initial binding claims must match the published inventory')
                    result['binding_claims'] = deepcopy(claims)
                    info = result['view'].setdefault('progressive', {})
                    # Full initial resynchronization bypasses analyze_flat's
                    # ledger builder. Use the same deterministic discovery.
                    if 'decision_ledger' not in info:
                        info['decision_ledger'], info['next_decision_id'] = light.discover(
                            result['view']['items'], info.get('source_open_choices', {}), [], 1)
                        info['question_policy'] = 'answer_delta'
                # Answer transactions build a temporary heuristic view before
                # restoring the authoritative carried ledger. Do not register
                # or validate that intermediate state as a publishable queue.
                if result.get('turns') or getattr(self, '_decomposing_pool', False):
                    return result
                return register_questions(result)

            def validated(call, stage, prompt, payload, validator):
                if stage in ('contract_permission_decomposition', 'contract_none_decomposition'):
                    def check(raw):
                        result = validator(compile_plan(raw))
                        self._accepted_declarations = deepcopy(raw)
                        return result
                    return original(call, stage, PLAN + BINDINGS, payload, check)
                if stage == 'deferred_commit_review':
                    prompt = re.sub(r'loss\s+of an optional condition',
                                    'loss of a display-only annotation', prompt) + REVIEW
                    payload = review_permission_facts(payload)
                return original(call, stage, prompt, payload, validator)

            with patch.object(analysis, 'validated_call', validated), \
                 patch.object(updates, 'finish_view', finish_view), \
                 patch.object(light, 'answered_dimensions', self._public_answer_dimensions), \
                 patch.object(light, 'asked_ids', self._code_owned_asked_ids), \
                 patch.object(typed_transaction, 'answered_dimensions', self._public_answer_dimensions), \
                 patch.object(contract_method, 'compile_subject_lineage', compile_subject_lineage), \
                 patch.object(contract_method, 'validate_subject_edges', validate_subject_edges), \
                 patch.object(self.engine, 'public_question', public_question), \
                 patch.object(self.engine, 'prepare_field_menu', field_menu):
                yield self

    def _public_text(self, call, public, context):
        if public is None or not exposed_identifiers(public, context):
            return public
        return analysis.validated_call(call, 'public_wording', PUBLIC_WORDING,
            {'public': deepcopy(public)}, lambda raw: validate_public_wording(raw, public, context))

    def _publish(self, call, session, public, context):
        safe = self._public_text(call, public, context)
        if safe is not None and session.get('pending'):
            session = deepcopy(session)
            session['pending']['public'] = deepcopy(safe)
        return session, safe

    def prepare_menu(self, call, session, plan, context, max_options=5):
        self._publication_call = call
        updated, public = super().prepare_menu(call, session, plan, context, max_options)
        return self._publish(call, updated, public, context)

    def _complete_delivery(self, call, session, context):
        """Run before final deletion/render, never after a committed deletion."""
        for _ in range(3):
            view = session.get('view')
            if (not view or not view['items'] or light.pending(view)
                    or any(n.get('missing') for n in view['items'].values())
                    or session.get('contract_unresolved_reason')):
                return session
            if main.evidence_sources(session) and has_removal_authorization(session):
                session = self._decompose(call, session, context)
                session = sync_canonical(session)
                continue
            signature = completion_signature(session)
            if session.get('completion_signature') == signature and not readiness_issues(session):
                return session
            payload = completion_input(session, context, light.schema_payload(context))
            try:
                result = analysis.validated_call(call, 'binding_completion', COMPLETE, payload,
                    lambda raw: validate_completion(raw, session, context))
            except (ValueError, TypeError, KeyError) as exc:
                session = deepcopy(session)
                session['contract_unresolved_reason'] = 'Internal completion contract: ' + str(exc)
                return session
            new = deepcopy(session)
            new.setdefault('completion_audit', []).append({'input_signature': signature,
                'result': deepcopy(result), 'turn': len(session.get('turns', []))})
            if result['unresolved']:
                new['contract_unresolved_reason'] = '; '.join(result['unresolved'])
                return new
            for row in result['nodes']:
                node = new['view']['items'][row['id']]
                node['requirement'] = node['span'] = row['requirement']
                node['required_columns'] = row['columns']
                node['denied_columns'] = sorted(set(row['columns']) - set(new['allowed']))
                node['bindings'] = [row['columns']] if row['columns'] else []
                node['scope_tables'] = sorted({c.rsplit('.', 1)[0] for c in row['columns']}) or node['scope_tables']
                new['binding_claims'][row['id']] = row['binding_claims']
            session = sync_canonical(reframe(new, context))
            session['completion_signature'] = completion_signature(session)
            # Newly grounded dependencies must pass the normal permission
            # decomposition before any final text/deletion can be committed.
            if not main.evidence_sources(session):
                return session
        session = deepcopy(session)
        session['contract_unresolved_reason'] = 'Internal completion/permission projection did not converge'
        return session

    def commit_deferred(self, call, session, context):
        session = self._complete_delivery(call, session, context)
        if not has_removal_authorization(session):
            blocked = deepcopy(session)
            blocked['deferred_incomplete'] = True
            blocked['contract_unresolved_reason'] = (
                'Deferred deletion has no user or permission authorization')
            return blocked
        if (session.get('contract_unresolved_reason') or light.pending(session['view'])
                or any(n.get('missing') for n in session['view']['items'].values())):
            return session
        return super().commit_deferred(call, session, context)

    def _transaction(self, call, session, base, event, context):
        # Do this immediately before constructing the semantic transaction
        # payload. The model never supplies or routes decision identities.
        dimensions = self._public_answer_dimensions(event)
        if event.get('kind') == 'answer' and event.get('turn', {}).get('answer', {}).get('action') != 'unanswerable':
            ids = self._code_owned_asked_ids(base, event, dimensions)
            if not ids:
                raise ValueError('Cannot route answer to a unique pending decision')
            event.setdefault('turn', {})['decision_ids'] = sorted(ids)
        def contract_call(stage, prompt, payload):
            if stage in ('semantic_transaction', 'semantic_transaction_contract_retry'):
                payload = prepare_answer_input(payload)
                prompt += ANSWER_REPLACEMENT_CONTRACT
            return call(stage, prompt, payload)
        return super()._transaction(contract_call, session, base, event, context)

    def analyze_session(self, call, session, context, reconstruct=None):
        self._publication_call = call
        self._attach_unique_decision_id(session)
        previous = getattr(self, '_initializing_contracts', False)
        self._initializing_contracts = session.get('view') is None and not session.get('update_base')
        if self._initializing_contracts:
            self._initial_claims = None
        try:
            result = super().analyze_session(call, session, context, reconstruct)
            if not result.get('deferred_final'):
                result = self._complete_delivery(call, result, context)
                state = result.get('deferred_state')
                if (state and not state['committed'] and not result.get('contract_unresolved_reason')
                        and not light.pending(result['view'])
                        and not any(n.get('missing') for n in result['view']['items'].values())):
                    result = self.commit_deferred(call, result, context)
            return result
        finally:
            self._initializing_contracts = previous

    def render_session(self, call, session):
        view = session.get('view')
        if view and view['items'] and not session.get('contract_unresolved_reason'):
            issues = readiness_issues(session)
            if session.get('completion_signature') != completion_signature(session):
                issues.append({'code': 'internal_completion_not_verified'})
            if issues:
                return {'action': 'unresolved', 'reason': 'internal_completion_incomplete',
                        'issues': issues, 'text': '', 'available_for_text2sql': False}
        return super().render_session(call, session)

    def next_step(self, session):
        # All engine decision/public_question/commit_answer routes call this
        # scheduler, including NONE. Invalid identities cannot reach the user.
        view = session.get('view')
        if view:
            try:
                if 'decision_ledger' not in view.get('progressive', {}):
                    raise ValueError('Question publication requires a canonical decision ledger')
                rows = light.pending(view)
                ids = [r['decision_id'] for r in rows]
                if len(set(ids)) != len(ids) or any(r['owner_id'] not in view['items'] for r in rows):
                    raise ValueError('Decision ledger has duplicate IDs or inactive owners')
                for owner, item in view['items'].items():
                    if any(not any(r['owner_id'] == owner and r['dimension'] == dim for r in rows)
                           for dim in item.get('missing', [])):
                        raise ValueError('Current uncertainty has no registered decision')
                for question in view.get('questions', []):
                    bind_question(question, rows)
            except ValueError as exc:
                return {'action': 'unresolved', 'reason': 'question_identity_contract: ' + str(exc),
                        'turn': len(session.get('turns', []))}
        return super().next_step(session)

    def _decompose(self, call, session, context, none_event=None):
        # Initial analysis may discover blocked dependencies, but that is a
        # question projection, not permission to delete. Only an explicit
        # NONE/unanswerable event or recorded permission issue may create a
        # deferred deletion batch.
        if none_event is None and not has_removal_authorization(session):
            return session
        self._accepted_declarations = None
        updated = super()._decompose(call, session, context, none_event)
        if self._accepted_declarations and not updated.get('contract_unresolved_reason'):
            batch = updated['deferred_state']['batches'][-1]
            batch['contribution_declarations'] = deepcopy(self._accepted_declarations)
            batch['pruning_policy'] = VERSION
        return updated
