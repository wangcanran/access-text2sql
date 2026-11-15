"""Full-row, partial-column result matching with SQL-native ordering tie groups."""
from collections import Counter
import time
from sqlglot import exp
from retention_result_matcher import project

def outer_order(tree):
    while isinstance(tree, (exp.Subquery, exp.Paren)) and not tree.args.get('order'):
        tree = tree.this
    return tree, tree.args.get('order')

def rank_query(tree, names, dialect):
    """Add native DENSE_RANK for tie membership, without touching candidate SQL."""
    tree, order = outer_order(tree)
    if order is None:
        raise ValueError('No outer order')
    order = order.copy()
    if isinstance(tree, exp.Select):
        outputs = tree.expressions
        # Prefer a wrapper when every ordering key is already in the output.
        # It preserves DISTINCT and LIMIT before calculating tie groups.
        outer_ordering=order.copy()
        can_wrap=len(outputs)==len(names)
        for term in outer_ordering.expressions:
            e=term.this;indices=[]
            if isinstance(e,exp.Literal) and not e.is_string and e.this.isdigit():
                indices=[int(e.this)-1]
            elif isinstance(e,exp.Column) and not e.table:
                indices=[i for i,name in enumerate(names) if name==e.name or (dialect=='sqlite' and name.lower()==e.name.lower())]
            if not indices:
                indices=[i for i,out in enumerate(outputs) if (out.this if isinstance(out,exp.Alias) else out)==e]
            if len(indices)!=1 or not 0<=indices[0]<len(names) or names.count(names[indices[0]])!=1:
                can_wrap=False;break
            term.set('this',exp.column(names[indices[0]],table='__q',quoted=True))
        if can_wrap:
            return ('SELECT __q.*, DENSE_RANK() OVER ('+outer_ordering.sql(dialect=dialect)+') AS "__safeex_order_group" '
                    'FROM ('+tree.sql(dialect=dialect)+') AS __q ORDER BY "__safeex_order_group"')
        aliases = {e.alias: e.this for e in outputs if isinstance(e, exp.Alias)}
        for term in order.expressions:
            e = term.this
            if isinstance(e, exp.Literal) and not e.is_string and e.this.isdigit():
                idx = int(e.this)-1
                if not 0 <= idx < len(outputs): raise ValueError('Order ordinal out of bounds')
                e = outputs[idx]
                e = e.this if isinstance(e, exp.Alias) else e
                term.set('this', e.copy())
            elif isinstance(e, exp.Column) and not e.table:
                matches = [value for alias,value in aliases.items() if alias==e.name or (dialect=='sqlite' and alias.lower()==e.name.lower())]
                if len(matches)>1: raise ValueError('Ambiguous ORDER BY alias')
                if matches: term.set('this', matches[0].copy())
        rank = exp.Window(this=exp.DenseRank(), order=order)
        result = tree.copy()
        result.append('expressions', exp.alias_(rank, '__safeex_order_group', quoted=True))
        return result.sql(dialect=dialect)
    if isinstance(tree, exp.SetOperation):
        for term in order.expressions:
            e = term.this
            if isinstance(e, exp.Literal) and not e.is_string and e.this.isdigit():
                idx = int(e.this)-1
                if not 0<=idx<len(names): raise ValueError('Set order ordinal out of bounds')
                if names.count(names[idx])!=1: raise ValueError('Ambiguous compound output name')
                term.set('this',exp.column(names[idx],table='__q',quoted=True))
            elif isinstance(e,exp.Column) and names.count(e.name)==1:
                term.set('this',exp.column(e.name,table='__q',quoted=True))
            else: raise ValueError('Unsupported compound ORDER BY expression')
        return ('SELECT __q.*, DENSE_RANK() OVER ('+order.sql(dialect=dialect)+') AS "__safeex_order_group" '
                'FROM ('+tree.sql(dialect=dialect)+') AS __q ORDER BY "__safeex_order_group"')
    raise ValueError('Unsupported ordered root '+type(tree).__name__)

def ordered_reference(original, augmented):
    if len(original)!=len(augmented): raise ValueError('Rank probe changed row count')
    if original:
        width=len(original[0])
        if any(len(r)!=width+1 for r in augmented): raise ValueError('Rank probe changed width')
        if Counter(project(original,list(range(width)))) != Counter(project([r[:-1] for r in augmented],list(range(width)))):
            raise ValueError('Rank probe changed Gold output multiset')
    # Rank is computed using native database equality, collation, NULL placement, and direction.
    augmented=sorted(augmented,key=lambda r:r[-1])
    rows=[r[:-1] for r in augmented]
    blocks=[]
    start=0
    for i in range(1,len(augmented)+1):
        if i==len(augmented) or augmented[i][-1]!=augmented[start][-1]:
            blocks.append((start,i));start=i
    return rows,blocks

def match_ordered(gold,candidate,legal,candidate_width,blocks,*,max_checks=20000,seconds=5.):
    if len(gold)!=len(candidate):
        return dict(lower=0.,upper=0.,reason='row_count_mismatch',checks=0)
    if not gold:
        return dict(lower=0.,upper=min(len(legal),candidate_width)/len(legal),reason='empty_reference_unresolved',checks=0)
    if not legal: raise ValueError('No legal reference columns')
    if sum(b-a for a,b in blocks)!=len(gold): raise ValueError('Incomplete tie blocks')
    def signature(rows,cols):
        projected=project(rows,cols)
        return [Counter(projected[a:b]) for a,b in blocks]
    gm={i:signature(gold,[i]) for i in legal}
    cm={j:signature(candidate,[j]) for j in range(candidate_width)}
    options={i:[j for j in cm if gm[i]==cm[j]] for i in legal}
    order=sorted(legal,key=lambda i:(len(options[i]),i))
    deadline=time.monotonic()+seconds
    best=[];checks=0;exhausted=True;cache={}
    def visit(pos,mapping,used):
        nonlocal best,checks,exhausted
        if not exhausted or len(mapping)+len(order)-pos<=len(best):return
        if time.monotonic()>deadline or checks>=max_checks:
            exhausted=False;return
        if mapping:
            gi=tuple(i for i,j in mapping);cj=[j for i,j in mapping]
            if gi not in cache:cache[gi]=signature(gold,list(gi))
            checks+=1
            if cache[gi]!=signature(candidate,cj):return
            if len(mapping)>len(best):best=list(mapping)
            if len(best)==min(len(legal),candidate_width):return
        if pos==len(order):return
        i=order[pos]
        for j in options[i]:
            if j not in used:visit(pos+1,mapping+[(i,j)],used|{j})
        visit(pos+1,mapping,used)
    visit(0,[],set())
    value=len(best)/len(legal)
    return dict(lower=value,upper=value if exhausted else min(len(legal),candidate_width)/len(legal),
                reason='ordered_scored' if exhausted else 'ordered_mapping_search_pending',checks=checks,
                gold_columns=[i for i,j in best],candidate_columns=[j for i,j in best],tie_blocks=len(blocks))

def self_test():
    import sqlite3,sqlglot
    db=sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE t(a TEXT,k INT); INSERT INTO t VALUES ("a",1),("b",1),("c",2),("d",NULL);')
    tests=['SELECT a,k FROM t ORDER BY k DESC,a ASC',
           'SELECT a,k AS z FROM t ORDER BY z DESC',
           'SELECT a,k FROM t ORDER BY 2 ASC',
           'SELECT k,COUNT(*) AS n FROM t GROUP BY k ORDER BY n DESC',
           'SELECT DISTINCT k FROM t ORDER BY k',
           'SELECT a FROM t ORDER BY k DESC',
           'SELECT a,k FROM t WHERE k=1 UNION SELECT a,k FROM t WHERE k=2 ORDER BY 2,1']
    for sql in tests:
        cur=db.execute(sql);gold=cur.fetchall();names=[d[0] for d in cur.description]
        augmented=db.execute(rank_query(sqlglot.parse_one(sql,read='sqlite'),names,'sqlite')).fetchall()
        ordered,blocks=ordered_reference(gold,augmented)
        assert match_ordered(ordered,gold,list(range(len(names))),len(names),blocks)['lower']==1
    gold=[('a',1),('b',1),('c',2)]
    blocks=[(0,2),(2,3)]
    assert match_ordered(gold,[('b',1),('a',1),('c',2)],[0,1],2,blocks)['lower']==1
    assert match_ordered(gold,list(reversed(gold)),[0,1],2,blocks)['upper']==0
    assert match_ordered(gold,[('a',),('b',),('c',)],[0,1],1,blocks)['lower']==.5
    assert match_ordered(gold,[('a',1),('a',1),('c',2)],[0,1],2,blocks)['lower']==.5
    assert match_ordered(gold,[('a',1),('c',2)],[0,1],2,blocks)['upper']==0
    assert match_ordered([(1,),(2,)],[(2,),(1,)],[0],1,[(0,2)])['lower']==1
    print('ORDER_MATCHER_SELF_TEST_PASS')

if __name__=='__main__':self_test()
