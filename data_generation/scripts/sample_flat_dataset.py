"""Stratified sampling of reviewed flat cases with three distinct actual ratios."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('flat_disclosure_experiment/outputs/bird_test_3levels/completed'))
    parser.add_argument('--output', type=Path, default=Path('data/selected/bird/flat_three_distinct_330_seed42'))
    parser.add_argument('--size', type=int, default=330)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    groups = defaultdict(list)
    hashes = {}
    for path in sorted(args.source.glob('*.json')):
        raw = path.read_bytes()
        trace = json.loads(raw)['trace']
        levels = trace.get('levels', [])
        if trace.get('status') != 'review_passed' or len(levels) != 3:
            continue
        ratios = [round(row['effective_retention_ratio'], 12) for row in levels]
        if len(set(ratios)) != 3:
            continue
        if trace.get('difficulty') not in ('simple', 'moderate', 'challenging'):
            raise ValueError('Missing/invalid difficulty')
        if [r['level'] for r in levels] != [0, 1, 2] or any(r['status'] not in ('review_passed', 'source_identity') for r in levels):
            raise ValueError('Unexpected accepted level contract')
        groups[trace['difficulty']].append(trace)
        hashes[trace['question_id']] = hashlib.sha256(raw).hexdigest()
    total = sum(map(len, groups.values()))
    if not 0 < args.size <= total:
        raise ValueError('Sample size exceeds eligible population')
    order = ['simple', 'moderate', 'challenging']
    quotas = {k: args.size * len(groups[k]) // total for k in order}
    remainders = sorted(order, key=lambda k: (-(args.size * len(groups[k]) % total), order.index(k)))
    for k in remainders[:args.size - sum(quotas.values())]:
        quotas[k] += 1
    rng = random.Random(args.seed)
    selected = []
    for k in order:
        selected.extend(rng.sample(sorted(groups[k], key=lambda t: t['question_id']), quotas[k]))
    selected.sort(key=lambda t: t['question_id'])
    ids = [t['question_id'] for t in selected]
    assert len(ids) == len(set(ids)) == args.size
    args.output.mkdir(parents=True, exist_ok=False)
    cases, levels, public = [], [], []
    for t in selected:
        base = {k: t[k] for k in ('question_id', 'db_id', 'difficulty')}
        cases.append({**base, 'original_question': t['question'], 'source_split': 'test',
                      'trace_path': str(args.source / f"{t['question_id']}.json"),
                      'trace_sha256': hashes[t['question_id']],
                      'effective_retention_ratios': [r['effective_retention_ratio'] for r in t['levels']]})
        for row in t['levels']:
            levels.append({**base, 'original_question': t['question'], **row,
                           'natural_language': row['rewritten_request']})
            public.append({**base, 'level': row['level'],
                           'target_retention_ratio': row['target_retention_ratio'],
                           'effective_retention_ratio': row['effective_retention_ratio'],
                           'natural_language': row['rewritten_request']})
    manifest = {'size': args.size, 'levels': len(levels), 'seed': args.seed,
                'eligible_count': total, 'eligible_difficulty': {k: len(groups[k]) for k in order},
                'sample_difficulty': dict(Counter(t['difficulty'] for t in selected)),
                'method': 'proportional difficulty strata; largest remainder quotas; seeded sampling without replacement',
                'eligibility': 'case review_passed; three levels; three distinct effective_retention_ratio values (rounded to 12 decimals)',
                'source': str(args.source), 'source_split': 'test', 'question_ids': ids,
                'note': 'Model-reviewed subset, not a manual quality guarantee; actual ratios count fragment actions. Selected from project test split; do not use for prompt tuning and still claim held-out evaluation.'}
    for name, obj in [('manifest.json', manifest), ('cases.json', cases)]:
        (args.output / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    for name, rows in [('levels.jsonl', levels), ('interaction_inputs.jsonl', public)]:
        (args.output / name).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf8')
    (args.output / 'README.md').write_text(
        '# Flat disclosure dataset\n\n'
        '330 source cases by default, each with three levels. See manifest.json for exact counts.\n\n'
        '- cases.json: source request and trace provenance.\n'
        '- levels.jsonl: full level annotations and hidden source specifications; evaluation/private use.\n'
        '- interaction_inputs.jsonl: current natural language and metadata only; no hidden original or answers.\n'
        '- manifest.json: reproducible selection and difficulty quotas.\n\n'
        'Keep all three levels of a source in the same split. These cases inherit the project test split.\n', encoding='utf8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
