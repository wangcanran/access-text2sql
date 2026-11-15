"""Run fixed new-P requests through the four published pipelines."""
import argparse,copy,hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
def read(p):return json.loads(p.read_text())
def lines(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
def save(p,x):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(x,ensure_ascii=True,indent=2));tmp.replace(p)
def jl(p,rs):p.write_text(''.join(json.dumps(r,ensure_ascii=True)+'\n' for r in rs))
def run(cmd,folder,env):
 with (folder/'runner.log').open('a') as log:
  log.write(json.dumps(cmd)+'\n');log.flush();subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
def worker(job,arm,qid):
 from scripts.run_clarification_deepseek import configure
 configure()
 env=dict(os.environ,RBAC_API_BASE=os.environ['OPENAI_BASE_URL'],RBAC_API_KEY=os.environ['OPENAI_API_KEY'],RBAC_DEEPSEEK_THINKING='disabled',RBAC_REQUEST_TIMEOUT='180',PYTHONIOENCODING='utf-8',PYTHONPATH=str(ROOT))
 fixture=job/'fixture';cs=[c for c in read(fixture/'selection.json')['cases'] if c['question_id']==qid];assert cs
 folder=job/'runs'/arm/str(qid);folder.mkdir(parents=True,exist_ok=True)
 if (folder/'COMPLETE.json').exists():return read(folder/'COMPLETE.json')
 save(folder/'STATUS.json',dict(state='frontend',method=arm,qid=qid,pid=os.getpid(),started=time.time()))
 fronts={}
 if arm!='baseline':
  if arm=='main':
   front=folder/'frontend';cmd=[sys.executable,'-u','-m','scripts.run_clarification_v10','--model','deepseek-flash','--fixture-dir',str(fixture),'--selection',str(fixture/'selection.json'),'--out',str(front),'--unresolved-retries','2','--cases',','.join(f'{qid}:{c["level"]}' for c in cs)]
  else:
   front=folder/'frontend';cmd=[sys.executable,'-u','-m','scripts.p_expansion_baseline_adapter_20261115','--arm',arm,'--qid',str(qid),'--fixture',str(fixture),'--out',str(front)]
  if (front/'CONFIG.json').exists():cmd.append('--resume')
  suffix='reduction' if arm=='main' else 'baseline'
  paths=[front/f'{qid}_{c["level"]}_{suffix}.json' for c in cs]
  recorded=all(p.exists() and read(p).get('outcome') in ['deliver','refuse','unresolved','system_error'] for p in paths)
  if recorded:
   for p in paths:
    t=read(p)
    if arm=='main':assert all(t.get(k) is True for k in ['engine_unchanged','frozen_unchanged','runtime_unchanged','overlay_unchanged']),str(p)
   save(folder/'FRONTEND_REUSE_AUDIT.json',dict(reason='All planned sessions recorded; preserve exhausted outcomes and original retry budget',traces={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}))
  else:
   run(cmd,folder,env)
  if arm=='main':
   for c in cs:
    t=read(front/f'{qid}_{c["level"]}_reduction.json');fronts[c['level']]=dict(outcome=t['outcome'],text=(t.get('delivery') or {}).get('text',''),trace=str(front/f'{qid}_{c["level"]}_reduction.json'))
  else:
   for r in lines(front/'evaluation_rows.jsonl'):fronts[r['level']]=dict(outcome=r['outcome'],text=r['clarified_text'],trace=str(front/r['trace']))
 else:fronts={c['level']:dict(outcome='deliver',text='',trace=None) for c in cs}
 template={r['row']['metadata']['clarification_id']:r['row'] for r in lines(job/'backend_templates.jsonl')}
 refs={r['request_id']:r for r in lines(job/'references.jsonl')};inputs=[];records=[]
 for c in cs:
  rid=':'.join(str(c[k]) for k in ('question_id','level','role','policy_sha256'));f=fronts[c['level']]
  records.append(dict(id=rid,method=arm,model='deepseek_flash',level=c['level'],**f))
  if f['outcome']=='deliver':
   item=copy.deepcopy(template[rid])
   if arm!='baseline':assert f['text'].strip();item['input']=f['text']
   inputs.append(item)
 save(folder/'frontend_records.json',records);save(folder/'backend_inputs.json',inputs)
 sqls={}
 if inputs:
  save(folder/'STATUS.json',dict(state='generating_sql',method=arm,qid=qid,pid=os.getpid(),inputs=len(inputs)))
  env['RBAC_RESPONSE_AUDIT']=str(folder/'responses.jsonl')
  cmd=[sys.executable,'-u',str(ROOT/'scripts/predict_rbac_cloud_only.py'),'--provider','openai','--model','deepseek-flash','--dataset',cs[0]['dataset'],'--mode','rbac' if arm in ['baseline','serial'] else 'baseline','--no_dedupe','--shot_num','0','--temperature','0','--max_tokens','4096','--workers','2','--rate_limit','0','--save_every','1','--resume','--input',str(folder/'backend_inputs.json'),'--output',str(folder/'generated.sql')]
  if arm=='baseline':cmd.append('--structured')
  detail=folder/'generated_detailed.json';cp=folder/'generated_checkpoint.json'
  for attempt in range(3):
   run(cmd,folder,env);preds=read(detail);assert len(preds)==len(inputs)
   bad=[i for i,r in enumerate(preds) if not r['pred_sql'].strip() or r['pred_sql'].lower().startswith(('error','[error'))]
   if not bad:break
   save(folder/f'backend_failed_attempt{attempt+1}.json',[dict(index=i,prediction=preds[i]) for i in bad])
   if attempt<2:save(cp,[r for r in read(cp) if r['index'] not in bad])
  for item,pred in zip(inputs,preds):
   assert pred['database']==item['db_id'];sqls[item['metadata']['clarification_id']]=pred['pred_sql']
 from score_p_expansion_20261115 import score
 dbroot=ROOT/'data/manual_flat_gold_v1_databases_20261031/db_assets';scores=[]
 for r in records:
  s=score(refs[r['id']],sqls.get(r['id'],''),r['outcome'],dbroot);s.update(method=arm,model='deepseek_flash')
  if r['outcome']=='system_error':
   t=read(Path(r['trace']));known=(t.get('error_type'),t.get('error')) in [('JSONDecodeError','Expecting value: line 1 column 1 (char 0)'),('ValueError','Selected leaves are not jointly authorized and connected')]
   if known and t.get('calls') and all(c.get('response_status') in ['valid_json_object','text_received'] for c in t['calls']):
    s.update(status='scored',score_lower=0.,score_upper=0.,sql_access='absent',failure_reason='observed_model_output_or_state_validation_failure',frontend_error_type=t['error_type'],frontend_error=t['error'])
  scores.append(s)
 jl(folder/'scores.jsonl',scores)
 result=dict(method=arm,qid=qid,requests=len(scores),pending=sum(s['status']!='scored' for s in scores),outcomes={s:sum(r['outcome']==s for r in records) for s in sorted({r['outcome'] for r in records})},completed_at=time.time())
 save(folder/('COMPLETE.json' if result['pending']==0 else 'NEEDS_REVIEW.json'),result);save(folder/'STATUS.json',dict(state='complete' if not result['pending'] else 'needs_review',**result));return result
def main():
 p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--arm',required=True,choices=['baseline','serial','joint','main']);p.add_argument('--qid',type=int,required=True);a=p.parse_args()
 try:print(json.dumps(worker(a.job.resolve(),a.arm,a.qid)),flush=True)
 except Exception as e:
  save(a.job/'runs'/a.arm/str(a.qid)/'STATUS.json',dict(state='failed',method=a.arm,qid=a.qid,error_type=type(e).__name__,error=str(e),time=time.time()));raise
if __name__=='__main__':main()
