"""Declared dependencies and verified authorized connectivity."""
import re
from ...engine.analysis import indexes
from ...engine.execution import _authorized_graph, _connected
from .state import require, frontier


def check_columns(columns, context):
    known = indexes(context)[1]
    require(isinstance(columns, list) and all(isinstance(c, str) for c in columns),
            'Invalid columns: expected an array of schema-qualified strings, e.g. ["loan.loan_id"], not objects or expressions')
    require(len(columns) == len(set(columns)), 'Invalid columns: duplicate strings are not allowed')
    require(set(columns) <= set(known), f'Invalid columns: unknown schema fields {sorted(set(columns) - set(known))}')


def safe(columns, context, allowed):
    check_columns(list(columns), context)
    g, error = _authorized_graph(context, allowed)
    return not error and set(columns) <= set(allowed) and _connected(g, {indexes(context)[1][c] for c in columns})


def node_dependencies(node, context):
    """Bindings include private scope and explicitly named computation operands."""
    known = indexes(context)[1]
    def operand_columns(value):
        if isinstance(value, str):
            return {column for column in known
                    if re.search(r'(?<![\w.])' + re.escape(column) + r'(?![\w.])', value)}
        if isinstance(value, dict):
            return set().union(set(), *(operand_columns(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(set(), *(operand_columns(v) for v in value))
        return set()
    return (set(node['columns']) | set(node.get('scope_columns', []))
            | operand_columns(node.get('computation', {})))


def dependency_nodes(state, owner):
    ids = {owner}
    changed = True
    while changed:
        before = len(ids)
        for r in state['relations']:
            if r['status'] != 'confirmed':
                continue
            if r['kind'] == 'applies_to' and r['target'] in ids:
                ids.add(r['source'])
            if r['kind'] == 'depends_on' and r['source'] in ids:
                ids.add(r['target'])
        changed = len(ids) != before
    return ids


def dependencies(state, owner, context=None, *, exclude_owner=False):
    leaves = frontier(state)
    ids = dependency_nodes(state, owner)
    if exclude_owner:
        ids.discard(owner)
    return set().union(set(), *(node_dependencies(leaves[i], context) if context is not None
                               else set(leaves[i]['columns']) | set(leaves[i].get('scope_columns', []))
                               for i in ids))
