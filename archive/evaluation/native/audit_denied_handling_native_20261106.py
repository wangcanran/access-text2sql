"""Native permission checks for all delivered SQL on the fixed positive 80."""
import sys,json,collections,hashlib,time,re,traceback
from pathlib import Path
sys.path.insert(0,'/path/to/backend-environment')
import audit_denied_reference_20261105 as a
from retention_sql_output_units import parse_readonly

OUT=Path('/path/to/access/output/denied_handling_20261106')
cohort=a.read(OUT/'denied_cohort.jsonl');positive={r['request_id'] for r in a.anns if r['has_legal_gold_output']}
assert len(positive)==80
def refusal(r):
 s=r['sql'].strip()
 if r['original_outcome']=='refuse' and not s:return 'frontend_explicit_refuse'
 if s=='Sorry, I cannot answer.':return 'normalized_explicit_refuse'
 if re.fullmatch(r'''SELECT\s+['"]Sorry, I cannot answer\.['"](?:\s+AS\s+[\w"]+)?\s*;?''',s,re.I):return 'SQL_literal_explicit_refuse'
 return None

def main():
 out=OUT/'positive80_native_access.jsonl';out.write_text('')
 records=[];cache={}
 for r in cohort:
  if r['request_id'] not in positive:continue
  ck=tuple(r['cluster_key']);s=r['sql'];record={k:r[k] for k in ['candidate_id','request_id','method','model','cluster_key','sql_access']};record['sql_sha256']=hashlib.sha256(s.encode()).hexdigest()
  refused=refusal(r)
  if refused:record.update(status='refusal',refusal_evidence=refused)
  elif not s.strip() or s.startswith('Error:'):record.update(status='no_valid_SQL',error=s[:300])
  else:
   key=(ck[:2],s,tuple(sorted(a.refs[r['request_id']]['allowed_columns'])))
   if key not in cache:
    try:
     parse_readonly(s,'postgres' if ck[0]=='livesqlbench' else 'sqlite')
     n=a.native(ck,s,a.refs[r['request_id']]['allowed_columns'])
     # A query that fails parsing/name binding is not a certified valid delivery.
     # Resource/connection failures stay unknown; they are not refusals.
     if n['status']=='unresolved':
      msg=n.get('error','');low=msg.lower()
      resource=any(v in low for v in ['timeout','timed out','canceling statement','connection refused','could not connect','server closed','unable to open database','out of memory'])
      if not resource:n={**n,'status':'invalid_SQL','native_permission_unresolved_due_to_invalid_SQL':True}
     cache[key]=n
    except Exception as e:cache[key]={'status':'invalid_SQL','error':type(e).__name__+': '+str(e)[:1500]}
   record.update(cache[key])
  records.append(record)
  with out.open('a') as f:f.write(json.dumps(record,ensure_ascii=True)+'\n')
  if len(records)%100==0:
   a.write(OUT/'NATIVE_STATUS.json',{'processed':len(records),'total':1600,'statuses':dict(collections.Counter(x['status'] for x in records))});print(len(records),flush=True)
 assert len(records)==1600
 summary={'passed':True,'records':len(records),'unique_SQL_policy_checks':len(cache),'statuses':dict(collections.Counter(x['status'] for x in records)),'SQL_changed':False,'model_calls':0,'permissions':'native SQLite authorizer; PostgreSQL EXPLAIN under isolated column grants, all grant transactions rolled back','source_sha256':hashlib.sha256((OUT/'denied_cohort.jsonl').read_bytes()).hexdigest()}
 a.write(OUT/'NATIVE_COMPLETE.json',summary);print(json.dumps(summary),flush=True)
if __name__=='__main__':
 try:main()
 except Exception:a.write(OUT/'NATIVE_FAILED.json',{'traceback':traceback.format_exc()});raise
