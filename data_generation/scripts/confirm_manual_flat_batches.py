"""Confirm reviewed drafts only with a nonempty per-case rationale in the batch spec."""
import argparse
import json
from pathlib import Path

import manual_flat_review as audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('specs', nargs='+', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    counts = {}
    for path in args.specs:
        spec = json.loads(path.read_text(encoding='utf-8'))
        dataset = spec['dataset']
        lookup = {str(case['question_id']): case for case in audit.cases(dataset)}
        accepted = spec.get('accepted', {})
        if not isinstance(accepted, dict):
            raise ValueError(f'{path}: accepted must map each case ID to its individual rationale')
        ids = list(map(str, accepted)) + list(map(str, spec.get('rejected', {})))
        for case_id in ids:
            case = lookup[case_id]
            record = audit.load(dataset, case)
            if record.get('individually_adjudicated') and record.get('individual_rationale'):
                continue
            rationale = accepted.get(case_id, spec.get('rejected', {}).get(case_id, ''))
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError(f'{dataset}/{case_id}: missing individual rationale')
            if not record.get('manual_batch_note'):
                continue
            record['individually_adjudicated'] = True
            record['review_mode'] = 'case_level_manual_confirmation_with_trace_assisted_item_drafts'
            record['individual_rationale'] = rationale
            record['confirmation_source'] = str(path)
            result = audit.validate(case, record)
            if result['status'] not in ('certified', 'rejected'):
                raise ValueError(f'{dataset}/{case_id}: {result}')
            if not args.dry_run:
                audit.save(dataset, case, record)
            counts[result['status']] = counts.get(result['status'], 0) + 1
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    main()
