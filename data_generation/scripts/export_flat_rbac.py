"""Expand the selected flat questions over their original benchmark roles."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flat', type=Path, default=Path('data/selected/bird/flat_three_distinct_330_seed42'))
    parser.add_argument('--roles', type=Path, default=Path('data/selected/bird/column_level_rbac_dataset_bird_v3_no_sm.json'))
    parser.add_argument('--out', type=Path, default=Path('data/selected/bird/flat_330_roles'))
    args = parser.parse_args()
    cases = json.loads((args.flat / 'cases.json').read_text(encoding='utf8'))
    cases = {r['question_id']: r for r in cases}
    levels = [json.loads(line) for line in (args.flat / 'interaction_inputs.jsonl').read_text(encoding='utf8').splitlines() if line.strip()]
    texts = {(r['question_id'], r['level']): r for r in levels}
    assert len(texts) == len(levels) == len(cases) * 3
    rows = json.loads(args.roles.read_text(encoding='utf8'))
    rows = [r for r in rows if r['metadata']['question_id'] in cases]
    assert {r['metadata']['question_id'] for r in rows} == set(cases)
    identities = [(r['metadata']['question_id'], r['db_id'], r['role']) for r in rows]
    assert len(set(identities)) == len(rows), 'Duplicate question/role'
    for row in rows:
        case = cases[row['metadata']['question_id']]
        assert row['db_id'] == case['db_id'] and row['input'] == case['original_question']
        assert row['metadata']['gold_sql'].strip()
        assert row['metadata']['permission'] in ('allowed', 'denied')
    args.out.mkdir(parents=True, exist_ok=True)
    def save(name, value):
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    save('original.json', rows)
    for level in range(3):
        expanded = deepcopy(rows)
        for row in expanded:
            item = texts[row['metadata']['question_id'], level]
            assert item['db_id'] == row['db_id']
            row['input'] = item['natural_language']
            row['metadata'].update(flat_level=level,
                effective_retention_ratio=item['effective_retention_ratio'],
                reference_question=cases[row['metadata']['question_id']]['original_question'],
                evaluation_reference='Original gold SQL and original-role permission; not a unique gold interpretation of the vague input')
        save(f'level{level}.json', expanded)
    save('smoke.json', json.loads((args.out / 'level0.json').read_text(encoding='utf8'))[:8])
    manifest = {'questions': len(cases), 'rows_per_level': len(rows), 'three_level_rows': len(rows)*3,
        'reference_permission_counts': dict(Counter(r['metadata']['permission'] for r in rows)),
        'roles_per_question': dict(Counter(Counter(r['metadata']['question_id'] for r in rows).values())),
        'db_ids': sorted({r['db_id'] for r in rows}),
        'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (args.roles, args.flat/'cases.json', args.flat/'interaction_inputs.jsonl')},
        'warning': 'Original-gold recovery evaluation, not correctness of every plausible vague interpretation. No clarified requests are included.'}
    save('manifest.json', manifest)
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
