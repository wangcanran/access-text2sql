"""Run frozen backend/evaluation for complete arms while serial frontend runs."""
from pathlib import Path
import fcntl, hashlib, json, os, runpy, signal, subprocess, sys, time

JOB=Path('/path/to/access/output/four_arm_terminal_20261106')

def save(path, value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2))
    temporary.replace(path)

def main():
    signal.signal(signal.SIGHUP,signal.SIG_IGN)
    with (JOB/'early_backend.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pipeline=runpy.run_path(str(JOB/'complete_four_arm_terminal_cohort.py'))
        proof=json.loads((JOB/'COMPLETED_BACKEND_PREFLIGHT.json').read_text())
        for arm in ['no_pruning','no_clarification']:
            folder=JOB/arm
            rows=pipeline['selected_rows'](arm)
            assert len(rows)==2800 and all(r['outcome'] in ['deliver','refuse'] for r in rows)
            assert hashlib.sha256((folder/'evaluation_rows.jsonl').read_bytes()).hexdigest()==proof['arms'][arm]['source_rows_sha256']
            assert rows==pipeline['read'](folder/'evaluation_rows.jsonl')
            pipeline['prepare_inputs'](arm)
            for relative,digest in proof['arms'][arm]['backend_input_sha256'].items():
                assert hashlib.sha256((folder/'backend_inputs'/relative).read_bytes()).hexdigest()==digest
            manifestpath=JOB/'MANIFEST.json'
            manifest=json.loads(manifestpath.read_text()) if manifestpath.exists() else dict(arms={})
            manifest['arms'][arm]=dict(backend_mode='baseline',backend_requests=sum(r['outcome']=='deliver' for r in rows))
            save(manifestpath,manifest)
            save(JOB/'EARLY_BACKEND_STATUS.json',dict(pid=os.getpid(),arm=arm,state='running',time=time.time()))
            with (folder/'early_backend.log').open('a') as log:
                subprocess.run([sys.executable,'-u',str(JOB/'run_backend.py'),'--arm',arm],
                    cwd=pipeline['WORK'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,check=True)
        save(JOB/'EARLY_BACKEND_STATUS.json',dict(pid=os.getpid(),state='complete',arms=['no_pruning','no_clarification'],time=time.time()))

if __name__=='__main__':
    try:
        main()
    except BaseException as exc:
        save(JOB/'EARLY_BACKEND_ERROR.json',dict(error=repr(exc),time=time.time()))
        raise
