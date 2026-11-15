"""Finish report only after supervisor confirms every paired score."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();j=a.job.resolve();w=j/'workspace'
 while not (j/'COMPLETE.json').exists():
  status=json.loads((j/'STATUS.json').read_text())
  if status['state'] in ['failed_tasks_need_repair','smoke_needs_review','evaluation_needs_review']:
   (j/'REPORT_STATUS.json').write_text(json.dumps(dict(state='waiting_for_repair',upstream=status['state']),indent=2));return
  pid=json.loads((j/'BATCH_LAUNCH.json').read_text())['pid']
  if not Path('/proc',str(pid)).exists():
   (j/'REPORT_STATUS.json').write_text(json.dumps(dict(state='supervisor_stopped',upstream=status),indent=2));return
  time.sleep(20)
 cmd=[sys.executable,'-m','scripts.summarize_p_expansion_20261115','--job',str(j)]
 result=subprocess.run(cmd,cwd=w,env=dict(os.environ,PYTHONPATH=str(w)))
 (j/'REPORT_STATUS.json').write_text(json.dumps(dict(state='complete' if result.returncode==0 else 'report_failed',returncode=result.returncode),indent=2))
if __name__=='__main__':main()
