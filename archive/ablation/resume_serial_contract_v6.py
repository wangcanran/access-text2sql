"""Resume genuine pending serial cases with the established own-error protocol."""
from pathlib import Path
import collections
import fcntl
import hashlib
import json
import os
import runpy
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

JOB = Path('/path/to/access/output/four_arm_terminal_20261106')
WORK = JOB/'serial_state_repair_workspace'
ARM = 'clarify_then_prune'

def save(name, value):
    path = JOB/name
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)

def active():
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc/'cmdline').read_bytes().split(bytes([0]))
            state = (proc/'stat').read_text().split(') ', 1)[1][0]
            worker = any(x.endswith(b'/retry_four_arm_frontend_worker.py') for x in args) and str(WORK).encode() in args
            controller = str(JOB/'resume_serial_terminal_safely.py').encode() in args
            if state != 'Z' and (worker or controller):
                found.append(int(proc.name))
        except OSError:
            pass
    return found

def main():
    with (JOB/'serial_contract_v6.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            live = active()
            if not live:
                break
            save('SERIAL_CONTRACT_V6_STATUS.json', dict(state='waiting_existing_processes', pid=os.getpid(), live_pids=live, time=time.time()))
            time.sleep(30)
        tools = JOB/'contract_feedback_v6_tools'
        pipeline = runpy.run_path(str(JOB/'complete_four_arm_terminal_cohort.py'))
        for cycle in range(1, 3):
            subprocess.run([sys.executable, str(JOB/'resume_serial_terminal_safely.py'), '--export-only'], cwd=JOB, check=True)
            rows = pipeline['selected_rows'](ARM)
            pending = [r for r in rows if r['outcome'] not in {'deliver', 'refuse'}]
            save('SERIAL_CONTRACT_V6_STATUS.json', dict(state='recovering' if pending else 'frontend_complete', pid=os.getpid(), cycle=cycle, pending=len(pending), outcomes=dict(collections.Counter(r['outcome'] for r in rows)), time=time.time()))
            if not pending:
                break
            retry = JOB/ARM/'retries'
            number = max(int(p.name.split('_')[1]) for p in retry.glob('round_*') if p.is_dir())+1
            tasks = []
            for shard in range(2):
                subset = pending[shard::2]
                if not subset:
                    continue
                out = retry/f'round_{number+shard:02d}'
                selection = out.with_name(out.name+'_ids.json')
                history = out.with_name(out.name+'_prior_errors.json')
                assert not out.exists() and not selection.exists() and not history.exists()
                errors = {}; sources = []
                for row in subset:
                    path = Path(row['trace']); raw = path.read_bytes(); trace = json.loads(raw)
                    session = (trace.get('episode') or {}).get('session') or {}
                    delivery = (trace.get('episode') or {}).get('delivery') or {}
                    reason = trace.get('error') or session.get('contract_unresolved_reason') or delivery.get('reason')
                    assert isinstance(reason, str) and reason, row['id']
                    errors[str(row['question_id'])+'_'+str(row['level'])] = reason
                    sources.append(dict(id=row['id'], trace=str(path), sha256=hashlib.sha256(raw).hexdigest(), copied_field='public error only'))
                selection.write_text(json.dumps([r['id'] for r in subset]))
                history.write_text(json.dumps(errors))
                cmd = [sys.executable, '-u', str(tools/'retry_four_arm_frontend_worker.py'), '--workspace', str(WORK), '--selection', str(selection), '--variant', ARM, '--out', str(out), '--coarse-feedback', '--contract-feedback', '--prior-failure-feedback', str(history)]
                proof = dict(time=time.time(), command=cmd, failure_sources=sources, policy='First genuine terminal retained; all attempts and costs preserved; public own-error input change recorded', source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tools.glob('*.py')})
                out.with_name(out.name+'_prior_error_launch.json').write_text(json.dumps(proof, indent=2))
                tasks.append((out, cmd))
            def run(task):
                out, cmd = task
                with out.with_suffix('.log').open('x') as log:
                    return subprocess.run(cmd, cwd=WORK, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT).returncode
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, tasks))
            assert not any(results), results
        subprocess.run([sys.executable, str(JOB/'resume_serial_terminal_safely.py'), '--export-only'], cwd=JOB, check=True)
        rows = pipeline['selected_rows'](ARM)
        pending = sum(r['outcome'] not in {'deliver', 'refuse'} for r in rows)
        save('SERIAL_CONTRACT_V6_STATUS.json', dict(state='needs_cause_review' if pending else 'frontend_complete', pid=os.getpid(), pending=pending, outcomes=dict(collections.Counter(r['outcome'] for r in rows)), time=time.time()))
        if not pending:
            subprocess.run([sys.executable, str(JOB/'complete_four_arm_terminal_cohort.py')], cwd=JOB, check=True)

if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        save('SERIAL_CONTRACT_V6_ERROR.json', dict(error=repr(error), time=time.time()))
        raise
