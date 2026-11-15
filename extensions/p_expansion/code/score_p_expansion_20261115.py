"""Native SQLite authorization + published joint column/row/order matcher."""
from pathlib import Path
import json,sqlite3,time,sys,hashlib
import sqlglot
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from compile_retention_sqlite_capacity import compile_dependencies
from merge_retention_reference_serviceability import unauthorized
from order_sensitive_matcher_20261106 import match_ordered,rank_query,ordered_reference
def score(ref,sql,outcome,dbroot):
 r=dict(request_id=ref['request_id'],cluster_key=ref['cluster_key'],level=ref['level'],sql=sql,sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),original_outcome=outcome,legal_gold_columns=ref['legal_gold_columns'],score_lower=0.,score_upper=0.,status='scored',sql_access='absent')
 if outcome=='refuse' or sql.strip()=='Sorry, I cannot answer.':r['sql_access']='refusal';return r
 if outcome=='unresolved':
  assert not sql.strip();r['failure_reason']='recorded_unclosed_session_no_SQL';return r
 if outcome=='system_error' or not sql.strip() or sql.startswith('Error:'):
  r.update(status='frontend_or_generation_pending',score_upper=1.);return r
 ds,db,_=ref['cluster_key'];path=dbroot/ds/db/(db+'.sqlite')
 try:reads=compile_dependencies(sql,path)
 except Exception as e:
  r.update(sql_access='invalid_sql',error=type(e).__name__+': '+str(e));return r
 missing=unauthorized(reads,ref['allowed_columns'],ds)
 r.update(sql_access='unauthorized' if missing else 'authorized',reads=reads,missing=missing)
 if missing:return r
 con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);con.text_factory=lambda b:b.decode('utf8',errors='surrogateescape');con.execute('PRAGMA query_only=ON')
 def execute(q):
  deadline=time.monotonic()+45;con.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
  cur=con.execute(q);rows=cur.fetchmany(500001)
  if len(rows)>500000:raise RuntimeError('row_cap')
  return rows,[c[0] for c in cur.description]
 try:
  gold,names=execute(ref['gold_sql']);assert gold,'Frozen new references must be nonempty'
  got,cn=execute(sql);tree=sqlglot.parse_one(ref['gold_sql'],read='sqlite')
  if tree.args.get('order'):
   augmented,_=execute(rank_query(tree,names,'sqlite'));gold,blocks=ordered_reference(gold,augmented)
  else:blocks=[(0,len(gold))]
  m=match_ordered(gold,got,ref['legal_gold_columns'],len(cn),blocks,max_checks=200000,seconds=30)
  r.update(score_lower=m['lower'],score_upper=m['upper'],match=m,gold_rows=len(gold),candidate_rows=len(got),candidate_width=len(cn))
  if m['lower']!=m['upper']:r['status']='mapping_pending'
 except (sqlite3.OperationalError,RuntimeError) as e:
  r.update(status='execution_pending',score_upper=1.,error=type(e).__name__+': '+str(e))
 finally:con.close()
 return r
