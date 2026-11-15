"""Re-evaluate only nontrivial ordered outputs; frozen inference and safety evidence."""
from pathlib import Path
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse,gzip,hashlib,json,sqlite3,time,sys

ROOT=Path('/path/to/access')
HELPERS=ROOT/'output/result_output_completion_20261105_v1'
sys.path.insert(0,str(HELPERS))
from retention_sql_output_units import parse_readonly,sha
from order_sensitive_matcher_20261106 import outer_order,rank_query,ordered_reference,match_ordered,self_test

def write(path,obj):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(obj,ensure_ascii=True,default=str));temp.replace(path)

def prepare(job):
    with gzip.open(job/'frozen_scores.jsonl.gz','rt',encoding='utf-8') as f:
        frozen=[json.loads(s) for s in f]
    assert len(frozen)==29640 and len({r['candidate_id'] for r in frozen})==29640
    lookup={r['candidate_id']:r for r in frozen}
    taskpaths=[HELPERS/'tasks.jsonl']+sorted((ROOT/'output/strengthened_multibackend_20261105').glob('*/*/result_completion/tasks.jsonl'))
    assert len(taskpaths)==9
    origins={};gold_by_cluster={};gold_hashes={}
    for p in taskpaths:
        for line in p.open():
            t=json.loads(line);ck=tuple(t['cluster_key'])
            assert gold_by_cluster.setdefault(ck,t['gold_sql'])==t['gold_sql']
            cluster_path=p.parent/'clusters'/(sha(t['cluster_key'])[:24]+'.json')
            cluster=json.loads(cluster_path.read_text())
            gold_hash=cluster.get('gold_result_sha256')
            for r in t['candidates']:
                cid=r['candidate_id']
                if cid not in lookup:continue
                assert cid not in origins and lookup[cid]['sql']==r['sql']
                origins[cid]=dict(gold_hash=gold_hash,source=str(cluster_path))
    assert set(origins)==set(lookup)
    jobs=defaultdict(list);initial=[];stats=Counter()
    for r in frozen:
        r=dict(r);r['pre_order_lower']=r['score_lower'];r['pre_order_upper']=r['score_upper']
        ck=tuple(r['cluster_key']);ds=ck[0];dialect='postgres' if ds=='livesqlbench' else 'sqlite'
        tree=parse_readonly(gold_by_cluster[ck],dialect);tree,order=outer_order(tree)
        r['order_required']=order is not None
        if order is None:reason='no_outer_order_unchanged'
        elif r['score_upper']==0:reason='existing_zero_unchanged'
        elif r.get('gold_rows') is None:reason='existing_unresolved_no_row_evidence'
        elif r['gold_rows']<=1:reason='at_most_one_row_order_trivial'
        else:
            r['expected_gold_hash']=origins[r['candidate_id']]['gold_hash']
            jobs[ck].append(r);stats['requires_order_evaluation']+=1
            continue
        r['order_status']=reason;stats[reason]+=1;initial.append(r)
    tasks=[dict(cluster_key=list(ck),gold_sql=gold_by_cluster[ck],records=rs) for ck,rs in jobs.items()]
    (job/'tasks.jsonl').write_text(''.join(json.dumps(t,ensure_ascii=True)+'\n' for t in tasks))
    (job/'unchanged.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=True)+'\n' for r in initial))
    write(job/'PREPARATION.json',dict(records=len(frozen),clusters=len(tasks),counts=dict(stats),
         frozen_sha256=hashlib.sha256(gzip.decompress((job/'frozen_scores.jsonl.gz').read_bytes())).hexdigest(),
         task_source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in taskpaths}))
    print(json.dumps(dict(preparation=dict(stats),clusters=len(tasks))),flush=True)
    return tasks

def evaluate(task,seconds=20,row_cap=500000):
    ds,db,_=task['cluster_key'];dialect='postgres' if ds=='livesqlbench' else 'sqlite'
    result=dict(cluster_key=task['cluster_key'],records=[],status='running');con=None
    def pending(r,reason,error):
        return dict(r,score_lower=0.,score_upper=r['pre_order_upper'],order_status=reason,order_error=error)
    try:
        if dialect=='postgres':
            import psycopg2
            con=psycopg2.connect(host='/var/run/postgresql',user='root',dbname=db,connect_timeout=10,
                                options=f'-c default_transaction_read_only=on -c statement_timeout={seconds*1000}')
            con.set_session(readonly=True,autocommit=False,isolation_level='REPEATABLE READ')
        else:
            path=ROOT/'data/manual_flat_gold_v1_databases_20261031/db_assets'/ds/db/(db+'.sqlite')
            con=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,cached_statements=0)
            con.text_factory=lambda b:b.decode('utf-8',errors='surrogateescape')
            con.execute('PRAGMA query_only=ON');con.execute('BEGIN')
        def execute(sql):
            parse_readonly(sql,dialect)
            if dialect=='sqlite':
                deadline=time.monotonic()+seconds
                con.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            cur=con.cursor()
            if dialect=='postgres':cur.execute('SAVEPOINT order_eval')
            try:
                cur.execute(sql);data=cur.fetchmany(row_cap+1)
                if len(data)>row_cap:raise RuntimeError('row_cap_incomplete_result')
                return data,[x[0] for x in cur.description]
            except Exception:
                if dialect=='postgres':cur.execute('ROLLBACK TO SAVEPOINT order_eval')
                raise
            finally:
                if dialect=='postgres':cur.execute('RELEASE SAVEPOINT order_eval')
                cur.close()
        gold,names=execute(task['gold_sql']);gh=sha(gold)
        result.update(gold_result_sha256=gh,gold_rows=len(gold))
        probe=rank_query(parse_readonly(task['gold_sql'],dialect),names,dialect)
        augmented,_=execute(probe)
        ordered,blocks=ordered_reference(gold,augmented)
        result.update(rank_probe_sql=probe,tie_blocks=len(blocks),tie_block_sizes=[b-a for a,b in blocks])
        cache={}
        for item in task['records']:
            r=dict(item)
            if r['expected_gold_hash'] and gh!=r['expected_gold_hash']:
                result['records'].append(pending(r,'gold_snapshot_order_or_content_changed','Frozen Gold result hash mismatch'));continue
            sql=r['sql']
            if sql not in cache:
                try:
                    data,cn=execute(sql);cache[sql]=dict(data=data,width=len(cn),hash=sha(data))
                except Exception as exc:cache[sql]=dict(error=type(exc).__name__+': '+str(exc))
            e=cache[sql]
            if 'error' in e:
                result['records'].append(pending(r,'order_execution_unresolved',e['error']));continue
            if r.get('candidate_result_sha256') and e['hash']!=r['candidate_result_sha256']:
                result['records'].append(pending(r,'candidate_snapshot_order_or_content_changed','Frozen candidate result hash mismatch'));continue
            m=match_ordered(ordered,e['data'],r['legal_gold_columns'],e['width'],blocks)
            lo=m['lower'] if r['sql_access']=='authorized' else 0.
            hi=min(m['upper'],r['pre_order_upper'])
            assert lo<=hi+1e-12, 'New ordered match exceeds frozen score bound'
            r.update(score_lower=min(lo,hi),score_upper=hi,order_status=m['reason'],order_match=m,
                     order_candidate_result_sha256=e['hash'],order_gold_result_sha256=gh)
            result['records'].append(r)
        result['status']='complete'
    except Exception as exc:
        done={r['candidate_id'] for r in result['records']}
        result.update(status='order_reference_unresolved',error=type(exc).__name__+': '+str(exc))
        for r in task['records']:
            if r['candidate_id'] not in done:result['records'].append(pending(r,'order_reference_unresolved',result['error']))
    finally:
        if con is not None:con.close()
    return result

def summarize(job,tasks):
    records=[json.loads(s) for s in (job/'unchanged.jsonl').open()]
    for t in tasks:
        records.extend(json.loads((job/'clusters'/(sha(t['cluster_key'])[:24]+'.json')).read_text())['records'])
    assert len(records)==29640 and len({r['candidate_id'] for r in records})==29640
    (job/'scores.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=True,default=str)+'\n' for r in records))
    summaries=[]
    for model in ['deepseek_flash','qwen25_14b','arctic_r1_7b','qwen25_7b','qwen38_27b']:
        for method in ['baseline','serial','joint','main']:
            arm=[r for r in records if (r['model'],r['method'])==(model,method)];assert len(arm)==1482
            for group,n in [('all',1482),('allowed',1402),('denied',80)]:
                rs=[r for r in arm if group=='all' or r['original_permission']==group];assert len(rs)==n
                summaries.append(dict(model=model,method=method,group=group,N=n,
                    completion_lower=sum(r['score_lower'] for r in rs)/n,completion_upper=sum(r['score_upper'] for r in rs)/n,
                    previous_lower=sum(r['pre_order_lower'] for r in rs)/n,previous_upper=sum(r['pre_order_upper'] for r in rs)/n,
                    reduced=sum(r['score_upper']<r['pre_order_lower']-1e-12 for r in rs),
                    order_pending=sum(r['order_status'] in ['gold_snapshot_order_or_content_changed','candidate_snapshot_order_or_content_changed','order_reference_unresolved','order_execution_unresolved','ordered_mapping_search_pending'] for r in rs),
                    statuses=dict(Counter(r['order_status'] for r in rs))))
    write(job/'summary.json',summaries)
    changes=[r for r in records if abs(r['score_lower']-r['pre_order_lower'])>1e-12 or abs(r['score_upper']-r['pre_order_upper'])>1e-12]
    (job/'changed_records.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=True,default=str)+'\n' for r in changes))
    complete=dict(records=len(records),clusters=len(tasks),changes=len(changes),statuses=dict(Counter(r['order_status'] for r in records)),
                  lower_score_decreased=sum(r['score_lower']<r['pre_order_lower']-1e-12 for r in records),
                  upper_score_decreased=sum(r['score_upper']<r['pre_order_upper']-1e-12 for r in records),
                  new_model_calls=0,SHR_changed=False,BSS_recomputed=False)
    write(job/'COMPLETE.json',complete);print(json.dumps(complete),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);p.add_argument('--prepare-only',action='store_true');args=p.parse_args()
    self_test();job=args.job;job.mkdir(exist_ok=True)
    tasks=prepare(job)
    if args.prepare_only:return
    (job/'clusters').mkdir(exist_ok=True)
    pending=[t for t in tasks if not (job/'clusters'/(sha(t['cluster_key'])[:24]+'.json')).exists()]
    done=len(tasks)-len(pending)
    with ProcessPoolExecutor(max_workers=6) as pool:
        for future in as_completed({pool.submit(evaluate,t):t for t in pending}):
            result=future.result();write(job/'clusters'/(sha(result['cluster_key'])[:24]+'.json'),result)
            done+=1;write(job/'STATUS.json',dict(done=done,total=len(tasks)))
            print(json.dumps(dict(done=done,total=len(tasks),cluster=result['cluster_key'],status=result['status'])),flush=True)
    summarize(job,tasks)

if __name__=='__main__':main()
