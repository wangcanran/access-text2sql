"""Compact flat prompts; existing frame, relation and schema contracts."""
from ..model import parse_query_frame
from .analysis import inventory, interpretation, relations, validated_call, indexes
from .prompts import FLAT_COMPACT, FLAT_COMPACT_RELATIONS, FLAT_COMPACT_ANALYZE
from .relation_context import neighbor_context


def analyze_flat(call, payload, context):
    def check_frame(raw):
        frame = parse_query_frame(raw)
        if frame.original_text != payload['current_request']:
            raise ValueError('Segmentation changed current_request')
        if not frame.outputs and not frame.modifiers:
            raise ValueError('Expected at least one requirement fragment')
        return frame

    # Segmentation reads the current request; conversation is used in analysis.
    confirmed = [{k: turn[k] for k in ('target_fragment', 'target_role',
        'asked_dimension', 'answer', 'selected_options', 'selection_mode') if k in turn}
        for turn in payload['public_conversation']['turns']
        if turn['answer']['action'] in ('answer', 'choose')]
    frame = validated_call(call, 'compact_flat', FLAT_COMPACT,
        {**{k: v for k, v in payload.items() if k not in ('public_conversation', 'relation_context_turns')},
         'confirmed_updates': confirmed}, check_frame)
    edges = validated_call(call, 'compact_relations', FLAT_COMPACT_RELATIONS,
        frame.to_json(), lambda raw: relations(frame, raw))
    fragments = {r['item_id']: {**r, 'role': role}
                 for role, rows in (('output', frame.outputs), ('modifier', frame.modifiers))
                 for r in rows}

    def check_analysis(raw):
        if not isinstance(raw, dict) or set(raw) != {'items'} or not isinstance(raw['items'], list):
            raise ValueError('Expected items array')
        candidates, stripped = {}, []
        _, _, folded = indexes(context)
        for row in raw['items']:
            if not isinstance(row, dict) or 'field_candidates' not in row:
                raise ValueError('Every row needs field_candidates ([] when field is not missing)')
            row = dict(row)
            if row.get('grounded') is False and row.get('required_columns'):
                # Some responses use grounded=false to mean "not fully resolved"
                # while still returning exact schema dependencies. Preserve those
                # dependencies; missing decisions remain the unresolved state.
                row['grounded'] = True
            options = row['field_candidates']
            if not isinstance(options, list):
                raise ValueError('field_candidates must be an array')
            seen, parsed = set(), []
            for option in options:
                if (not isinstance(option, dict) or set(option) != {'column', 'label'}
                        or not isinstance(option['column'], str) or option['column'].casefold() not in folded
                        or not isinstance(option['label'], str) or not option['label'].strip()):
                    raise ValueError('A field candidate needs an actual schema column and a business label')
                col, label = folded[option['column'].casefold()], option['label'].strip()
                if col in seen:
                    continue
                seen.add(col)
                parsed.append({'column': col, 'label': label})
            if options and 'field' not in row.get('missing', []):
                raise ValueError('Only a missing field can have field_candidates')
            candidates[row['item_id']] = parsed
            stripped.append({k: v for k, v in row.items() if k != 'field_candidates'})
        result = interpretation(frame, {'items': stripped}, context)
        for row in result['items']:
            if 'question_focus' not in row:
                raise ValueError('Every item needs question_focus, including {} when clear')
            if row['status'] == 'clear' and not row['bindings'] and row['kind'] != 'instruction':
                raise ValueError('A clear attribute/calculation/relation needs schema dependencies')
            row['field_candidates'] = candidates[row['item_id']]
        return result

    analysis_payload = {k: v for k, v in payload.items() if k != 'relation_context_turns'}
    if 'relation_context_turns' in payload:
        evidence, _ = neighbor_context(payload['relation_context_turns'], fragments)
        analysis_payload['confirmed_neighbor_context'] = evidence
    result = validated_call(call, 'compact_analysis', FLAT_COMPACT_ANALYZE,
        {**analysis_payload, 'frame': frame.to_json(), 'relations': edges,
         'required_fragments': list(fragments.values())}, check_analysis)
    return frame, fragments, result, edges, inventory(frame)
