"""Conservative end-to-end bridge for the isolated hybrid withdrawal pilot.

The live graph has applies_to/context edges, not ownership-certified requires
edges. Never equate an orphan modifier with a disposable dependency here.
"""
from .deletion_plan import DeletionPlanUpdate, code_deletable
from .lightweight import shared_review_ids


VERSION = 'hybrid_live_conservative_v2'
RENDER = '''Write one natural-language database query request. Return JSON only:
{"request":"..."}. returned_values determine the requested returned results.
nonreturned_requirements supply population restrictions, subjects, associations,
calculation qualifiers, grouping and ordering through the typed edges. They do
not request extra returned values. A field needed for filtering, joining or
ordering is not automatically returned. Preserve every active contribution,
scope, ambiguity, quantities, AND/OR and negation. Do not add values, formulas,
interpretations, withdrawal instructions or permission explanations.
'''


def code_eligible(view, target, decision):
    node = view['items'][target]
    if (decision.get('remaining_requirements') != []
            or decision.get('role') != node['role']):
        return False, 'whole_current_purpose_not_certified'
    edges = view['relations']['edges']
    if any(e['target_id'] == target and e['kind'] == 'context' for e in edges):
        return False, 'surviving_context_consumer'
    if shared_review_ids(view, {target}):
        return False, 'modifier_ownership_needs_semantic_review'
    if node['kind'] in ('relation', 'instruction'):
        return False, 'subject_or_result_anchor_needs_semantic_review'
    # An empty model-generated remainder is not a decomposition certificate
    # for a coarse compound owner. Require the existing current unit plan too.
    allowed, reason = code_deletable(view, target, decision)
    if not allowed:
        return False, reason
    return True, 'whole_purpose_without_dependent_nodes'


class HybridUpdate(DeletionPlanUpdate):
    def _remove(self, call, session, basis, q, trigger, context):
        base = basis['view']
        target = q['item']['item_id']
        allowed, reason = code_eligible(base, target, q.get('scoped_permission_decision', {}))
        # Parent implementation's orphan cascade is unreachable when a modifier
        # qualifies this target. All remaining nodes/edges are mechanically kept.
        if allowed:
            result = self._code_remove(session, basis, q, trigger, context, reason)
            result['transaction_audit'][-1]['execution'] = VERSION + ':code'
        else:
            event = {'kind': 'removal', 'target': target, 'dimension': q['dimension'],
                     'question': q['focus'], 'decision': q.get('scoped_permission_decision', {})}
            result = self._transaction(call, session, base, event, context)
            replacements = result['transaction_audit'][-1]['replacements']
            result['transaction_audit'][-1]['execution'] = VERSION + ':model'
            result.setdefault('reduction_audit', []).append({
                'after_turn': len(session['turns']), 'trigger': trigger,
                'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
                'question': q['focus'], 'asked_dimension': q['dimension'],
                'previous_request': session['current_request'], 'request': result['current_request'],
                'source_inventory': base['inventory_id'], 'method': VERSION,
                'scoped_permission_decision': event['decision'],
                'analysis_mode': 'hybrid_local_snapshot', 'fallback_reason': reason,
                'receipt': {'request': result['current_request'],
                    'drop_ids': [p for p, cs in replacements.items() if not cs],
                    'revise_ids': [p for p, cs in replacements.items() if cs]}})
        result['reduction_audit'][-1]['method'] = VERSION
        return result

    def render_session(self, call, session):
        def role_explicit(stage, prompt, payload):
            if stage.removesuffix('_contract_retry') == 'final_active_request':
                # Retries wrap the original input: keep the recovery envelope.
                payload = dict(payload)
                active = payload['input'] if stage.endswith('_contract_retry') else payload
                active = dict(active)
                nodes = active.pop('nodes')
                active['returned_values'] = [n for n in nodes if n['role'] == 'output']
                active['nonreturned_requirements'] = [n for n in nodes if n['role'] != 'output']
                if stage.endswith('_contract_retry'):
                    payload['input'] = active
                else:
                    payload = active
                prompt = RENDER
            return call(stage, prompt, payload)
        return super().render_session(role_explicit, session)
