"""Present whole-node replacement obligations independently of menu purposes."""
from copy import deepcopy


ANSWER_REPLACEMENT_CONTRACT = '''
WHOLE-NODE REPLACEMENT CONTRACT:
updates[parent] REPLACES the COMPLETE node in nodes, not only event.purpose.
event.purpose describes the question's local purpose; its role/kind never
reclassifies the containing node. A modifier question inside an output still
requires an output descendant preserving the requested returned result.
replacement_contract is code-owned. For must_keep_output parents retain the
returned meaning and returned binding claims from binding_claims; keep all
unaffected subject, condition and operand claims too. Write the selected answer
into that full result, or split with explicit edges and an output descendant.
Do not return only the selected operand/filter as the parent's whole replacement.
pending_decisions remain pending and must be routed within their own parent's
descendants. Only asked_decisions settle. A field selection supplies an operand
or attribute; it does not cancel a count, formula, date, threshold or subject.
'''


def prepare_answer_input(payload):
    result = deepcopy(payload)
    active = result.get('input', result)
    event = active.get('event', {})
    if event.get('kind') != 'answer':
        return result
    # A menu's remainder is a purpose-local deletion aid, not an exhaustive
    # description of what survives an ordinary answer. The full nodes/claims
    # below are authoritative. Do not alter the recorded event itself.
    event.get('purpose', {}).pop('remaining_requirements', None)
    asked = set(event.get('asked_decisions', []))
    protected = set(active.get('preserve_outputs', []))
    editable = set(active.get('editable_ids', []))
    active['replacement_contract'] = {
        n['id']: {'must_keep_output': n['id'] in protected,
                  'source_role': n['role'],
                  'pending_decisions': [d['id'] for d in n.get('decisions', []) if d['id'] not in asked]}
        for n in active.get('nodes', []) if n['id'] in editable}
    return result
