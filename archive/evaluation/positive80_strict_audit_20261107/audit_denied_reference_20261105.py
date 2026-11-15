"""Candidate-blind audit. Preserve frozen labels and evaluate finite extra witnesses."""
import collections, hashlib, json, sqlite3, sys, time, traceback
from pathlib import Path
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.pushdown_projections import pushdown_projections
from sqlglot.schema import MappingSchema
import psycopg2
from psycopg2 import sql as psql

ROOT=Path('/path/to/access/collaboration/ppt_analysis_20261103_100147')
BASE=ROOT/'artifacts/main_analysis_server_20261102/requirement_retention'
OUT=Path('/path/to/access/output/denied_reference_audit_20261105')
DB=Path('/path/to/access/data/manual_flat_gold_v1_databases_20261031/db_assets')
sys.path.insert(0,str(ROOT/'scripts'))
from compile_retention_sqlite_capacity import compile_dependencies
from build_sql_output_capacity import pg_reads
from merge_retention_reference_serviceability import unauthorized
from audit_retention_feasibility_alignment import original_goal_query

def read(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))
def emit(name,x):
 with (OUT/name).open('a') as f:f.write(json.dumps(x,ensure_ascii=False,default=str)+'\n')
refs={r['id']:r for r in read(BASE/'semantic_v1/blind_request_reference.jsonl')}
plans={tuple(r['cluster_key']):r for r in read(BASE/'execution_plan_v23/goal_execution_plan.jsonl')}
anns=read(BASE/'subset_preserving_capacity_v1/denied_legal_output_annotations.jsonl')
units=read(BASE/'subset_preserving_capacity_v1/request_output_capacity.jsonl')
witnesses={(tuple(w['cluster_key']),w['gold_column']):w for w in read(BASE/'subset_preserving_capacity_v1/output_dependency_witnesses.jsonl')}
pgschemas=json.loads((BASE/'postgres_catalog_schemas_v1.json').read_text());schemas={}
native_cache={};deps_cache={};variants_cache={};result_cache={}

def schema(ck):
 ds,db,_=ck
 if ds=='livesqlbench':return pgschemas[db]
 if (ds,db) not in schemas:
  with sqlite3.connect((DB/ds/db/(db+'.sqlite')).resolve().as_uri()+'?mode=ro',uri=True) as c:
   schemas[ds,db]={t:{r[1]:r[2] or 'TEXT' for r in c.execute('PRAGMA table_info("'+t.replace('"','""')+'")')} for t, in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
 return schemas[ds,db]

def deps(ck,q):
 key=(tuple(ck[:2]),q)
 if key not in deps_cache:
  try:
   d=pg_reads(q,schema(ck)) if ck[0]=='livesqlbench' else compile_dependencies(q,DB/ck[0]/ck[1]/(ck[1]+'.sqlite'))
   deps_cache[key]={'reads':d}
  except Exception as e:deps_cache[key]={'error':type(e).__name__+': '+str(e)}
 return deps_cache[key]

def native(ck,q,grants):
 key=(tuple(ck[:2]),q,tuple(sorted(grants)))
 if key in native_cache:return native_cache[key]
 c=None;reads=[]
 try:
  if ck[0]!='livesqlbench':
   c=sqlite3.connect((DB/ck[0]/ck[1]/(ck[1]+'.sqlite')).resolve().as_uri()+'?mode=ro',uri=True,cached_statements=0)
   def auth(action,t,col,db,src):
    if action==sqlite3.SQLITE_READ:
     reads.append((t or '',col or ''))
     return sqlite3.SQLITE_DENY if unauthorized([(t or '',col or '')],grants,ck[0]) else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE) else sqlite3.SQLITE_DENY
   c.set_authorizer(auth);c.execute('EXPLAIN '+q).fetchall()
   out={'status':'authorized','engine':'sqlite_native_authorizer','reads':reads}
  else:
   c=psycopg2.connect(host='/var/run/postgresql',user='root',dbname=ck[1],connect_timeout=8,options='-c statement_timeout=5000');cur=c.cursor()
   role='audit_denied_20261105_temp'
   cur.execute('CREATE ROLE '+role+' NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT')
   cur.execute('GRANT USAGE ON SCHEMA public TO '+role)
   cur.execute("SELECT count(*) FROM information_schema.table_privileges WHERE grantee='PUBLIC' AND table_schema='public' AND privilege_type='SELECT'")
   if cur.fetchone()[0]:raise ValueError('PUBLIC SELECT privileges invalidate isolated role test')
   by=collections.defaultdict(list)
   for g in grants:
    t,col=g.split('.',1);by[t].append(col)
   for t,cols in by.items():
    if t not in schema(ck):raise ValueError('grant table missing '+t)
    if '*' in cols:cmd=psql.SQL('GRANT SELECT ON {} TO {}').format(psql.Identifier(t),psql.Identifier(role))
    else:cmd=psql.SQL('GRANT SELECT ({}) ON {} TO {}').format(psql.SQL(',').join(map(psql.Identifier,cols)),psql.Identifier(t),psql.Identifier(role))
    cur.execute(cmd)
   cur.execute('SET LOCAL ROLE '+role)
   cur.execute('EXPLAIN (FORMAT JSON) '+q);cur.fetchone()
   out={'status':'authorized','engine':'postgres_native_isolated_column_grants_rollback'}
 except Exception as e:out={'status':'blocked' if isinstance(e,psycopg2.errors.InsufficientPrivilege) or 'prohibited' in str(e) or 'not authorized' in str(e) else 'unresolved','error':type(e).__name__+': '+str(e)}
 finally:
  if c:
   try:c.rollback();c.close()
   except Exception:pass
 native_cache[key]=out;return out

def execute(ck,q):
 key=(tuple(ck[:2]),q)
 if key in result_cache:return result_cache[key]
 c=None
 try:
  if ck[0]=='livesqlbench':
   c=psycopg2.connect(host='/var/run/postgresql',user='root',dbname=ck[1],connect_timeout=8,options='-c statement_timeout=5000 -c default_transaction_read_only=on');cur=c.cursor()
  else:
   c=sqlite3.connect((DB/ck[0]/ck[1]/(ck[1]+'.sqlite')).resolve().as_uri()+'?mode=ro',uri=True)
   c.text_factory=lambda v:v.decode('utf8',errors='surrogateescape')
   deadline=time.monotonic()+5;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000);cur=c.cursor()
  cur.execute(q);rows=cur.fetchmany(100001)
  if len(rows)>100000:raise ValueError('row cap; incomplete')
  out={'rows':rows,'width':len(cur.description)}
 except Exception as e:out={'error':type(e).__name__+': '+str(e)}
 finally:
  if c:c.close()
 result_cache[key]=out;return out

def bag(rows):
 def n(x):
  if isinstance(x,(float,int)) or type(x).__name__=='Decimal':
   try:return ('number',round(float(x),8))
   except Exception:pass
  return (type(x).__name__,json.dumps(x,sort_keys=True,default=str))
 return collections.Counter(tuple(n(x) for x in row) for row in rows)

def subset_check(ck,q,col):
 gold=execute(ck,plans[ck]['gold_sql']);cand=execute(ck,q)
 if 'error' in gold or 'error' in cand:return {'status':'execution_unresolved','gold_error':gold.get('error'),'candidate_error':cand.get('error')}
 if cand['width']!=1:return {'status':'width_mismatch'}
 gb=bag([(r[col],) for r in gold['rows']]);cb=bag(cand['rows']);extra=sum((cb-gb).values())
 return {'status':'nonempty_subset' if cb and not extra else 'empty' if not cb else 'not_subset','gold_rows':len(gold['rows']),'candidate_rows':len(cand['rows']),'extra_rows':extra,'coverage':len(cand['rows'])/len(gold['rows']) if gold['rows'] else None}

def alternatives(ck,col):
 key=(ck,col)
 if key in variants_cache:return variants_cache[key]
 dialect='postgres' if ck[0]=='livesqlbench' else 'sqlite';w=witnesses[key];q=w.get('projection_query');vs=[]
 # Eliminate unused CTE/subquery output expressions, retaining all relational clauses.
 if q:
  try:
   t=qualify(sqlglot.parse_one(q,read=dialect),dialect=dialect,schema=MappingSchema(schema(ck),dialect=dialect,normalize=dialect!='postgres'),expand_stars=True,validate_qualify_columns=True)
   optimized=pushdown_projections(t).sql(dialect=dialect)
   if optimized!=q:vs.append(('dead_projection_elimination',optimized))
  except Exception as e:emit('transform_errors.jsonl',{'cluster_key':ck,'column':col,'error':str(e)})
  # Stronger branch of a positive WHERE OR keeps only original qualifying rows.
  try:
   t=sqlglot.parse_one(q,read=dialect)
   if isinstance(t,exp.Select) and t.args.get('where') and not any(t.args.get(k) for k in ('group','having','qualify','limit','offset')) and not any(isinstance(n,(exp.AggFunc,exp.Window)) and n.find_ancestor(exp.Select) is t for e in t.expressions for n in e.walk()):
    def positive_or(n):
     if isinstance(n,exp.Paren):return positive_or(n.this)
     if isinstance(n,exp.Or):return [n]+positive_or(n.this)+positive_or(n.expression)
     if isinstance(n,exp.And):return positive_or(n.this)+positive_or(n.expression)
     return []
    nodes=positive_or(t.args['where'].this)
    for i in range(min(8,len(nodes))):
     for side in ('this','expression'):
      v=t.copy();target=positive_or(v.args['where'].this)[i];target.replace(target.args[side].copy());vs.append(('positive_OR_branch_subset',v.sql(dialect=dialect)))
  except Exception as e:emit('transform_errors.jsonl',{'cluster_key':ck,'column':col,'error':str(e)})
 # A UNION arm supplies original rows. DISTINCT single-column arm is a bag subset.
 try:
  t=sqlglot.parse_one(plans[ck]['gold_sql'],read=dialect)
  if isinstance(t,exp.Union) and not t.args.get('limit') and not t.args.get('offset') and not any(isinstance(n,(exp.Except,exp.Intersect)) for n in t.walk()):
   def leaves(n):return leaves(n.this)+leaves(n.expression) if isinstance(n,exp.Union) else [n]
   for branch in leaves(t):
    if not isinstance(branch,exp.Select):continue
    b=branch.copy()
    if t.args.get('with_'):b.set('with_',t.args['with_'].copy())
    qq,proof=original_goal_query({'gold_sql':b.sql(dialect=dialect),'cluster_key':ck},{'gold_columns':[col]})
    if qq:
     b2=sqlglot.parse_one(qq,read=dialect)
     if not b2.args.get('limit') and not b2.args.get('offset'):
      b2.set('distinct',exp.Distinct());vs.append(('UNION_arm_distinct_subset',b2.sql(dialect=dialect)))
 except Exception as e:emit('transform_errors.jsonl',{'cluster_key':ck,'column':col,'error':str(e)})
 variants_cache[key]=list(dict.fromkeys(vs));return variants_cache[key]

def main():
 for name in ['positive_unit_audit.jsonl','negative_unit_search.jsonl','extra_witnesses.jsonl','transform_errors.jsonl','full_gold_native.jsonl']:(OUT/name).write_text('')
 positives=[];extra=[];done=0;full=[]
 for u in units:
  ck=tuple(u['cluster_key']);col=u['gold_column'];r=refs[u['request_id']];w=witnesses[ck,col];record={'request_id':r['id'],'cluster_key':ck,'gold_column':col,'old_capacity':u['capacity_lower']}
  if u['capacity_lower']:
   q=w.get('projection_query') or w.get('dependency_query');d=deps(ck,q)
   record.update(query=q,dependency_check=d,blocked_dependencies=unauthorized(d['reads'],r['allowed_columns'],ck[0]) if 'reads' in d else None,native=native(ck,q,r['allowed_columns']),result_subset=subset_check(ck,q,col))
   positives.append(record);emit('positive_unit_audit.jsonl',record)
  else:
   checks=[]
   for kind,q in alternatives(ck,col):
    d=deps(ck,q)
    if 'reads' not in d or unauthorized(d['reads'],r['allowed_columns'],ck[0]):continue
    v={**record,'transform':kind,'query':q,'reads':d['reads'],'native':native(ck,q,r['allowed_columns']),'result_subset':subset_check(ck,q,col)}
    checks.append(v)
    if v['native']['status']=='authorized' and v['result_subset']['status']=='nonempty_subset':extra.append(v);emit('extra_witnesses.jsonl',v)
   record.update(variants_tested=len(alternatives(ck,col)),permission_pass_candidates=len(checks),validated_extra=len([v for v in checks if v['native']['status']=='authorized' and v['result_subset']['status']=='nonempty_subset']))
   emit('negative_unit_search.jsonl',record)
  done+=1
  if done%100==0:write(OUT/'STATUS.json',{'units':done,'total':len(units),'positive_checked':len(positives),'extra_witnesses':len(extra)});print(done,len(extra),flush=True)
 for a in anns:
  if not a['has_legal_gold_output']:continue
  ck=tuple(a['cluster_key']);r=refs[a['request_id']];width=max(c for k,c in witnesses if k==ck)+1
  if len(a['authorized_gold_columns'])==width:
   v={'request_id':r['id'],'cluster_key':ck,'native':native(ck,plans[ck]['gold_sql'],r['allowed_columns'])};full.append(v);emit('full_gold_native.jsonl',v)
 oldpos={a['request_id'] for a in anns if a['has_legal_gold_output']}
 summary={'passed':True,'denied_requests':len(anns),'positive_requests':len(oldpos),'positive_units':len(positives),'positive_native_status':dict(collections.Counter(x['native']['status'] for x in positives)),'positive_subset_status':dict(collections.Counter(x['result_subset']['status'] for x in positives)),'full_gold_positive_requests':len(full),'full_gold_native_status':dict(collections.Counter(x['native']['status'] for x in full)),'extra_output_units':len({(x['request_id'],x['gold_column']) for x in extra}),'new_positive_requests':len({x['request_id'] for x in extra}-oldpos),'new_positive_clusters':len({tuple(x['cluster_key']) for x in extra if x['request_id'] not in oldpos}),'extra_transform_counts':dict(collections.Counter(x['transform'] for x in extra)),'candidate_outputs_read':False,'frozen_artifacts_changed':False,'search_exhaustive':False,'notes':'Finite witness search; no global impossibility claim. Native grants created transactionally and always rolled back. Per-column subset check does not certify joint tuple correspondence.'}
 write(OUT/'AUDIT_COMPLETE.json',summary);print(json.dumps(summary),flush=True)

if __name__=='__main__':
 try:main()
 except Exception:write(OUT/'FAILED.json',{'traceback':traceback.format_exc()});raise
