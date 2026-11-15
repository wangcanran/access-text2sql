"""v2 deletion mechanics with typed question and continuation contracts."""
from copy import deepcopy

from clarification_deferred_deletion.baseline import light, analysis, module
from clarification_deferred_deletion.method import DeferredDeletion, reframe
from .questions import apply_questions, contract
from .prompts import DECISION_CONTRACT, UPDATE_CONTRACT

VERSION = 'deferred_deletion_v3_1_identity_continuations_operation_first'


class ReclassifyQuestion(Exception):
    def __init__(self, decision):
        self.decision = decision


class TypedDeferredDeletion(DeferredDeletion):
    def decision_input(self, prompt, payload):
        payload = deepcopy(payload)
        payload['pending_decisions'] = getattr(self, '_compiler_pending', [])
        return prompt + DECISION_CONTRACT, payload

    def compiled_decision(self, decision, source, context):
        decision = deepcopy(decision)
        decision['clarification'] = contract(decision.get('clarification'))
        c = decision['clarification']
        old = {d['decision_id']: d for d in getattr(self, '_compiler_pending', [])}
        if set(c['continue_decisions']) - set(old):
            raise ValueError('continue_decisions must reference supplied pending IDs')
        if any(q == decision['question'] or q in {d['question'] for d in old.values()}
               for q in c['followups'].values()):
            raise ValueError('Reuse existing IDs; a followup must not repeat the current or a pending question')
        if c['dimension'] in ('operation', 'value'):
            decision['field_candidates'] = []
        if (c['dimension'] == 'scope' and decision['role'] == 'output' and 'field' not in c['followups']
                and not any(old[d]['dimension'] == 'field' for d in c['continue_decisions'])):
            raise ValueError('Output entity selection must preserve an explicit returned-field followup')
        return decision

    def decision_dimension(self, decision, original):
        dim = decision['clarification']['dimension']
        if dim in ('operation', 'value'):
            raise ReclassifyQuestion(decision)
        return dim

    def prepare_menu(self, call, session, plan, context, max_options=5):
        current = self.engine.decision(session, plan)
        self._compiler_pending = [d for d in module('experiments.decision_update.ledger').pending(session['view'])
                                  if d['owner_id'] == current['item']['item_id']]
        try:
            return super().prepare_menu(call, session, plan, context, max_options)
        except ReclassifyQuestion as exc:
            # Change the pending decision's type before any user-facing question
            # is issued. No answer/deletion event is fabricated.
            new = deepcopy(session)
            q = self.engine.decision(session, plan)
            ids = set(q.get('decision_ids', []))
            decision = exc.decision
            dim = decision['clarification']['dimension']
            matches = [d for d in new['view']['progressive']['decision_ledger']
                       if d['decision_id'] in ids]
            if not matches:
                raise ValueError('Reclassification requires an explicit pending decision identity')
            for d in matches:
                d['dimension'], d['question'] = dim, decision['question']
            self._refresh_focus(new)
            new.setdefault('question_contract_audit', []).append(dict(
                event='reclassified_before_question', decision_ids=sorted(ids),
                old_dimension=q['dimension'], dimension=dim, question=decision['question']))
            return reframe(new, context), None

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
        routes = raw.pop('followup_routes', {}) if isinstance(raw, dict) else {}
        def updater(*args):
            args = list(args)
            args[2] = {**args[2], 'followup_routes': routes}
            return apply_questions(*args)
        return light.transaction(session, base, event, raw, context, question_updater=updater)

    def _transaction(self, call, session, base, event, context):
        # Retain the base local planner/validator; only enrich its same call.
        def typed_call(stage, prompt, payload):
            payload = deepcopy(payload)
            active = payload['input'] if stage.endswith('_contract_retry') else payload
            c = event.get('turn', {}).get('scoped_permission_decision', {}).get('clarification', {})
            active['mandatory_followups'] = c.get('followups', {})
            active['continued_decisions'] = c.get('continue_decisions', [])
            active['answer_dimension'] = c.get('dimension', event['dimension'])
            return call(stage, prompt + UPDATE_CONTRACT, payload)
        return super()._transaction(typed_call, session, base, event, context)
