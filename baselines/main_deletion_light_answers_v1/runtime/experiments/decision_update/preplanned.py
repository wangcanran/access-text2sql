"""Compile a conditional withdrawal with the menu; commit it without an API.

The prepared edit is tied to one inventory, never reused after another update.
Invalid optional plans do not invalidate a usable business menu. They fall back
to the single snapshot updater and are counted explicitly in the audit.
"""
from copy import deepcopy

from .deletion_plan import DeletionPlanUpdate, deletion_region, snapshot_response
from .lightweight import node_payload, transaction


VERSION = 'menu_preplanned_withdrawal_v1'
PLAN = '''
In this SAME response include withdrawal, a conditional survivor snapshot for
withdrawing the WHOLE local requirement you just identified (not one unchosen
menu option). It is internal, not a user answer. It changes nothing unless the
protocol later withdraws this purpose. Use:
"withdrawal":{"updates":{"f1":[{"id":"p1","role":"output",
"text":"independent survivor","columns":["t.c"],
"questions":{"field":"Which returned information?"}}]},"edges":[]}.
Only withdrawal_region IDs may change. Omit unchanged nodes; [] drops a parent.
Every child has id, role, text, columns; kind/tables inherit. questions is the
complete set of unresolved scope/field/operation/value questions, default {}.
Do not copy pending IDs. Split independent survivors, with explicit roles.
An output is returned information; a condition never becomes an extra output.
Remove dependent parameters with their purpose, retain independent returns and
filters. Keep subjects/associations needed by survivors. If removing an identity
filter, retain the subject when other requirements still concern it. Retain
shared qualifications for surviving outputs, dropping exclusively orphaned ones.
Edges are [source,target,kind]: applies_to qualifies; context needs an antecedent.
Use new aliases and existing IDs. Return changed/split attachments. One-to-one
same-role attachments and unchanged edges are retained in code. No new intent,
resolved ambiguity, substitute subject, access explanation or workaround.
If this is one independent contribution, updates can simply map its ID to [].
'''


class PreplannedUpdate(DeletionPlanUpdate):
    def __init__(self):
        super().__init__()
        self._planning_session = None

    def prepare_menu(self, call, session, plan, context, max_options=5):
        previous = self._planning_session
        self._planning_session = session
        try:
            return super().prepare_menu(call, session, plan, context, max_options)
        finally:
            self._planning_session = previous

    def decision_input(self, prompt, payload):
        session = self._planning_session
        if not session:
            return prompt, payload
        view, target = session['view'], payload['source']['id']
        editable = deletion_region(view, target)
        edges = [e for e in view['relations']['edges']
                 if {e['source_id'], e['target_id']} & editable]
        visible = editable | {e[k] for e in edges for k in ('source_id', 'target_id')}
        return prompt + PLAN, {**payload,
            'withdrawal_region': sorted(editable),
            'withdrawal_context': [node_payload(n) for i, n in view['items'].items()
                                   if i in visible and i != target],
            'withdrawal_edges': [[e[k] for k in ('source_id', 'target_id', 'kind')]
                                 for e in edges]}

    def compiled_decision(self, decision, source, context):
        decision = deepcopy(decision)
        raw = decision.pop('withdrawal', None)
        session = self._planning_session
        if not session:
            return decision
        base, target = session['view'], source['item_id']
        editable = deletion_region(base, target)
        event = {'kind': 'removal', 'target': target,
                 'dimension': decision['origin_dimension'],
                 'question': decision['question'], 'decision': decision}
        record = {'version': VERSION, 'inventory_id': base['inventory_id'],
                  'target': target, 'editable_ids': sorted(editable)}
        try:
            if not isinstance(raw, dict):
                raise ValueError('No conditional withdrawal supplied')
            raw = snapshot_response(raw, base)
            # The very same commit validator runs speculatively. No mutation,
            # model repair, exact-quote contract or premature graph replacement.
            transaction(session, base, event, raw, context, editable_ids=editable)
            record.update(status='ready', raw=raw)
        except (ValueError, TypeError, KeyError) as exc:
            record.update(status='fallback', reason=str(exc))
        decision['prepared_withdrawal'] = record
        return decision

    def _remove(self, call, session, basis, q, trigger, context):
        base = basis['view']
        target = q['item']['item_id']
        decision = q.get('scoped_permission_decision', {})
        prepared = decision.get('prepared_withdrawal', {})
        fallback = prepared.get('reason', 'no_prepared_menu_decision')
        if (prepared.get('version') == VERSION and prepared.get('status') == 'ready'
                and prepared.get('inventory_id') == base['inventory_id']
                and prepared.get('target') == target):
            event = {'kind': 'removal', 'target': target,
                     'dimension': q['dimension'], 'question': q['focus'],
                     'decision': decision}
            try:
                new = transaction(session, base, event, prepared['raw'], context,
                                  editable_ids=prepared['editable_ids'])
            except (ValueError, TypeError, KeyError) as exc:
                fallback = 'commit_validation: ' + str(exc)
            else:
                new['needs_final_render'] = True
                new = self._attach_plan(new)
                tx = new['transaction_audit'][-1]
                tx['execution'] = VERSION
                new.setdefault('reduction_audit', []).append({
                    'after_turn': len(session['turns']), 'trigger': trigger,
                    'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
                    'question': q['focus'], 'asked_dimension': q['dimension'],
                    'previous_request': session['current_request'],
                    'request': new['current_request'], 'source_inventory': base['inventory_id'],
                    'method': VERSION, 'analysis_mode': 'prepared_withdrawal',
                    'receipt': {'request': new['current_request'],
                        'drop_ids': [p for p, cs in tx['replacements'].items() if not cs],
                        'revise_ids': [p for p, cs in tx['replacements'].items() if cs]}})
                return new
        elif prepared.get('status') == 'ready':
            fallback = 'stale_prepared_inventory_or_target'
        new = super()._remove(call, session, basis, q, trigger, context)
        new['transaction_audit'][-1]['prepared_fallback'] = fallback
        return new
