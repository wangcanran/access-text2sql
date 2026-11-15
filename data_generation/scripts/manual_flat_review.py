"""Evidence-bound, per-obligation manual review of flat disclosure examples."""
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / 'artifacts/flat_disclosure_cross/recoverable_flat200_fullpool_queue_20261031'
REVIEWS = QUEUE / 'manual_reviews'
DATASETS = ('bird', 'livesql', 'spider')
VERDICTS = ('preserved', 'askable', 'silent_loss', 'contradiction')


def cases(dataset):
    path = QUEUE / f'{dataset}_full_pool_review_queue.jsonl'
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def source_hash(case):
    keys = ('question_id', 'db_id', 'original_question', 'source_obligations', 'levels', 'trace_path')
    return hashlib.sha256(json.dumps({k: case.get(k) for k in keys}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def path_for(dataset, case):
    return REVIEWS / dataset / f"{case['question_id']}.json"


def load(dataset, case):
    path = path_for(dataset, case)
    review = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'dataset': dataset, 'question_id': case['question_id'], 'trace_path': case['trace_path'],
        'source_sha256': source_hash(case),
        'decisions': {str(level): {o['item_id']: None for o in case['source_obligations']} for level in (0, 1)},
    }
    if (review.get('dataset'), review.get('question_id'), review.get('trace_path'), review.get('source_sha256')) != (
        dataset, case['question_id'], case['trace_path'], source_hash(case)):
        raise ValueError('Review/source mismatch')
    return review


def save(dataset, case, review):
    path = path_for(dataset, case)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)
    return path


def validate(case, review):
    errors, pending, rejected = [], [], []
    ids = {o['item_id'] for o in case['source_obligations']}
    if set(review.get('decisions', {})) != {'0', '1'}:
        errors.append('Expected exactly levels 0 and 1')
    for level in (0, 1):
        text = next(x['text'] for x in case['levels'] if x['level'] == level)
        decisions = review.get('decisions', {}).get(str(level), {})
        if set(decisions) != ids:
            errors.append(f'Level {level}: obligation IDs do not match source')
        for item_id in ids:
            label = f'L{level}/{item_id}'
            entry = decisions.get(item_id)
            if entry is None:
                pending.append(label)
                continue
            if not isinstance(entry, dict) or entry.get('verdict') not in VERDICTS:
                errors.append(f'{label}: invalid verdict')
                continue
            verdict, evidence = entry['verdict'], entry.get('evidence', '')
            if verdict != 'silent_loss' and (not evidence or evidence not in text):
                errors.append(f'{label}: evidence must be an exact excerpt from the rewrite')
            if verdict == 'silent_loss' and evidence:
                errors.append(f'{label}: silent loss has no positive evidence; leave evidence empty')
            if verdict == 'askable' and not entry.get('question', '').strip():
                errors.append(f'{label}: specify the clarification question')
            if verdict in ('silent_loss', 'contradiction'):
                rejected.append(label)
                if not entry.get('reason', '').strip():
                    errors.append(f'{label}: specify a reason')
    coverage = review.get('source_coverage')
    if review.get('manual_batch_note') and (not review.get('individually_adjudicated') or not review.get('individual_rationale')):
        pending.append('individual_adjudication')
    if coverage is None:
        pending.append('source_coverage')
    elif not isinstance(coverage, dict) or coverage.get('verdict') not in ('complete', 'incomplete'):
        errors.append('source_coverage must be complete or incomplete')
    elif coverage['verdict'] == 'incomplete':
        rejected.append('source_coverage')
        if not coverage.get('reason', '').strip():
            errors.append('Incomplete source obligation inventory requires a reason')
    for level in (0, 1):
        check = review.get('overall', {}).get(str(level))
        if check is None:
            pending.append(f'L{level}/overall')
        elif not isinstance(check, dict) or check.get('verdict') not in ('faithful', 'unfaithful'):
            errors.append(f'L{level}/overall must be faithful or unfaithful')
        elif check['verdict'] == 'unfaithful':
            rejected.append(f'L{level}/overall')
            if not check.get('reason', '').strip():
                errors.append(f'L{level}/overall unfaithful requires a reason')
    status = 'invalid' if errors else 'pending' if pending else 'rejected' if rejected else 'certified'
    return {'status': status, 'pending': sorted(pending), 'rejected': sorted(rejected), 'errors': errors}


def hints(case):
    path = ROOT / case['trace_path']
    if not path.is_file():
        return []
    trace = json.loads(path.read_text(encoding='utf-8')).get('trace', {})
    result = []
    for level in trace.get('levels', []):
        if level.get('level') in (0, 1):
            attempts = level.get('attempts') or []
            attempt = attempts[-1] if attempts else {}
            result.append({'level': level['level'], 'edits': attempt.get('edits', []),
                           'model_review': attempt.get('review', {})})
    return result


def show(dataset, case, no_hints=False):
    print(f"[{dataset}/{case['question_id']}] {case['db_id']} | queue: {case.get('status')}")
    print('Original:', case['original_question'])
    for o in case['source_obligations']:
        print(f"  {o['item_id']}: {o['requirement']}")
    for level in sorted(case['levels'], key=lambda x: x['level']):
        print(f"Level {level['level']} [{','.join(level.get('selected_ids', []))}]: {level['text']}")
    review = load(dataset, case)
    print('Manual:', json.dumps(validate(case, review), ensure_ascii=False))
    for level, entries in review['decisions'].items():
        present = {k: v for k, v in entries.items() if v is not None}
        if present:
            print('Recorded level', level, json.dumps(present, ensure_ascii=False))
    if not no_hints:
        print('Model hints, not manual judgments:', json.dumps(hints(case), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('show', 'batch', 'init', 'record', 'coverage', 'overall', 'validate', 'summary', 'next', 'export'))
    parser.add_argument('dataset', nargs='?', choices=DATASETS)
    parser.add_argument('question_id', nargs='?')
    parser.add_argument('level', nargs='?', type=int, choices=(0, 1))
    parser.add_argument('item_id', nargs='?')
    parser.add_argument('verdict', nargs='?', choices=VERDICTS)
    parser.add_argument('--evidence', default='')
    parser.add_argument('--question', default='')
    parser.add_argument('--reason', default='')
    parser.add_argument('--coverage-verdict', choices=('complete', 'incomplete'))
    parser.add_argument('--no-hints', action='store_true')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.action in ('summary', 'next', 'batch', 'export'):
        selected = []
        shown = 0
        for dataset in ([args.dataset] if args.dataset else DATASETS):
            counts = {'certified': 0, 'rejected': 0, 'pending': 0, 'invalid': 0, 'pre_excluded': 0}
            for case in cases(dataset):
                if case.get('status') == 'excluded_on_inspection' and not path_for(dataset, case).exists():
                    counts['pre_excluded'] += 1
                    continue
                status = validate(case, load(dataset, case))['status']
                counts[status] += 1
                if args.action == 'export' and status == 'certified':
                    selected.append({'dataset': dataset, 'question_id': case['question_id'],
                                     'db_id': case['db_id'], 'source_sha256': source_hash(case),
                                     'review_mode': load(dataset, case).get('review_mode', 'explicit_item_review')})
                if args.action == 'next' and status == 'pending':
                    show(dataset, case, args.no_hints)
                    return
                if args.action == 'batch' and status == 'pending' and shown < args.limit:
                    show(dataset, case, args.no_hints)
                    print()
                    shown += 1
            if args.action == 'summary':
                print(dataset, counts)
        if args.action == 'export':
            if args.out is None:
                parser.error('export requires --out')
            args.out.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.out.with_suffix(args.out.suffix + '.tmp')
            temporary.write_text(json.dumps({'certified': selected}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            os.replace(temporary, args.out)
            print(f'Exported {len(selected)} certified cases to {args.out}')
        return
    if args.dataset is None or args.question_id is None:
        parser.error('Specify dataset and question_id')
    matches = [case for case in cases(args.dataset) if str(case['question_id']) == args.question_id]
    if len(matches) != 1:
        parser.error(f'Expected one case; found {len(matches)}')
    case = matches[0]
    review = load(args.dataset, case)
    if args.action == 'show':
        show(args.dataset, case, args.no_hints)
    elif args.action == 'init':
        print(save(args.dataset, case, review))
    elif args.action == 'record':
        if args.level is None or args.item_id is None or args.verdict is None:
            parser.error('record requires level item_id verdict')
        if args.item_id not in review['decisions'][str(args.level)]:
            parser.error('Unknown obligation ID')
        trial = json.loads(json.dumps(review))
        trial['decisions'][str(args.level)][args.item_id] = {
            'verdict': args.verdict, 'evidence': args.evidence,
            'question': args.question, 'reason': args.reason,
        }
        result = validate(case, trial)
        if result['errors']:
            parser.error('; '.join(result['errors']))
        save(args.dataset, case, trial)
        print(json.dumps(result, ensure_ascii=False))
    elif args.action == 'coverage':
        if args.coverage_verdict is None:
            parser.error('coverage requires --coverage-verdict complete|incomplete')
        trial = json.loads(json.dumps(review))
        trial['source_coverage'] = {'verdict': args.coverage_verdict, 'reason': args.reason}
        result = validate(case, trial)
        if result['errors']:
            parser.error('; '.join(result['errors']))
        save(args.dataset, case, trial)
        print(json.dumps(result, ensure_ascii=False))
    elif args.action == 'overall':
        if args.level is None or args.item_id not in ('faithful', 'unfaithful'):
            parser.error('overall requires level faithful|unfaithful')
        trial = json.loads(json.dumps(review))
        trial.setdefault('overall', {})[str(args.level)] = {'verdict': args.item_id, 'reason': args.reason}
        result = validate(case, trial)
        if result['errors']:
            parser.error('; '.join(result['errors']))
        save(args.dataset, case, trial)
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(validate(case, review), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
