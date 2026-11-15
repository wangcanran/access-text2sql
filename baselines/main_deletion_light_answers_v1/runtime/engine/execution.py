"""Deterministic pre-Text2SQL dependency checks.

This module validates the semantic bindings produced by clarification. It is
not a SQL planner: passing means the requested bindings are permission-safe
and jointly connected by verified foreign keys, not that a generated query is
syntactically or semantically correct.
"""
from collections import defaultdict, deque

from .analysis import indexes
import sqlglot
from sqlglot import exp


def _authorized_graph(context, allowed):
    _, columns, _ = indexes(context)
    graph = defaultdict(set)
    for fk in context.get('foreign_keys', []):
        if fk.get('source') != 'sqlite_pragma':
            return None, 'unverified foreign-key metadata'
        source = fk['from_table']
        key = (source, fk.get('constraint_id'))
        # Build groups first so composite keys are all-or-nothing.
        graph.setdefault(('__fk__', key), []).append(fk)
    table_graph = defaultdict(set)
    for key, parts in graph.items():
        parts = sorted(parts, key=lambda x: x['sequence'])
        if [p['sequence'] for p in parts] != list(range(len(parts))):
            return None, 'invalid foreign-key sequence'
        targets = {p['to_table'] for p in parts}
        if len(targets) != 1:
            return None, 'invalid foreign-key target'
        deps = {p['from_table'] + '.' + p['from_column'] for p in parts}
        deps |= {p['to_table'] + '.' + p['to_column'] for p in parts}
        if not deps <= set(columns):
            return None, 'foreign key references unknown column'
        if not deps <= set(allowed):
            continue
        source, target = parts[0]['from_table'], parts[0]['to_table']
        table_graph[source].add(target)
        table_graph[target].add(source)
    return table_graph, None


def _connected(graph, tables):
    tables = set(tables)
    if len(tables) <= 1:
        return True
    seen, queue = {next(iter(tables))}, deque([next(iter(tables))])
    while queue:
        for target in graph[queue.popleft()] - seen:
            seen.add(target)
            queue.append(target)
    return tables <= seen


def connected_binding_witness(graph, columns, binding_options):
    """Find one mutually connected choice of complete bundles, without products.

    Each bundle must fit wholly inside one authorized component. Every item must
    have a bundle in the same component. This proves connectivity only, never
    semantic equivalence, cardinality preservation, or the correctness of a join.
    """
    components = {}
    for table in sorted(set(columns.values())):
        if table in components:
            continue
        seen, queue = {table}, deque([table])
        while queue:
            for neighbor in graph[queue.popleft()] - seen:
                seen.add(neighbor)
                queue.append(neighbor)
        for member in seen:
            components[member] = table
    choices = []
    common = set(components.values())
    for item in binding_options:
        by_component = {}
        for bundle in sorted(item['options']):
            locations = {components[columns[c]] for c in bundle}
            if len(locations) == 1:
                by_component.setdefault(next(iter(locations)), bundle)
        common &= set(by_component)
        choices.append((item['item_id'], by_component))
    if not binding_options:
        return {}
    if not common:
        return None
    component = min(common)
    return {iid: options[component] for iid, options in choices}


def validate_semantic_dependencies(state, context):
    """Return an auditable semantic authorization result for current state."""
    _, columns, _ = indexes(context)
    allowed = set(state.get('allowed', []))
    reasons = []
    required = set()
    tables = set()
    binding_options = []
    if state.get('dirty'):
        reasons.append({'code': 'pending_reanalysis', 'items': list(state['dirty'])})
    for iid, item in state.get('items', {}).items():
        if item.get('status') in ('unknown', 'unresolved', 'refused'):
            reasons.append({'code': 'incomplete_item', 'item_id': iid,
                            'status': item.get('status')})
            continue
        if item.get('status') != 'known':
            reasons.append({'code': 'invalid_status', 'item_id': iid,
                            'status': item.get('status')})
            continue
        bindings = item.get('allowed_bindings', [])
        if item.get('kind') != 'instruction' and not bindings:
            reasons.append({'code': 'missing_binding', 'item_id': iid})
        valid_bindings = []
        for binding in bindings:
            binding = set(binding)
            if not binding <= set(columns):
                reasons.append({'code': 'unknown_column', 'item_id': iid})
                continue
            if not binding <= allowed:
                reasons.append({'code': 'unauthorized_binding', 'item_id': iid})
                continue
            valid_bindings.append(sorted(binding))
            required |= binding
            tables |= {columns[c] for c in binding}
        if valid_bindings:
            binding_options.append({'item_id': iid, 'options': valid_bindings})
    graph, graph_error = _authorized_graph(context, allowed)
    witness = None
    if graph_error:
        reasons.append({'code': 'foreign_key_metadata', 'detail': graph_error})
    elif graph is not None:
        witness = connected_binding_witness(graph, columns, binding_options)
        if witness is None:
            reasons.append({'code': 'disconnected_tables', 'tables': sorted(tables)})
    return {
        'semantic_authorized': not reasons,
        'sql_authorized': False,
        'reasons': reasons,
        'required_columns': sorted(required),
        'binding_options': binding_options,
        'connectivity_witness': witness,
        'required_columns_scope': 'union of alternatives for audit; not all required together',
        'tables': sorted(tables),
        'join_paths_verified': not bool(graph_error) and not any(
            r['code'] == 'disconnected_tables' for r in reasons),
        'authorization_scope': 'semantic bindings and authorized FK connectivity only',
    }


def validate_sql_dependencies(sql, context, allowed, expected_columns=None,
                              expected_bindings=None):
    """Validate columns/tables used by a generated SQLite SQL statement.

    This is deliberately a dependency checker, not a SQL correctness scorer.
    It rejects unknown tables, ambiguous/unqualified columns, wildcards and
    unauthorized columns, and requires all referenced tables to be connected
    by fully authorized declared foreign keys. ``expected_columns`` is the
    semantic contract from clarification; missing contract columns are also
    reported, while authorized FK columns may be added by the SQL generator.
    """
    if not isinstance(sql, str) or not sql.strip():
        return {'sql_authorized': False, 'reasons': [{'code': 'empty_sql'}],
                'tables': [], 'columns': []}
    try:
        statements = sqlglot.parse(sql, read='sqlite')
        if len(statements) != 1:
            return {'sql_authorized': False,
                    'reasons': [{'code': 'multiple_statements'}],
                    'tables': [], 'columns': []}
        tree = statements[0]
    except Exception as exc:
        return {'sql_authorized': False,
                'reasons': [{'code': 'parse_error', 'detail': str(exc)}],
                'tables': [], 'columns': []}
    if not isinstance(tree, (exp.Select, exp.SetOperation)) or any(
            isinstance(node, (exp.DML, exp.DDL, exp.Into, exp.Command)) for node in tree.walk()):
        return {'sql_authorized': False,
                'reasons': [{'code': 'non_read_statement', 'statement_type': type(tree).__name__}],
                'tables': [], 'columns': []}
    _, schema_columns, _ = indexes(context)
    allowed = set(allowed or [])
    by_table = defaultdict(set)
    for qualified in schema_columns:
        table, column = qualified.split('.', 1)
        by_table[table].add(column)
    # Let sqlglot resolve lexical SQL scopes instead of sharing one alias map.
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.optimizer.scope import traverse_scope, Scope
    reasons = []
    if any(not isinstance(n.parent, exp.Count) for n in tree.find_all(exp.Star)):
        reasons.append({'code': 'wildcard_column'})
    folded = {c.casefold(): c for c in schema_columns}
    table_names = {t.casefold(): t for t in by_table}
    schema = {t: {c: 'UNKNOWN' for c in cols} for t, cols in by_table.items()}
    referenced, physical_tables = set(), set()
    try:
        qualified_tree = qualify(tree.copy(), dialect='sqlite', schema=schema,
                                 infer_schema=False, expand_stars=False)
        for scope in traverse_scope(qualified_tree):
            for source in scope.sources.values():
                if isinstance(source, exp.Table):
                    name = table_names.get(source.name.casefold())
                    if name is None or source.db or source.catalog:
                        raise ValueError('Unknown physical table: ' + source.sql())
                    physical_tables.add(name)
            for column in scope.columns:
                current = scope
                source = current.sources.get(column.table)
                while source is None and current.can_be_correlated and current.parent:
                    current = current.parent
                    source = current.sources.get(column.table)
                if isinstance(source, Scope):
                    continue  # qualification validates exposed outputs; inner scope owns dependencies
                if not isinstance(source, exp.Table):
                    raise ValueError('Unresolved column: ' + column.sql())
                key = (source.name + '.' + column.name).casefold()
                if key not in folded:
                    raise ValueError('Unknown column: ' + column.sql())
                referenced.add(folded[key])
    except Exception as exc:
        reasons.append({'code': 'unknown_column', 'detail': str(exc),
                        'phase': 'scope_resolution'})
    tables = sorted(physical_tables)
    unauthorized = sorted(referenced - allowed)
    if unauthorized:
        reasons.append({'code': 'unauthorized_column', 'columns': unauthorized})
    graph, graph_error = _authorized_graph(context, allowed)
    if graph_error:
        reasons.append({'code': 'foreign_key_metadata', 'detail': graph_error})
    elif graph is not None and not _connected(graph, tables):
        reasons.append({'code': 'disconnected_tables', 'tables': tables})
    expected = set(expected_columns or [])
    missing = sorted(expected - referenced)
    if missing:
        reasons.append({'code': 'missing_semantic_column', 'columns': missing})
    unsatisfied = []
    for requirement in expected_bindings or []:
        options = requirement.get('options', [])
        if options and not any(set(option) <= referenced for option in options):
            unsatisfied.append(requirement['item_id'])
    if unsatisfied:
        reasons.append({'code': 'missing_semantic_binding', 'items': sorted(unsatisfied)})
    return {'sql_authorized': not reasons, 'reasons': reasons,
            'tables': tables, 'columns': sorted(referenced),
            'expected_columns': sorted(expected), 'missing_expected_columns': missing}


def authorize_generated_sql(state, context, sql):
    """Join state-level semantic checks with actual SQL dependency checks."""
    semantic = validate_semantic_dependencies(state, context)
    sql_result = validate_sql_dependencies(sql, context, state.get('allowed', []),
                                           expected_bindings=semantic['binding_options'])
    reasons = list(semantic['reasons']) + list(sql_result['reasons'])
    return {
        'authorized': bool(semantic['semantic_authorized'] and sql_result['sql_authorized']),
        'semantic_authorized': semantic['semantic_authorized'],
        'sql_authorized': sql_result['sql_authorized'],
        'reasons': reasons,
        'required_columns': semantic['required_columns'],
        'binding_options': semantic['binding_options'],
        'sql_columns': sql_result['columns'],
        'sql_tables': sql_result['tables'],
        'missing_expected_columns': sql_result.get('missing_expected_columns', []),
    }
