"""Deterministic clarification decision types.

The model identifies an unresolved dimension. The runner assigns the protocol
for that decision from the fragment's role and kind, so public wording and menu
cardinality cannot drift into a different semantic question.
"""


def classify(item, dimension):
    """Return the stable semantic type for one fragment decision."""
    if dimension == 'permission':
        return 'permission_choice'
    if dimension == 'scope':
        return 'scope_field'
    if dimension == 'operation':
        return 'operation'
    if dimension == 'value':
        return 'value'
    if dimension != 'field':
        raise ValueError('Unknown clarification dimension: ' + str(dimension))
    if item.get('role') == 'output' and item.get('kind') == 'attribute':
        return 'return_field'
    if item.get('kind') == 'calculation':
        # A formula with no operation is not a one-column operand choice yet.
        if 'operation' in item.get('missing', []):
            return 'calculation_specification'
        return 'calculation_operand'
    if item.get('role') == 'modifier':
        return 'predicate_field'
    return 'field'


def protocol(item, dimension, *, strict=False):
    """Return menu mode/cardinality for a classified decision.

    Strict permission decisions use the same field protocol as ordinary gaps.
    Legacy sessions retain complete local choices for reproducibility.
    """
    decision_type = classify(item, dimension)
    if strict and dimension == 'permission':
        return {**protocol(item, 'field', strict=True), 'type': 'permission_field'}
    if strict and dimension == 'scope':
        return {'type': 'business_scope', 'mode': 'scope', 'multi_select': False}
    if strict and dimension == 'field' and decision_type != 'return_field':
        return {'type': 'calculation_operand' if item.get('kind') == 'calculation' else decision_type,
                'mode': 'operand', 'multi_select': True}
    if decision_type == 'permission_choice':
        return {'type': decision_type, 'mode': 'complete', 'multi_select': False}
    if decision_type == 'return_field':
        return {'type': decision_type, 'mode': 'return_fields', 'multi_select': True}
    if decision_type in ('predicate_field', 'calculation_operand', 'scope_field'):
        return {'type': decision_type, 'mode': 'operand', 'multi_select': False}
    return {'type': decision_type, 'mode': 'open', 'multi_select': False}

