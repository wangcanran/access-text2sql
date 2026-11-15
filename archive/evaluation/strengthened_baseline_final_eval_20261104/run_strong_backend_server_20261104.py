"""Server-resident strong-baseline generation and original SQL evaluation."""
import argparse,collections,csv,fcntl,hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path('/path/to/access')
JOB=ROOT/'output/strengthened_baseline_final_eval_20261104'
sys.path.insert(0,str(ROOT/'scripts'))
def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def save(p,x):
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2));tmp.replace(p)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--arm',choices=['serial','joint'],required=True);a=ap.parse_args();arm=a.arm;folder=JOB/arm;status=folder/'PIPELINE_STATUS.json';manifest=read(JOB/'MANIFEST.json')
    with (folder/'pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            from run_clarification_deepseek import configure
            configure()
            env=dict(os.environ,RBAC_API_BASE=os.environ['OPENAI_BASE_URL'],RBAC_API_KEY=os.environ['OPENAI_API_KEY'],RBAC_DEEPSEEK_THINKING='disabled',RBAC_REQUEST_TIMEOUT='180',PYTHONIOENCODING='utf-8')
            front={r['id']:r for r in rows(folder/'evaluation_rows.jsonl')};assert len(front)==2800
            done=0;tasks=[]
            for ds in ('bird','spider','livesqlbench'):
                for lv in range(3):
                    source=folder/'backend_inputs'/ds/f'level{lv}.json';items=read(source);dest=folder/'generation'/ds;dest.mkdir(parents=True,exist_ok=True)
                    sql=dest/f'level{lv}.sql';detail=dest/f'level{lv}_detailed.json';cp=dest/f'level{lv}_checkpoint.json';env['RBAC_RESPONSE_AUDIT']=str(dest/f'level{lv}_responses.jsonl')
                    cmd=[sys.executable,'-u',str(JOB/'predict_rbac_cloud_only.py'),'--provider','openai','--model','deepseek-flash','--dataset',ds,'--mode',manifest['arms'][arm]['backend_mode'],'--no_dedupe','--shot_num','0','--temperature','0','--max_tokens','4096','--workers','4','--rate_limit','0','--save_every','10','--resume','--input',str(source),'--output',str(sql)]
                    for attempt in range(1,4):
                        save(status,dict(state='generating',arm=arm,pid=os.getpid(),dataset=ds,level=lv,completed_prior_shards=done,shard_attempt=attempt,total=manifest['arms'][arm]['backend_requests']))
                        with (dest/f'level{lv}.log').open('a') as f:subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
                        predictions=read(detail);assert len(items)==len(predictions)
                        bad=[i for i,p in enumerate(predictions) if p['pred_sql'].lower().startswith(('error','[error')) or not p['pred_sql'].strip()]
                        if not bad:break
                        save(dest/f'level{lv}_failed_attempt{attempt}.json',[dict(index=i,prediction=predictions[i]) for i in bad])
                        if attempt<3:save(cp,[r for r in read(cp) if r['index'] not in bad])
                    if bad:raise RuntimeError('Generation failures remain; see failed_attempt artifacts')
                    for item,pred in zip(items,predictions):
                        assert pred['database']==item['db_id'];rid=item['metadata']['clarification_id'];r=front[rid]
                        tasks.append(dict(id=rid,model='deepseek_flash',method=arm,dataset=ds,db_id=item['db_id'],level=lv,has_pruning=r['has_pruning'],original_permission=r['original_permission'],sql=pred['pred_sql'],gold_sql=item.get('gold_sql') or item['metadata'].get('gold_sql') or item['output'],policy=item['policy'],allowed_columns=r['allowed_columns']))
                    done+=len(items);print('GENERATED',arm,ds,lv,done,flush=True)
            assert len(tasks)==len({r['id'] for r in tasks})==manifest['arms'][arm]['backend_requests']
            (folder/'sql_tasks.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in tasks))
            save(status,dict(state='scoring',arm=arm,generated=done,pid=os.getpid()))
            if (JOB/'SCORING_HOLD.json').exists():
                save(status,dict(state='scoring_held_protocol_review',arm=arm,generated=done))
                return
            subprocess.run([sys.executable,'-u',str(JOB/'score_strong_backend_remote_20261104.py'),'--job',str(folder)],cwd=ROOT,check=True)
            scores={r['id']:r for r in rows(folder/'sql_results.jsonl')}
            from score_paired_main_sql_20261102_remote import is_refusal
            from analyze_paired_main_20261102 import category,counts
            classified=[]
            from strong_baseline_score_mapping import classify
            for rid,r in front.items():
                s=scores.get(rid);assert (s is not None)==(r['outcome']=='deliver')
                classified.append(classify(arm,r,s,category))
            (folder/'classifications.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in classified))
            metrics=[]
            for key in [None,'dataset','level']:
                groups=[None] if key is None else sorted({r[key] for r in classified})
                for group in groups:
                    rs=[r for r in classified if key is None or r[key]==group];metrics.append(dict(arm=arm,model='deepseek_flash',grouping=key or 'overall',group=group,**counts(collections.Counter(r['category'] for r in rs),sum(r['original_execution_match'] for r in rs))))
            with (folder/'metrics.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=metrics[0].keys());w.writeheader();w.writerows(metrics)
            save(status,dict(state='complete',arm=arm,generated=done,cohort=2800,scoring=read(folder/'SCORING_STATUS.json'),overall=metrics[0],completed_at=time.time()))
            print('COMPLETE',arm,flush=True)
        except BaseException as e:
            save(status,dict(state='failed',arm=arm,error_type=type(e).__name__,error=str(e),at=time.time()));raise
if __name__=='__main__':main()
