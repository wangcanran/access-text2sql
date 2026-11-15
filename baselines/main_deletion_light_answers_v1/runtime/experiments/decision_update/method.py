"""Opt-in fused update using the unchanged main validators and authorization."""
from copy import deepcopy

from ... import method as main
from .ledger import pending, discover, apply_effects, force_pending, answer_witnesses
from .prompts import FUSED


class DecisionUpdate(main.MainMethod):
    def __init__(self):
        super().__init__(clarity_first=True)

    def analyze_flat(self, call, payload, context):
        base, event = self._base, self._event
        if not base or event['kind'] == 'refresh':
            old = pending(base) if base else []
            def refreshed(stage, prompt, data):
                raw = call(stage, prompt, data)
                return force_pending(raw, old) if stage.startswith('progressive_analysis') else raw
            result = super().analyze_flat(refreshed, payload, context)
            return self.attach(result, old, base)
        # Ask the main implementation to prepare its exact region. Intercept at
        # its refine call, then validate a fused response by replaying both existing
        # validators. No source patch and no second model call are needed.
        old = pending(base)
        completed = None

        def intercepted(stage, prompt, local_payload):
            nonlocal completed
            if stage != 'progressive_refine':
                return call(stage, prompt, local_payload)
            editable = {r['item_id'] for r in local_payload['editable_fragments']}
            boundary = {i: n for i, n in base['items'].items() if i not in editable}
            removal = event.get('receipt', {})
            removal_quote = removal.get('scoped_permission_decision', {}).get('quote') or removal.get('target_fragment', '')
            combined = {**payload, **local_payload,
                'boundary_nodes': [main.analysis_row(n) for n in boundary.values()],
                'previous_relations': base['relations'],
                'pending_decisions': [r for r in old if r['owner_id'] in editable],
                'answer_witnesses': answer_witnesses(event), 'removal_quote': removal_quote,
                'public_conversation': self._history(self._session) if self._session else []}
            mapping_rules = main.frozen_module('prompts').FLAT_COMPACT_ANALYZE + main.MAPPING_RULES + main.ROLE_CONTRACT

            def check(raw):
                if not isinstance(raw, dict) or set(raw) != {'pieces', 'items', 'edges', 'decision_effects'}:
                    raise ValueError('Expected pieces, items, edges and decision_effects')
                pieces = deepcopy(raw['pieces'])
                ids = [r.pop('piece_id') for r in pieces]
                if ids != [f'p{i+1}' for i in range(len(ids))]:
                    raise ValueError('Use p1,p2,... in piece order')
                clean, choices_list = main.source_choices({'pieces': pieces}, 'pieces')
                piece_choices = dict(zip(ids, choices_list))
                for row in clean['pieces']:
                    row.pop('piece_id', None)
                fragments, _, _ = main.refine_inventory(clean, base, editable,
                    set(local_payload['removed_ids']), payload['current_request'])
                descendants = fragments[len(boundary):]
                translated = dict(zip(ids, [r['item_id'] for r in descendants]))
                children = {temp: (translated[temp], piece['parent_id']) for temp, piece in zip(ids, pieces)}
                survivors = apply_effects(old, raw['decision_effects'], event, children, editable)
                for temp, piece in zip(ids, pieces):
                    for decision in survivors:
                        if decision['owner_id'] == translated[temp] and not any(
                                e['dimension'] == decision['dimension'] and e['question'] == decision['question']
                                for e in piece['open_choices']):
                            piece['open_choices'].append({'dimension': decision['dimension'],
                                'span': piece['span'], 'question': decision['question']})
                mapping = {'items': deepcopy(raw['items']), 'edges': deepcopy(raw['edges'])}
                translated_choices = {translated[temp]: entries for temp, entries in piece_choices.items()}
                for row in mapping['items']:
                    if row['item_id'] not in translated:
                        raise ValueError('Each mapping row must reference a local piece ID')
                    row['item_id'] = translated[row['item_id']]
                for edge in mapping['edges']:
                    for key in ('source_id', 'target_id'):
                        edge[key] = translated.get(edge[key], edge[key])
                mapping = main.preserve_source_choices(mapping, translated_choices)
                force_pending(mapping, survivors)

                def replay(stage, prompt, data):
                    if stage == 'progressive_refine':
                        return {'pieces': [
                            {'parent_id': row['parent_id'], 'span': row['span'],
                             'open_choices': deepcopy(raw_piece['open_choices'])}
                            for row, raw_piece in zip(clean['pieces'], pieces)]}
                    if stage == 'progressive_analysis':
                        return deepcopy(mapping)
                    # The outer combined call owns the single bounded retry.
                    raise ValueError('Combined validation: ' + data.get('contract_error', stage))

                parsed = super(DecisionUpdate, self).analyze_flat(replay, payload, context)
                parsed = self.attach(parsed, survivors, base)
                parsed[4]['progressive']['decision_effects'] = deepcopy(raw['decision_effects'])
                parsed[4]['progressive']['update_experiment'] = 'fused_refine_mapping_v1'
                return parsed

            completed = main.frozen_module('analysis').validated_call(
                call, 'decision_update', mapping_rules + FUSED, combined, check)
            # Stop the outer dry run after successful, atomic validation.
            raise _Completed()

        try:
            result = super().analyze_flat(intercepted, payload, context)
        except _Completed:
            return completed
        # A removal deleting all editable nodes needs no local model call.
        removed = set(event.get('receipt', {}).get('receipt', {}).get('drop_ids', []))
        return self.attach(result, [r for r in old if r['owner_id'] not in removed], base)

    @staticmethod
    def attach(result, survivors, base):
        info = result[4]['progressive']
        items = {r['item_id']: {**r, 'span': result[1][r['item_id']]['span']}
                 for r in result[2]['items']}
        next_id = base.get('progressive', {}).get('next_decision_id', 1) if base else 1
        ledger, next_id = discover(items, info.get('source_open_choices', {}), survivors, next_id)
        info['decision_ledger'], info['next_decision_id'] = ledger, next_id
        return result


class _Completed(Exception):
    """Internal control transfer, deliberately outside the validation retry types."""
