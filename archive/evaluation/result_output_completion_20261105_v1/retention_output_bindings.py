"""Deterministic output-expression bindings, including named CTE projections.

Bindings identify corresponding outputs only. Execution-result comparison checks
the actual population, associations and values; a binding is not correctness.
"""
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import build_scope,Scope
from sqlglot.schema import MappingSchema

class UnresolvedBinding(ValueError):pass

def output_signatures(sql,schema,dialect='sqlite'):
    trees=[t for t in sqlglot.parse(sql,read=dialect) if t is not None]
    if len(trees)!=1 or not isinstance(trees[0],(exp.Select,exp.SetOperation)):
        raise UnresolvedBinding('Only a single SELECT output scope is supported')
    if any(isinstance(n,(exp.DDL,exp.DML,exp.Command,exp.Into)) for n in trees[0].walk()):
        raise UnresolvedBinding('Non-read query')
    if isinstance(trees[0],exp.SetOperation):
        tree=trees[0]
        # EXCEPT and INTERSECT can only retain values from the left output.
        # UNION may introduce other expressions, so require common bindings.
        left=output_signatures(tree.this.sql(dialect=dialect),schema,dialect)
        if isinstance(tree,(exp.Except,exp.Intersect)):return left
        right=output_signatures(tree.expression.sql(dialect=dialect),schema,dialect)
        if len(left)!=len(right):raise UnresolvedBinding('Set output width mismatch')
        return [a if a['signature'] is not None and a['signature']==b['signature'] else
            dict(signature=None,status='mapping_pending',reason='Union branches have different output expressions')
            for a,b in zip(left,right)]
    # This is output correspondence only; row populations are checked by the
    # execution comparator. Do not bind filter literals as output columns
    # (SQLite also permits double-quoted string literals in filters).
    for select in trees[0].find_all(exp.Select):
        for clause in ('where','having','qualify','order','limit','offset'):
            select.set(clause,None)
    tree=qualify(trees[0],dialect=dialect,
        schema=MappingSchema(schema,dialect=dialect,normalize=dialect!='postgres'),
        expand_stars=True,validate_qualify_columns=True)
    root=build_scope(tree)
    def resolve(expression,scope,stack=frozenset()):
        if isinstance(expression,exp.Alias):return resolve(expression.this,scope,stack)
        if isinstance(expression,(exp.Select,exp.Subquery,exp.SetOperation)):
            raise UnresolvedBinding('Scalar subquery or set-operation output requires an explicit mapping')
        if isinstance(expression,exp.Column):
            source=scope.sources.get(expression.table)
            if isinstance(source,exp.Table):
                return exp.column(expression.name,table=source.name,quoted=True)
            if isinstance(source,Scope):
                key=(id(source),expression.name)
                if key in stack:raise UnresolvedBinding('Recursive output binding')
                matches=[e for e in source.expression.expressions if e.alias_or_name==expression.name]
                if len(matches)!=1:raise UnresolvedBinding('Ambiguous nested output')
                return resolve(matches[0],source,stack|{key})
            raise UnresolvedBinding('Unbound output column')
        clone=expression.copy();clone.comments=None
        for name,value in expression.args.items():
            if isinstance(value,exp.Expression):clone.set(name,resolve(value,scope,stack))
            elif isinstance(value,list):clone.set(name,[resolve(v,scope,stack) if isinstance(v,exp.Expression) else v for v in value])
        return clone
    signatures=[]
    for output in root.expression.expressions:
        try:signatures.append(dict(signature=resolve(output,root).sql(dialect=dialect),status='bound'))
        except UnresolvedBinding as e:signatures.append(dict(signature=None,status='mapping_pending',reason=str(e)))
    return signatures

def map_goal_outputs(reference_signatures,candidate_signatures,required_indices):
    """Map a predefined joint goal without reusing one candidate output twice."""
    mapping=[];used=set()
    for i in required_indices:
        target=reference_signatures[i]['signature']
        if target is None:return dict(status='reference_mapping_pending',candidate_columns=None)
        matches=[j for j,v in enumerate(candidate_signatures) if v['signature']==target and j not in used]
        if not matches:return dict(status='required_output_not_bound',candidate_columns=None)
        j=matches[0];used.add(j);mapping.append(j)
    return dict(status='output_expression_bound',candidate_columns=mapping)
