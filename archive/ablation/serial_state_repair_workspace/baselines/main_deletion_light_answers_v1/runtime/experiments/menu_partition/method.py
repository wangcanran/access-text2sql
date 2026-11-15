"""Materialize a decision's independent remainder BEFORE exposing its menu."""
from copy import deepcopy

from ..cancel_answer.method import CancelAnswer, cancelling
from ..decision_update.ledger import pending
from ..decision_update.lightweight import transaction

VERSION = 'light_menu_partition_v1'
PARTITION = '''
MENU-TIME PARTITION: In this SAME response, when source contains independent
remainders, also return partition:{"nodes":[...],"edges":[...]}.
Use node shape {"id":"ask","text":"...","role":"output|modifier",
"kind":"attribute|calculation|relation|instruction","tables":["table"],
"columns":["table.column"],"questions":{"value":"..."},"pending":["d1"]}.
Exactly one node has id ask: the local requirement identified by the decision.
Other nodes materialize EVERY independent remainder, not just a summary list.
Do not add a profile/entity heading as an extra returned value. Separate a shared
subject restriction as a modifier when needed, attaching it to surviving outputs.
Edges are [local_id_or_boundary_id,local_id_or_boundary_id,applies_to|context].
Carry all old dependencies to the children that actually need them; include join
keys. Distinguish shared anchors from output-exclusive calculations. Do not copy
a deleted/absent anchor back from read_only_context. Keep subjects explicit.
Route each input pending decision to its appropriate child via pending:[id].
This is a meaning-preserving partition BEFORE any answer; resolve NO question,
invent NO values, and discard NO requirement. Preserve scope, AND/OR and roles.
Keep output alternatives/compound Boolean predicates together when splitting
would change their meaning. A single local requirement uses partition:null and
remaining_requirements:[]. No extra call or post-answer decomposition is needed.
'''


class _PartitionReady(Exception):
    def __init__(self, session):
        self.session = session


class MenuPartition(CancelAnswer):
    def prepare_menu(self, call, session, plan, context, max_options=5):
        previous = getattr(self, '_partition_session', None)
        self._partition_session = session
        try:
            return super().prepare_menu(call, session, plan, context, max_options)
        except _PartitionReady as ready:
            # Identity changed: discard the old plan and let the normal planner
            # choose from the new inventory. This is NOT a user interaction.
            return ready.session, None
        finally:
            self._partition_session = previous

    def decision_input(self, prompt, payload):
        payload = deepcopy(payload)
        base = self._partition_session['view']
        payload['pending_decisions'] = [
            {'id': d['decision_id'], 'dimension': d['dimension'], 'question': d['question']}
            for d in pending(base) if d['owner_id'] == payload['source']['id']]
        return prompt + PARTITION, payload

    def compiled_decision(self, decision, source, context):
        split = decision.pop('partition', None)
        if split is None:
            if decision.get('remaining_requirements'):
                raise ValueError('Independent remainders need materialized partition nodes')
            return decision
        if not isinstance(split, dict) or set(split) != {'nodes', 'edges'}:
            raise ValueError('Partition needs nodes and edges')
        nodes = deepcopy(split['nodes'])
        if not isinstance(nodes, list) or len(nodes) < 2:
            raise ValueError('Partition needs an asked node and independent remainder')
        asked = [n for n in nodes if isinstance(n, dict) and n.get('id') == 'ask']
        if len(asked) != 1:
            raise ValueError('Partition needs exactly one ask alias')
        # One authoritative meaning for menu, update, and cancellation. Never
        # ask the model to repeat identical strings as an interface contract.
        asked[0].update(text=decision['requirement'], role=decision['role'],
                        kind=decision['kind'], tables=decision['scope_tables'],
                        columns=decision['required_columns'])
        asked[0]['candidates'] = {c['column']: c['label'] for c in decision['field_candidates']}
        session = self._partition_session
        event = {'kind': 'answer', 'target': source['item_id'], 'dimension': 'partition',
                 'question': 'Partition without answering any decision',
                 'turn': {'decision_ids': [], 'answer': {'action': 'partition', 'text': ''}}}
        raw = {'updates': {source['item_id']: nodes}, 'edges': split['edges']}
        result = transaction(session, session['view'], event, raw, context,
                             editable_ids={source['item_id']})
        children = result['transaction_audit'][-1]['replacements'][source['item_id']]
        target = children[nodes.index(asked[0])]
        prepared = deepcopy(decision)
        prepared['remaining_requirements'] = []  # Now independent graph nodes.
        result.setdefault('partition_decisions', {})[target] = prepared
        result.setdefault('partition_audit', []).append({
            'source': source['item_id'], 'asked': target, 'children': children,
            'decision': deepcopy(decision), 'model_calls_added': 0})
        decision['_partition_commit'] = result
        return decision

    def commit_local(self, session, base, event, raw, context, editable):
        return transaction(session, base, event, raw, context, editable_ids=editable,
                           keep_shared=event['kind'] == 'removal')

    def _transaction(self, call, session, base, event, context):
        def scoped_call(stage, prompt, payload):
            if cancelling(event):
                prompt += ('\nCancellation write authority is ONLY editable_ids. '
                           'Other independent outputs and shared qualifiers are read-only. '
                           'Code preserves unchanged shared qualifiers and surviving edges; '
                           'do not recopy boundary-to-boundary edges. '
                           'Remove exclusive qualifications inside editable_ids.\n')
            return call(stage, prompt, payload)
        return super()._transaction(scoped_call, session, base, event, context)

    def compile_decision(self, call, payload, context, *, field, scope=False):
        session = self._partition_session
        cached = session.get('partition_decisions', {}).get(payload['source']['item_id'])
        if cached:
            return deepcopy(cached)
        decision = super().compile_decision(call, payload, context, field=field, scope=scope)
        result = decision.pop('_partition_commit', None)
        if result is not None:
            # Raised OUTSIDE validated_call: an accepted identity transition
            # must never be treated as a model-contract failure/retry.
            raise _PartitionReady(result)
        return decision

    def edit_boundary(self, base, event):
        if not cancelling(event):
            return super().edit_boundary(base, event)
        ids = {event['target']}
        # Only qualifications whose EVERY attachment is cancelled are writable.
        # Shared qualifiers and independent outputs remain read-only boundary.
        while True:
            previous = set(ids)
            for iid, node in base['items'].items():
                targets = {e['target_id'] for e in base['relations']['edges']
                           if e['source_id'] == iid and e['kind'] == 'applies_to'}
                if node['role'] == 'modifier' and targets and targets <= ids:
                    ids.add(iid)
            if ids == previous:
                return ids
