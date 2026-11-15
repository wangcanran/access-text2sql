"""Bounded cause-informed retries for current pending nonserial cases only.

The original supervisor continues the serial cohort. This controller occupies
at most one additional frontend slot, retains every attempt, and never relabels
or overwrites an existing completion. Its status is separate from the pipeline.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

JOB = Path('/path/to/access/output/four_arm_terminal_20261106')
sys.path.insert(0, str(JOB))
import complete_four_arm_terminal_cohort as pipeline


def live_workers():
    workers = []
    for folder in Path('/proc').iterdir():
        if not folder.name.isdigit():
            continue
        try:
            args = (folder/'cmdline').read_bytes().split(b'\0')
            if any(a.startswith(str(JOB).encode()) and a.endswith(b'/retry_four_arm_frontend_worker.py') for a in args):
                if (folder/'stat').read_text().split(') ', 1)[1][0] != 'Z':
                    workers.append(int(folder.name))
        except (OSError, ProcessLookupError):
            pass
    return workers


def save(value):
    value.update(controller_pid=os.getpid(), updated_at=time.time())
    target = JOB/'CONTRACT_RECOVERY_STATUS.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(target)


def main():
    import fcntl
    with (JOB/'contract_recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        tools = JOB/'contract_feedback_v3_tools'
        source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in tools.glob('*.py')}
        for cycle in range(3):
            for arm in ('no_clarification', 'no_pruning'):
                rows = pipeline.selected_rows(arm)
                pending = [r['id'] for r in rows if r['outcome'] not in pipeline.TERMINAL]
                if not pending:
                    continue
                while len(live_workers()) >= 2:
                    save(dict(state='waiting_frontend_slot', active_pids=live_workers(), arm=arm))
                    time.sleep(15)
                # Recompute after waiting, so completed cases never reenter.
                rows = pipeline.selected_rows(arm)
                pending = [r['id'] for r in rows if r['outcome'] not in pipeline.TERMINAL]
                if not pending:
                    continue
                parent = JOB/arm/'retries'
                number = max(int(p.name.split('_')[1]) for p in parent.glob('round_*') if p.is_dir()) + 1
                out = parent/f'round_{number:02d}'
                selection = out.with_name(out.name+'_ids.json')
                assert not out.exists() and not selection.exists()
                selection.write_text(json.dumps(pending))
                command = [sys.executable, '-u', str(tools/'retry_four_arm_frontend_worker.py'),
                           '--workspace', str(pipeline.WORK), '--selection', str(selection),
                           '--variant', arm, '--out', str(out), '--contract-feedback', '--coarse-feedback']
                launch = dict(arm=arm, cycle=cycle, round=number, selected_ids=pending,
                              source_sha256=source_hashes, command=command, started_at=time.time())
                with out.with_suffix('.log').open('x') as log:
                    worker = subprocess.Popen(command, cwd=pipeline.WORK, stdout=log, stderr=subprocess.STDOUT)
                    launch['worker_pid'] = worker.pid
                    out.with_name(out.name+'_recovery_launch.json').write_text(json.dumps(launch, indent=2))
                    save(dict(state='retrying', **launch))
                    returncode = worker.wait()
                counts = {}
                for row in pipeline.selected_rows(arm):
                    counts[row['outcome']] = counts.get(row['outcome'], 0) + 1
                save(dict(state='round_complete', arm=arm, round=number, returncode=returncode, outcomes=counts))
                if returncode:
                    raise RuntimeError(f'Worker {worker.pid} exited with {returncode}; inspect its log before restarting')
        pending = {arm: sum(r['outcome'] not in pipeline.TERMINAL for r in pipeline.selected_rows(arm))
                   for arm in ('no_clarification', 'no_pruning')}
        save(dict(state='complete' if not any(pending.values()) else 'needs_cause_review', pending=pending))


if __name__ == '__main__':
    main()
