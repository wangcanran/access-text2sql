"""Run an explicit failed-case subset through the unchanged frozen runner."""
from pathlib import Path
from contextlib import ExitStack, nullcontext
import argparse,hashlib,json,runpy,sys

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--workspace',type=Path,required=True)
    ap.add_argument('--selection',type=Path,required=True)
    ap.add_argument('--variant',required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--check-only',action='store_true')
    ap.add_argument('--coarse-feedback', action='store_true',
                    help='Append precise diagnostics after rejected coarse JSON; retain original validators')
    ap.add_argument('--contract-feedback', action='store_true',
                    help='Explain existing analysis/decomposition errors on retries only')
    ap.add_argument('--prior-failure-feedback', type=Path,
                    help='Per-case public contract errors from its own preceding failed attempt')
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
    proof=dict(iterative_validation=dict(version='bounded_validation_recovery_v1',extra_attempts=4,scope='same validator and public failure feedback; no output mutation'),selected_ids=sorted(ids),worker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               policy='first genuine deliver/refuse, unchanged frozen frontend and prompts',variant=args.variant)
    recovery = nullcontext()
    contract_recovery = nullcontext()
    prior_recovery = nullcontext()
    if args.prior_failure_feedback:
        import scripts
        folder = Path(__file__).resolve().parent
        scripts.__path__ = [str(folder), *scripts.__path__]
        from scripts.ablation_prior_failure_feedback import installed as prior_installed, VERSION as prior_version
        from clarification_deferred_deletion.baseline import module
        history = json.loads(args.prior_failure_feedback.read_text())
        proof['prior_failure_feedback'] = dict(version=prior_version,
            input_path=str(args.prior_failure_feedback),
            input_sha256=hashlib.sha256(args.prior_failure_feedback.read_bytes()).hexdigest(),
            source_sha256=hashlib.sha256((folder/'ablation_prior_failure_feedback.py').read_bytes()).hexdigest(),
            scope='Only own preceding public contract error; initial coarse/analysis payloads include this diagnostic; no private answers or evaluation labels')
        prior_recovery = prior_installed(module('recorder').Recorder, history)
    if args.contract_feedback:
        import scripts
        folder = Path(__file__).resolve().parent/'coarse_feedback_tools'
        if not folder.is_dir():
            folder = Path(__file__).resolve().parent
        scripts.__path__ = [str(folder), *scripts.__path__]
        from scripts.ablation_contract_retry_feedback import installed, VERSION
        from clarification_deferred_deletion.baseline import module
        proof['contract_feedback'] = dict(version=VERSION,
            scope='Retry contract_error diagnostics only; no response mutation or validator changes',
            source_sha256=hashlib.sha256((folder/'ablation_contract_retry_feedback.py').read_bytes()).hexdigest())
        contract_recovery = installed(module('recorder').Recorder)
    if args.coarse_feedback:
        import scripts
        folder = Path(__file__).resolve().parent/'coarse_feedback_tools'
        if not folder.is_dir():
            folder = Path(__file__).resolve().parent
        scripts.__path__ = [str(folder), *scripts.__path__]
        from scripts.ablation_coarse_retry_feedback import installed
        from clarification_deferred_deletion.baseline import main as frozen_main
        proof['coarse_feedback'] = dict(
            scope='Rejected coarse JSON feedback only; original validation, initial prompts and all semantic/policy gates unchanged',
            source_sha256={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in
                           ['ablation_coarse_retry_feedback.py','audit_ablation_coarse_failures.py']})
        recovery = installed(frozen_main)
    (args.out/'RETRY_SELECTION.json').write_text(json.dumps(proof,indent=2))
    # The frozen runner requires an empty destination. Keep the selection proof
    # beside it; every attempted trace and its source snapshots remain intact.
    proofpath=args.out.with_name(args.out.name+'_selection_proof.json')
    (args.out/'RETRY_SELECTION.json').replace(proofpath)
    sys.argv=[str(args.workspace/'scripts/run_four_arm_ablations.py'),'--variant',args.variant,
              '--model','deepseek-flash','--out',str(args.out)]
    if (args.out/'CONFIG.json').exists():sys.argv.append('--resume')
    if args.check_only:sys.argv.append('--check-only')
    with ExitStack() as stack:
        stack.enter_context(recovery)
        stack.enter_context(contract_recovery)
        from scripts.ablation_iterative_validation import installed as iterative_installed
        stack.enter_context(iterative_installed())
        stack.enter_context(prior_recovery)
        runpy.run_path(str(args.workspace/'scripts/run_four_arm_ablations.py'),run_name='__main__')

if __name__=='__main__':main()
