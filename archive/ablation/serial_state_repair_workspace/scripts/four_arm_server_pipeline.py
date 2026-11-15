"""Detached server orchestration: actual frontends -> SQL -> frozen scorer.

Run only from the staged workspace. All arms keep incomplete observations.
"""
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

WORKSPACE = Path(__file__).resolve().parents[1]
JOB = WORKSPACE.parent
ARMS = ('full', 'no_clarification', 'no_pruning', 'clarify_then_prune')
MUTEX = threading.Lock()
BACKEND_MUTEX = threading.Lock()


def read(p):
    return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]


def save(p, value):
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2))
    tmp.replace(p)


def run_arm(arm):
    dest = JOB / arm
    dest.mkdir(exist_ok=True)
    save(dest / 'FRONTEND_STATUS.json', dict(state='running', arm=arm, pid=os.getpid()))
    statuses = []
    for shard in range(2):
        out = dest / 'frontend' / f'shard{shard}'
        cmd = [sys.executable, '-u', str(WORKSPACE / 'scripts/run_four_arm_ablations.py'),
               '--variant', arm, '--model', 'deepseek-flash', '--out', str(out),
               '--num-shards', '2', '--shard-index', str(shard)]
        if (out / 'CONFIG.json').exists():
            cmd.append('--resume')
        log = (dest / f'frontend_shard{shard}.log').open('a')
        proc = subprocess.Popen(cmd, cwd=WORKSPACE, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
        statuses.append(proc.wait())
        log.close()
        if statuses[-1]:
            break
    if any(statuses):
        raise RuntimeError(f'{arm}: frontend process failure {statuses}')
    rows = [r for shard in range(2)
            for r in read(dest / 'frontend' / f'shard{shard}' / 'evaluation_rows.jsonl')]
    if len(rows) != 2800 or len({r['id'] for r in rows}) != 2800:
        raise RuntimeError(f'{arm}: incomplete or duplicate cohort')
    (dest / 'evaluation_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    counts = {outcome: sum(r['outcome'] == outcome for r in rows)
              for outcome in sorted({r['outcome'] for r in rows})}
    save(dest / 'FRONTEND_STATUS.json', dict(state='complete', arm=arm, outcomes=counts,
                                          recorded=2800, completed_at=time.time()))
    backend = dest / 'backend_inputs'
    if not (backend / 'MANIFEST.json').exists():
        subprocess.run([sys.executable, str(WORKSPACE / 'scripts/prepare_clarification_text2sql_inputs.py'),
            '--rows', str(dest / 'evaluation_rows.jsonl'), '--selection', str(WORKSPACE /
            'artifacts/clarification_main/manual_flat_gold_v1_instruction_v10_preflight_20261101/selection.json'),
            '--gold-dir', '/path/to/access/data/gold/manual_flat_gold_v1',
            '--out', str(backend)], cwd=WORKSPACE, check=True)
    n = sum(r['outcome'] == 'deliver' and bool(r['clarified_text'].strip()) for r in rows)
    with MUTEX:
        manifest = json.loads((JOB / 'MANIFEST.json').read_text())
        manifest['arms'][arm]['backend_requests'] = n
        save(JOB / 'MANIFEST.json', manifest)
    with BACKEND_MUTEX, (dest / 'backend.log').open('a') as log:
        subprocess.run([sys.executable, '-u', str(JOB / 'run_backend.py'), '--arm', arm],
                       cwd=WORKSPACE, stdout=log, stderr=subprocess.STDOUT, check=True)
    return dict(arm=arm, frontend=counts, backend_requests=n, state='complete')


def main():
    with (JOB / 'four_arm.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(JOB / 'STATUS.json', dict(state='running', pid=os.getpid(), started_at=time.time()))
        completed = []
        failures = []
        # All four arms run concurrently, one shard per arm at a time. This
        # preserves contemporaneous controls under the 2 GiB container cap.
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs = {pool.submit(run_arm, arm): arm for arm in ARMS}
            for future in concurrent.futures.as_completed(jobs):
                arm = jobs[future]
                try:
                    completed.append(future.result())
                except Exception as exc:
                    failures.append(dict(arm=arm, error_type=type(exc).__name__, error=str(exc)))
                save(JOB / 'STATUS.json', dict(state='running', pid=os.getpid(),
                    completed=completed, failures=failures))
        save(JOB / 'STATUS.json', dict(state='failed' if failures else 'complete',
            completed=completed, failures=failures, ended_at=time.time()))


if __name__ == '__main__':
    main()
