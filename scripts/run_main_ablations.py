"""Run one isolated v10 ablation on the fixed paired cohort; no SQL generation."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_strengthened_baselines as shared
from scripts.run_clarification_v10 import verify_sources, SOURCE_DIRS, LOCK
from clarification_ablation_v1.method import AblationMethod, VARIANTS, VERSION
from clarification_deferred_deletion.baseline import module


def source_hashes():
    files = [Path(__file__), ROOT/'scripts/run_strengthened_baselines.py',
             ROOT/'scripts/run_clarification_v10.py', LOCK]
    for folder in [*SOURCE_DIRS.values(), 'clarification_ablation_v1']:
        files.extend(sorted((ROOT/folder).glob('*.py')))
    return {p.relative_to(ROOT).as_posix(): shared.sha(p) for p in files}


def configuration(args, cases):
    return dict(version=VERSION, variant=args.variant, model=args.model,
        turns=args.turns, max_calls=args.max_calls, pilot=args.pilot,
        shard_index=args.shard_index, num_shards=args.num_shards,
        ids=[a['id'] for a, *_ in cases],
        fixture_selection_sha256=shared.sha(args.fixture/'selection.json'),
        runtime_source_sha256=module('runtime').runtime_hashes(),
        source_sha256=source_hashes(), execution_feedback=False,
        simulator='requester_protocol_v6_one_stage_gold',
        response_retries=1, transport_retries=1, clarity_first=True,
        deterministic_empty_relations=True, retry_failed_cases=False,
        deterministic_dependency_propagation=VARIANTS[args.variant][0],
        pending_obligation_terminal_gate=VARIANTS[args.variant][1])


def summarize(rows, planned):
    complete = [r for r in rows if r['outcome'] in {'deliver', 'refuse'}]
    confusion = Counter(r['classification'] for r in complete)
    return dict(planned=planned, recorded=len(rows), completed=len(complete),
        outcomes=dict(Counter(r['outcome'] for r in rows)), confusion=dict(confusion),
        completion_rate=len(complete)/planned,
        observed_VR=confusion['FP']/planned, observed_OR=confusion['FN']/planned,
        pending_cases=planned-len(rows), unresolved_or_error=len(rows)-len(complete),
        rate_denominator=planned,
        note='Observed VR/OR must be read with completion rate; missing decisions are not correct decisions.')


def export(out, cases):
    rows = []
    for audit, case, source, context in cases:
        path = out/f"{case['question_id']}_{case['level']}_reduction.json"
        if not path.is_file():
            continue
        trace = shared.read(path)
        session = (trace.get('episode') or {}).get('session') or {}
        events = (session.get('obligation_pool') or {}).get('events') or []
        pruned = [e for e in events if e.get('after') == 'pruned']
        has_pruning = bool(pruned or session.get('removal_no_query'))
        outcome = trace.get('outcome', 'incomplete_trace')
        complete = outcome in {'deliver', 'refuse'}
        # Match the main decision audit exactly: committed pruning, not text length.
        classification = None
        if complete:
            classification = (('FN' if has_pruning else 'TP')
                if audit['original_permission'] == 'allowed' else
                ('TN' if has_pruning else 'FP'))
        delivery = trace.get('delivery') or {}
        rows.append(dict(id=audit['id'], question_id=case['question_id'],
            level=case['level'], dataset=case['dataset'], db_id=case['db_id'],
            role=case['role'], policy_sha256=case['policy_sha256'],
            original_permission=audit['original_permission'], outcome=outcome,
            has_pruning=has_pruning, classification=classification,
            complete_request_decision=('denied' if has_pruning else 'allowed') if complete else 'unknown',
            clarified_text=delivery.get('text', '') if outcome == 'deliver' else '',
            allowed_columns=case['allowed_columns'], frontend_only=True,
            committed_pruning_events=pruned, removal_no_query=session.get('removal_no_query'),
            cost=trace.get('cost'), trace=str(path.resolve()), trace_sha256=shared.sha(path),
            ablation=trace.get('ablation')))
    for name, data in [('evaluation_rows.jsonl', rows),
                       ('clarified_requests.jsonl', shared.generation_inputs(rows))]:
        (out/name).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in data), encoding='utf8')
    stats = summarize(rows, len(cases))
    module('storage').atomic_json(out/'SUMMARY.json', stats)
    return stats


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--variant', choices=VARIANTS, required=True)
    p.add_argument('--fixture', type=Path, default=shared.FIXTURE)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', default='deepseek-flash')
    p.add_argument('--turns', type=int, default=30)
    p.add_argument('--max-calls', type=int, default=80)
    p.add_argument('--pilot', action='store_true')
    p.add_argument('--limit', type=int, help='New cases this invocation; not a changed cohort')
    p.add_argument('--shard-index', type=int, default=0)
    p.add_argument('--num-shards', type=int, default=1)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--check-only', action='store_true')
    p.add_argument('--export-only', action='store_true')
    p.add_argument('--prepare-only', action='store_true',
                   help='Write validated config/source snapshots; never configure a model client')
    args = p.parse_args()
    if sum((args.check_only, args.export_only, args.prepare_only)) > 1:
        p.error('Choose only one offline operation')
    if (args.turns < 0 or args.max_calls < 1 or args.num_shards < 1
            or not 0 <= args.shard_index < args.num_shards
            or (args.limit is not None and args.limit < 1)):
        p.error('Invalid budget, shard, or limit')
    verify_sources()
    cases = shared.prepare(args)
    cases = [c for i, c in enumerate(cases) if i % args.num_shards == args.shard_index]
    if not cases:
        p.error('Empty shard')
    cfg = configuration(args, cases)
    if args.check_only:
        print(json.dumps(dict(validated=len(cases), variant=args.variant,
            cohort_sha256=hashlib.sha256('\n'.join(sorted(cfg['ids'])).encode()).hexdigest(),
            source_lock='verified', counts=dict(Counter(
                a['dataset']+':'+a['original_permission'] for a, *_ in cases)))))
        return
    args.out = args.out.resolve()
    atomic = module('storage').atomic_json
    if args.resume or args.export_only:
        if not (args.out/'CONFIG.json').is_file() or shared.read(args.out/'CONFIG.json') != cfg:
            p.error('Existing configuration differs; refusing reuse or resume')
    else:
        if args.out.exists() and any(args.out.iterdir()):
            p.error('Output must be empty, or use --resume')
        args.out.mkdir(parents=True, exist_ok=True)
        for name in cfg['source_sha256']:
            dest = args.out/'source_snapshot'/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((ROOT/name).read_bytes())
        module('runtime').snapshot_sources(args.out/'runtime_snapshot')
        atomic(args.out/'CONFIG.json', cfg)
    if args.prepare_only:
        print(json.dumps(dict(prepared=len(cases), variant=args.variant,
                              out=str(args.out), model_calls=0)))
        return
    if args.export_only:
        print(json.dumps(export(args.out, cases)))
        return
    # Transport is configured only after all offline validation has passed.
    if args.model in {'deepseek-flash', 'deepseek-v4-pro'}:
        from scripts.run_clarification_deepseek import configure, install_v4_non_thinking
        configure()
        install_v4_non_thinking(module('live').Recorder)
    else:
        module('environment').load_dotenv(ROOT/'clarification_main/.env')
    roles = shared.read(args.fixture/'roles.json')
    live = module('live')
    completed_now = 0
    try:
        for audit, case, source, context in cases:
            qid, level = case['question_id'], case['level']
            path = args.out/f'{qid}_{level}_reduction.json'
            if path.exists():
                previous = shared.read(path)
                if (previous.get('ablation') or {}).get('variant') != args.variant:
                    p.error('Existing trace is incomplete or from another variant: '+str(path))
                continue
            if args.limit is not None and completed_now >= args.limit:
                break
            verify_sources()
            if source_hashes() != cfg['source_sha256']:
                raise RuntimeError('Source changed during experiment')
            role = next(r for r in roles if r['db_id'] == case['db_id']
                and r['role'] == case['role']
                and r['metadata']['question_id'] == case['policy_source_question_id'])
            source = dict(source, levels={int(k): v for k, v in source['levels'].items()})
            loaded = (source, context, role, case['allowed_columns'],
                dict(sql=source['reference_sql'], question_id=qid, path=str(args.fixture/'sources.json')))
            method = AblationMethod(variant=args.variant)
            run_args = argparse.Namespace(out=args.out, model=args.model, turns=args.turns,
                max_calls=args.max_calls, reuse_dir=[], response_retries=1,
                transport_retries=1, clarity_first=True, disable_deterministic_empty_relations=False)
            with patch.object(live, 'load_case', lambda q, l: loaded), \
                 patch.object(live, 'MainMethod', lambda **kw: method):
                outcome = live.run_case(run_args, qid, level)
            trace = shared.read(path)
            trace['config'].update(method=VERSION, integrated_method=VERSION, variant=args.variant)
            trace['ablation'] = method.audit()
            verify_sources()
            trace['ablation_sources_unchanged'] = source_hashes() == cfg['source_sha256']
            atomic(path, trace)
            if not trace['ablation_sources_unchanged']:
                raise RuntimeError('Ablation source changed during case')
            completed_now += 1
            print('FINISHED', args.variant, qid, level, outcome, flush=True)
    finally:
        print(json.dumps(export(args.out, cases)), flush=True)


if __name__ == '__main__':
    main()
