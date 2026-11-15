"""Portable fixture adapter; preserve the locked paper mechanism and prompts."""
import argparse,hashlib,json,sys,runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def main():
 p=argparse.ArgumentParser();p.add_argument('--method',choices=['access','serial','policy','no_clarification','no_pruning','clarify_then_prune'],required=True);p.add_argument('--fixture',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--model',default='deepseek-flash');p.add_argument('--check-only',action='store_true');p.add_argument('--resume',action='store_true');p.add_argument('--cases');a=p.parse_args()
 fixture=a.fixture.resolve();selection=json.loads((fixture/'selection.json').read_text(encoding='utf8'))
 chosen={tuple(map(int,x.split(':'))) for x in a.cases.split(',')} if a.cases else None
 if a.method=='access':
  from scripts import run_clarification_v10 as runner
  sys.argv=['run_clarification_v10','--model',a.model,'--selection',str(fixture/'selection.json'),'--fixture-dir',str(fixture),'--out',str(a.out),'--unresolved-retries','2']
  if a.cases:sys.argv+=['--cases',a.cases]
  if a.check_only:sys.argv.append('--check-only')
  if a.resume:sys.argv.append('--resume')
  runner.main();return
 from scripts import run_strengthened_baselines as shared
 from clarification_deferred_deletion.baseline import module
 def prepare(args):
  for name,h in selection['fixture_payload_sha256'].items():assert shared.sha(fixture/name)==h,name
  sources=shared.read(fixture/'sources.json');roles=shared.read(fixture/'roles.json');schemas=shared.read(fixture/'schemas.json');loaded=[]
  for c in selection['cases']:
   if chosen is not None and (c['question_id'],c['level']) not in chosen:continue
   s=sources[str(c['question_id'])];role=next(r for r in roles if r['db_id']==c['db_id'] and r['role']==c['role'] and r['metadata']['question_id']==c['policy_source_question_id'])
   context=module('live').schema_context(schemas[c['db_id']]);allowed=module('live').engine_module('policy').allowed_columns(context,role['policy'])
   assert set(allowed)==set(c['allowed_columns']);assert s['source_sha256']==c['source_sha256']
   assert hashlib.sha256(json.dumps(role['policy'],sort_keys=True).encode()).hexdigest()==c['policy_sha256']
   loaded.append((dict(c,id=':'.join(str(c[k]) for k in ['question_id','level','role','policy_sha256'])),c,s,context))
  assert loaded
  if chosen is not None:assert len(loaded)==len(chosen)
  return loaded
 shared.prepare=prepare
 if a.method in ['serial','policy']:
  from scripts import run_bird_conv_nl_v2,run_bird_joint_nl_v2
  runner=run_bird_conv_nl_v2 if a.method=='serial' else run_bird_joint_nl_v2
  sys.argv=['frontend','--fixture',str(fixture),'--out',str(a.out),'--model',a.model,'--turns','30','--max-calls','80','--agent-max-tokens','512']
 else:
  sys.argv=['ablations','--fixture',str(fixture),'--out',str(a.out),'--model',a.model,'--variant',a.method,'--turns','30','--max-calls','80']
 if a.check_only:sys.argv.append('--check-only')
 if a.resume:sys.argv.append('--resume')
 if a.method in ['serial','policy']:runner.main()
 else:runpy.run_module('scripts.run_four_arm_ablations',run_name='__main__')
if __name__=='__main__':main()
