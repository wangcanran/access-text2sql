"""Run an explicit failed-case subset through the unchanged frozen runner."""
from pathlib import Path
import argparse,hashlib,json,runpy,sys

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--workspace',type=Path,required=True)
    ap.add_argument('--selection',type=Path,required=True)
    ap.add_argument('--variant',required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--check-only',action='store_true')
    args=ap.parse_args()
    sys.path.insert(0,str(args.workspace))
    from scripts import run_strengthened_baselines as shared
    ids=set(json.loads(args.selection.read_text()))
    prepare=shared.prepare
    def selected(options):
        cases=[c for c in prepare(options) if c[0]['id'] in ids]
        assert len(cases)==len(ids)
        return cases
    shared.prepare=selected
    args.out.mkdir(parents=True,exist_ok=True)
    proof=dict(selected_ids=sorted(ids),worker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               policy='first genuine deliver/refuse, unchanged frozen frontend and prompts',variant=args.variant)
    (args.out/'RETRY_SELECTION.json').write_text(json.dumps(proof,indent=2))
    # The frozen runner requires an empty destination. Keep the selection proof
    # beside it; every attempted trace and its source snapshots remain intact.
    proofpath=args.out.with_name(args.out.name+'_selection_proof.json')
    (args.out/'RETRY_SELECTION.json').replace(proofpath)
    sys.argv=[str(args.workspace/'scripts/run_four_arm_ablations.py'),'--variant',args.variant,
              '--model','deepseek-flash','--out',str(args.out)]
    if (args.out/'CONFIG.json').exists():sys.argv.append('--resume')
    if args.check_only:sys.argv.append('--check-only')
    runpy.run_path(str(args.workspace/'scripts/run_four_arm_ablations.py'),run_name='__main__')

if __name__=='__main__':main()
