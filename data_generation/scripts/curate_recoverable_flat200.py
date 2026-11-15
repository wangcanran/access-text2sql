"""Export inspected three-level ambiguity cases with explicit rejection reasons."""
import argparse
import json
from collections import Counter
from pathlib import Path

from select_recoverable_flat200 import eligible, spider_difficulties

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / 'artifacts/flat_disclosure_cross/recoverable_flat200_v2_20261031'
REJECT = {
    'bird': {
        59: 'Website output becomes generic institutional information; no web/contact slot remains.',
        297: 'Non-carcinogenic restriction disappears at level 0.',
        344: 'Mythic rarity disappears at level 0.',
        989: 'Champion becomes featured/key competitor without a winning criterion.',
        1177: 'Level 1 changes one identified patient into a patient population.',
        105: 'Level 0 asks for a district client profile instead of the original loan-linked client profile.',
        935: 'Level 0 replaces the exact finish criterion with unspecified race-related criteria.',
        49: 'Level 0 turns highest closed-school count into selection based merely on closed schools.',
        521: 'The status requested for a specific play format becomes an unspecified game characteristic.',
        446: 'Level 0 changes the percentage output into an untyped summary.',
        487: 'Level 0 changes the percentage output into an untyped numerical summary.',
        673: 'The oldest-user selection becomes a generic well-defined user subgroup.',
    },
    'livesql': {
        1: 'Top-end maturity restriction disappears at level 0.',
        143: 'Level 1 changes a particular star-wobble discovery technique into any technique.',
        195: 'Level 0 asks for summary information instead of one maximum score.',
        117: 'Level 0 removes both risk and reliability qualifiers and asks for generic flow details.',
        267: 'Level 0 changes fastest network growth into unspecified network changes.',
        121: 'Level 1 drops the rocky-only and no-gas-giants planetary restrictions.',
        123: 'Level 0 uses a selected plant group with no explicit claimed-status condition.',
        89: 'Level 0 does not retain a distinct output slot for each notice category.',
        42: 'Level 0 weakens the highest-concentration selection to most relevant.',
        94: 'Level 1 changes distinct shipping companies into unspecified company counts.',
    },
    'spider': {
        74: 'Level 1 changes a single award maximum into a notably high number.',
        84: 'Level 1 loses the car-maker object of the requested count.',
        150: 'Level 0 turns a count into generic shop information.',
        232: 'Greatest number of orchestras becomes unspecified use by orchestras.',
        234: 'Grade 10 vanishes from both levels; only a generic student group remains.',
        0: 'Level 0 changes a maximum tenure condition into merely being based on tenure.',
        81: 'Level 1 changes zero ships lost into an unspecified tonnage range.',
        106: 'Level 0 loses the conjunctive cats-and-dogs condition, replacing it with unspecified pet types.',
        161: 'Level 0 reduces earliest graduation to a particular graduate without an ordering slot.',
        163: 'Level 1 removes the student-owner constraint without a group slot.',
        217: 'Level 0 refers to this performance metric without a recoverable metric identity.',
        245: 'Level 0 weakens biggest bonus into merely a notable bonus.',
        23: 'Level 0 changes a friends-count requirement into an unspecified condition and generic student information.',
        25: 'Level 1 replaces average horsepower with an unspecified aggregate.',
        33: 'Level 0 loses descending earnings as a ranking measure.',
        87: 'Level 0 silently removes the cat-pet dimension from the exclusion.',
        95: 'Level 0 drops the cartoon-writing relation in favor of unspecified engagement.',
        124: 'Level 1 replaces a requested country attribute with a time/tenure attribute.',
        220: 'Level 0 explicitly says the student identifier is not required, contradicting the original.',
        298: 'Level 0 changes comparison against any European country into comparison against every member.',
        50: 'Level 0 uses distinct languages currently used, silently adding a current-use restriction.',
    },
}

def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Output directory already exists')
    args.out.mkdir(parents=True)
    difficulties = spider_difficulties()
    summary = {}
    for dataset in ('bird', 'livesql', 'spider'):
        pool = eligible(dataset, difficulties)
        by_id = {row['question_id']: row for row in pool}
        seed = json.loads((SEED / f'{dataset}_cases.json').read_text(encoding='utf8'))
        seed_ids = {r['question_id'] for r in seed}
        selected = [r for r in pool if r['question_id'] in seed_ids and r['question_id'] not in REJECT[dataset]]
        extra = [r for r in pool if r['question_id'] not in seed_ids and r['question_id'] not in REJECT[dataset]]
        extra.sort(key=lambda r: (len(r['quality_flags']), -r['quality_score'], r['question_id']))
        selected += extra[:200-len(selected)]
        selected.sort(key=lambda r: r['question_id'])
        assert len(selected) == 200 and len({r['question_id'] for r in selected}) == 200
        assert all([l['level'] for l in r['levels']] == [0, 1, 2] for r in selected)
        assert all(r['levels'][0]['effective_retention_ratio'] < r['levels'][1]['effective_retention_ratio'] < r['levels'][2]['effective_retention_ratio'] for r in selected)
        chosen = {r['question_id'] for r in selected}
        ledger = []
        for row in sorted(pool, key=lambda r: r['question_id']):
            qid = row['question_id']
            ledger.append({'question_id': qid, 'db_id': row['db_id'],
                'difficulty': row['difficulty'],
                'decision': 'excluded_on_inspection' if qid in REJECT[dataset] else 'candidate_selected' if qid in chosen else 'eligible_not_selected',
                'reason': REJECT[dataset].get(qid, 'Eligible; sample capacity' if qid not in chosen else 'Candidate with source and three level texts available; individual semantic certification pending'),
                'trace_path': row['trace_path']})
        (args.out / f'{dataset}_cases.json').write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding='utf8')
        (args.out / f'{dataset}_review_ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf8')
        with (args.out / f'{dataset}_full_pool_review_queue.jsonl').open('w', encoding='utf8') as fh:
            for row in sorted(pool, key=lambda r: r['question_id']):
                trace = json.loads((ROOT / row['trace_path']).read_text(encoding='utf8'))['trace']
                fh.write(json.dumps({'question_id': row['question_id'], 'db_id': row['db_id'],
                    'difficulty': row['difficulty'], 'original_question': row['original_question'],
                    'source_obligations': trace['source_obligations']['obligations'],
                    'levels': [{'level': l['level'], 'text': l['rewritten_request'],
                        'selected_ids': l['selected_ids']} for l in trace['levels']],
                    'status': 'excluded_on_inspection' if row['question_id'] in REJECT[dataset]
                              else 'pending_manual_obligation_review',
                    'reason': REJECT[dataset].get(row['question_id']),
                    'trace_path': row['trace_path']}, ensure_ascii=False) + '\n')
        with (args.out / f'{dataset}_interaction_inputs.jsonl').open('w', encoding='utf8') as fh:
            for row in selected:
                for l in row['levels']:
                    fh.write(json.dumps({'question_id': row['question_id'], 'db_id': row['db_id'],
                        'difficulty': row['difficulty'], 'level': l['level'],
                        'natural_language': l['natural_language'],
                        'original_question': row['original_question'],
                        'trace_path': row['trace_path']}, ensure_ascii=False) + '\n')
        summary[dataset] = {'eligible': len(pool), 'selected': len(selected),
            'excluded_by_manual_review': len(REJECT[dataset]),
            'difficulty': dict(Counter(r['difficulty'] for r in selected)),
            'databases': len({r['db_id'] for r in selected})}
    (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(summary, ensure_ascii=False))

if __name__ == '__main__':
    main()
