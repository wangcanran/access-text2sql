import json
from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--role', choices=('broad', 'restricted'), required=True)
args = parser.parse_args()
source = Path(f'clarification_useful_residual_v6/fixtures/gpt52_stratified40_{args.role}.json')
run = Path(f'artifacts/clarification_main/v10_240_{args.role}_20261030')
failed = set()
for path in run.glob('*_reduction.json'):
    row = json.loads(path.read_text(encoding='utf8'))
    if row.get('outcome') in {'unresolved', 'system_error'}:
        failed.add((row['question_id'], row['level']))
payload = json.loads(source.read_text(encoding='utf8'))
payload['design'] = f'retry_unresolved_and_system_error_from_v10_{args.role}'
payload['source_run'] = str(run)
payload['cases'] = [row for row in payload['cases']
                    if (row['question_id'], row['level']) in failed]
assert len(payload['cases']) == len(failed)
out = Path(f'clarification_useful_residual_v6/fixtures/v10_{args.role}_failures.json')
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf8')
print(len(payload['cases']), out)
