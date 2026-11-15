import json
from pathlib import Path

root = Path(__file__).resolve().parent / 'fixtures' / 'gpt52_stratified80'
sample = json.loads((root / 'SAMPLE.json').read_text(encoding='utf8'))
ids = {row['question_id'] for row in sample['questions'][:40]}
for role in ('broad', 'restricted'):
    source = json.loads((root / f'{role}.json').read_text(encoding='utf8'))
    rows = [row for row in source['cases'] if row['question_id'] in ids]
    assert len(rows) == 120
    out = Path(__file__).resolve().parent / 'fixtures' / f'gpt52_stratified40_{role}.json'
    out.write_text(json.dumps({
        'seed': sample['seed'],
        'design': 'first40_sampled_questions_single_role_three_levels',
        'role_slot': role,
        'source_sample': 'gpt52_stratified80/SAMPLE.json',
        'cases': rows,
    }, ensure_ascii=False, indent=2), encoding='utf8')
    print(role, len(rows), out)
