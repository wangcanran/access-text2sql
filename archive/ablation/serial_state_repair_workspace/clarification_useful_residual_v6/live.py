"""Frozen-source contract-v5 live runner; cases use existing fixed policy fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from clarification_deferred_deletion.baseline import ROOT, module
from clarification_deferred_deletion.__main__ import overlay_hashes
from clarification_deferred_v3_2.__main__ import hashes
from .method import UsefulResidualMethod as ContractMethod, VERSION, ORIGIN_VERSION


def source_hashes(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.py')}


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--cases', default=None)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', default='gpt-5.5')
    # semantic-compiler remains archived as an independent experiment.  It is
    # deliberately not exposed by the v6 runner after the heldout regression:
    # its adapter still delegates graph mutation to the legacy transaction
    # path, so exposing it here makes it look like a supported mechanism while
    # two different authorities can update the same state.
    p.add_argument('--mechanism', choices=('legacy', 'obligation-pool', 'adaptive-pool', 'adaptive-pool-v2', 'fact-pool', 'joint-fact-pool', 'dependency-pool', 'semantic-compiler-boundary'), default='legacy')
    p.add_argument('--max-cases', type=int, default=None,
                   help='Run only the first N cases of the frozen selection; resume later')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--unresolved-retries', type=int, default=0,
                   help='Retry each unresolved/system_error case up to N additional times')
    p.add_argument('--selection', type=Path,
                   default=Path(__file__).resolve().parent/'fixtures/heldout20.json')
    p.add_argument('--check-only', action='store_true')
    args = p.parse_args()
    method_class, version = ContractMethod, VERSION
    if args.mechanism == 'obligation-pool':
        from .pool_method import PoolMethod, VERSION as POOL_VERSION
        method_class, version = PoolMethod, POOL_VERSION
    elif args.mechanism == 'adaptive-pool':
        from clarification_question_pool_experiment.method import AdaptivePoolMethod, VERSION as EXP_VERSION
        method_class, version = AdaptivePoolMethod, EXP_VERSION
    elif args.mechanism == 'adaptive-pool-v2':
        from clarification_question_pool_experiment.lifecycle_method import LifecyclePoolMethod, VERSION as EXP_VERSION
        method_class, version = LifecyclePoolMethod, EXP_VERSION
    elif args.mechanism == 'fact-pool':
        from clarification_question_pool_experiment.fact_method import FactPoolMethod, VERSION as EXP_VERSION
        method_class, version = FactPoolMethod, EXP_VERSION
    elif args.mechanism == 'joint-fact-pool':
        from clarification_question_pool_experiment.joint_method import JointFactMethod, VERSION as EXP_VERSION
        method_class, version = JointFactMethod, EXP_VERSION
    elif args.mechanism == 'dependency-pool':
        from clarification_question_pool_experiment.dependency_method import DependencyMethod, VERSION as EXP_VERSION
        method_class, version = DependencyMethod, EXP_VERSION
    elif args.mechanism == 'semantic-compiler-boundary':
        from clarification_question_pool_experiment.compiler_boundary_method import BoundaryCompilerMethod, VERSION as EXP_VERSION
        method_class, version = BoundaryCompilerMethod, EXP_VERSION
    selection_data = json.loads(args.selection.read_text(encoding='utf8'))
    pairs = ([tuple(map(int, value.split(':'))) for value in args.cases.split(',')]
             if args.cases else [(r['question_id'], r['level']) for r in selection_data['cases']])
    if len(pairs) != len(set(pairs)):
        p.error('Duplicate cases')
    if args.max_cases is not None and not 1 <= args.max_cases <= len(pairs):
        p.error('--max-cases must be between 1 and the selection size')
    if args.unresolved_retries < 0:
        p.error('--unresolved-retries must be >= 0')
    live = module('live')
    selected = selection_data['cases']
    roles = json.loads(live.POLICIES.read_text(encoding='utf8'))
    schemas = json.loads(live.SCHEMAS.read_text(encoding='utf8'))
    loaded = {}
    for qid, level in pairs:
        row = next((r for r in selected if (r['question_id'], r['level']) == (qid, level)), None)
        if not row:
            raise ValueError('No fixed selection for '+str((qid, level)))
        source = live.load_source(live.DATA, qid)
        assert source['source_sha256'] == row['source_sha256']
        role = next(r for r in roles if r['db_id'] == row['db_id'] and r['role'] == row['role']
                    and r['metadata']['question_id'] == row['policy_source_question_id'])
        assert hashlib.sha256(json.dumps(role['policy'], sort_keys=True).encode()).hexdigest() == row['policy_sha256']
        context = live.schema_context(schemas[row['db_id']])
        allowed = live.engine_module('policy').allowed_columns(context, role['policy'])
        assert set(allowed) == set(row['allowed_columns'])
        loaded[qid, level] = source, context, role, allowed, live.load_reference_sql(live.GOLD, source)
    if args.check_only:
        print('Validated', len(pairs), 'fixed fixtures')
        return
    existing = list(args.out.iterdir()) if args.out.exists() else []
    if args.resume:
        if not (args.out/'CONFIG.json').is_file():
            p.error('--resume requires an existing CONFIG.json')
        old = json.loads((args.out/'CONFIG.json').read_text(encoding='utf8'))
        if old['cases'] != [list(pair) for pair in pairs] or old['model'] != args.model or \
                old['policy_fixture_sha256'] != hashlib.sha256(args.selection.read_bytes()).hexdigest():
            p.error('Resume selection or model differs from the frozen run')
        runtime_hashes = old['runtime_source_sha256']
        if module('runtime').runtime_hashes() != runtime_hashes or \
                source_hashes(Path(__file__).resolve().parent) != old['method_source_sha256']:
            p.error('Source changed since the initial run')
    else:
        if any(x.name != 'SELECTION.json' for x in existing):
            p.error('Output must be new or empty')
        args.out.mkdir(parents=True, exist_ok=True)
        runtime_hashes = module('runtime').snapshot_sources(args.out/'sources')
    folders = [('v2', ROOT/'clarification_deferred_deletion'),
               ('v4_1', ROOT/'clarification_deferred_v3_2'),
               ('v5', ROOT/'clarification_contract_v5'), ('method', Path(__file__).resolve().parent)]
    if args.mechanism in ('adaptive-pool', 'adaptive-pool-v2', 'fact-pool', 'joint-fact-pool', 'dependency-pool', 'semantic-compiler-boundary'):
        folders.append(('experiment', ROOT/'clarification_question_pool_experiment'))
    if not args.resume:
        for label, folder in folders:
            for source in folder.glob('*.py'):
                target = args.out/(label+'_sources')/source.name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(source.read_bytes())
    config = dict(method=version, cases=pairs, model=args.model,
                  runtime_source_sha256=runtime_hashes,
                  overlay_v2_sha256=overlay_hashes(),
                  overlay_v4_sha256=hashes(ROOT/'clarification_deferred_v3_2'),
                  overlay_v5_sha256=source_hashes(ROOT/'clarification_contract_v5'),
                  method_source_sha256=source_hashes(Path(__file__).resolve().parent),
                  base_method='contract_v5_25_subject_obligation_compiler',
                  origin_version=ORIGIN_VERSION, acceptance_contract='render_provenance_v1',
                  pruning_policy='scope_and_required_dependencies_are_hard',
                  cache=False, replay=False, first_attempts=True,
                  unresolved_retries=args.unresolved_retries,
                  policy_fixture=str(args.selection.resolve()),
                  policy_fixture_sha256=hashlib.sha256(args.selection.read_bytes()).hexdigest())
    if args.mechanism in ('adaptive-pool', 'adaptive-pool-v2', 'fact-pool', 'joint-fact-pool', 'dependency-pool', 'semantic-compiler-boundary'):
        config['experiment_source_sha256'] = source_hashes(ROOT/'clarification_question_pool_experiment')
    atomic = module('storage').atomic_json
    if args.resume:
        if json.loads(json.dumps(config)) != old:
            p.error('Frozen run configuration changed; refusing resume')
    else:
        atomic(args.out/'CONFIG.json', config)
    module('environment').load_dotenv(ROOT/'clarification_main/.env')
    for qid, level in pairs[:args.max_cases]:
        trace_path = args.out/f'{qid}_{level}_reduction.json'
        if args.resume and trace_path.is_file():
            previous = json.loads(trace_path.read_text(encoding='utf8'))
            if previous.get('outcome') not in {'unresolved', 'system_error'}:
                continue
            archive = args.out/f'{qid}_{level}_retry0.json'
            if archive.exists():
                p.error(f'Incomplete trace and archive both exist: {trace_path}')
            trace_path.rename(archive)
        retry_index = 0
        while True:
            run_args = argparse.Namespace(out=args.out, model=args.model, turns=30, max_calls=80,
                reuse_dir=[], response_retries=1, transport_retries=1, clarity_first=True,
                disable_deterministic_empty_relations=False)
            with patch.object(live, 'load_case', lambda q, l: loaded[q, l]), \
                 patch.object(live, 'MainMethod', lambda **kw: method_class()):
                outcome = live.run_case(run_args, qid, level)
            path = args.out/f'{qid}_{level}_reduction.json'
            d = json.loads(path.read_text(encoding='utf8'))
            if (d.get('outcome') not in {'unresolved', 'system_error'} or
                    retry_index >= args.unresolved_retries):
                break
            archive = args.out/f'{qid}_{level}_retry{retry_index + 1}.json'
            path.rename(archive)
            retry_index += 1
        d['config'].update(method=version, integrated_method=version)
        d['overlay_unchanged'] = (overlay_hashes() == config['overlay_v2_sha256'] and
            hashes(ROOT/'clarification_deferred_v3_2') == config['overlay_v4_sha256'] and
            source_hashes(ROOT/'clarification_contract_v5') == config['overlay_v5_sha256'] and
                  source_hashes(Path(__file__).resolve().parent) == config['method_source_sha256'])
        if args.mechanism in ('adaptive-pool', 'adaptive-pool-v2', 'fact-pool', 'joint-fact-pool', 'dependency-pool', 'semantic-compiler-boundary'):
            d['overlay_unchanged'] &= source_hashes(ROOT/'clarification_question_pool_experiment') == config['experiment_source_sha256']
        atomic(path, d)
        print('FINISHED', qid, level, outcome, d['actual_cost'], flush=True)
    completed = sum((args.out/f'{qid}_{level}_reduction.json').is_file() for qid, level in pairs)
    if completed == len(pairs):
        from .acceptance import main as accept
        accept([str(args.out)])
    else:
        print(f'PARTIAL {completed}/{len(pairs)}; run again with --resume', flush=True)


if __name__ == '__main__':
    main()
