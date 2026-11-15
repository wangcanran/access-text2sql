"""Compile explicitly adjudicated cases into evidence-bound manual review records.

Input JSON: {"dataset":"spider", "accepted":[1,2],
             "rejected":{"3":"reason"}, "reviewer_note":"I read each original and both rewrites"}.
Only IDs explicitly listed in accepted/rejected are changed. Trace edits supply literal
evidence suggestions, never semantic verdicts. Cases with missing evidence are left
pending and reported for individual review.
"""
import argparse
import json
from pathlib import Path

import manual_flat_review as audit


def excerpt(text, phrase):
    if phrase and phrase in text:
        return phrase
    return None


def evidence(case, level, item_id):
    text = next(row['text'] for row in case['levels'] if row['level'] == level)
    trace = json.loads((audit.ROOT / case['trace_path']).read_text(encoding='utf-8'))['trace']
    trace_level = next(row for row in trace['levels'] if row['level'] == level)
    attempts = trace_level.get('attempts') or []
    edit = next((e for e in attempts[-1].get('edits', []) if e['item_id'] == item_id), None) if attempts else None
    if edit:
        return excerpt(text, edit.get('replacement'))
    inventory = next((e for e in trace_level.get('protected_fragments', []) if e['item_id'] == item_id), None)
    if inventory:
        return excerpt(text, inventory.get('span'))
    spec = next((e for e in trace_level.get('source_specifications', []) if e['item_id'] == item_id), None)
    if spec:
        return excerpt(text, spec.get('source_specification'))
    return None


def commit(spec, dry_run=False):
    dataset = spec['dataset']
    cases = {str(case['question_id']): case for case in audit.cases(dataset)}
    accepted_input = spec.get('accepted', {})
    accepted = list(map(str, accepted_input))
    rejected = {str(k): v for k, v in spec.get('rejected', {}).items()}
    if set(accepted) & set(rejected):
        raise ValueError('Case cannot be both accepted and rejected')
    outcome = {'certified': [], 'rejected': [], 'needs_evidence': {}, 'errors': {}}
    for case_id in accepted + list(rejected):
        try:
            case = cases[case_id]
            if case['status'] == 'excluded_on_inspection':
                raise ValueError('Previously excluded case requires individual review')
            review = audit.load(dataset, case)
            if audit.path_for(dataset, case).exists():
                raise ValueError('Existing manual review requires individual review')
            review['source_coverage'] = {'verdict': 'complete', 'reason': ''}
            review['overall'] = {}
            review['manual_batch_note'] = spec.get('reviewer_note', '')
            rationale = accepted_input.get(case_id) if isinstance(accepted_input, dict) else None
            if rationale is not None:
                if not isinstance(rationale, str) or not rationale.strip():
                    raise ValueError('Case-specific acceptance rationale is required')
                review['individual_rationale'] = rationale
                review['individually_adjudicated'] = True
                review['review_mode'] = 'case_level_manual_confirmation_with_trace_assisted_item_drafts'
            missing = []
            for level in (0, 1):
                text = next(x['text'] for x in case['levels'] if x['level'] == level)
                selected = set(next(x['selected_ids'] for x in case['levels'] if x['level'] == level))
                for item in case['source_obligations']:
                    item_id = item['item_id']
                    phrase = evidence(case, level, item_id)
                    if not phrase:
                        missing.append(f'L{level}/{item_id}')
                        continue
                    verdict = 'askable' if item_id in selected else 'preserved'
                    review['decisions'][str(level)][item_id] = {
                        'verdict': verdict, 'evidence': phrase,
                        'question': f"Which specific {item['requirement'].rstrip('.?')} do you mean?" if verdict == 'askable' else '',
                        'reason': '',
                    }
                review['overall'][str(level)] = {'verdict': 'faithful', 'reason': ''}
            if missing:
                outcome['needs_evidence'][case_id] = missing
                continue
            if case_id in rejected:
                reason = rejected[case_id]
                if not reason:
                    raise ValueError('Rejection requires reason')
                review['individual_rationale'] = reason
                review['individually_adjudicated'] = True
                review['review_mode'] = 'case_level_manual_confirmation_with_trace_assisted_item_drafts'
                if 'source obligation inventory' in reason.lower():
                    review['source_coverage'] = {'verdict': 'incomplete', 'reason': reason}
                review['overall']['0'] = {'verdict': 'unfaithful', 'reason': reason}
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
    print(json.dumps(commit(spec, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
