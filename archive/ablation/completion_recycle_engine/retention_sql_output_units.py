"""SQL-only output-position inventory and conservative deterministic scoring.

No natural-language definitions or previous goal exclusions enter this module.
Column matching uses expression provenance, never a search for best result score.
The unchanged Gold query is always executed before projection.
"""
from collections import Counter
import hashlib
import json

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.schema import MappingSchema
from retention_output_bindings import output_signatures
from retention_result_matcher import compare_goal_subset


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode()).hexdigest()


def parse_readonly(sql, dialect):
    trees = [t for t in sqlglot.parse(sql, read=dialect) if t is not None and not isinstance(t,exp.Semicolon)]
    if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation, exp.Subquery)):
        raise ValueError('Expected a single read-only query')
    tree = trees[0]
    if any(isinstance(n, (exp.DDL, exp.DML, exp.Command, exp.Into)) for n in tree.walk()):
        raise ValueError('Non-read query rejected')
    return tree


def projections(tree):
    while isinstance(tree, (exp.Subquery, exp.Paren)):
        tree = tree.this
    if isinstance(tree, exp.Select):
        return tree.expressions
    if isinstance(tree, exp.SetOperation):
        left, right = projections(tree.this), projections(tree.expression)
        if len(left) != len(right):
            raise ValueError('Set operation output widths differ')
        return left
    raise ValueError('Unsupported root: ' + type(tree).__name__)


def has_star(outputs):
    return any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and e.is_star) for e in outputs)


def output_contract(sql, schema, dialect):
    tree = parse_readonly(sql, dialect)
    items = projections(tree)
    expanded = has_star(items)
    if expanded:
        tree = qualify(tree, dialect=dialect,
                       schema=MappingSchema(schema, dialect=dialect, normalize=dialect != 'postgres'),
                       expand_stars=True, validate_qualify_columns=True)
        items = projections(tree)
        if has_star(items):
            raise ValueError('Star expansion unresolved')
    return {'width': len(items), 'star_expanded': expanded,
            'query_kind': type(tree).__name__,
            'expressions': [e.sql(dialect=dialect) for e in items],
            'names': [e.alias_or_name for e in items]}


def conservative_signatures(sql, schema, dialect):
    tree = parse_readonly(sql, dialect)
    # The older resolver erases aliases of base tables; two instances of the
    # same table would lose their role in a self join. Do not certify those.
    for scope in traverse_scope(tree):
        tables = [s.name for s in scope.sources.values() if isinstance(s, exp.Table)]
        if len(tables) != len(set(tables)):
            raise ValueError('Self-join role correspondence requires explicit binding')
    signatures=output_signatures(sql, schema, dialect)
    for row in signatures:
        s=row.get('signature')
        if s:
            expression=sqlglot.parse_one(s,read=dialect)
            if any(isinstance(n,exp.AggFunc) for n in expression.walk()) and not any(isinstance(n,exp.Column) for n in expression.walk()):
                # COUNT(*) has no column lineage. Include the query's table
                # multiset; expression identity alone cannot identify its object.
                tables=sorted((s.catalog,s.db,s.name) for scope in traverse_scope(tree)
                    for s in scope.sources.values() if isinstance(s,exp.Table))
                row['signature']=s+'|table_context='+json.dumps(tables,ensure_ascii=True)
    return signatures


def bind_positions(gold, candidate):
    """Unique expression signatures only; one candidate position used once.

    Duplicate expressions on either side stay unresolved rather than assigning
    one candidate column to several Gold units. Aliases alone never bind.
    """
    gs = [r.get('signature') for r in gold]
    cs = [r.get('signature') for r in candidate]
    gc, cc = Counter(gs), Counter(cs)
    result = []
    for i, s in enumerate(gs):
        if s is None:
            status, j = 'gold_expression_unresolved', None
        elif gc[s] != 1 or cc[s] > 1:
            status, j = 'duplicate_expression_ambiguous', None
        elif cc[s] == 1:
            status, j = 'expression_bound', cs.index(s)
        else:
            status, j = 'expression_not_bound', None
        result.append({'gold_column': i, 'candidate_column': j, 'mapping_status': status})
    return result


def score_positions(gold_rows, candidate_rows, bindings):
    rows = []
    for b in bindings:
        row = dict(b)
        j = b['candidate_column']
        if j is None:
            row.update(status='mapping_unresolved', recall=None, precision=None,
                       subset_valid=None, valid_subset_coverage=None)
        else:
            r = compare_goal_subset(gold_rows, candidate_rows, [b['gold_column']], [j],
                                    mapping_reviewed=True, empty_mapping_supported=False)
            row.update(status=r['status'], recall=r['retention'], precision=r['precision'],
                       matched_rows=r.get('matched_rows'), gold_rows=r.get('gold_rows'),
                       candidate_rows=r.get('candidate_rows'), extra_rows=r.get('extra_candidate_rows'))
            row['subset_valid'] = (r['extra_candidate_rows'] == 0) if r['status'] == 'scored' else None
            row['valid_subset_coverage'] = (r['retention'] if row['subset_valid'] else 0.) if r['status'] == 'scored' else None
        rows.append(row)
    return rows


def joint_diagnostic(gold_rows, candidate_rows, bindings):
    bound = [b for b in bindings if b['candidate_column'] is not None]
    if not bound:
        return {'status': 'no_bound_outputs'}
    r = compare_goal_subset(gold_rows, candidate_rows, [b['gold_column'] for b in bound],
                            [b['candidate_column'] for b in bound], mapping_reviewed=True)
    return {**r, 'bound_output_count': len(bound),
            'full_gold_output_width_matched': len(bound) == len(bindings)}
