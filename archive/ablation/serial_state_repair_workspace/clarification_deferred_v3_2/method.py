"""Default: a valid answer closes the asked question, at user specificity."""
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import patch

from clarification_deferred_deletion.baseline import light, analysis, module
from clarification_deferred_deletion.method import DeferredDeletion, reframe
from .questions import apply_questions, contract, POLICY, ledger
from .prompts import DECISION_CONTRACT, ANSWER_UPDATE, REVIEW_POLICY, INITIAL_QUESTION_POLICY
from .transaction import transaction
from .structure import check_structure

VERSION = 'deferred_deletion_v4_1_canonical_ids_answer_settlement'


class ReclassifyQuestion(Exception):
    def __init__(self, decision):
        self.decision = decision


def reclassified_decisions(view, question, decision):
    """Resolve a compiler reclassification to one existing local ledger ID."""
    ids = set(question.get('decision_ids', []))
    dim = decision['clarification']['dimension']
    ledger = view['progressive']['decision_ledger']
    candidates = [d for d in ledger if d['owner_id'] == question['item']['item_id']
                  and d['dimension'] == dim]
    matches = candidates if len(candidates) == 1 else [
        d for d in ledger if d['decision_id'] in ids]
    if not matches:
        owner_decisions = [d for d in ledger if d['owner_id'] == question['item']['item_id']]
        if len(owner_decisions) == 1:
            matches = owner_decisions
    if not matches:
        raise ValueError('Reclassification requires an explicit pending decision identity')
    if any(d['owner_id'] != question['item']['item_id'] for d in matches):
        raise ValueError('Reclassification cannot cross question owners')
    return matches


def remove_unbacked_questions(session):
    """Typed questions require ledger identity before reaching the user."""
    view = session.get('view')
    if view and 'decision_ledger' in view.get('progressive', {}):
        view['questions'] = [q for q in view['questions'] if q.get('decision_ids')]
    return session


class TypedDeferredDeletion(DeferredDeletion):
    @contextmanager
    def installed(self):
        # All answer and decomposition transactions share the canonical ID
        # allocator, including calls made inside the frozen deferred layer.
        with super().installed(), patch.object(light, 'transaction', transaction):
            yield self

    def _remove(self, call, session, basis, q, trigger, context):
        # Some services require the literal JSON instruction whenever the
        # recorder selects response_format=json_object. WITHDRAW inherited from
        # v2 only showed an object example, which is insufficient for that API.
        def json_call(stage, prompt, payload):
            raw = call(stage, prompt + '\nReturn JSON only. ' + REVIEW_POLICY, payload)
            return self._settled_plan(raw, getattr(self, '_decomposition_session', basis)) if stage.removesuffix(
                '_contract_retry') == 'deferred_none_decomposition' else raw
        return check_structure(super()._remove(json_call, session, basis, q, trigger, context))

    def decision_input(self, prompt, payload):
        payload = deepcopy(payload)
        payload['pending_decisions'] = getattr(self, '_compiler_pending', [])
        # One complete compiler contract; the inherited example lacks the
        # required dimension and forbids the operation/value routing we support.
        return DECISION_CONTRACT, payload

    def compiled_decision(self, decision, source, context):
        decision = deepcopy(decision)
        decision['clarification'] = contract(decision.get('clarification'))
        c = decision['clarification']
        if c['dimension'] in ('operation', 'value'):
            decision['field_candidates'] = []
        return decision

    def decision_dimension(self, decision, original):
        dim = decision['clarification']['dimension']
        if dim in ('operation', 'value'):
            raise ReclassifyQuestion(decision)
        return dim

    def prepare_menu(self, call, session, plan, context, max_options=5):
        current = self.engine.decision(session, plan)
        self._compiler_asked = set(current.get('decision_ids', []))
        self._compiler_pending = [d for d in module('experiments.decision_update.ledger').pending(session['view'])
                                  if d['owner_id'] == current['item']['item_id']]
        try:
            updated, public = super().prepare_menu(call, session, plan, context, max_options)
            return check_structure(updated), public
        except ReclassifyQuestion as exc:
            # Change the pending decision's type before any user-facing question
            # is issued. No answer/deletion event is fabricated.
            new = deepcopy(session)
            q = self.engine.decision(session, plan)
            decision = exc.decision
            dim = decision['clarification']['dimension']
            matches = reclassified_decisions(new['view'], q, decision)
            ids = {d['decision_id'] for d in matches}
            for d in matches:
                d['dimension'], d['question'] = dim, decision['question']
            self._refresh_focus(new)
            new.setdefault('question_contract_audit', []).append(dict(
                event='reclassified_before_question', decision_ids=sorted(ids),
                old_dimension=q['dimension'], dimension=dim, question=decision['question']))
            return check_structure(remove_unbacked_questions(reframe(new, context))), None

    def next_step(self, session):
        plan = super().next_step(session)
        if plan['action'] != 'menu' or not session.get('view'):
            return plan
        q = next((q for q in session['view']['questions'] if q['key'] == plan['question_id']), None)
        if not q or q['dimension'] != 'field' or q['item']['kind'] != 'calculation':
            return plan
        owner = q['item']['item_id']
        if q['item']['denied_columns'] or 'scope' in q['item']['missing']:
            return plan
        operation = next((x for x in session['view']['questions'] if x['item']['item_id'] == owner
                          and x['dimension'] == 'operation' and x['key'] not in session['view']['deferred']), None)
        if operation:
            return dict(action='ask', turn=len(session['turns']), question_id=operation['key'])
        return plan

    @staticmethod
    def _refresh_focus(session):
        decisions = session['view']['progressive']['decision_ledger']
        for iid, n in session['view']['items'].items():
            focus = {}
            for d in decisions:
                if d['owner_id'] == iid:
                    focus.setdefault(d['dimension'], []).append(d['question'])
            n['question_focus'] = {dim: ' '.join(dict.fromkeys(qs)) for dim, qs in focus.items()}
            n['missing'] = list(focus)

    def commit_transaction(self, session, base, event, raw, context):
        raw = deepcopy(raw)
        if isinstance(raw, dict):
            # Ignore deprecated continuation metadata in saved/legacy outputs.
            raw.pop('followup_routes', None)
        authoritative = []
        def updater(*args):
            result = apply_questions(*args)
            authoritative.extend(deepcopy(result[0]))
            return result
        new = light.transaction(session, base, event, raw, context,
                                compound_answers=True, question_updater=updater)
        if event['kind'] == 'answer':
            # The base runtime has a later generic-output repair/discover pass.
            # Its heuristic must not mint a replacement for a settled question.
            new['view']['progressive']['decision_ledger'] = authoritative
            self._refresh_focus(new)
            new['clarification_policy'] = POLICY
            effects = new['transaction_audit'][-1]['question_effects']
            settled = [x for x in effects if x.get('effect') == 'resolved']
            new.setdefault('settled_questions', []).extend(deepcopy(settled))
            new = reframe(new, context)
            self._refresh_focus(new)
            return check_structure(remove_unbacked_questions(new))
        return check_structure(remove_unbacked_questions(new))

    def _transaction(self, call, session, base, event, context):
        # Retain the base local planner/validator; only enrich its same call.
        def typed_call(stage, prompt, payload):
            payload = deepcopy(payload)
            active = payload['input'] if stage.endswith('_contract_retry') else payload
            active['clarification_policy'] = POLICY
            return call(stage, ANSWER_UPDATE, payload)
        return super()._transaction(typed_call, session, base, event, context)

    def analyze_session(self, call, session, context, reconstruct=None):
        def policy_call(stage, prompt, payload):
            if stage.removesuffix('_contract_retry') in ('progressive_coarse', 'progressive_analysis'):
                # Source-only choices become persistent ledger obligations.
                # Prevent speculative questions here, not by silently deleting
                # them after the user has been asked or after an answer.
                prompt += INITIAL_QUESTION_POLICY
            if stage.removesuffix('_contract_retry') in ('deferred_decomposition', 'deferred_none_decomposition'):
                payload = deepcopy(payload)
                active = payload['input'] if stage.endswith('_contract_retry') else payload
                active['settled_questions'] = getattr(self, '_decomposition_session', session).get('settled_questions', [])
                prompt += '\nPreviously settled purposes must not be reopened as questions. Return JSON only.'
            raw = call(stage, prompt, payload)
            return self._settled_plan(raw, getattr(self, '_decomposition_session', session)) if stage.removesuffix('_contract_retry') in (
                'deferred_decomposition', 'deferred_none_decomposition') else raw
        return check_structure(remove_unbacked_questions(
            super().analyze_session(policy_call, session, context, reconstruct)))

    def plan_payload(self, session, context):
        self._decomposition_session = session
        payload = super().plan_payload(session, context)
        payload.update(clarification_policy=POLICY, settled_questions=session.get('settled_questions', []))
        return payload

    @staticmethod
    def _settled_plan(raw, session):
        """Later permission decomposition cannot reopen an answered lineage.

        Existing independent obligations remain through explicit decision_ids;
        the canonical plan validator restores their original question text.
        """
        if not isinstance(raw, dict) or not isinstance(raw.get('nodes'), list) or not session.get('view'):
            return raw
        settled = {d['owner'] for d in session.get('settled_questions', [])}
        if not settled:
            return raw
        parents = session['view'].get('progressive', {}).get('parents', {})
        pending = {d['decision_id']: d for d in ledger.pending(session['view'])}
        def answered_lineage(iid):
            seen = set()
            while iid and iid not in seen:
                if iid in settled:
                    return True
                seen.add(iid)
                iid = parents.get(iid)
            return False
        raw = deepcopy(raw)
        for n in raw['nodes']:
            if not isinstance(n, dict) or not answered_lineage(n.get('parent')):
                continue
            dimensions = {pending[d]['dimension'] for d in n.get('decision_ids', []) if d in pending}
            if isinstance(n.get('questions'), dict):
                n['questions'] = {dim: q for dim, q in n['questions'].items() if dim in dimensions}
                if 'field' not in n['questions']:
                    n['candidates'] = {}
        return raw

    def commit_deferred(self, call, session, context):
        def policy_review(stage, prompt, payload):
            if stage.removesuffix('_contract_retry') == 'deferred_commit_review':
                prompt += REVIEW_POLICY
            return call(stage, prompt, payload)
        return super().commit_deferred(policy_review, session, context)
