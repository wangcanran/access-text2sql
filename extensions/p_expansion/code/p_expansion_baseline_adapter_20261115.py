"""Adapt dataset identity only; invoke unchanged published baseline runners."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def main():
 p=argparse.ArgumentParser();p.add_argument('--arm',choices=['serial','joint'],required=True);p.add_argument('--qid',type=int,required=True);p.add_argument('--fixture',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--check-only',action='store_true');p.add_argument('--resume',action='store_true');a=p.parse_args()
 from scripts import run_strengthened_baselines as shared
 from scripts import run_bird_conv_nl_v2,run_bird_joint_nl_v2
 from clarification_deferred_deletion.baseline import module
 def prepare(args):
  selection=shared.read(args.fixture/'selection.json')
  for name,h in selection['fixture_payload_sha256'].items():assert shared.sha(args.fixture/name)==h
  sources=shared.read(args.fixture/'sources.json');schemas=shared.read(args.fixture/'schemas.json');roles=shared.read(args.fixture/'roles.json');loaded=[]
  for c in selection['cases']:
   if c['question_id']!=a.qid:continue
   s=sources[str(c['question_id'])];role=next(r for r in roles if r['metadata']['question_id']==c['policy_source_question_id'] and r['role']==c['role'])
   context=module('live').schema_context(schemas[c['db_id']]);allowed=module('live').engine_module('policy').allowed_columns(context,role['policy'])
   assert set(allowed)==set(c['allowed_columns']);assert s['source_sha256']==c['source_sha256']
   assert hashlib.sha256(json.dumps(role['policy'],sort_keys=True).encode()).hexdigest()==c['policy_sha256']
   row=dict(c,id=':'.join(str(c[k]) for k in ('question_id','level','role','policy_sha256')))
   loaded.append((row,c,s,context))
  assert loaded;return loaded
 shared.prepare=prepare
 runner=run_bird_conv_nl_v2 if a.arm=='serial' else run_bird_joint_nl_v2
 sys.argv=[str(runner.__file__),'--fixture',str(a.fixture),'--out',str(a.out),'--model','deepseek-flash','--turns','30','--max-calls','80','--agent-max-tokens','512']
 if a.check_only:sys.argv.append('--check-only')
 if a.resume:sys.argv.append('--resume')
 runner.main()
if __name__=='__main__':main()
