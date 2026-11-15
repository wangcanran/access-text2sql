"""Server watcher: score new strong backends with the frozen result-completion metric."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import fcntl

ROOT=Path('/path/to/access')
JOB=ROOT/'output/strengthened_multibackend_20261105'
ENGINE=ROOT/'output/result_output_completion_20261105_v1'
sys.path.insert(0,str(ENGINE))


def read(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def write(p, rows):
    p.write_text(''.join(json.dumps(r,ensure_ascii=True)+'\n' for r in rows))


def score(name, arm):
    source=JOB/name/arm
    dest=source/'result_completion'
    dest.mkdir(exist_ok=True)
    if (dest/'SUMMARY.json').exists(): return
    refs=read(JOB/'frozen_completion_reference.jsonl')
    proofs={r['candidate_sql_sha256']:r for r in read(JOB/'permission_source_reviews.jsonl')
            if r.get('native_explain') and 'reads' in r}
    scores={r['id']:r for r in read(source/'sql_results.jsonl')}
    sqls={r['id']:r['sql'] for r in read(source/'sql_tasks.jsonl')}
    front={r['id']:r for r in read(source/'evaluation_rows.jsonl')}
    assert len(front)==len(refs)==2800
    tasks={};cohort=[]
    for ref in refs:
        rid=ref['request_id'];s=scores.get(rid);sql=sqls.get(rid,'')
        assert (s is not None)==(front[rid]['outcome']=='deliver')
        access=s['sql_access'] if s else 'no_sql'
        digest=hashlib.sha256(sql.encode()).hexdigest()
        evidence=None
        if access=='unresolved' and digest in proofs:
            proof=proofs[digest]
            grants={v.casefold() for v in ref['allowed_columns']}
            bad=[[t,c] for t,c in proof['reads'] if f'{t}.{c}'.casefold() not in grants and f'{t}.*'.casefold() not in grants]
            access='unauthorized' if bad else 'authorized'
            evidence=dict(candidate_sql_sha256=digest,reads=proof['reads'],unauthorized_dependencies=bad)
        item=dict(candidate_id=f'{arm}:{name}:{rid}',method=arm,model=name,
            request_id=rid,cluster_key=ref['cluster_key'],original_permission=ref['original_permission'],
            level=ref['level'],sql_access=access,legal_gold_columns=ref['legal_gold_columns'],
            sql=sql,original_outcome=front[rid]['outcome'],allowed_columns=ref['allowed_columns'])
        item['correct_stop']=front[rid]['outcome']=='refuse' or bool(s and s['generation_status']=='downstream_refusal')
        if evidence: item['source_permission_review']=evidence
        cohort.append(item)
        if not item['legal_gold_columns']: continue
        ck=tuple(ref['cluster_key'])
        if ck not in tasks: tasks[ck]=dict(cluster_key=list(ck),gold_sql=ref['gold_sql'],candidates=[])
        tasks[ck]['candidates'].append(item)
    assert sum(bool(r['legal_gold_columns']) for r in cohort)==1482
    write(dest/'cohort.jsonl',cohort)
    write(dest/'tasks.jsonl',tasks.values())
    with (dest/'scoring.log').open('a') as log:
        subprocess.run([sys.executable,'-u',str(ENGINE/'score_result_completion_20261105.py'),
                        '--job',str(dest),'--workers','6'],stdout=log,stderr=subprocess.STDOUT,check=True)
    native={r['candidate_id']:r for r in read(dest/'scores.jsonl')}
    positive=[native[r['candidate_id']] for r in cohort if r['legal_gold_columns']]
    denied=[r for r in positive if r['original_permission']=='denied']
    zero=[r for r in cohort if not r['legal_gold_columns']]
    assert len(positive)==1482 and len(denied)==80 and len(zero)==1318
    def summary(rs):
        return dict(N=len(rs),completion_lower=sum(r['score_lower'] for r in rs)/len(rs),
                    completion_upper=sum(r['score_upper'] for r in rs)/len(rs),
                    full_success_lower=sum(r['score_lower']>=1-1e-12 for r in rs)/len(rs),
                    full_success_upper=sum(r['score_upper']>=1-1e-12 for r in rs)/len(rs),
                    unresolved=sum(abs(r['score_lower']-r['score_upper'])>1e-12 for r in rs))
    result=dict(model=name,method=arm,metric='result_output_completion_permissions_v2_20261105',
                positive=summary(positive),denied_positive=summary(denied),
                zero_N=len(zero),zero_correct_handling=sum(r['correct_stop'] for r in zero)/len(zero),
                reference_sha256=hashlib.sha256((JOB/'frozen_completion_reference.jsonl').read_bytes()).hexdigest(),
                scorer_sha256=hashlib.sha256((ENGINE/'score_result_completion_20261105.py').read_bytes()).hexdigest())
    (dest/'SUMMARY.json').write_text(json.dumps(result,indent=2))
    print('COMPLETION_SCORED',name,arm,json.dumps(result),flush=True)


def main():
    with (JOB/'completion_watcher.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pending={(m,a) for m in ('qwen25_7b','qwen25_14b','arctic_r1_7b','qwen38_27b') for a in ('serial','joint')}
        while pending:
            for model,arm in sorted(pending):
                status=JOB/model/arm/'PIPELINE_STATUS.json'
                if status.exists() and json.loads(status.read_text()).get('state')=='complete':
                    score(model,arm);pending.remove((model,arm))
            (JOB/'COMPLETION_STATUS.json').write_text(json.dumps(dict(state='waiting' if pending else 'complete',pending=sorted(pending)),indent=2))
            if pending:
                upstream=JOB/'STATUS.json'
                if upstream.exists() and json.loads(upstream.read_text()).get('state')=='failed':
                    raise RuntimeError('Backend supervisor failed; inspect STATUS.json')
                time.sleep(30)


if __name__=='__main__': main()
