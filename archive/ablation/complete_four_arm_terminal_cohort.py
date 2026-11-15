"""Repair nonterminal frontend cases, then resume SQL with verified reuse."""
from pathlib import Path
import collections,fcntl,hashlib,json,os,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
OLD=Path('/path/to/access/output/four_arm_executable_20261105')
JOB=OLD.parent/'four_arm_terminal_20261106'
WORK=OLD/'workspace'
ARMS=('no_pruning','no_clarification','clarify_then_prune')
TERMINAL={'deliver','refuse'}
def read(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
def write(p,rows):p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
def save(p,value):
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.replace(p)
def active_generators():
    result=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            cmd=(p/'cmdline').read_bytes().split(bytes([0]))
            if str(OLD/'predict_rbac_cloud_only.py').encode() in cmd and (p/'stat').read_text().split(') ',1)[1][0]!='Z':result.append(int(p.name))
        except OSError:pass
    return result
def initial(arm):
    d=OLD/arm
    rows=read(d/'evaluation_rows.jsonl') if (d/'evaluation_rows.jsonl').exists() else sum([read(d/'frontend'/f'shard{i}/evaluation_rows.jsonl') for i in range(2)],[])
    assert len(rows)==len({r['id'] for r in rows})==2800
    if arm=='clarify_then_prune' and (JOB/'SERIAL_STATE_REPAIR_ACTIVE.json').exists():
        # A corrected serial implementation must cover the entire fixed cohort;
        # never splice its successful retries into the preceding implementation.
        rows=[dict(r,outcome='not_run') for r in rows]
    return rows
def selected_rows(arm):
    base=initial(arm);chosen={r['id']:r for r in base}
    for p in sorted((JOB/arm/'retries').glob('round_*/evaluation_rows.jsonl')):
        for row in read(p):
            assert row['id'] in chosen
            if chosen[row['id']]['outcome'] not in TERMINAL:chosen[row['id']]=row
    return [chosen[r['id']] for r in base]

def active_frontends(arm):
    result=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            cmd=(p/'cmdline').read_bytes().split(bytes([0]))
            if (str(JOB/'retry_four_arm_frontend_worker.py').encode() in cmd
                    and b'--variant' in cmd and cmd[cmd.index(b'--variant')+1]==arm.encode()
                    and (p/'stat').read_text().split(') ',1)[1][0]!='Z'):
                result.append(int(p.name))
        except (OSError,IndexError):pass
    return result

def repair_arm(arm):
    d=JOB/arm;d.mkdir(exist_ok=True)
    while active_frontends(arm):
        save(d/'RETRY_STATUS.json',dict(state='waiting_existing_retry',pids=active_frontends(arm)))
        time.sleep(15)
    rows=selected_rows(arm);pending=[r['id'] for r in rows if r['outcome'] not in TERMINAL]
    if not pending:return 0
    rounds=[int(p.name.split('_')[1]) for p in (d/'retries').glob('round_*') if p.is_dir()]
    round_number=max(rounds,default=0)+1
    if round_number>20:
        save(d/'RETRY_STATUS.json',dict(state='needs_cause_repair',pending=len(pending),attempt_limit=20));return len(pending)
    save(d/'RETRY_STATUS.json',dict(state='retrying',round=round_number,terminal=2800-len(pending),pending=len(pending)))
    dest=d/'retries'/f'round_{round_number:02d}';dest.parent.mkdir(parents=True,exist_ok=True)
    selection=dest.with_name(dest.name+'_ids.json');selection.write_text(json.dumps(pending))
    workspace=JOB/'serial_state_repair_workspace' if arm=='clarify_then_prune' and (JOB/'SERIAL_STATE_REPAIR_ACTIVE.json').exists() else WORK
    cmd=[sys.executable,'-u',str(JOB/'retry_four_arm_frontend_worker.py'),'--workspace',str(workspace),
         '--selection',str(selection),'--variant',arm,'--out',str(dest)]
    with dest.with_suffix('.log').open('a') as log:
        result=subprocess.run(cmd,cwd=workspace,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:save(d/'LAST_WORKER_FAILURE.json',dict(round=round_number,returncode=result.returncode))
    remaining=sum(r['outcome'] not in TERMINAL for r in selected_rows(arm))
    save(d/'RETRY_STATUS.json',dict(state='round_complete',round=round_number,pending=remaining,returncode=result.returncode))
    return remaining
def prepare_inputs(arm):
    d=JOB/arm
    existing=d/'backend_inputs/MANIFEST.json'
    if existing.exists():
        manifest=json.loads(existing.read_text())
        assert manifest['source_sha256']==hashlib.sha256((d/'evaluation_rows.jsonl').read_bytes()).hexdigest(), 'Backend frontend rows changed'
        assert manifest['selection_sha256']==hashlib.sha256((WORK/'artifacts/clarification_main/manual_flat_gold_v1_instruction_v10_preflight_20261101/selection.json').read_bytes()).hexdigest()
        assert (d/'REUSE_AUDIT.json').exists(), 'Input preparation was interrupted; inspect before reseeding'
        return
    subprocess.run([sys.executable,str(WORK/'scripts/prepare_clarification_text2sql_inputs.py'),
       '--rows',str(d/'evaluation_rows.jsonl'),'--selection',str(WORK/'artifacts/clarification_main/manual_flat_gold_v1_instruction_v10_preflight_20261101/selection.json'),
       '--gold-dir','/path/to/access/data/gold/manual_flat_gold_v1','--out',str(d/'backend_inputs')],cwd=WORK,check=True)
    reused=0
    for p in (d/'backend_inputs').glob('*/level*.json'):
        ds=p.parent.name;lv=p.stem
        oldinputs=OLD/arm/'backend_inputs'/ds/p.name;oldcp=OLD/arm/'generation'/ds/(lv+'_checkpoint.json')
        if not oldinputs.exists() or not oldcp.exists():continue
        olditems=json.loads(oldinputs.read_text());newitems=json.loads(p.read_text())
        oldfront={r['id']:r for r in initial(arm)};newfront={r['id']:r for r in read(d/'evaluation_rows.jsonl')}
        newindex={item['metadata']['clarification_id']:i for i,item in enumerate(newitems)};seed=[]
        for cp in json.loads(oldcp.read_text()):
            olditem=olditems[cp['index']];rid=olditem['metadata']['clarification_id']
            if rid not in newindex:continue
            if oldfront[rid]['trace_sha256']!=newfront[rid]['trace_sha256']:continue
            target=newitems[newindex[rid]]
            assert {k:v for k,v in olditem.items() if k!='metadata'}=={k:v for k,v in target.items() if k!='metadata'}
            if not cp.get('response','').strip() or cp['response'].lower().startswith(('error','[error')):continue
            seed.append(dict(cp,index=newindex[rid]));reused+=1
        dest=d/'generation'/ds;dest.mkdir(parents=True,exist_ok=True)
        (dest/(lv+'_checkpoint.json')).write_text(json.dumps(seed,ensure_ascii=False))
    save(d/'REUSE_AUDIT.json',dict(checkpoints_seeded=reused,rule='same case ID, identical selected trace, same backend input'))
def main():
    JOB.mkdir(exist_ok=True)
    with (JOB/'terminal_completion.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Original SQL generation owns checkpoint files until it finishes.
        # Its subsequent one-worker scorer can safely overlap one frontend.
        while active_generators():
            save(JOB/'STATUS.json',dict(state='waiting_existing_generation',pid=os.getpid(),generator_pids=active_generators()));time.sleep(30)
        # Refill a free worker as soon as an arm finishes a round. A long serial
        # round must not leave the other slot idle or delay all other retries.
        queue=collections.deque(ARMS);futures={}
        with ThreadPoolExecutor(max_workers=2) as pool:
            while queue or futures:
                while queue and len(futures)<2:
                    arm=queue.popleft();futures[pool.submit(repair_arm,arm)]=arm
                save(JOB/'STATUS.json',dict(state='repairing_frontends',pid=os.getpid(),
                    frontend_workers=2,active_arms=list(futures.values()),queued_arms=list(queue)))
                finished,_=wait(futures,return_when=FIRST_COMPLETED)
                for future in finished:
                    arm=futures.pop(future);remaining=future.result()
                    rounds=[int(p.name.split('_')[1]) for p in (JOB/arm/'retries').glob('round_*') if p.is_dir()]
                    if remaining and max(rounds,default=0)<20:queue.append(arm)
        counts={}
        for arm in ARMS:
            rows=selected_rows(arm);counts[arm]=dict(collections.Counter(r['outcome'] for r in rows))
            for row in rows:
                path=Path(row['trace']);assert hashlib.sha256(path.read_bytes()).hexdigest()==row['trace_sha256']
                trace=json.loads(path.read_text());assert trace['ablation_sources_unchanged']
                assert trace['ablation']['variant']==arm
            write(JOB/arm/'evaluation_rows.jsonl',rows)
            save(JOB/arm/'SELECTED_TRACES.json', {r['id']:dict(trace=r['trace'],sha256=r['trace_sha256']) for r in rows})
        pending={a:sum(v for k,v in c.items() if k not in TERMINAL) for a,c in counts.items()}
        if any(pending.values()):
            save(JOB/'STATUS.json',dict(state='needs_cause_repair',pid=os.getpid(),pending=pending,outcomes=counts));return
        manifest=dict(arms={a:dict(backend_mode='baseline',backend_requests=counts[a].get('deliver',0)) for a in ARMS})
        save(JOB/'MANIFEST.json',manifest)
        for arm in ARMS:
            save(JOB/'STATUS.json',dict(state='backend',pid=os.getpid(),arm=arm,frontend_outcomes=counts))
            prepare_inputs(arm)
            status=JOB/arm/'PIPELINE_STATUS.json'
            if status.exists() and json.loads(status.read_text()).get('state')=='complete':
                tasks=read(JOB/arm/'sql_tasks.jsonl')
                expected={r['id'] for r in read(JOB/arm/'evaluation_rows.jsonl') if r['outcome']=='deliver'}
                assert len(tasks)==len(expected) and {r['id'] for r in tasks}==expected
                continue
            with (JOB/arm/'backend.log').open('a') as log:
                subprocess.run([sys.executable,'-u',str(JOB/'run_backend.py'),'--arm',arm],cwd=WORK,stdout=log,stderr=subprocess.STDOUT,check=True)
        save(JOB/'RESUME_STATUS.json',dict(state='complete',pid=os.getpid(),completed=list(ARMS)))
        save(JOB/'STATUS.json',dict(state='backend_complete',pid=os.getpid(),frontend_outcomes=counts))
        subprocess.run([sys.executable,'-u',str(JOB/'score_four_arm_ordered.py')],cwd=JOB,check=True)
        save(JOB/'STATUS.json',dict(state='complete' if json.loads((JOB/'strict_ordered_evaluation/STATUS.json').read_text())['state']=='complete' else 'scoring_needs_review',pid=os.getpid(),frontend_outcomes=counts))
if __name__=='__main__':
    try:main()
    except BaseException as exc:
        JOB.mkdir(exist_ok=True);save(JOB/'ERROR.json',dict(error=repr(exc),at=time.time()));raise
