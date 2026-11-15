"""Run only the integrated main method, with isolated matrix workers."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys

from .runtime import ROOT, PACKAGE, engine_module, engine_hashes, runtime_hashes, snapshot_sources
from .method import MainMethod, METHOD_VERSION, CLARITY_VERSION, VERSION, SIMULATOR_SCOPE_VERSION
from .inputs import load_source, load_reference_sql
from .schema import schema_context
from .storage import atomic_json
from .recorder import Recorder, cost
from .response_boundary import ResponseBoundaryError
from .deterministic_calls import wrap
from .report import summarize
from .acceptance import main as accept

DATA = ROOT / 'data/selected/bird/flat_three_distinct_330_seed42'
SCHEMAS = ROOT / 'artifacts/bird_mini_dev_quality_audit_20261009/schemas.json'
POLICIES = ROOT / 'data/selected/bird/column_level_rbac_dataset_bird_v3_no_sm_effective_300.json'
GOLD = ROOT / 'data/selected/bird/original_1534_split_seed42/test.json'


def load_case(qid, level):
    source = load_source(DATA, qid)
    fixtures = json.loads((PACKAGE / 'matrix_cases.json').read_text(encoding='utf-8'))
    fixture = next((r for r in fixtures if (r['question_id'], r['level']) == (qid, level)), None)
    if fixture is None:
        raise ValueError('No configured policy fixture for this case/level')
    if source['source_sha256'] != fixture['source_sha256']:
        raise ValueError('Selected dataset changed since the fixed policy fixture')
    schemas = json.loads(SCHEMAS.read_text(encoding='utf-8'))
    context = schema_context(schemas[source['db_id']])
    policies = json.loads(POLICIES.read_text(encoding='utf-8'))
    role = next(r for r in policies if r['db_id'] == source['db_id'] and r['role'] == fixture['role']
                and r['metadata']['question_id'] == fixture['policy_source_question_id'])
    allowed = engine_module('policy').allowed_columns(context, role['policy'])
    if set(allowed) != set(fixture['allowed_columns']):
        raise ValueError('Effective role policy differs from fixed comparison fixture')
    reference = load_reference_sql(GOLD, source)
    return source, context, role, allowed, reference


def run_case(args, qid, level):
    manifest = json.loads((args.out / 'CONFIG.json').read_text(encoding='utf-8'))
    expected = manifest['runtime_source_sha256']
    if runtime_hashes() != expected:
        raise RuntimeError('Runtime changed after matrix configuration')
    source, context, role, allowed, reference = load_case(qid, level)
    path = args.out / f'{qid}_{level}_reduction.json'
    if path.exists():
        raise FileExistsError('Refusing to overwrite an existing case trace: ' + str(path))
    before = engine_hashes()
    record = {'question_id': qid, 'level': level, 'arm': 'reduction', 'db_id': source['db_id'],
        'request': source['levels'][level]['rewritten_request'], 'reference_request': source['question'],
        'source_sha256': source['source_sha256'], 'allowed_columns': allowed, 'role': role['role'],
        'simulator_sql_reference': reference, 'model': args.model, 'calls': [], 'episode': {},
        'runtime_source_sha256': expected,
        'config': {'method': METHOD_VERSION, 'integrated_method': METHOD_VERSION,
            'source_choices': CLARITY_VERSION if args.clarity_first else None,
            'progressive_fragments': VERSION, 'simulator_scope': SIMULATOR_SCOPE_VERSION,
            'max_turns': args.turns, 'first_delivery_only': True,
            'simulator': 'requester_protocol_v6_one_stage_gold', 'prompt_profile': 'frozen',
            'no_extra_consent': True, 'sql_execution_evaluated': False,
            'reconstruction': 'original_public_input_plus_all_effective_turns_and_reductions',
            'structure_update': 'event_driven_local_refinement_with_parent_provenance',
            'deletion': 'blocked_dependency_focus_and_coherent_receipt',
            'source_runtime': 'clarification_main/engine',
            'deterministic_empty_relations': not args.disable_deterministic_empty_relations,
            'response_recovery': {'response_retries': args.response_retries,
                'transport_retries': args.transport_retries, 'timeout_seconds': 120,
                'shared_attempt_budget': args.max_calls}}}

    def save():
        atomic_json(path, record)

    save()
    try:
        recorder = Recorder(args.model, args.max_calls, args.reuse_dir,
            timeout=120, empty_retries=args.response_retries, transport_retries=args.transport_retries)
        call = recorder.bind(record, path, 0, 'reduction')
        if not args.disable_deterministic_empty_relations:
            call = wrap(call, record.setdefault('deterministic_calls', []))

        def ask(question, history):
            return engine_module('simulator').simulate_requester_with_sql(call,
                source['question'], question, history, source.get('flat_units', []), reference['sql'])

        with MainMethod(clarity_first=args.clarity_first).installed():
            record['delivery'] = engine_module('harness').run_session(call, record['request'],
                context, source['db_id'], allowed, ask, max_turns=args.turns,
                save=save, episode=record['episode'], method='main')
        record['outcome'] = record['delivery']['action']
    except ResponseBoundaryError as exc:
        record.update(outcome='model_response_error', error_type=type(exc).__name__, error=str(exc),
            response_failure=exc.kind, failure_layer='response_boundary', available_for_text2sql=False)
    except Exception as exc:
        record.update(outcome='system_error', error_type=type(exc).__name__,
            error=str(exc).replace(os.getenv('OPENAI_API_KEY', '<no-key>'), '<redacted>'),
            available_for_text2sql=False)
    record['cost'] = cost(record['calls'])
    record['actual_cost'] = cost([c for c in record['calls'] if 'reused_from' not in c])
    record['engine_unchanged'] = engine_hashes() == before
    # Compatibility with the saved-trace acceptance contract: now the local engine.
    record['frozen_unchanged'] = record['engine_unchanged']
    record['runtime_unchanged'] = runtime_hashes() == expected
    if not record['runtime_unchanged']:
        record.update(outcome='system_error', error_type='RuntimeSourceChanged',
            error='Runtime changed during the case', available_for_text2sql=False)
    save()
    return record['outcome']


def worker_command(args, qid, level):
    cmd = [sys.executable, '-m', 'clarification_main', '--out', str(args.out),
        '--case', str(qid), str(level), '--model', args.model, '--turns', str(args.turns),
        '--max-calls', str(args.max_calls), '--response-retries', str(args.response_retries),
        '--transport-retries', str(args.transport_retries)]
    for flag in ('clarity_first', 'disable_deterministic_empty_relations'):
        if getattr(args, flag):
            cmd.append('--' + flag.replace('_', '-'))
    for path in args.reuse_dir:
        cmd.extend(['--reuse-dir', str(path)])
    return cmd


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ids', default='113,1461,796,24')
    p.add_argument('--levels', default='0,1,2')
    p.add_argument('--model', default='gpt-5.5')
    p.add_argument('--turns', type=int, default=30)
    p.add_argument('--max-calls', type=int, default=180)
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--response-retries', type=int, choices=(0, 1, 2), default=1)
    p.add_argument('--transport-retries', type=int, choices=(0, 1, 2), default=0)
    p.add_argument('--clarity-first', action='store_true')
    p.add_argument('--disable-deterministic-empty-relations', action='store_true')
    p.add_argument('--reuse-dir', type=Path, action='append', default=[])
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--case', type=int, nargs=2, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if min(args.workers, args.max_calls) < 1 or args.turns < 0:
        p.error('workers/max-calls must be positive; turns must be nonnegative')
    args.out = args.out.resolve()
    args.reuse_dir = [x.resolve() for x in args.reuse_dir]
    if args.case:
        outcome = run_case(args, *args.case)
        print(*args.case, outcome, flush=True)
        return
    ids, levels = [int(x) for x in args.ids.split(',')], [int(x) for x in args.levels.split(',')]
    if len(set(ids)) != len(ids) or len(set(levels)) != len(levels):
        p.error('Duplicate case IDs/levels are not permitted')
    if args.out.exists() and any(args.out.iterdir()):
        p.error('--out must be new or empty to preserve previous traces')
    args.out.mkdir(parents=True, exist_ok=True)
    hashes = snapshot_sources(args.out)
    config = {**vars(args), 'out': str(args.out), 'reuse_dir': [str(x) for x in args.reuse_dir],
        'runtime_source_sha256': hashes, 'integrated_method': METHOD_VERSION,
        'source_choices': CLARITY_VERSION if args.clarity_first else None}
    atomic_json(args.out / 'CONFIG.json', config)
    print('OUTPUT', args.out, flush=True)

    def worker(qid, level):
        with (args.out / f'{qid}_{level}.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(worker_command(args, qid, level), cwd=ROOT, stdout=log, stderr=log)
        return qid, level, result.returncode

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(worker, qid, level) for qid in ids for level in levels]
        for job in as_completed(jobs):
            print('FINISHED', job.result(), flush=True)
    summarize(args.out)
    accept([str(args.out)])


if __name__ == '__main__':
    main()
