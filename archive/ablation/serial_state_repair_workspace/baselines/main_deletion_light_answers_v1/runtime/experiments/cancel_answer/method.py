"""One semantic update call for accepted answers and local cancellations."""
from copy import deepcopy

from ...engine import analysis
from ..decision_update.lightweight import (
    LightweightUpdate, region, event_payload, node_payload, schema_payload,
    protected_outputs, transaction)
from ..decision_update.light_prompt import UPDATE
from ..decision_update.ledger import pending
from ..decision_update.question_delta import asked_ids
from ..decision_update.lightweight import answered_dimensions

VERSION = 'light_cancel_answer_v1'
CANCEL = '''CANCEL ANSWER: answer.action=cancel means cancel ONLY event.purpose,
not the whole owner or query. The cancelled purpose is no longer pending.
Return [] for a parent only when no independent meaning remains in it.
For changed nodes return surviving text, columns and questions; omit unchanged
nodes. Do not turn the cancelled purpose into a vague request to ask again.
Preserve independent outputs, filters and unanswered details. Remove modifiers
exclusive to cancelled outputs; keep shared constraints on surviving outputs.
Keep surviving subjects explicit, without a dangling reference to a deleted
anchor. If a subject choice genuinely remains, keep a menu question for it.
Do not restore the cancelled anchor or invent a replacement subject.
Update incident edges in the same response. No separate deletion plan or prose
rewrite. Dependencies and questions of changed nodes describe survivors only.
purpose.remaining_requirements is a hint, not an exhaustive list.
'''
# Keep the existing answer vocabulary/shape; replace only removal instructions.
PROMPT = UPDATE[:UPDATE.index('REMOVAL:')] + CANCEL + '\n' + UPDATE[UPDATE.index('questions keys:'):]


def cancelling(event):
    return event.get('turn', {}).get('answer', {}).get('action') == 'cancel'


def material_event(event):
    """Reuse existing atomic drop validation, not a second semantic updater."""
    event = deepcopy(event)
    if cancelling(event):
        event['kind'] = 'removal'
        event['decision'] = event['turn']['scoped_permission_decision']
    return event


def editable_region(base, event):
    ids = region(base, event)  # Ordinary directed propagation, no component flood.
    if cancelling(event):
        # Incoming qualifications may become orphaned. Grant review access,
        # never deletion authority, to direct adjacent nodes as well.
        for edge in base['relations']['edges']:
            if event['target'] in (edge['source_id'], edge['target_id']):
                ids.update((edge['source_id'], edge['target_id']))
    return ids


class CancelAnswer(LightweightUpdate):
    def edit_boundary(self, base, event):
        return editable_region(base, event)

    def commit_local(self, session, base, event, raw, context, editable):
        return transaction(session, base, event, raw, context, editable_ids=editable)

    def _transaction(self, call, session, base, event, context):
        editable = self.edit_boundary(base, event)
        edges = [e for e in base['relations']['edges']
                 if {e['source_id'], e['target_id']} & editable]
        visible = editable | {e[k] for e in edges for k in ('source_id', 'target_id')}
        semantic = material_event(event)
        payload = {'event': event_payload(event), 'editable_ids': sorted(editable),
            'preserve_outputs': sorted(protected_outputs(base, semantic) & editable),
            'nodes': [node_payload(n) for i, n in base['items'].items() if i in visible],
            'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')] for e in edges],
            'schema': schema_payload(context)}
        if not cancelling(event):
            payload['event']['asked_decisions'] = sorted(asked_ids(base, event, answered_dimensions(event)))
            for node in payload['nodes']:
                node.pop('questions')
                node['decisions'] = [{'id': d['decision_id'], 'dimension': d['dimension'],
                                     'question': d['question']} for d in pending(base)
                                    if d['owner_id'] == node['id']]
        def commit(raw):
            result = self.commit_local(session, base, semantic, raw, context, editable)
            result['transaction_audit'][-1].update(event=event_payload(event), execution=VERSION)
            return result
        return analysis.validated_call(call, 'semantic_transaction', PROMPT, payload, commit)

    def _remove(self, call, session, basis, q, trigger, context):
        # Protocol adapter only: both refusal and empty menu enter the ordinary
        # answer updater, without code_deletable/deletion_patch or a second call.
        decision = deepcopy(q.get('scoped_permission_decision', {}))
        if not decision.get('requirement'):
            raise ValueError('Cancellation requires the currently asked local purpose')
        turn = {'answer': {'action': 'cancel', 'text': 'Cancel this requirement.'},
                'scoped_permission_decision': decision, 'selected_options': []}
        event = {'kind': 'answer', 'target': q['item']['item_id'],
                 'dimension': q['dimension'], 'question': q['focus'], 'turn': turn}
        result = self._transaction(call, session, basis['view'], event, context)
        result.setdefault('reduction_audit', []).append({
            'after_turn': len(session['turns']), 'trigger': trigger, 'method': VERSION,
            'target_fragment': q['item']['span'], 'question': q['focus'],
            'scoped_permission_decision': decision,
            'previous_request': session['current_request'], 'request': result['current_request'],
            'analysis_mode': 'ordinary_local_answer'})
        return result
