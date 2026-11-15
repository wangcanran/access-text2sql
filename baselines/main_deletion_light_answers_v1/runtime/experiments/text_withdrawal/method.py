"""An independent comparison arm: prose owns deletion, new analysis owns the graph."""
from copy import deepcopy

from ... import method as main
from ...engine import analysis, session_update, simple_session
from ...objective_contract import has_query_objective
from ..purpose_withdrawal.method import PurposeWithdrawal
from ..decision_update.lightweight import node_payload

VERSION = 'text_withdrawal_fresh_graph_v1'

REWRITE = '''Rewrite the current active query after cancelling the specified local
requirement. Return only JSON {"request":"remaining natural-language query"}.
The active nodes and relations together describe the current query; they are
not a list of returned columns. Use them to produce ONE complete remaining query.
The cancellation identifies the purpose of the current question, not the whole
compound source node. Remove only that purpose and its exclusive qualifications.
Keep other outputs, their subjects/associations, unanswered details and filters.
If a condition applies to several surviving outputs, retain it for those outputs;
if its only output was removed, remove the orphaned condition too. Cancelling a
filter preserves its subject and independently requested values. Never promote a
filter subject, operand or calculation parameter to an additional output.
Preserve AND/OR, negation, quantities and uncertainty. Do not restore earlier
removed intent, select new fields, invent details or propose external workarounds.
Return an empty request only if no query objective remains. No SQL or explanation.
'''


def checked_request(raw):
    if not isinstance(raw, dict) or set(raw) != {'request'} or not isinstance(raw['request'], str):
        raise ValueError('Expected request string; empty only when no objective remains')
    return raw['request'].strip()


class TextWithdrawal(PurposeWithdrawal):
    def fresh_graph(self, call, request, context, db_name):
        # No old graph, historical dialogue or removed dependency is an input.
        old = self._base, self._event, self._session
        self._base, self._event, self._session = None, None, None
        try:
            return self.analyze_flat(call, {'db_name': db_name,
                'schema': {k: context[k] for k in ('tables', 'foreign_keys')},
                'column_meanings': context.get('column_language', {}),
                'current_request': request,
                'public_conversation': {'turns': []}}, context)
        finally:
            self._base, self._event, self._session = old

    def _remove(self, call, session, basis, q, trigger, context):
        base = basis['view']
        target = q['item']['item_id']
        if target not in base['items']:
            raise ValueError('Withdrawal target must be current')
        decision = q.get('scoped_permission_decision', {})
        purpose = decision.get('requirement')
        if not purpose:
            raise ValueError('Withdrawal requires the local purpose used for the menu')
        payload = {'active_nodes': [node_payload(n) for n in base['items'].values()],
            'relations': [[e[k] for k in ('source_id', 'target_id', 'kind')]
                          for e in base['relations']['edges']],
            'cancellation': {'source_id': target, 'requirement': purpose,
                'role': decision.get('role'), 'question': q['focus']}}
        request = analysis.validated_call(call, 'withdrawal_request', REWRITE, payload, checked_request)
        new = deepcopy(session)
        for key in ('update_base', 'progressive_deletion_base', 'lineage_pending',
                    'lineage_resync_signature', 'lineage_review_signature'):
            new.pop(key, None)
        new.update(current_request=request, pending=None, menu_frontiers={},
                   menu_blockers={}, permission_issues={})
        inventory = simple_session.signature(base['inventory_id'], VERSION, request,
                                             len(session.get('reduction_audit', [])))
        if request:
            frame, fragments, mapping, edges, metadata = self.fresh_graph(call, request, context, new.get('db_name'))
            info = deepcopy(metadata['progressive'])
            items = {}
            for row in mapping['items']:
                iid = row['item_id']
                columns = sorted({c for b in row['bindings'] for c in b})
                items[iid] = {**row, **fragments[iid], 'required_columns': columns,
                    'denied_columns': sorted(set(columns) - set(new['allowed']))}
            frame_json = frame.to_json()
        else:
            items, edges = {}, {'edges': []}
            info = {'decision_ledger': [], 'question_policy': 'answer_snapshot',
                    'retired': {}, 'parents': {}, 'next_decision_id': 1}
            frame_json = {'original_text': '', 'outputs': [], 'modifiers': []}
            metadata = {'progressive': info}
        new['view'] = {'items': items, 'frame': frame_json, 'relations': edges,
            'progressive': info, 'inventory_id': inventory, 'questions': [],
            'segmentation': {'mode': VERSION, 'inventory': metadata},
            'deferred': [], 'candidate_audit': [], 'shared_decisions': [],
            'update_audit': {'mode': VERSION}}
        new['removal_no_query'] = not has_query_objective(items.values())
        new['needs_final_render'] = True
        new.setdefault('reduction_audit', []).append({
            'after_turn': len(session['turns']), 'trigger': trigger,
            'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
            'question': q['focus'], 'asked_dimension': q['dimension'],
            'previous_request': session['current_request'], 'request': request,
            'source_inventory': base['inventory_id'], 'method': VERSION,
            'scoped_permission_decision': deepcopy(decision), 'analysis_mode': VERSION})
        new.setdefault('transaction_audit', []).append({'event': {
            'kind': 'removal', 'target': target, 'purpose': purpose},
            'execution': VERSION, 'previous_inventory': base['inventory_id'],
            'inventory': inventory, 'fresh_graph': True})
        # Recompute permissions from the new graph. This does not authorize SQL;
        # semantic omissions remain a measurable model risk, not a proven removal.
        new = session_update.finish_view(new, context)
        main.check_active_state(new)
        return new
