"""Select three-level cases by the recoverability of their hidden obligations."""
import argparse
import json
from collections import Counter
from pathlib import Path

from select_recoverable_flat200 import eligible, spider_difficulties

EXCLUSIONS = {
    'bird': {
        498: 'French-language output silently becomes an ordinary name; no language slot remains.',
    },
    'livesql': {},
    'spider': {
        124: 'Level 1 changes the requested country attribute to a time/tenure attribute.',
    },
}

FLOORS = {
    'bird': {'challenging': 35, 'moderate': 50},
    'spider': {'extra': 25, 'hard': 35, 'medium': 70},
    'livesql': {},
}

def recoverability_audit(row):
    """Use trace evidence, never word overlap, as the primary ranking signal.

    A selected obligation needs a localized replacement or an explicit whole-text
    review. A reviewer calling it preserved is a warning, not automatic proof of
    a bad sentence. Semantic silent-loss decisions remain documented separately.
    """
    from select_recoverable_flat200 import ROOT
    trace = json.loads((ROOT / row['trace_path']).read_text(encoding='utf8'))['trace']
    obligation_ids = {item['item_id'] for item in trace['source_obligations']['obligations']}
    warnings = []
    explicit_slots = 0
    for level in trace['levels'][:2]:
        attempts = level.get('attempts') or []
        attempt = attempts[-1] if attempts else {}
        selected = set(level['selected_ids'])
        checks = {item['item_id']: item['result'] for item in
                  attempt.get('review', {}).get('checks', [])}
        edits = {item['item_id']: item for item in attempt.get('edits', [])}
        for item_id in obligation_ids:
            expected = 'hidden' if item_id in selected else 'preserved'
            if checks.get(item_id) != expected:
                warnings.append(f"level{level['level']}:{item_id}:review_{checks.get(item_id, 'missing')}")
        for item_id in selected:
            edit = edits.get(item_id)
            if edit and str(edit.get('replacement', '')).strip():
                explicit_slots += 1
            elif attempt.get('mode') == 'whole_fallback':
                warnings.append(f"level{level['level']}:{item_id}:whole_text_review")
            else:
                warnings.append(f"level{level['level']}:{item_id}:no_local_edit")
    row['recoverability_audit'] = {
        'explicit_replacement_slots': explicit_slots,
        'warnings': warnings,
        'warning_count': len(warnings),
    }
    return row

def select_recoverable(pool, count, name):
    groups = {}
    for row in pool:
        groups.setdefault(row['difficulty'], []).append(row)
    def ranking(row):
        audit = row['recoverability_audit']
        # The old lexical score is diagnostic only; it does not rank selections.
        return (audit['warning_count'], -audit['explicit_replacement_slots'], row['question_id'])
    for rows in groups.values():
        rows.sort(key=ranking)
    chosen = []
    for difficulty, minimum in FLOORS[name].items():
        chosen.extend(groups[difficulty][:minimum])
    ids = {r['question_id'] for r in chosen}
    remaining = sorted((r for r in pool if r['question_id'] not in ids), key=ranking)
    chosen.extend(remaining[:count - len(chosen)])
    return sorted(chosen, key=lambda r: r['question_id'])

def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--size', type=int, default=200)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Use a new output directory')
    if args.size < 1:
        parser.error('Size must be positive')
    args.out.mkdir(parents=True)
    difficulties = spider_difficulties()
    summary = {}
    for name in ('bird', 'livesql', 'spider'):
        pool = eligible(name, difficulties)
        excluded = EXCLUSIONS[name]
        by_id = {row['question_id']: row for row in pool}
        if missing := set(excluded) - set(by_id):
            raise ValueError(f'{name}: excluded IDs are not eligible: {missing}')
        usable = [recoverability_audit(row) for row in pool if row['question_id'] not in excluded]
        selected = select_recoverable(usable, min(args.size, len(usable)), name)
        assert len({r['question_id'] for r in selected}) == len(selected)
        assert all([l['level'] for l in r['levels']] == [0, 1, 2] for r in selected)
        (args.out / f'{name}_cases.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf8')
        (args.out / f'{name}_excluded_cases.json').write_text(json.dumps([
            {'question_id': i, 'reason': reason, 'original_question': by_id[i]['original_question'],
             'levels': by_id[i]['levels']} for i, reason in excluded.items()
        ], ensure_ascii=False, indent=2), encoding='utf8')
        with (args.out / f'{name}_interaction_inputs.jsonl').open('w', encoding='utf8') as fh:
            for row in selected:
                for level in row['levels']:
                    fh.write(json.dumps({'question_id': row['question_id'], 'db_id': row['db_id'],
                        'difficulty': row['difficulty'], 'level': level['level'],
                        'natural_language': level['natural_language'],
                        'original_question': row['original_question'], 'trace_path': row['trace_path']},
                        ensure_ascii=False) + '\n')
        summary[name] = {'eligible': len(pool), 'excluded_after_review': len(excluded),
            'selected': len(selected), 'interaction_inputs': len(selected) * 3,
            'difficulty': dict(Counter(row['difficulty'] for row in selected)),
            'databases': len({row['db_id'] for row in selected}),
            'flagged_for_further_review': sum(bool(row['recoverability_audit']['warnings']) for row in selected),
            'explicit_replacement_slots': sum(row['recoverability_audit']['explicit_replacement_slots'] for row in selected)}
    (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(summary, ensure_ascii=False))

if __name__ == '__main__':
    main()
