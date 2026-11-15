"""Permission-bounded candidate domains for clarification menus."""
from .analysis import indexes


def _meaning(context, column):
    value = context.get('column_language', {}).get(column, '')
    if isinstance(value, dict):
        return value.get('exact') or value.get('paraphrase') or value.get('broad') or ''
    if value:
        return value
    for table in context.get('tables', []):
        for col in table.get('columns', []):
            if table['name'] + '.' + col['name'] == column:
                return col.get('phrase', '')
    return ''


def field_domain(item, state, context, *, max_options=5):
    return field_domain_explicit(item, state.get('allowed', []), context, max_options=max_options)


def field_domain_explicit(item, allowed, context, *, max_options=5):
    """Return an internal domain and a public-safe option projection.

    The domain is scoped by grounding, not by every table in the database. A
    candidate is a single column here. For an explicit field gap in a calculation
    or modifier, it confirms only an operand, not the complete requirement.
    """
    _, columns, _ = indexes(context)
    scopes = item.get('scope_tables', [])
    allowed = set(allowed)
    # Check a definite single-table permission universe before assessing
    # expression support. This does not rule out cross-table implementations.
    if len(scopes) == 1 and not any(columns[c] == scopes[0] for c in allowed):
        return {'status': 'no_accessible_field', 'columns': [], 'size': 0,
                'baseline_size': len(allowed), 'reduction': 1.0 if allowed else 0.0,
                'scope': scopes[0], 'shown': False, 'options': [],
                'display_blockers': ['empty_domain'],
                'reason': 'No permitted column in the resolved single-table scope; cross-table options are not assessed.'}
    if item.get('kind') != 'attribute' and 'field' not in item.get('missing', []):
        return {'status': 'compound_candidate_required', 'columns': [], 'size': None,
                'baseline_size': None, 'reduction': None, 'shown': False,
                'reason': 'A raw-column choice cannot preserve a calculation or relation; a complete candidate is required.'}
    if len(scopes) != 1:
        return {'status': 'scope_required', 'columns': [], 'size': None,
                'baseline_size': None, 'reduction': None, 'shown': False,
                'reason': 'Choose an entity scope before offering fields from competing tables.'}
    scope = scopes[0]
    candidates = sorted(c for c in allowed if columns[c] == scope)
    # The baseline is the permission-bounded field universe. It is kept in the
    # audit plan only; public questions expose business meanings, never counts
    # or physical identifiers. A unique table scope is the deterministic
    # information that shrinks this universe.
    baseline_size = len(allowed)
    reduction = (1 - len(candidates) / baseline_size) if baseline_size else 0.0
    meanings = [_meaning(context, c) for c in candidates]
    shown = (bool(candidates) and len(candidates) <= max_options and
             all(str(x).strip() for x in meanings))
    distinct_labels = len({str(x).strip().casefold() for x in meanings}) == len(meanings)
    shown = shown and distinct_labels
    blockers = []
    if not distinct_labels:
        blockers.append('indistinguishable_business_labels')
    if not candidates:
        blockers.append('empty_domain')
    if len(candidates) > max_options:
        blockers.append('menu_too_large')
    if any(not str(x).strip() for x in meanings):
        blockers.append('missing_business_labels')
    return {'status': 'available' if candidates else 'no_accessible_field',
            'columns': candidates, 'size': len(candidates),
            'baseline_size': baseline_size, 'reduction': reduction, 'scope': scope,
            'shown': shown,
            'display_blockers': blockers,
            'options': [{'id': chr(65 + i), 'business_meaning': meanings[i]}
                        for i, c in enumerate(candidates)] if shown else [],
            'reason': 'bounded unique-scope domain' if shown else ', '.join(blockers)}


def validate_choice(plan, choices, state):
    if plan.get('action') != 'choose' or not isinstance(choices, list) or not choices:
        raise ValueError('Expected a candidate menu choice')
    options = {o['id']: o['column'] for o in plan.get('internal_options', [])}
    if not options or len(set(choices)) != len(choices) or not set(choices) <= options.keys():
        raise ValueError('Choice is outside the computed candidate domain')
    columns = [options[x] for x in choices]
    if not set(columns) <= set(state.get('allowed', [])):
        raise ValueError('Choice is not permitted')
    return columns
