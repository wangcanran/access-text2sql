"""One fresh recovery attempt per current failed request, carrying own errors."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time

JOB = Path('/path/to/access/output/four_arm_terminal_20261106')
sys.path.insert(0, str(JOB))
import complete_four_arm_terminal_cohort as pipeline


def main():
    with (JOB/'prior_error_recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        tools = JOB/'prior_error_feedback_tools_v1'
        status = JOB/'PRIOR_ERROR_RECOVERY_STATUS.json'
        for arm in ('no_clarification', 'no_pruning'):
            rows = [r for r in pipeline.selected_rows(arm) if r['outcome'] not in pipeline.TERMINAL]
            if not rows:
                continue
            errors = {}
            sources = []
            for row in rows:
                path = Path(row['trace'])
                raw = path.read_bytes()
                trace = json.loads(raw)
                session = (trace.get('episode') or {}).get('session') or {}
                reason = trace.get('error') or session.get('contract_unresolved_reason')
                assert isinstance(reason, str) and reason
                errors[str(row['question_id'])+'_'+str(row['level'])] = reason
                sources.append(dict(id=row['id'], trace=str(path), sha256=hashlib.sha256(raw).hexdigest(), copied_field='error or contract_unresolved_reason only'))
            parent = JOB/arm/'retries'
            number = max(int(p.name.split('_')[1]) for p in parent.glob('round_*') if p.is_dir()) + 1
            out = parent/f'round_{number:02d}'
            assert not out.exists()
            selection = out.with_name(out.name+'_ids.json')
            history = out.with_name(out.name+'_prior_errors.json')
            assert not selection.exists() and not history.exists()
            selection.write_text(json.dumps([r['id'] for r in rows]))
            history.write_text(json.dumps(errors))
            cmd = [sys.executable, '-u', str(tools/'retry_four_arm_frontend_worker.py'),
                   '--workspace', str(pipeline.WORK), '--selection', str(selection),
                   '--variant', arm, '--out', str(out), '--coarse-feedback', '--contract-feedback',
                   '--prior-failure-feedback', str(history)]
            proof = dict(controller_pid=os.getpid(), state='retrying', arm=arm, round=number,
                         command=cmd, failure_sources=sources, started_at=time.time(),
                         source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in tools.glob('*.py')})
            with out.with_suffix('.log').open('x') as log:
                worker = subprocess.Popen(cmd, cwd=pipeline.WORK, stdout=log, stderr=subprocess.STDOUT)
                proof['worker_pid'] = worker.pid
                out.with_name(out.name+'_prior_error_launch.json').write_text(json.dumps(proof, indent=2))
                status.write_text(json.dumps(proof, indent=2))
                code = worker.wait()
            if code:
                status.write_text(json.dumps(dict(proof, state='worker_error', returncode=code), indent=2))
                raise RuntimeError(f'Worker exited {code}; inspect log before restarting')
        pending = {arm:sum(r['outcome'] not in pipeline.TERMINAL for r in pipeline.selected_rows(arm)) for arm in ('no_clarification', 'no_pruning')}
        status.write_text(json.dumps(dict(controller_pid=os.getpid(), state='complete' if not any(pending.values()) else 'needs_cause_review', pending=pending, finished_at=time.time()), indent=2))


if __name__ == '__main__':
    main()
