"""Audit reachable answer-transition combinations in saved live traces.

This is a coverage audit, not a semantic quality score. It separates a model
response problem from a missing state-transition case by inspecting the runner's
own turn metadata and the semantic transaction payload.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def traces(folder):
    yield from sorted(Path(folder).glob('*_reduction.json'))


def audit(folder):
    counts = Counter()
    errors = Counter()
    cases = 0
    no_ids = []
    empty_asked = []
    mismatches = []
    for path in traces(folder):
        cases += 1
        record = json.loads(path.read_text(encoding='utf8'))
        if record.get('error'):
            errors[record['error']] += 1
        turns = (record.get('episode', {}).get('session', {}).get('turns') or [])
        sem = iter(c for c in record.get('calls', []) if c.get('stage') == 'semantic_transaction')
        for turn in turns:
            asked = turn.get('asked_dimension')
            origin = (turn.get('scoped_permission_decision') or {}).get('origin_dimension')
            if not asked:
                continue
            counts[(origin or 'none', asked)] += 1
            if origin and origin != asked:
                mismatches.append({'case': path.name, 'origin': origin, 'asked': asked,
                                   'decision_ids': turn.get('decision_ids', []),
                                   'requested_slots': turn.get('requested_slots', [])})
            if not turn.get('decision_ids'):
                no_ids.append({'case': path.name, 'asked': asked,
                               'origin': origin or 'none',
                               'requested_slots': turn.get('requested_slots', [])})
            try:
                call = next(sem)
            except StopIteration:
                continue
            try:
                payload = json.loads(call['messages'][1]['content'])
                event = payload.get('event', {})
                if event.get('asked_decisions') == [] and asked not in ('unanswerable', 'refuse'):
                    empty_asked.append({'case': path.name, 'asked': asked,
                                        'origin': origin or 'none',
                                        'requested_slots': turn.get('requested_slots', []),
                                        'reason': 'semantic_payload_asked_decisions_empty'})
            except (KeyError, TypeError, json.JSONDecodeError):
                pass
    return dict(cases=cases, combinations={f'{a}->{b}': n for (a, b), n in sorted(counts.items())},
                origin_asked_mismatches=mismatches, turns_without_decision_ids=no_ids,
                semantic_payload_empty_asked=empty_asked, errors=dict(errors))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folders', nargs='+', type=Path)
    args = parser.parse_args()
    for folder in args.folders:
        report = audit(folder)
        print(json.dumps({'folder': str(folder), **report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
