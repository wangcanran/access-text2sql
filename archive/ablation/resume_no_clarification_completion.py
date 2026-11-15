from pathlib import Path
import sys,json,time,signal,traceback
J=Path('/path/to/access/output/four_arm_terminal_20261106')
signal.signal(signal.SIGHUP,signal.SIG_IGN)
sys.path.insert(0,str(J))
import completion_adapter
completion_adapter.ENGINE=J/'completion_recycle_engine'
try:
 completion_adapter.score('deepseek_flash','no_clarification')
 p=J/'no_clarification/PIPELINE_STATUS.json'
 p.write_text(json.dumps(dict(state='complete',arm='no_clarification',generated=1806,cohort=2800,completed_at=time.time(),completion_recovery='COMPLETION_RECYCLE_PROTOCOL.json'),indent=2))
 (J/'EARLY_BACKEND_STATUS.json').write_text(json.dumps(dict(state='complete',arms=['no_pruning','no_clarification'],completed_at=time.time()),indent=2))
 (J/'COMPLETION_RECOVERY_STATUS.json').write_text(json.dumps(dict(state='complete',completed_at=time.time()),indent=2))
except BaseException as exc:
 (J/'COMPLETION_RECOVERY_STATUS.json').write_text(json.dumps(dict(state='failed',error=repr(exc),at=time.time()),indent=2))
 raise
