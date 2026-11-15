"""Deterministic consistency checks; no text interpretation or model calls."""


def has_query_objective(items):
    """A discourse instruction alone does not supply a returned contribution.

    Uncertain attributes/calculations still count: this is not a completeness or
    permission test and must not reject a query simply because it is vague.
    """
    return any(n.get('role') == 'output' and n.get('kind') != 'instruction'
               for n in items)


def validate_removal_objective(raw, graph, decision, target_id):
    """Reject a receipt contradicting an already qualified final-output decision.

    The decision describes a local purpose, not an entire compound container.
    Only its explicit empty remainder permits retiring that container. Historical
    evidence and modifiers cannot independently introduce a new returned value.
    """
    if not decision or decision.get('role') != 'output' or decision.get('remaining_requirements') != []:
        return
    active = [n for n in graph['nodes'] if not n.get('evidence_only')]
    owner = next((n for n in active if n['item_id'] == target_id), None)
    if (owner is None or owner['role'] != 'output'
            or not decision.get('quote') or decision['quote'] not in owner['span']):
        return
    outputs = {n['item_id'] for n in active if n['role'] == 'output'}
    if outputs != {target_id}:
        return
    if raw['request'].strip() or target_id not in raw['drop_ids']:
        raise ValueError('The local output decision has no independent remainder and no other '
                         'current output survives. Return an empty request and drop its owner; '
                         'a heading or unspecified version of the removed output is not a remainder.')
