"""Main-method style, code-owned field candidate domains for tree leaves."""
from ...engine.candidates import field_domain_explicit

def _tables_for(focus, schema):
    known = {t['name'] for t in schema.get('tables', [])}
    cols = list(focus.get('scope_columns', [])) + list(focus.get('columns', []))
    return sorted({c.split('.', 1)[0] for c in cols if '.' in c and c.split('.', 1)[0] in known})

def domain(focus, schema, allowed=()):
    scope_tables = _tables_for(focus, schema)
    item = {'kind': 'attribute', 'missing': ['field'], 'scope_tables': scope_tables}
    return field_domain_explicit(item, allowed, schema, max_options=5)


def candidates(focus, schema, allowed=()):
    result = domain(focus, schema, allowed)
    if not result.get('shown'):
        return []
    return [{'label': option['business_meaning'], 'columns': [],
             '_internal_columns': [column]}
            for option, column in zip(result['options'], result['columns'])]
