"""Bounded concurrency; deterministic smoke then all paired requests."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
import argparse,fcntl,json,os,subprocess,sys,time,hashlib
ROOT=Path(__file__).resolve().parents[1]
def save(p,x):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(x,ensure_ascii=True,indent=2));t.replace(p)
def main():
 p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--workers',type=int,default=8);a=p.parse_args();j=a.job.resolve()
 with (j/'supervisor.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  manifest=json.loads((j/'MANIFEST.json').read_text());plan=json.loads((j/'RUN_PLAN.json').read_text())
  for rel,h in manifest['files'].items():assert hashlib.sha256((j/rel.replace('\\','/')).read_bytes()).hexdigest()==h,rel
  order=list(dict.fromkeys(c['question_id'] for c in plan['ordered_cases']));arms=plan['methods']
  def task(arm,qid):
   folder=j/'runs'/arm/str(qid);folder.mkdir(parents=True,exist_ok=True)
   if (folder/'COMPLETE.json').exists():return dict(arm=arm,qid=qid,exitcode=0,reused=True)
   cmd=[sys.executable,'-u','-m','scripts.run_p_expansion_20261115','--job',str(j),'--arm',arm,'--qid',str(qid)]
   with (folder/'process.log').open('a') as log:
    result=subprocess.run(cmd,cwd=ROOT,env=dict(os.environ,PYTHONPATH=str(ROOT)),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
   return dict(arm=arm,qid=qid,exitcode=result.returncode,reused=False)
  started=time.time();completed=[]
  def snapshot(phase):
   scores=[];states=Counter();byarm={}
   for arm in arms:
    rows=[]
    for f in (j/'runs'/arm).glob('*/scores.jsonl'):rows += [json.loads(s) for s in f.read_text().splitlines() if s.strip()]
    assert len(rows)==len({r['request_id'] for r in rows});scores+=rows
    byarm[arm]=dict(scored=len(rows),pending=sum(r['status']!='scored' for r in rows))
   for f in (j/'runs').glob('*/*/STATUS.json'):states[json.loads(f.read_text())['state']]+=1
   save(j/'STATUS.json',dict(state=phase,pid=os.getpid(),workers=a.workers,started=started,updated=time.time(),planned_requests=manifest['new_requests'],planned_method_requests=manifest['new_requests']*4,completed_tasks=len(completed),task_states=dict(states),by_method=byarm))
   tmp=j/'scores.partial.tmp';tmp.write_text(''.join(json.dumps(s,ensure_ascii=True)+'\n' for s in scores));tmp.replace(j/'scores.partial.jsonl')
  for phase,qids in [('smoke',plan['smoke_question_ids']),('full',[q for q in order if q not in plan['smoke_question_ids']])]:
   tasks=[(arm,q) for q in qids for arm in arms];snapshot(phase)
   with ThreadPoolExecutor(max_workers=a.workers) as pool:
    futures=[pool.submit(task,*t) for t in tasks]
    for f in as_completed(futures):
     result=f.result();completed.append(result);snapshot(phase);print(json.dumps(result),flush=True)
   failures=[x for x in completed if x['exitcode']!=0]
   if failures:
    save(j/'FAILED_TASKS.json',failures);snapshot('failed_tasks_need_repair');return
   if phase=='smoke':
    pending=[]
    for arm,q in tasks:
     if not (j/'runs'/arm/str(q)/'COMPLETE.json').exists():pending.append(dict(arm=arm,qid=q))
    if pending:save(j/'PILOT_PENDING.json',pending);snapshot('smoke_needs_review');return
  snapshot('checking_completeness');s=[json.loads(x) for x in (j/'scores.partial.jsonl').read_text().splitlines()];expected=manifest['new_requests']*4
  assert len(s)==expected and len({(r['method'],r['request_id']) for r in s})==expected
  pending=[r for r in s if r['status']!='scored' or r['score_lower']!=r['score_upper']]
  save(j/'PENDING.json',pending)
  if pending:snapshot('evaluation_needs_review');return
  (j/'scores.jsonl').write_bytes((j/'scores.partial.jsonl').read_bytes());snapshot('complete')
  save(j/'COMPLETE.json',dict(requests=manifest['new_requests'],method_requests=expected,finished=time.time(),source_manifest_sha256=hashlib.sha256((j/'MANIFEST.json').read_bytes()).hexdigest()))
if __name__=='__main__':main()
