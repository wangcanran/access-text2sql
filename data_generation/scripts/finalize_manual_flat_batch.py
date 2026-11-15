"""Finalize individually reviewed cases using explicit case/obligation decisions.

Input JSON contains dataset and a list of case objects. Each case must provide a
nonempty rationale, coverage and overall verdicts, plus an entry for every level
and obligation: {verdict, evidence, question, reason}. No model field is copied.
"""
import argparse
import json
from pathlib import Path

import manual_flat_review as audit


def finalize(spec, dry_run=False):
    dataset = spec['dataset']
    lookup = {str(case['question_id']): case for case in audit.cases(dataset)}
    outcome = {'certified': [], 'rejected': [], 'errors': {}}
    for item in spec['cases']:
        case_id = str(item['question_id'])
        try:
            case = lookup[case_id]
            if not item.get('rationale', '').strip():
                raise ValueError('Missing case-specific rationale')
            review = audit.load(dataset, case)
            source_ids = {o['item_id'] for o in case['source_obligations']}
            if set(item.get('decisions', {})) != {'0', '1'}:
                raise ValueError('Expected levels 0 and 1')
            for level in ('0', '1'):
                if set(item['decisions'][level]) != source_ids:
                    raise ValueError(f'Level {level} obligations differ from source')
            review['decisions'] = item['decisions']
            review['source_coverage'] = item['source_coverage']
            review['overall'] = item['overall']
            review['individual_rationale'] = item['rationale']
            review['individually_adjudicated'] = True
            review.pop('manual_batch_note', None)
            result = audit.validate(case, review)
            if result['status'] not in ('certified', 'rejected'):
                raise ValueError(str(result))
            if not dry_run:
                audit.save(dataset, case, review)
            outcome[result['status']].append(case_id)
        except Exception as error:
            outcome['errors'][case_id] = str(error)
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding='utf-8'))
    print(json.dumps(finalize(spec, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
