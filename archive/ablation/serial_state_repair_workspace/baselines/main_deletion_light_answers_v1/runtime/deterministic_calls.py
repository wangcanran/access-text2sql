"""Only replace model work whose result is fixed by the existing contract."""
from copy import deepcopy

from .model import parse_query_frame


def single_fragment_relations(stage, payload):
    if stage != 'compact_relations' or not isinstance(payload, dict):
        return None
    # to_json includes gaps, while the segmentation parser does not accept it.
    if set(payload) != {'original_text', 'outputs', 'modifiers', 'gaps'} or payload['gaps'] != []:
        return None
    try:
        frame = parse_query_frame({k: v for k, v in payload.items() if k != 'gaps'})
    except (TypeError, ValueError):
        return None
    if len(frame.outputs) + len(frame.modifiers) != 1:
        return None
    # analysis.relations forbids a == b, so no valid edge is possible.
    return {'edges': []}


def wrap(call, audit, on_skip=None):
    def invoke(stage, prompt, payload):
        result = single_fragment_relations(stage, payload)
        if result is None:
            return call(stage, prompt, payload)
        if on_skip is not None:
            on_skip(stage, prompt, payload, result)
        audit.append({'stage': stage, 'reason': 'one_node_no_self_edges',
                      'payload': deepcopy(payload), 'result': deepcopy(result)})
        return result
    return invoke
