"""Fresh reference-only audit: never read model candidates; never relabel originals."""
from pathlib import Path
from collections import Counter,defaultdict
import sys,json,time,hashlib,traceback,sqlite3
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.pushdown_projections import pushdown_projections
from sqlglot.schema import MappingSchema
import psycopg2

sys.path.insert(0,'/path/to/backend-environment')
import audit_denied_reference_20261105 as a
from order_sensitive_matcher_20261106 import outer_order,rank_query,ordered_reference
OUT=Path('/path/to/access/output/positive80_strict_audit_20261107')
OUT.mkdir(exist_ok=True)
if (OUT/'COMPLETE.json').exists():raise RuntimeError('Completed audit exists; use a new output directory')
a.OUT=OUT
def write(name,v):
 p=OUT/name;t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,ensure_ascii=True,indent=2,default=str));t.replace(p)
def append(name,v):
 with (OUT/name).open('a') as f:f.write(json.dumps(v,ensure_ascii=True,default=str)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
paths=[a.BASE/'semantic_v1/blind_request_reference.jsonl',a.BASE/'execution_plan_v23/goal_execution_plan.jsonl',a.BASE/'subset_preserving_capacity_v1/denied_legal_output_annotations.jsonl',a.BASE/'subset_preserving_capacity_v1/request_output_capacity.jsonl',a.BASE/'subset_preserving_capacity_v1/output_dependency_witnesses.jsonl']
source_hashes={str(p):sha(p) for p in paths}
refs=[r for r in a.refs.values() if r['original_permission']=='denied'];ann={r['request_id']:r for r in a.anns};pos={rid for rid,r in ann.items() if r['has_legal_gold_output']}
assert len(refs)==1398 and len(pos)==80 and set(ann)=={r['id'] for r in refs}
wrows=a.read(paths[-1]);witness_groups=defaultdict(list)
for r in wrows:witness_groups[tuple(r['cluster_key']),r['gold_column']].append(r)
write('PROTOCOL.json',dict(source_hashes=source_hashes,denied_requests=1398,fixed_positive_unique_ids=80,
 positive_source_clusters=len({tuple(a.refs[rid]['cluster_key']) for rid in pos}),
 source_witness_rows=len(wrows),unique_witness_keys=len(witness_groups),
 duplicate_witness_keys=[dict(cluster_key=k[0],gold_column=k[1],versions=len(v)) for k,v in witness_groups.items() if len(v)>1],
 candidate_outputs_read=False,model_calls=0,original_labels_changed=False,
 search='Native full Gold on all1398; all2234 original output units using recorded witnesses and schema-qualified dead-projection removal; exact joint row multiplicities and Gold order ties. No filter/order relaxation credited.',
 completeness_scope='Complete enumeration of original output units and this declared transformation family, not an impossibility proof over all SQL rewrites.',
 execution_limits=dict(seconds_per_query=8,max_rows=100000),script_sha256=sha(Path(__file__))))
exec_cache={};order_cache={};variant_cache={}
def execute(ck,q):
 key=tuple(ck[:2]),q
 if key in exec_cache:return exec_cache[key]
 c=None
 try:
  if ck[0]=='livesqlbench':
   c=psycopg2.connect(host='/var/run/postgresql',user='root',dbname=ck[1],connect_timeout=8,options='-c statement_timeout=8000 -c default_transaction_read_only=on')
  else:
   c=sqlite3.connect((a.DB/ck[0]/ck[1]/(ck[1]+'.sqlite')).resolve().as_uri()+'?mode=ro',uri=True)
   c.text_factory=lambda x:x.decode('utf8',errors='surrogateescape');end=time.monotonic()+8;c.set_progress_handler(lambda:int(time.monotonic()>end),1000)
  cur=c.cursor();cur.execute(q);names=[d[0] for d in cur.description];rows=cur.fetchmany(100001)
  if len(rows)>100000:raise ValueError('row_cap_incomplete')
  v=dict(rows=rows,names=names)
 except Exception as e:v=dict(error=type(e).__name__+': '+str(e))
 finally:
  if c:c.close()
 exec_cache[key]=v;return v
def order_reference(ck):
 if ck in order_cache:return order_cache[ck]
 q=a.plans[ck]['gold_sql'];g=execute(ck,q);dialect='postgres' if ck[0]=='livesqlbench' else 'sqlite'
 if 'error' in g:return {'error':g['error']}
 tree,order=outer_order(sqlglot.parse_one(q,read=dialect))
 if order is None or len(g['rows'])<=1:v=dict(rows=g['rows'],blocks=[(0,len(g['rows']))],required=order is not None)
 else:
  try:
   probe=rank_query(tree,g['names'],dialect);aug=execute(ck,probe)
   if 'error' in aug:raise ValueError(aug['error'])
   rows,blocks=ordered_reference(g['rows'],aug['rows']);v=dict(rows=rows,blocks=blocks,required=True,rank_query=probe)
  except Exception as e:v=dict(error=type(e).__name__+': '+str(e))
 order_cache[ck]=v;return v
def compare(ck,q,cols):
 g=execute(ck,a.plans[ck]['gold_sql']);c=execute(ck,q)
 if 'error' in g or 'error' in c:return dict(status='execution_unresolved',gold_error=g.get('error'),candidate_error=c.get('error'))
 if len(c['names'])!=len(cols):return dict(status='width_mismatch',candidate_width=len(c['names']),expected_width=len(cols))
 gp=[tuple(r[i] for i in cols) for r in g['rows']]
 out=dict(gold_rows=len(gp),witness_rows=len(c['rows']),bag_equal=a.bag(gp)==a.bag(c['rows']),
  gold_projection_sha256=hashlib.sha256(json.dumps(gp,default=str).encode()).hexdigest(),witness_result_sha256=hashlib.sha256(json.dumps(c['rows'],default=str).encode()).hexdigest())
 if not out['bag_equal']:return dict(out,status='full_row_mismatch')
 o=order_reference(ck)
 if 'error' in o:return dict(out,status='order_unresolved',error=o['error'])
 og=[tuple(r[i] for i in cols) for r in o['rows']]
 ok=all(a.bag(og[st:en])==a.bag(c['rows'][st:en]) for st,en in o['blocks'])
 return dict(out,status='full_rows_order_match' if ok else 'order_mismatch',order_required=o['required'],tie_blocks=len(o['blocks']),empty=not gp)
def variants(ck,col):
 key=ck,col
 if key in variant_cache:return variant_cache[key]
 dialect='postgres' if ck[0]=='livesqlbench' else 'sqlite';vv=[];seen=set()
 for n,w in enumerate(witness_groups[key]):
  q=w.get('projection_query')
  if q and q not in seen:
   vv.append(dict(kind='recorded_projection_'+str(n),query=q,structural_proof=w.get('projection_proof',w.get('proof')),exact_projection_family=bool(w.get('exact_projection_family'))));seen.add(q)
 # Generate from the unchanged Gold independently of the old dependency judgment.
 try:
  q,proof=a.original_goal_query(a.plans[ck],{'gold_columns':[col]})
  if q and q not in seen:vv.append(dict(kind='fresh_safe_projection',query=q,structural_proof=proof,exact_projection_family=True));seen.add(q)
 except Exception as e:append('transform_errors.jsonl',dict(cluster_key=ck,gold_column=col,stage='safe_projection',error=str(e)))
 for v in list(vv):
  try:
   t=qualify(sqlglot.parse_one(v['query'],read=dialect),dialect=dialect,schema=MappingSchema(a.schema(ck),dialect=dialect,normalize=dialect!='postgres'),expand_stars=True,validate_qualify_columns=True)
   q=pushdown_projections(t).sql(dialect=dialect)
   if q not in seen:vv.append(dict(v,kind=v['kind']+'_dead_projection_elimination',query=q));seen.add(q)
  except Exception as e:append('transform_errors.jsonl',dict(cluster_key=ck,gold_column=col,stage='dead_projection',error=str(e)))
 variant_cache[key]=vv;return vv

def main():
 for name in ['full_gold_native.jsonl','unit_audit.jsonl','joint_positive.jsonl','transform_errors.jsonl']:(OUT/name).write_text('')
 full={}
 for i,r in enumerate(refs,1):
  ck=tuple(r['cluster_key']);q=a.plans[ck]['gold_sql'];n=a.native(ck,q,r['allowed_columns'])
  x=dict(request_id=r['id'],cluster_key=ck,role=r['role'],level=r['level'],in_original80=r['id'] in pos,native=n,gold_sql_sha256=hashlib.sha256(q.encode()).hexdigest())
  full[r['id']]=x;append('full_gold_native.jsonl',x)
  if i%100==0:write('STATUS.json',dict(stage='full_gold_native',completed=i,total=1398,counts=dict(Counter(t['native']['status'] for t in full.values()))))
 write('STATUS.json',dict(stage='units',completed=0,total=len(a.units),full_native_counts=dict(Counter(t['native']['status'] for t in full.values()))))
 units=[]
 for i,u in enumerate(a.units,1):
  ck=tuple(u['cluster_key']);col=u['gold_column'];r=a.refs[u['request_id']];out=dict(request_id=r['id'],cluster_key=ck,gold_column=col,in_original80=r['id'] in pos,previous_positive=bool(u['capacity_lower']),checks=[])
  for v in variants(ck,col):
   n=a.native(ck,v['query'],r['allowed_columns']);x=dict(v,native=n)
   if n['status']=='authorized':x['result']=compare(ck,v['query'],[col])
   out['checks'].append(x)
  good=[x for x in out['checks'] if x.get('result',{}).get('status')=='full_rows_order_match']
  strict=[x for x in good if x['exact_projection_family']]
  out.update(any_full_row_order_witness=bool(good),any_structurally_exact_witness=bool(strict),
      native_unresolved=any(x['native']['status']=='unresolved' for x in out['checks']),
      result_unresolved=any(x.get('result',{}).get('status') in ['execution_unresolved','order_unresolved'] for x in out['checks']))
  units.append(out);append('unit_audit.jsonl',out)
  if i%75==0:write('STATUS.json',dict(stage='units',completed=i,total=len(a.units),positive_witness_units=sum(x['any_full_row_order_witness'] for x in units)))
 # Recheck the exact joint witness already used to justify all80, independent of method outputs.
 prev=a.read(Path('/path/to/access/output/denied_reference_audit_20261105/joint_positive_audit.jsonl'))
 joint=[]
 for x in prev:
  if x['kind']!='existing_positive':continue
  r=a.refs[x['request_id']];ck=tuple(r['cluster_key']);q=x['query'];n=a.native(ck,q,r['allowed_columns'])
  v=dict(request_id=r['id'],cluster_key=ck,columns=x['columns'],query=q,native=n)
  if n['status']=='authorized':v['result']=compare(ck,q,x['columns'])
  joint.append(v);append('joint_positive.jsonl',v)
 by=defaultdict(list)
 for u in units:by[u['request_id']].append(u)
 records=[]
 for r in refs:
  uu=by[r['id']];fc=full[r['id']];op=ann[r['id']]
  records.append(dict(request_id=r['id'],cluster_key=r['cluster_key'],role=r['role'],level=r['level'],in_original80=r['id'] in pos,
    original_columns=op['authorized_gold_columns'],observed_full_row_columns=[u['gold_column'] for u in uu if u['any_full_row_order_witness']],
    structurally_exact_columns=[u['gold_column'] for u in uu if u['any_structurally_exact_witness']],
    full_gold_native_status=fc['native']['status'],unresolved=any(u['native_unresolved'] or u['result_unresolved'] for u in uu)))
 (OUT/'request_audit.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
 result=dict(passed=True,denied=1398,unit_records=len(units),fixed80=80,
    full_gold_native_counts=dict(Counter(r['native']['status'] for r in full.values())),
    full_gold_authorized_inside80=sum(r['in_original80'] and r['native']['status']=='authorized' for r in full.values()),
    full_gold_authorized_outside80=sum(not r['in_original80'] and r['native']['status']=='authorized' for r in full.values()),
    inside80_any_full_row_witness=sum(r['in_original80'] and bool(r['observed_full_row_columns']) for r in records),
    outside80_any_full_row_witness=sum(not r['in_original80'] and bool(r['observed_full_row_columns']) for r in records),
    inside80_structural_exact_witness=sum(r['in_original80'] and bool(r['structurally_exact_columns']) for r in records),
    outside80_structural_exact_witness=sum(not r['in_original80'] and bool(r['structurally_exact_columns']) for r in records),
    unresolved_requests=sum(r['unresolved'] for r in records),
    joint80_results=dict(Counter(r.get('result',{}).get('status',r['native']['status']) for r in joint)),
    native_unique_checks=len(a.native_cache),query_executions=len(exec_cache),candidate_outputs_read=False,new_model_calls=0,
    search_exhaustive_over_all_SQL=False,original_labels_changed=False,
    source_hashes_unchanged=all(sha(Path(p))==h for p,h in source_hashes.items()))
 assert result['source_hashes_unchanged'] and len(records)==1398 and len(joint)==80
 write('COMPLETE.json',result);print(json.dumps(result),flush=True)
if __name__=='__main__':
 try:main()
 except Exception:write('FAILED.json',dict(traceback=traceback.format_exc()));raise
