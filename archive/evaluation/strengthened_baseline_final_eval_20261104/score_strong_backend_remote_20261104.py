"""Reuse unchanged original SQL scorer on existing server databases."""
import argparse,collections,hashlib,json,sys,fcntl
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
ROOT=Path('/path/to/access');sys.path.insert(0,str(ROOT/'scripts'))
import score_paired_main_sql_20261102_remote as original
EXPECTED='b42b053e7319b015fbefa332e0e62bab480cf93a12ff5f7be031a2df125dd3ef'
def read(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();job=a.job
    if (job.parent/'SCORING_HOLD.json').exists():raise RuntimeError('Scoring held pending method-specific protocol review')
    assert hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()==EXPECTED
    with (job/'scorer.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);tasks=read(job/'sql_tasks.jsonl');path=job/'sql_results.jsonl';done={r['id']:r for r in read(path)} if path.exists() else {}
        assert len({t['id'] for t in tasks})==len(tasks)
        with path.open('a',buffering=1) as f,ProcessPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(original.score,t) for t in tasks if t['id'] not in done]
            for fu in as_completed(futures):
                r=fu.result();f.write(json.dumps(r,ensure_ascii=False)+'\n');done[r['id']]=r
        assert set(done)=={t['id'] for t in tasks}
        status=dict(state='complete',tasks=len(tasks),scorer_sha256=EXPECTED,gold_errors=sum(not r['gold_ok'] for r in done.values()),sql_access=dict(collections.Counter(r['sql_access'] for r in done.values())),generation=dict(collections.Counter(r['generation_status'] for r in done.values())))
        (job/'SCORING_STATUS.json').write_text(json.dumps(status,indent=2));print(json.dumps(status),flush=True)
if __name__=='__main__':main()
