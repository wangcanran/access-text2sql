"""Offline menu-equivalence audit using recorded real model responses.

No simulator, live API, or historical final output is used as a quality judge.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from ...live import load_case
from ...response_boundary import decode_response
from ...runtime import ROOT
from ...saved_calls import resolve_saved_call
from ...storage import atomic_json
from .baseline import LightMainMethod
from .method import CachedLightMain
from .store import fingerprint


def audit(paths):
    rows = []
    for path in paths:
        record = json.loads(path.read_text(encoding='utf8'))
        context = load_case(record['question_id'], record['level'])[1]
        responses = {}
        for saved in record['calls']:
            entry = resolve_saved_call(saved, ROOT, model=record['model'])
            if 'response' not in entry or 'messages' not in entry:
                continue
            try:
                raw = decode_response(entry['response'])
            except ValueError:
                continue
            messages = entry['messages']
            key = fingerprint([entry['stage'], messages[0]['content'], json.loads(messages[1]['content'])])
            responses[key] = (raw, entry.get('usage', {}).get('total_tokens', 0))
        for index, session in enumerate(record['episode']['snapshots']):
            if not session.get('view') or session.get('pending'):
                continue
            off, on = LightMainMethod(), CachedLightMain(namespace='offline-audit:' + str(path))
            calls, tokens = [], []
            def call(stage, prompt, payload):
                key = fingerprint([stage, prompt, payload])
                if key not in responses:
                    raise LookupError('No exact saved response for ' + stage)
                value, usage = responses[key]
                calls.append(stage)
                tokens.append(usage)
                return deepcopy(value)
            row = {'source': str(path), 'snapshot': index}
            try:
                with off.installed():
                    plan = off.next_step(session)
                    if plan['action'] != 'menu':
                        continue
                    expected = off.prepare_menu(call, deepcopy(session), plan, context)
                row['baseline_recorded_calls'] = len(calls)
                row['baseline_recorded_tokens'] = sum(tokens)
                calls.clear()
                tokens.clear()
                with on.installed():
                    cold = on.prepare_menu(call, deepcopy(session), plan, context)
                    row['cold_recorded_calls'] = len(calls)
                    calls.clear()
                    tokens.clear()
                    warm = on.prepare_menu(call, deepcopy(session), plan, context)
                    row['warm_recorded_calls'] = len(calls)
                row.update(equal=expected == cold == warm, cache=on.artifacts.summary())
            except (LookupError, ValueError, TypeError, KeyError) as exc:
                row.update(skipped=True, reason=str(exc))
            rows.append(row)
    return {'type': 'offline_replay_not_fresh_live', 'rows': rows,
        'verified_menus': sum(not r.get('skipped') and r.get('equal', False) for r in rows),
        'skipped': sum(bool(r.get('skipped')) for r in rows),
        'avoided_recorded_calls_on_exact_repeat': sum(r.get('baseline_recorded_calls', 0) -
            r.get('warm_recorded_calls', 0) for r in rows if not r.get('skipped'))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--traces', type=Path, nargs='+', default=[ROOT / 'artifacts/clarification_main' /
        '20261021_decision_update_light_v11/light' / (case + '_reduction.json')
        for case in ('796_0', '24_0', '113_1')])
    args = p.parse_args()
    if args.out.exists():
        p.error('Refusing to overwrite a previous audit')
    report = audit(args.traces)
    atomic_json(args.out, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
