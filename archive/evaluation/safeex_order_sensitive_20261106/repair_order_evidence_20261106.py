from pathlib import Path
import json,sys
JOB=Path('/path/to/access/output/safeex_order_sensitive_20261106')
sys.path.insert(0,str(JOB))
from run_order_safeex_20261106 import evaluate,summarize,write,sha,self_test
self_test()
tasks=[json.loads(s) for s in (JOB/'tasks.jsonl').open()]
(JOB/'first_pass').mkdir(exist_ok=True)
for t in tasks:
    p=JOB/'clusters'/(sha(t['cluster_key'])[:24]+'.json')
    old=json.loads(p.read_text())
    pending={r['candidate_id'] for r in old['records'] if r['order_status'] in ['order_reference_unresolved','order_execution_unresolved']}
    if not pending:continue
    (JOB/'first_pass'/p.name).write_bytes(p.read_bytes())
    sub=dict(t,records=[r for r in t['records'] if r['candidate_id'] in pending])
    fresh=evaluate(sub,seconds=120)
    replacement={r['candidate_id']:r for r in fresh['records']}
    old['records']=[replacement.get(r['candidate_id'],r) for r in old['records']]
    old['retry_evidence']={k:v for k,v in fresh.items() if k!='records'}
    old['status']=fresh['status']
    write(p,old)
    print(json.dumps(dict(cluster=t['cluster_key'],n=len(pending),status=fresh['status'],error=fresh.get('error'),statuses={r['order_status'] for r in fresh['records']}),default=list),flush=True)
summarize(JOB,tasks)
