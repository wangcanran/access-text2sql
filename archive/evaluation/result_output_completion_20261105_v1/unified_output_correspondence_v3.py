"""Result-blind output-role correspondence, not a SQL equivalence claim.

Unique physical-column dependency sets identify the same requested quantity.
Numeric casts/arithmetic and aggregate variants may correspond; their actual
values, populations and multiplicities must still pass native result scoring.
Ambiguous roles and unsupported lineage remain pending.
"""
import json
import sqlglot
from sqlglot import exp


def role(signature):
    if signature is None:
        return None
    expression, _, context = signature.partition('|table_context=')
    try:
        tree = sqlglot.parse_one(expression,read='postgres')
    except sqlglot.errors.ParseError:
        try:tree = sqlglot.parse_one(expression,read='sqlite')
        except sqlglot.errors.ParseError:return None
    columns = tuple(sorted({(c.table.casefold(),c.name.casefold()) for c in tree.find_all(exp.Column)}))
    # COUNT(*) and COUNT(column) identify the same count target when their
    # physical table domain is unambiguous. NULL/distinct differences are
    # correctness questions, not reasons to omit the result comparison.
    root = tree.this if isinstance(tree,exp.Alias) else tree
    if isinstance(root,exp.Count):
        tables = {t for t,c in columns}
        if context:
            tables.update(v[-1].casefold() for v in json.loads(context))
        return ('count',tuple(sorted(tables))) if len(tables)==1 else None
    if columns:
        return ('column_dependencies',columns)
    return ('literal_expression',expression)


def augment_bindings(gold,candidate,mapping):
    mapping = dict(mapping)
    gr = [role(r.get('signature')) for r in gold]
    cr = [role(r.get('signature')) for r in candidate]
    for i,value in enumerate(gr):
        if i in mapping or value is None:
            continue
        if gr.count(value)!=1 or cr.count(value)!=1:
            continue
        j=cr.index(value)
        if j not in mapping.values():
            mapping[i]=j
    return mapping
