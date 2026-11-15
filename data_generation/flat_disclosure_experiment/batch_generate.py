"""Resumable flat disclosure generation over the sealed BIRD test split.

Each source question gets exactly retention levels 0, 0.5 and 1.  API responses
are content-addressed, so an interrupted question can resume without paying for
requests that already completed. Completed question traces are skipped.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tqdm import tqdm

from intent_refinement_bird_v2.llm_client import load_dotenv

from .live_smoke import ROOT, run_case, DISCLOSURE_FRAME_PROMPT, ROLE_PROMPT, INVENTORY_PROMPT, OBLIGATION_PROMPT, DIRECTION_PROMPT, DIRECTION_REPAIR_PROMPT, REWRITE_PROMPT, REPLACEMENT_PROMPT, REVIEW_PROMPT
from .storage import ResponseCache, RunLock, atomic_json, atomic_text, digest

RATIOS = [0.0, 0.5, 1.0]
DEFAULT_INPUT = ROOT / 'data/selected/bird/original_1534_split_seed42/test_generation_input.json'
DEFAULT_SCHEMAS = ROOT / 'artifacts/bird_mini_dev_quality_audit_20261009/schemas.json'
PROMPTS = dict(frame=DISCLOSURE_FRAME_PROMPT, role=ROLE_PROMPT, inventory=INVENTORY_PROMPT,
               obligation=OBLIGATION_PROMPT, direction=DIRECTION_PROMPT,
               direction_repair=DIRECTION_REPAIR_PROMPT, rewrite=REWRITE_PROMPT,
               replacement=REPLACEMENT_PROMPT, review=REVIEW_PROMPT)


def load_cases(path):
    text = path.read_text(encoding='utf-8-sig')
    rows = ([json.loads(line) for line in text.splitlines() if line.strip()]
            if path.suffix == '.jsonl' else json.loads(text))
    if not isinstance(rows, list) or not rows:
        raise ValueError('Input must be a nonempty JSON array or JSONL stream')
    cases, ids = [], set()
    for row in rows:
        qid = row.get('question_id')
        if type(qid) is not int or qid < 0 or qid in ids:
            raise ValueError('question_id must be a unique nonnegative integer')
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('db_id', 'question')):
            raise ValueError(f'Invalid db_id/question: {qid}')
        ids.add(qid)
        # SQL/evidence/RBAC labels are never passed to the generator.
        case = {k: row[k] for k in ('question_id', 'db_id', 'question')}
        if 'difficulty' in row:
            case['difficulty'] = row['difficulty']
        cases.append(case)
    return cases


def implementation_hash():
    paths = [*Path(__file__).parent.glob('*.py'),
             ROOT / 'request_disclosure_experiment/run_smoke.py',
             ROOT / 'request_disclosure_experiment/disclosure.py',
             ROOT / 'clause_frame_experiment/model.py']
    return digest({str(p.relative_to(ROOT)): p.read_text(encoding='utf8')
                   for p in sorted(paths) if not p.name.startswith('test_')})


def validate_completed(trace, case):
    if any(trace.get(k) != v for k, v in case.items()):
        raise ValueError('Completed case source differs from input')
    rows = trace.get('levels', [])
    if len(rows) != 3 or [r.get('level') for r in rows] != [0, 1, 2]:
        raise ValueError('Completed case must contain exactly three levels')
    if [r.get('target_retention_ratio') for r in rows] != RATIOS:
        raise ValueError('Completed case retention ratios differ')
    allowed = {'review_passed', 'source_identity', 'needs_review'}
    for row in rows:
        if row.get('status') not in allowed or not isinstance(row.get('rewritten_request'), str) or not row['rewritten_request'].strip():
            raise ValueError('Completed case contains an unfinished level')
    if rows[2]['rewritten_request'] != case['question'] or rows[2]['status'] != 'source_identity':
        raise ValueError('Retention 1 must preserve the exact original question')
    expected = 'needs_review' if any(r['status'] == 'needs_review' for r in rows) else 'review_passed'
    if trace.get('status') != expected:
        raise ValueError('Completed case status conflicts with level statuses')


def prepare_run(out, contract, resume):
    manifest = out / 'manifest.json'
    if manifest.exists():
        if not resume:
            raise ValueError('Output exists; use --resume or a new directory')
        old = json.loads(manifest.read_text(encoding='utf8'))
        old_contract = old.get('contract', {})
        compatible = Path(__file__).with_name('resume_compatibility.json')
        pairs = json.loads(compatible.read_text(encoding='utf8')) if compatible.exists() else {}
        migrated = dict(old_contract)
        if pairs.get(old_contract.get('implementation')) == contract['implementation']:
            migrated['implementation'] = contract['implementation']
        if migrated != contract:
            raise ValueError('Input/schema/prompts/code/model/options changed; use a new output directory')
    else:
        # A crash after manifest commit is recoverable; unknown preexisting data is not.
        if any(p.name not in {'.run.lock', 'manifest.json.tmp'} for p in out.iterdir()):
            raise ValueError('Nonempty output has no manifest; use a new directory')
        atomic_json(manifest, {'created_at': datetime.now(timezone.utc).isoformat(), 'contract': contract})
    atomic_json(out / 'prompts.json', PROMPTS)


def read_completed(out, cases):
    completed = {}
    for case in cases:
        path = out / 'completed' / f"{case['question_id']}.json"
        if path.exists():
            row = json.loads(path.read_text(encoding='utf8'))
            if row.get('sha256') != digest(row.get('trace')):
                raise ValueError(f'Committed trace checksum mismatch: {path}')
            validate_completed(row['trace'], case)
            completed[case['question_id']] = row['trace']
    return completed


def export_results(out, cases):
    completed = read_completed(out, cases)
    rows, audit, errors = [], [], []
    for case in cases:
        qid = case['question_id']
        trace = completed.get(qid)
        if trace is not None:
            for row in trace['levels']:
                rows.append({**case, 'source_id': qid, **row,
                             'natural_language': row['rewritten_request']})
            audit.append({'source_id': qid, 'status': trace['status'], 'levels_generated': 3,
                          'trace': f'completed/{qid}.json'})
        else:
            path = out / f'{qid}.json'
            if path.exists():
                trace = json.loads(path.read_text(encoding='utf8'))
                if trace.get('status') == 'error':
                    errors.append({'source_id': qid, 'error': trace.get('error'), 'trace': f'{qid}.json'})
    for name, data in [('levels', rows), ('audit', audit), ('errors', errors)]:
        atomic_text(out / f'{name}.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in data))
    summary = {'cases': len(cases), 'completed': len(completed),
               'review_passed': sum(t['status'] == 'review_passed' for t in completed.values()),
               'needs_review': sum(t['status'] == 'needs_review' for t in completed.values()),
               'errors': len(errors), 'remaining': len(cases) - len(completed),
               'levels': len(rows), 'ratios': RATIOS,
               'note': 'Ratios count fragments, not semantic information. Model review is not human acceptance.'}
    atomic_json(out / 'summary.json', summary)
    return summary


def execute(args, cases, schemas, generator=run_case):
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    contract = {'format_version': 1, 'input': digest(cases), 'schemas': digest(schemas),
                'prompts': digest(PROMPTS), 'implementation': implementation_hash(),
                'model': args.model, 'review_model': args.review_model,
                'base_url': os.getenv('OPENAI_BASE_URL'), 'seed': args.seed,
                'repair_attempts': args.repair_attempts, 'ratios': RATIOS, 'rewrite_mode': 'local'}
    with RunLock(out / '.run.lock'):
        prepare_run(out, contract, args.resume)
        completed = read_completed(out, cases)
        pending = [c for c in cases if c['question_id'] not in completed]
        if args.max_cases is not None:
            pending = pending[:args.max_cases]
        args.fraction = 0.5
        args.rewrite_mode = 'local'
        args.replay_frames = None
        export_results(out, cases)  # Rebuild truncated or stale derived JSONL files.

        def run(case):
            qid = case['question_id']
            cache = ResponseCache(out / 'response_cache' / str(qid))
            trace = generator(case, args, schemas, out, RATIOS, None, cache)
            if trace.get('status') in ('review_passed', 'needs_review'):
                validate_completed(trace, case)
                atomic_json(out / 'completed' / f'{qid}.json', {'trace': trace, 'sha256': digest(trace)})
            return trace

        counts = {'ok': sum(t['status'] == 'review_passed' for t in completed.values()),
                  'review': sum(t['status'] == 'needs_review' for t in completed.values()),
                  'failed': 0, 'resumed': len(completed)}
        tqdm.write(f'Loaded {len(cases)} cases; resumed {len(completed)}; queued {len(pending)} this run.')
        try:
            with tqdm(total=len(cases), initial=len(completed), desc='Flat generation',
                      unit='case', dynamic_ncols=True, mininterval=0.5) as progress:
                progress.set_postfix(counts)
                args.progress_log = tqdm.write
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = {pool.submit(run, case) for case in pending}
                    while futures:
                        done, futures = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                        for future in done:
                            trace = future.result()
                            key = {'review_passed': 'ok', 'needs_review': 'review'}.get(trace.get('status'), 'failed')
                            counts[key] += 1
                            progress.update(1)
                        if done:
                            export_results(out, cases)
                            progress.set_postfix(counts)
                        else:
                            progress.refresh()
        finally:
            args.progress_log = None
            summary = export_results(out, cases)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--schemas', type=Path, default=DEFAULT_SCHEMAS)
    parser.add_argument('--output', type=Path, default=ROOT / 'flat_disclosure_experiment/outputs/bird_test_3levels')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--model', default='gpt-5.1')
    parser.add_argument('--review-model', default='gpt-5.5')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--max-cases', type=int, help='Maximum unfinished cases this invocation; resume without it for all')
    parser.add_argument('--repair-attempts', type=int, choices=(0, 1, 2), default=1)
    parser.add_argument('--dry-run', action='store_true', help='Validate full input and schema coverage without API calls or output writes')
    args = parser.parse_args()
    if args.workers < 1 or (args.max_cases is not None and args.max_cases < 1):
        parser.error('workers/max-cases must be positive')
    try:
        cases = load_cases(args.input)
        schemas = json.loads(args.schemas.read_text(encoding='utf8'))
        missing = {c['db_id'] for c in cases} - schemas.keys()
        if missing:
            raise ValueError(f'Missing database schemas: {sorted(missing)}')
        from request_disclosure_experiment.run_smoke import schema_context
        for db in sorted({c['db_id'] for c in cases}):
            context = schema_context(schemas[db])
            if not context['tables']:
                raise ValueError(f'Empty schema: {db}')
        if args.dry_run:
            print(json.dumps({'cases': len(cases), 'databases': len({c['db_id'] for c in cases}),
                              'ratios': RATIOS, 'expected_levels': len(cases) * 3}, indent=2))
            return
        load_dotenv(ROOT / 'clause_frame_experiment/.env')
        if not os.environ.get('OPENAI_API_KEY'):
            raise ValueError('OPENAI_API_KEY is not configured')
        summary = execute(args, cases, schemas)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary['errors']:
            raise SystemExit(1)
    except (ValueError, OSError) as exc:
        parser.error(str(exc).replace(os.getenv('OPENAI_API_KEY') or '\0', '<redacted>'))


if __name__ == '__main__':
    main()


